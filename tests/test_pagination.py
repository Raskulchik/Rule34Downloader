"""Pagination contract in PostSource.iter_posts, exercised via a stub.

These cover the loop-termination rules that keep a misbehaving upstream from
hanging the tool forever.
"""

from __future__ import annotations

from r34dl.models import PostPage
from r34dl.tagging import TagSet

from .helpers import StubSource, make_page

TAGS = TagSet.parse("cute")


async def _collect(source: StubSource, **kwargs) -> list:
    return [post async for post in source.iter_posts(TAGS, **kwargs)]


class TestTermination:
    async def test_stops_on_short_page(self):
        source = StubSource({
            1: make_page(range(100), has_more=True),
            2: make_page(range(100, 150), has_more=False),
        })
        assert len(await _collect(source)) == 150
        assert source.requested == [1, 2]

    async def test_stops_when_source_says_no_more(self):
        source = StubSource({
            1: make_page(["a"], has_more=False),
            2: make_page(["b"], has_more=True),
        })
        assert [p.id for p in await _collect(source)] == ["a"]

    async def test_empty_page_ends_iteration(self):
        source = StubSource({1: PostPage((), 1, True)})
        assert await _collect(source) == []

    async def test_missing_page_ends_iteration(self):
        source = StubSource({1: make_page(["a"], has_more=True)})
        assert [p.id for p in await _collect(source)] == ["a"]

    async def test_max_pages_caps_a_runaway_source(self):
        source = StubSource({p: make_page([f"{p}x{i}" for i in range(10)], has_more=True)
                             for p in range(1, 100)})
        assert len(await _collect(source, max_pages=3)) == 30
        assert source.requested == [1, 2, 3]


class TestLimits:
    async def test_max_posts_stops_early(self):
        source = StubSource({p: make_page([f"{p}x{i}" for i in range(50)], has_more=True)
                             for p in range(1, 50)})
        assert len(await _collect(source, max_posts=7)) == 7

    async def test_max_posts_of_zero_yields_nothing(self):
        source = StubSource({1: make_page(["a"], has_more=True)})
        assert await _collect(source, max_posts=0) == []


class TestDeduplication:
    async def test_same_post_across_pages_is_yielded_once(self):
        shared = "dup"
        source = StubSource({
            1: make_page([shared, "a"], has_more=True),
            2: make_page([shared, "b"], has_more=False),
        })
        assert [p.id for p in await _collect(source)] == ["dup", "a", "b"]

    async def test_post_without_id_is_not_deduped(self):
        # Without an id we cannot tell copies apart, so we must not silently
        # drop them.
        from r34dl.models import Post

        def two_anon(_page):
            return PostPage((Post("", "u1"), Post("", "u2")), 1, False)

        source = StubSource({1: two_anon})
        assert len(await _collect(source)) == 2