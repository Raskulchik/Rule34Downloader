"""Reusable test doubles."""

from __future__ import annotations

from r34dl.models import Post, PostPage
from r34dl.sources.base import PostSource, SourceInfo


class StubSource(PostSource):
    """A source that replays canned pages.

    Only :meth:`fetch_page` is overridden - pagination, de-duplication and
    limit handling come from the production base class, so those stay under test.
    """

    name = "stub"

    def __init__(self, pages, client=None):
        super().__init__(client)
        self.pages = pages
        self.requested: list[int] = []

    async def fetch_page(self, tags, page: int) -> PostPage:
        self.requested.append(page)
        page_result = self.pages.get(page)
        if page_result is None:
            return PostPage((), page, False)
        if callable(page_result):
            page_result = page_result(page)
        return page_result

    def info(self) -> SourceInfo:
        return SourceInfo(name=self.name, description="stub")


def make_post(post_id: str, url: str | None = None, **kwargs) -> Post:
    """Build a :class:`Post` with sensible defaults."""
    return Post(id=post_id, file_url=url if url is not None else f"https://x.test/{post_id}.png",
                **kwargs)


def make_page(ids, *, has_more: bool) -> PostPage:
    """Build a page of posts from a list of ids."""
    return PostPage(tuple(make_post(i) for i in ids), 0, has_more)