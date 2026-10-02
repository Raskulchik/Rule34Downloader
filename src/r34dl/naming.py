"""Turning a remote file URL into a safe, unique local path.

Upstream URLs carry no filename at all (the CDN uses a bare hash), and the same
image can legitimately appear under several posts. Both cases are handled here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from pathlib import Path

from .errors import StorageError
from .tagging import MAX_TAG_LENGTH

#: Longest name most filesystems accept, including the extension.
MAX_FILENAME_LENGTH = 200
#: Byte length to stay under on FAT/exFAT volumes (255 bytes, but multi-byte
#: UTF-8 characters expand).
MAX_FILENAME_BYTES = 240

_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS_RE = re.compile(r"\s+")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,8})$")


def sanitize_component(value: str, fallback: str = "file") -> str:
    """Make ``value`` safe to use as a single path component."""
    cleaned = _UNSAFE_RE.sub("_", value)
    # Trailing dots and spaces must go first: Windows silently strips them,
    # which makes the created file unreachable by the name we asked for.
    cleaned = cleaned.strip(". ")
    cleaned = _WS_RE.sub("_", cleaned).strip("_. ")
    if not cleaned:
        return fallback

    stem, ext = _split_ext(cleaned)
    stem = stem[:MAX_TAG_LENGTH].rstrip("_. ") or fallback

    name = f"{stem}{ext}"
    # Stay inside both the character and the byte budget: multi-byte names
    # expand, and FAT/exFAT volumes cap a component at 255 bytes.
    while len(name.encode("utf-8")) > MAX_FILENAME_BYTES and len(stem) > 1:
        stem = stem[:-1].rstrip("_. ") or fallback
        name = f"{stem}{ext}"
    if len(name) > MAX_FILENAME_LENGTH:
        name = name[:MAX_FILENAME_LENGTH]

    if stem.lower() in _WINDOWS_RESERVED:
        name = f"_{stem}{ext}"
    return name


def _normalise_ext(ext: str) -> str:
    """Normalise an extension to lower-case, dot-prefixed form, or ''."""
    if not ext:
        return ""
    cleaned = ext.strip().lstrip(".").lower()
    if not cleaned or not cleaned.isalnum() or len(cleaned) > 8:
        return ""
    return f".{cleaned}"


def _split_ext(name: str) -> tuple[str, str]:
    """Split ``name`` into (stem, extension).

    The returned extension keeps its leading dot, or is ``""`` when absent.
    """
    match = _EXT_RE.search(name)
    if not match:
        return name, ""
    return name[: match.start()], f".{match.group(1)}"


def filename_for(url: str, fallback_id: str, declared_ext: str | None = None) -> str:
    """Derive a filename from a media URL.

    A URL that already carries a filename wins. The media CDN serves
    extension-less URLs (a bare hash), so in that case the post id is used
    instead - it is more recognisable than the hash - and the extension the
    source declared is appended.

    Args:
        url: The remote media URL.
        fallback_id: Post id, used when the URL yields no usable filename.
        declared_ext: Extension the listing source reported, e.g. ``"png"``.
    """
    path_part = url.split("?", 1)[0].split("#", 1)[0]
    tail = path_part.rsplit("/", 1)[-1] if "/" in path_part else path_part
    stem, ext = _split_ext(tail.strip()) if tail.strip(".") else ("", "")

    if ext:
        return sanitize_component(f"{stem}{ext}")

    declared = _normalise_ext(declared_ext or "")
    if not stem:
        return sanitize_component(f"{fallback_id}{declared}")

    # Extension-less URL: prefer the post id over an opaque CDN hash.
    return sanitize_component(f"{fallback_id or stem}{declared}")


def unique_path(directory: Path, filename: str, *, avoid: Collection[Path] = frozenset()) -> Path:
    """Return a path inside ``directory`` that collides with nothing.

    Different posts sometimes reference the same filename. Rather than
    overwriting, disambiguate with a numeric suffix.

    Args:
        directory: Where the file should live.
        filename: The desired name.
        avoid: Paths already spoken for but not yet written to disk. Concurrent
            downloads need this: a name merely *reserved* by another coroutine
            does not exist on the filesystem yet.
    """
    candidate = directory / filename
    if not candidate.exists() and candidate not in avoid:
        return candidate

    stem, ext = _split_ext(filename)
    for index in range(1, 10_000):
        candidate = directory / f"{stem}_{index}{ext}"
        if not candidate.exists() and candidate not in avoid:
            return candidate
    raise StorageError(f"could not find a free filename for {filename!r}")


def ensure_directory(path: Path) -> Path:
    """Create ``path``, returning it. Raises a typed error on failure."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"cannot create directory {path}: {exc}") from exc
    if not os.access(path, os.W_OK):
        raise StorageError(f"directory is not writable: {path}")
    return path