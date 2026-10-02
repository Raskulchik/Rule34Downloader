"""Deciding where files land, and writing them safely."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from collections.abc import AsyncIterable, Iterable
from pathlib import Path

from .errors import StorageError
from .naming import ensure_directory

log = logging.getLogger(__name__)


def default_downloads_dir() -> Path:
    """Where downloads go when the user does not say otherwise."""
    if os.name == "nt":
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            return Path(userprofile) / "Downloads"
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        return Path(xdg).expanduser()
    return Path.home() / "Downloads"


class OutputLayout:
    """Resolves the destination directory, with a graceful fallback.

    If the requested base directory cannot be created (read-only mount, denied
    permissions, ...) we fall back to a writable location instead of crashing -
    the same idea the original tool had, but applied consistently and in one
    place.
    """

    def __init__(self, base_dir: Path, folder_name: str) -> None:
        self.base_dir = base_dir
        self.folder_name = folder_name
        self.fallback_used: Path | None = None
        self.target: Path | None = None

    def resolve(self) -> Path:
        """Create and return the destination directory.

        Raises:
            StorageError: only if no writable location could be found at all.
        """
        candidates = [self.base_dir / self.folder_name]
        if self.base_dir != default_downloads_dir():
            candidates.append(default_downloads_dir() / self.folder_name)
        candidates.append(Path.home() / self.folder_name)

        last_error: StorageError | None = None
        for candidate in candidates:
            try:
                ensure_directory(candidate)
            except StorageError as exc:
                last_error = exc
                log.debug("cannot use %s: %s", candidate, exc)
                continue
            self.target = candidate
            if candidate != candidates[0]:
                self.fallback_used = candidate
            return candidate

        raise StorageError(
            "no writable download directory found; tried: "
            + ", ".join(str(c) for c in candidates)
        ) from last_error


_PART_COUNTER = itertools.count()


def _part_path(path: Path) -> Path:
    """A temp path unique to this write.

    Two coroutines racing for the same destination must not share one temp
    file, or their bytes interleave into a corrupt image.
    """
    return path.with_name(f"{path.name}.{next(_PART_COUNTER)}.part")


def atomic_write(path: Path, chunks: Iterable[bytes]) -> int:
    """Write an iterable of chunks to ``path`` atomically (blocking).

    Returns:
        Number of bytes written.
    """
    tmp = _part_path(path)
    total = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
                total += len(chunk)
        os.replace(tmp, path)
    except OSError as exc:
        _cleanup(tmp)
        raise StorageError(f"cannot write {path}: {exc}") from exc
    except BaseException:
        _cleanup(tmp)
        raise
    return total


async def atomic_write_async(path: Path, chunks: AsyncIterable[bytes]) -> int:
    """Stream an async byte source into ``path`` atomically.

    Network reads stay on the event loop while the blocking disk writes are
    pushed to a worker thread, so a slow drive cannot stall the other
    downloads.

    Data lands in a sibling ``.part`` file and is renamed into place only on
    success, so an interrupted run never leaves a half-written file that a
    later resume would mistake for a finished one.

    Returns:
        Number of bytes written.

    Raises:
        StorageError: if the filesystem refuses the write.
    """
    tmp = _part_path(path)
    written = 0

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"cannot prepare {path.parent}: {exc}") from exc

    try:
        handle = await asyncio.to_thread(open, tmp, "wb")
        try:
            async for chunk in chunks:
                written += len(chunk)
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.shield(asyncio.to_thread(handle.close))
        await asyncio.to_thread(os.replace, tmp, path)
    except OSError as exc:
        await _cleanup_async(tmp)
        raise StorageError(f"cannot write {path}: {exc}") from exc
    except BaseException:
        # Covers CancelledError: never leave a stray .part behind.
        await _cleanup_async(tmp)
        raise
    return written


async def _cleanup_async(path: Path) -> None:
    try:
        await asyncio.to_thread(_cleanup, path)
    except Exception:  # pragma: no cover - best effort
        log.debug("could not remove temp file %s", path)


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.debug("could not remove temp file %s", path)