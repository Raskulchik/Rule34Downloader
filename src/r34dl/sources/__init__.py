"""Listing sources: where a tag query is turned into a list of posts.

A source is anything that can turn a :class:`~r34dl.tagging.TagSet` into a
sequence of :class:`~r34dl.models.PostPage`. Adding a new backend means adding
one module here and registering it - nothing else in the codebase changes.
"""

from __future__ import annotations

from typing import Any

from ..errors import ConfigurationError
from .base import PostSource, SourceInfo
from .html_list import PahealHtmlSource
from .json_api import JsonApiSource

#: name -> factory. ``auto`` picks the first source that answers.
_REGISTRY: dict[str, type[PostSource]] = {
    "json": JsonApiSource,
    "paheal": PahealHtmlSource,
}

AUTO = "auto"

__all__ = [
    "AUTO",
    "SOURCES",
    "JsonApiSource",
    "PahealHtmlSource",
    "PostSource",
    "SourceInfo",
    "available_sources",
    "build_source",
]

SOURCES: tuple[str, ...] = (AUTO, *_REGISTRY.keys())


def available_sources() -> tuple[str, ...]:
    return SOURCES


def build_source(name: str, **kwargs: Any) -> PostSource:
    """Instantiate a source by name.

    Raises:
        ConfigurationError: if ``name`` is not registered.
    """
    key = name.strip().lower()
    try:
        factory = _REGISTRY[key]
    except KeyError:
        raise ConfigurationError(
            f"unknown source {name!r}; available: {', '.join(SOURCES)}"
        ) from None
    return factory(**kwargs)