"""The contract every listing source implements."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..models import Post, PostPage
from ..net.client import HttpClient
from ..tagging import TagSet


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Human-facing description of a source, used by ``--list-sources``."""

    name: str
    description: str
    needs_cookie: bool = False


class PostSource(abc.ABC):
    """Fetches posts matching a tag query, one page at a time.

    Subclasses implement :meth:`fetch_page` only; pagination bookkeeping and the
    page cap live here.
    """

    #: Registry key.
    name: str = "base"
    #: Longest page length the source can produce.
    page_size: int = 100

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @abc.abstractmethod
    async def fetch_page(self, tags: TagSet, page: int) -> PostPage:
        """Fetch a single 1-based page of results."""

    @abc.abstractmethod
    def info(self) -> SourceInfo:
        """Describe this source for the user."""

    async def iter_posts(
        self, tags: TagSet, *, max_pages: int = 200, max_posts: int | None = None
    ) -> AsyncIterator[Post]:
        """Yield posts page by page until the source is exhausted.

        Args:
            tags: The tag query.
            max_pages: Hard stop, so a misbehaving source cannot loop forever.
            max_posts: Stop early once this many posts have been yielded.

        Yields:
            Post: Normalised posts, de-duplicated by post id across pages.
        """
        seen: set[str] = set()
        yielded = 0
        for page in range(1, max_pages + 1):
            result = await self.fetch_page(tags, page)
            for post in result.posts:
                if max_posts is not None and yielded >= max_posts:
                    return
                if post.id and post.id in seen:
                    continue
                if post.id:
                    seen.add(post.id)
                yielded += 1
                yield post
            if not result.has_more or not result.posts:
                return

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"


__all__ = ["Post", "PostPage", "PostSource", "SourceInfo"]