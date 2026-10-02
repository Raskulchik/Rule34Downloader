"""Console output and prompts.

All user-facing I/O lives here so the pipeline stays silent and testable.
"""

from __future__ import annotations

import sys
from typing import Any, TextIO

# tqdm is optional: the tool must run without it.
_tqdm_factory: Any
try:
    from tqdm import tqdm as _tqdm_factory

    HAS_TQDM = True
except ImportError:  # pragma: no cover - depends on the environment
    HAS_TQDM = False


class ProgressBar:
    """A progress bar that degrades to a no-op when tqdm is unavailable."""

    __slots__ = ("_bar",)

    def __init__(self, total: int, desc: str, stream: TextIO) -> None:
        self._bar = (
            _tqdm_factory(total=total, desc=desc, unit="file", leave=True, file=stream)
            if HAS_TQDM and total > 0
            else None
        )

    def tick(self, amount: int = 1) -> None:
        if self._bar is not None:
            self._bar.update(amount)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def __enter__(self) -> ProgressBar:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Console:
    """Small wrapper around stdout/stderr with consistent formatting."""

    def __init__(self, stream: TextIO | None = None, quiet: bool = False) -> None:
        self.stream = stream or sys.stdout
        self.quiet = quiet

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=self.stream)

    def warn(self, message: str) -> None:
        print(f"warning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"error: {message}", file=sys.stderr)

    def plain(self, message: str = "") -> None:
        print(message, file=self.stream)

    def progress(self, total: int, desc: str) -> ProgressBar:
        """Build a progress bar for ``total`` units of work."""
        return ProgressBar(0 if self.quiet else total, desc, self.stream)

    # -- prompts ---------------------------------------------------------
    def ask(self, prompt: str, default: str = "") -> str:
        """Ask a question. Falls back to ``default`` when stdin is closed."""
        try:
            answer = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            self.plain()
            return default
        return answer or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        """Ask a yes/no question, accepting the common shorthands."""
        hint = "[Y/n]" if default else "[y/N]"
        answer = self.ask(f"{prompt} {hint} ").lower()
        if not answer:
            return default
        return answer in {"y", "yes", "da", "да", "1"}

    def ask_int(self, prompt: str, default: int) -> int:
        """Ask for a number, re-asking on nonsense input."""
        while True:
            raw = self.ask(prompt, str(default))
            try:
                value = int(raw)
            except ValueError:
                self.warn(f"not a number: {raw!r}")
                continue
            if value < 0:
                self.warn("must not be negative")
                continue
            return value