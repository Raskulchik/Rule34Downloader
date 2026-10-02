"""Command line interface.

Two modes share one pipeline:

* interactive - asks for tags, count and confirmation (the original behaviour)
* scripted    - ``--tags``/``--limit``/``--yes`` make it usable from a shell,
  cron job, or a shell function.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings
from .errors import ConfigurationError, R34Error
from .net.client import USER_AGENT, HttpClient
from .pipeline import run_download
from .sources import SOURCES, build_source
from .storage import default_downloads_dir
from .tagging import TagSet
from .ui import Console

EPILOG = """\
examples:
  r34dl                                  interactive mode
  r34dl -t "Miyabi -ai_generated"       search directly, still confirms
  r34dl -t "cute" -n 20 -y               fully non-interactive
  r34dl -t "1girl" -o ~/archive --flat   one folder, no tag subfolder
  r34dl -t "cute" --dry-run              list what would be downloaded
  r34dl -t "1girl" --cookie "PHPSESSID=..."   pass the anti-bot wall
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r34dl",
        description="Download posts from rule34 by tag.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-t", "--tags", help="tag query, e.g. 'Miyabi -ai_generated'")
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=default_downloads_dir(),
        help="base directory for downloads (default: %(default)s)",
    )
    parser.add_argument(
        "-n", "--limit", type=int, metavar="N",
        help="download at most N posts (0 or omitted = everything)",
    )
    parser.add_argument(
        "-s", "--source", choices=SOURCES, default="auto",
        help="listing source to use (default: %(default)s)",
    )
    parser.add_argument("-c", "--concurrency", type=int, default=5,
                        help="parallel downloads, 1-16 (default: %(default)s)")
    parser.add_argument("-r", "--rate", type=float, default=4.0, metavar="N",
                        help="max requests per second (default: %(default)s)")
    parser.add_argument("--max-pages", type=int, default=200,
                        help="stop after this many list pages (default: %(default)s)")
    parser.add_argument("--cookie", help="Cookie header, e.g. 'PHPSESSID=...'")
    parser.add_argument("--user-agent", default=USER_AGENT, help="override the User-Agent")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout")
    parser.add_argument("--flat", action="store_true",
                        help="save into the output dir directly, without a tag subfolder")
    parser.add_argument("--overwrite", action="store_true", help="re-download existing files")
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation prompts")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be downloaded, write nothing")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="posts per batch before pausing (default: %(default)s)")
    parser.add_argument("--list-sources", action="store_true", help="show sources and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    return parser


def settings_from_args(args: argparse.Namespace, console: Console) -> Settings:
    """Build :class:`Settings`, prompting for anything missing.

    Raises:
        ConfigurationError: if the interaction cannot be satisfied.
    """
    if args.list_sources:
        print_sources(console)
        raise SystemExit(0)

    settings = Settings(
        tags=args.tags or "",
        limit=args.limit if args.limit and args.limit > 0 else None,
        max_pages=args.max_pages,
        source=args.source,
        output_dir=args.output_dir.expanduser().resolve(),
        flat=args.flat,
        concurrency=args.concurrency,
        batch_size=args.batch_size,
        request_rate=args.rate,
        max_attempts=5,
        timeout=args.timeout,
        cookie=args.cookie,
        user_agent=args.user_agent,
        assume_yes=args.yes,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        quiet=args.quiet,
        verbose=args.verbose,
    ).clamp()

    # --- interactive fallbacks ---
    if not settings.tags.strip():
        if not sys.stdin.isatty():
            raise ConfigurationError(
                "no tags given and stdin is not a terminal; pass --tags '...'"
            )
        settings = settings.with_overrides(
            tags=console.ask("Tags? (example: Miyabi -ai_generated): ")
        )
        if not settings.tags.strip():
            raise ConfigurationError("at least one tag is required")

    # Validate now so a typo fails before any request is made.
    TagSet.parse(settings.tags)

    if (
        settings.limit is None
        and not settings.assume_yes
        and not settings.dry_run
        and sys.stdin.isatty()
    ):
        answer = console.ask_int("How many posts? (0 or Enter = all): ", 0)
        if answer > 0:
            settings = settings.with_overrides(limit=answer)

    return settings


def print_sources(console: Console) -> None:
    console.plain("Available sources:")
    for name in SOURCES:
        if name == "auto":
            continue
        # A throwaway client is enough; info() does not perform I/O.
        source = build_source(name, client=HttpClient())
        info = source.info()
        flag = " (needs --cookie)" if info.needs_cookie else ""
        console.plain(f"  {info.name:<8} {info.description}{flag}")


async def _amain(settings: Settings, console: Console) -> int:
    report = await run_download(settings, console)
    console.plain()
    if report.cancelled:
        console.warn("interrupted - partial download, re-run to resume")
    console.plain(f"Done: {report.summary()}.")
    if report.failures:
        console.warn(f"{len(report.failures)} file(s) failed; re-run to retry them.")
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose, args.quiet)
    console = Console(quiet=args.quiet or args.dry_run)

    try:
        settings = settings_from_args(args, console)
    except ConfigurationError as exc:
        console.error(str(exc))
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)

    if settings.verbose:
        console.plain(f"Settings: {settings}")

    try:
        return asyncio.run(_guarded(settings, console))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        console.warn("\ninterrupted")
        return 130
    except R34Error as exc:
        console.error(str(exc))
        return 1


async def _guarded(settings: Settings, console: Console) -> int:
    """Run the pipeline with Ctrl+C turned into clean cancellation."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:  # pragma: no cover - always set in practice
        return await _amain(settings, console)

    def _cancel() -> None:
        task.cancel()

    installed: list[signal.Signals] = []
    for sig in _cancel_signals():
        try:
            loop.add_signal_handler(sig, _cancel)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows, or a non-main thread: fall back to KeyboardInterrupt.
            pass
    try:
        return await _amain(settings, console)
    except asyncio.CancelledError:
        console.warn("\ninterrupted - partial download, re-run to resume")
        return 130
    finally:
        for sig in installed:
            _suppress(loop.remove_signal_handler, sig)


def _cancel_signals() -> tuple[signal.Signals, ...]:
    if hasattr(signal, "SIGINT"):
        return (signal.SIGINT,)
    return ()


def _suppress(fn: Callable[..., Any], *args: Any) -> None:
    """Best-effort cleanup: never let teardown mask the real error."""
    with contextlib.suppress(Exception):
        fn(*args)


def _configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if not quiet else logging.ERROR
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


__all__ = ["build_parser", "main"]