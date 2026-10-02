"""Parsing and rendering of a tag query.

The user types a bare tag string (``"Miyabi -ai_generated"``). This module turns
that into two things: a URL query for the source, and a filesystem-safe folder
name.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

from .errors import ConfigurationError

MAX_TAG_LENGTH = 100
MAX_TAGS = 40

#: Characters the source treats as query separators.
_SPLIT_RE = re.compile(r"\s+")
#: Anything not allowed in a folder name on Windows/macOS/Linux.
_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Tag:
    """A single tag, optionally negated with a ``-`` prefix."""

    name: str
    negated: bool = False

    def __str__(self) -> str:
        return f"-{self.name}" if self.negated else self.name

    def encoded(self) -> str:
        quoted = quote(self.name, safe="_-.")
        return f"-{quoted}" if self.negated else quoted


@dataclass(frozen=True, slots=True)
class TagSet:
    """An ordered, de-duplicated collection of tags."""

    tags: tuple[Tag, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(self.tags)

    def __len__(self) -> int:
        return len(self.tags)

    def __iter__(self) -> Iterator[Tag]:
        return iter(self.tags)

    @classmethod
    def parse(cls, raw: str | None) -> TagSet:
        """Parse a raw user string into a validated tag set.

        Raises:
            ConfigurationError: if any individual tag is malformed or the set is
                too large to be a sensible search.
        """
        if raw is None:
            return cls(())
        tokens = [token for token in _SPLIT_RE.split(raw.strip()) if token]
        if not tokens:
            return cls(())
        if len(tokens) > MAX_TAGS:
            raise ConfigurationError(f"too many tags ({len(tokens)}, max {MAX_TAGS})")

        seen: set[tuple[str, bool]] = set()
        tags: list[Tag] = []
        for token in tokens:
            negated = token.startswith("-")
            name = token[1:] if negated else token
            if not name:
                raise ConfigurationError(
                    f"invalid tag: {token!r} (a bare '-' excludes nothing)"
                )
            if len(name) > MAX_TAG_LENGTH:
                raise ConfigurationError(f"tag too long (max {MAX_TAG_LENGTH}): {name!r}")
            if any(char.isspace() for char in name):
                raise ConfigurationError(f"tag cannot contain whitespace: {name!r}")

            key = (name, negated)
            if key in seen:
                continue
            seen.add(key)
            tags.append(Tag(name=name, negated=negated))
        return cls(tuple(tags))

    def query(self) -> str:
        """Render as a query string fragment (space separated)."""
        return " ".join(tag.encoded() for tag in self.tags)

    def legacy_query(self) -> str:
        """Render the way the original tool did: ``+``-joined.

        Kept because some sources are more forgiving with that form.
        """
        return "+".join(tag.encoded() for tag in self.tags)

    def folder_name(self, fallback: str = "untagged") -> str:
        """A short, filesystem-safe directory name derived from the tags."""
        if not self.tags:
            return fallback

        name = "_".join(str(tag) for tag in self.tags)
        name = _UNSAFE_RE.sub("_", name)
        name = _WS_RE.sub("_", name).strip("_. ")
        if not name:
            return fallback
        return _cap(name, 80)

    def __str__(self) -> str:
        return " ".join(str(tag) for tag in self.tags)


def _cap(value: str, limit: int) -> str:
    """Shorten ``value`` to ``limit`` chars, keeping it collision-free."""
    if len(value) <= limit:
        return value
    return f"{value[: limit - 9].rstrip('_. ')}_{_digest(value)[:8]}"


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()