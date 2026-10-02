"""Exception hierarchy for r34dl.

Every error the pipeline raises deliberately derives from :class:`R34Error` so
that the CLI can distinguish "we expected this and handled it" from "the code
has a bug".
"""

from __future__ import annotations


class R34Error(Exception):
    """Base class for every error raised by this package."""


class SourceError(R34Error):
    """A post listing source failed."""


class SourceUnavailableError(SourceError):
    """The source is temporarily unusable (CAPTCHA wall, 5xx, geo-block)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BotProtectedError(SourceError):
    """The host answered with an anti-bot / CAPTCHA interstitial.

    Recoverable by the user: they can supply a session cookie, or switch to
    another source.
    """

    def __init__(self, host: str, status: int) -> None:
        super().__init__(
            f"{host} returned an anti-bot page (HTTP {status}). "
            f"Pass --cookie 'PHPSESSID=...' with your own session, "
            f"or use --source paheal."
        )
        self.host = host
        self.status = status


class DownloadError(R34Error):
    """A single file could not be downloaded."""


class StorageError(R34Error):
    """The destination filesystem refused an operation."""


class ConfigurationError(R34Error):
    """User-supplied configuration is invalid."""