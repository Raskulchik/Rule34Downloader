"""Runtime configuration.

One dataclass carries every tunable, so the CLI is the only place that decides
values and the rest of the package just reads them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .storage import default_downloads_dir


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the pipeline needs to run."""

    # -- what to fetch --
    tags: str = ""
    limit: int | None = None
    max_pages: int = 200
    source: str = "auto"

    # -- where to put it --
    output_dir: Path = field(default_factory=default_downloads_dir)
    flat: bool = False

    # -- how hard to try --
    concurrency: int = 5
    batch_size: int = 100
    request_rate: float = 4.0
    max_attempts: int = 5
    timeout: float = 30.0

    # -- session --
    cookie: str | None = None
    user_agent: str | None = None

    # -- behaviour --
    assume_yes: bool = False
    dry_run: bool = False
    overwrite: bool = False
    quiet: bool = False
    verbose: bool = False

    def with_overrides(self, **kwargs: Any) -> Settings:
        """Return a copy with the given fields replaced."""
        return replace(self, **kwargs)

    def clamp(self) -> Settings:
        """Force values into safe ranges.

        Guards against a user (or a typo) asking for something absurd such as
        10_000 concurrent connections.
        """
        return self.with_overrides(
            concurrency=max(1, min(self.concurrency, 16)),
            max_attempts=max(1, min(self.max_attempts, 10)),
            max_pages=max(1, self.max_pages),
            limit=self.limit if self.limit is None else max(0, self.limit),
            request_rate=max(0.1, min(self.request_rate, 30.0)),
        )