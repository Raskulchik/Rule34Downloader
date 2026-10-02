"""Concurrent, resumable downloading of post files."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from .errors import DownloadError, SourceUnavailableError, StorageError
from .models import DownloadReport, Outcome, Post
from .naming import filename_for, unique_path
from .net.client import HttpClient
from .storage import atomic_write_async
from .ui import ProgressBar

log = logging.getLogger(__name__)

#: Substrings that mean "this host is serving an anti-bot page".
BOT_MARKERS = (b"captcha", b"challenge-platform", b"cf-browser-verification")


class Downloader:
    """Fetches posts into a directory, politely and resumably.

    Existing files are skipped, so re-running a command after an interruption
    picks up where it left off. A failure on one file never aborts the rest.
    """

    def __init__(
        self,
        client: HttpClient,
        *,
        concurrency: int = 5,
        overwrite: bool = False,
        on_progress: ProgressBar | None = None,
        batch_size: int = 100,
        batch_pause: float = 0.0,
        referer: str | None = None,
    ) -> None:
        self.client = client
        self.concurrency = max(1, concurrency)
        self.overwrite = overwrite
        self.progress = on_progress
        self.batch_size = max(1, batch_size)
        self.batch_pause = batch_pause
        self.referer = referer
        self._semaphore = asyncio.Semaphore(self.concurrency)
        #: Destination paths reserved during this run, to defeat races.
        self._claimed: set[Path] = set()

    async def download_all(self, posts: Iterable[Post], directory: Path) -> DownloadReport:
        """Download every post in ``posts`` into ``directory``."""
        report = DownloadReport()
        materialised = list(posts)

        try:
            for start in range(0, len(materialised), self.batch_size):
                batch = materialised[start : start + self.batch_size]
                await asyncio.gather(
                    *(asyncio.create_task(self._guard(p, directory, report)) for p in batch)
                )
                # Be a good citizen between batches.
                if self.batch_pause and start + self.batch_size < len(materialised):
                    await asyncio.sleep(self.batch_pause)
        except asyncio.CancelledError:
            report.cancelled = True
            raise
        finally:
            if self.progress:
                self.progress.close()

        return report

    async def _guard(self, post: Post, directory: Path, report: DownloadReport) -> None:
        """Run one download, recording the outcome instead of raising."""
        try:
            outcome, detail = await self.download_one(post, directory)
        except asyncio.CancelledError:
            report.record(post, Outcome.CANCELLED)
            raise
        except (DownloadError, StorageError, SourceUnavailableError) as exc:
            log.warning("post %s failed: %s", post.id or "?", exc)
            report.record(post, Outcome.FAILED, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("unexpected error for post %s", post.id or "?")
            report.record(post, Outcome.FAILED, f"{type(exc).__name__}: {exc}")
        else:
            report.record(post, outcome, detail)
            if self.progress:
                self.progress.tick()

    async def download_one(self, post: Post, directory: Path) -> tuple[Outcome, str]:
        """Download a single post. Returns the outcome and a human detail."""
        if not post.file_url:
            return Outcome.SKIPPED_NO_SOURCE, "no file url"

        filename = filename_for(post.file_url, fallback_id=post.id or "post", declared_ext=post.ext)

        # Claim the destination under the semaphore: checking and then writing
        # without it would let two posts race for one filename.
        async with self._semaphore:
            target = self._claim(directory, filename)
            if target is None:
                return Outcome.SKIPPED_EXISTING, "already downloaded"
            try:
                written = await self._fetch_to(post, target)
            except asyncio.CancelledError:
                raise
            except (StorageError, SourceUnavailableError, DownloadError):
                raise
            except OSError as exc:
                raise DownloadError(f"disk error: {exc}") from exc

        return Outcome.DOWNLOADED, f"{written} bytes"

    def _claim(self, directory: Path, filename: str) -> Path | None:
        """Reserve a destination path, or return None if it is already there."""
        target = directory / filename
        if target.exists():
            if not self.overwrite:
                return None
            try:
                target.unlink()
            except OSError as exc:
                raise DownloadError(f"cannot replace {target}: {exc}") from exc

        # Disambiguate names already claimed earlier in this same run.
        target = unique_path(directory, filename, avoid=self._claimed)
        self._claimed.add(target)
        return target

    async def _fetch_to(self, post: Post, target: Path) -> int:
        """Stream one post to ``target`` atomically."""
        url = post.file_url
        if url is None:  # pragma: no cover - callers filter these out
            raise DownloadError("post has no file url")

        async def _stream() -> AsyncIterator[bytes]:
            first = True
            async for chunk in self.client.stream(url, referer=self.referer):
                if first:
                    first = False
                    if _looks_like_html_or_captcha(chunk):
                        raise DownloadError(
                            "server returned an HTML/anti-bot page instead of an image"
                        )
                yield chunk

        try:
            return await atomic_write_async(target, _stream())
        except StorageError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise DownloadError(f"{type(exc).__name__}: {exc}") from exc


def _looks_like_html_or_captcha(first_chunk: bytes) -> bool:
    """Guard against saving a CAPTCHA page as if it were the image."""
    lowered = first_chunk[:2048].lower()
    if any(marker in lowered for marker in BOT_MARKERS):
        return True
    head = lowered.lstrip()[:200]
    return head.startswith(b"<html") or head.startswith(b"<!doctype")


__all__ = ["Downloader"]