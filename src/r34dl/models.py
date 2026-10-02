"""Immutable data models shared across the pipeline.

Both listing sources normalise into :class:`Post`, so the downloader never has
to care whether a record came from JSON or from scraped HTML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .errors import SourceError


class Outcome(StrEnum):
    """Terminal state of one download attempt."""

    DOWNLOADED = "downloaded"
    SKIPPED_EXISTING = "skipped-existing"
    SKIPPED_NO_SOURCE = "skipped-no-source"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_success(self) -> bool:
        return self is not Outcome.FAILED


@dataclass(frozen=True, slots=True)
class Post:
    """A single listing entry, normalised across sources."""

    id: str
    file_url: str | None
    md5: str | None = None
    ext: str | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None
    score: int | None = None
    tags: tuple[str, ...] = ()
    created_at: str | None = None
    source: str = "unknown"

    @property
    def dimensions(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "?"


@dataclass(frozen=True, slots=True)
class PostPage:
    """One page of results plus the metadata needed to keep paginating."""

    posts: tuple[Post, ...]
    page: int
    has_more: bool

    def __len__(self) -> int:
        return len(self.posts)


@dataclass(slots=True)
class DownloadReport:
    """Aggregated result of a download run."""

    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    failures: list[tuple[str, str]] = field(default_factory=list)

    def record(self, post: Post, outcome: Outcome, detail: str = "") -> None:
        if outcome is Outcome.DOWNLOADED:
            self.downloaded += 1
        elif outcome is Outcome.FAILED:
            self.failed += 1
            self.failures.append((post.id, detail or "unknown error"))
        elif outcome is Outcome.CANCELLED:
            self.cancelled = True
        else:
            self.skipped += 1

    @property
    def total(self) -> int:
        return self.downloaded + self.skipped + self.failed

    def summary(self) -> str:
        parts = [f"{self.downloaded} downloaded"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return ", ".join(parts)


def coerce_post(raw: object, source: str) -> Post:
    """Build a :class:`Post` from an untrusted source record.

    The upstream API is a PHP application, so scalar fields can arrive as
    strings and URL fields can arrive as a single-element array or as an object
    keyed by ``"0"`` depending on the deployment. Everything is coerced here so
    that no other module has to defend against that.
    """
    if not isinstance(raw, dict):
        raise SourceError(f"expected an object from {source}, got {type(raw).__name__}")

    return Post(
        id=str(raw.get("id") or raw.get("post_id") or "").strip(),
        file_url=first_url(raw.get("file_url")),
        md5=_opt_str(raw.get("md5")),
        ext=_opt_str(raw.get("file_ext") or raw.get("ext")),
        width=_opt_int(raw.get("width")),
        height=_opt_int(raw.get("height")),
        size=_opt_int(raw.get("file_size")),
        score=_opt_int(raw.get("score")),
        tags=_opt_tags(raw.get("tags")),
        created_at=_opt_str(raw.get("created_at")),
        source=source,
    )


def first_url(value: object) -> str | None:
    """Extract the first usable URL from a loosely typed value.

    Handles every shape observed in the wild: a plain string, a JSON array, and
    a PHP-style object keyed by numeric strings.
    """
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if isinstance(value, (list, tuple)):
        for item in value:
            url = first_url(item)
            if url:
                return url
        return None
    if isinstance(value, dict):
        for key in sorted(value, key=_numeric_sort_key):
            url = first_url(value[key])
            if url:
                return url
        return None
    return None


def _numeric_sort_key(key: object) -> tuple[int, float | str]:
    text = str(key)
    return (0, float(text)) if text.isdigit() else (1, text)


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: object) -> int | None:
    text = _opt_str(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _opt_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part for part in str(value).split() if part)