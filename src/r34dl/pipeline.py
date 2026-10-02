"""Wiring the pieces together: fetch -> resolve -> download."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .download import Downloader
from .errors import ConfigurationError, R34Error
from .models import DownloadReport, Post
from .net.client import HttpClient, RetryPolicy
from .net.ratelimit import RateLimiter
from .sources import AUTO, PostSource, build_source
from .storage import OutputLayout
from .tagging import TagSet
from .ui import Console

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Plan:
    """The resolved destination and tag set for a run."""

    tags: TagSet
    directory: Path
    fallback_used: Path | None


def build_client(settings: Settings) -> HttpClient:
    """Construct an HTTP client honouring the configured rate limit."""
    limiter = RateLimiter(rate=settings.request_rate, period=1.0)
    return HttpClient(
        retry=RetryPolicy(attempts=settings.max_attempts),
        limiter=limiter,
        cookie=settings.cookie,
        timeout=settings.timeout,
    )


def resolve_plan(settings: Settings) -> Plan:
    """Work out where files will be written, creating the directory."""
    tags = TagSet.parse(settings.tags)
    folder = tags.folder_name() if not settings.flat else "r34dl"
    layout = OutputLayout(settings.output_dir, folder)
    directory = layout.resolve()
    if layout.fallback_used is not None:
        log.info("using fallback directory %s", layout.fallback_used)
    return Plan(tags=tags, directory=directory, fallback_used=layout.fallback_used)


async def collect_posts(
    source: PostSource,
    settings: Settings,
    on_page: Callable[[int], None] | None = None,
) -> list[Post]:
    """Gather posts from ``source``, up to the configured limit."""
    posts: list[Post] = []
    async for post in source.iter_posts(
        TagSet.parse(settings.tags),
        max_pages=settings.max_pages,
        max_posts=settings.limit,
    ):
        posts.append(post)
        if on_page and len(posts) % settings.batch_size == 0:
            on_page(len(posts))
    if on_page:
        on_page(len(posts))
    return posts


async def run_download(settings: Settings, console: Console) -> DownloadReport:
    """Execute a full download run.

    Raises:
        R34Error: on unrecoverable problems; callers present the message.
    """
    plan = resolve_plan(settings)
    posts, source_name = await _gather(plan.tags, settings, console)

    if not posts:
        console.plain("Nothing found.")
        return DownloadReport()

    console.info(f"Found {len(posts)} posts total.")
    if settings.dry_run:
        _preview(posts, plan.directory, console)
        return DownloadReport()

    if not settings.assume_yes and not console.confirm(f"Start download of {len(posts)} posts?"):
        console.info("Cancelled by user.")
        return DownloadReport()

    async with build_client(settings) as client:
        # The listing source doubles as the download host, so keep using it.
        downloader = Downloader(
            client,
            concurrency=settings.concurrency,
            overwrite=settings.overwrite,
            on_progress=console.progress(len(posts), "Downloading"),
            batch_size=settings.batch_size,
            referer=_referer_for(source_name),
        )
        report = await downloader.download_all(posts, plan.directory)

    _report(console, report)
    return report


def _referer_for(source_name: str) -> str | None:
    """Send a Referer some CDNs expect on image requests."""
    return {
        "paheal": "https://rule34.paheal.net/",
        "json": "https://rule34.xxx/",
    }.get(source_name)


def _report(console: Console, report: DownloadReport) -> None:
    if report.failed:
        console.warn(f"{report.failed} file(s) failed; re-run to retry them.")


async def _gather(
    tags: TagSet, settings: Settings, console: Console
) -> tuple[list[Post], str]:
    """List posts, trying each candidate source in turn.

    Returns:
        The posts found and the name of the source that produced them.

    Raises:
        R34Error: if every candidate source failed.
    """
    names = _candidate_sources(settings.source)
    errors: list[str] = []
    empty = False

    async with build_client(settings) as client:
        for name in names:
            source = build_source(name, client=client)
            label = source.info().name
            console.info(f"Fetching post list via {label}...")
            try:
                posts = await collect_posts(source, settings, on_page=_page_printer(console))
            except R34Error as exc:
                log.debug("source %s failed: %s", label, exc)
                console.warn(f"{label} unavailable: {exc}")
                errors.append(f"{label}: {exc}")
                continue
            if posts:
                return posts, label
            # The source answered cleanly, there is simply nothing to download.
            # Trying the others would just repeat this.
            console.warn(f"{label} returned no posts matching these tags.")
            empty = True
            break

    if errors and not empty:
        raise ConfigurationError(
            "could not reach any listing source:\n  - " + "\n  - ".join(errors)
        )
    return [], names[0] if names else AUTO


def _candidate_sources(preferred: str) -> tuple[str, ...]:
    """Source names to try, in order."""
    if preferred and preferred != AUTO:
        return (preferred,)
    # paheal first: it works without a cookie, the JSON API often does not.
    return ("paheal", "json")


def _page_printer(console: Console) -> Callable[[int], None]:
    last = {"n": -1}

    def _on_page(count: int) -> None:
        if count != last["n"]:
            last["n"] = count
            console.info(f"  retrieved {count} posts")

    return _on_page


def _preview(posts: list[Post], directory: Path, console: Console) -> None:
    console.plain()
    console.plain(f"Would write {len(posts)} files to {directory}:")
    for post in posts[:10]:
        console.plain(f"  {post.id:>10}  {post.dimensions:>10}  {post.file_url}")
    if len(posts) > 10:
        console.plain(f"  ... and {len(posts) - 10} more")