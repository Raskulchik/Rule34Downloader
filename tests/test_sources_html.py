"""HTML listing source: parsing and pagination."""

from __future__ import annotations

from r34dl.sources.html_list import PahealHtmlSource, parse_listing
from r34dl.tagging import TagSet


class TestParseListing:
    def test_extracts_posts(self, listing_html):
        posts = parse_listing(listing_html)
        assert len(posts) == 2

    def test_fields(self, listing_html):
        post = parse_listing(listing_html)[0]
        assert post.id == "7451852"
        assert post.file_url == (
            "https://r34i.paheal-cdn.net/89/e1/89e109021ec0a151f3fbe17d767b2527"
        )
        assert post.ext == "png"
        assert post.source == "paheal"

    def test_metadata_from_title_attribute(self, listing_html):
        post = parse_listing(listing_html)[0]
        assert (post.width, post.height) == (4000, 3000)
        assert post.size == int(1.4 * 1024 * 1024)
        assert post.dimensions == "4000x3000"

    def test_tags_are_split(self, listing_html):
        assert parse_listing(listing_html)[1].tags == (
            "cute",
            "edit",
            "mechafetus",
            "screenshot_edit",
        )

    def test_thumbnail_url_is_not_mistaken_for_the_file(self, listing_html):
        # The <img> points at r34t (a thumbnail host); only r34i is the file.
        for post in parse_listing(listing_html):
            assert "r34t." not in post.file_url

    def test_entries_without_a_file_url_are_dropped(self, listing_html):
        # The third entry in the fixture is banned, so it has no "File Only".
        assert all(post.id != "999" for post in parse_listing(listing_html))

    def test_empty_page(self, empty_listing_html):
        assert parse_listing(empty_listing_html) == ()

    def test_garbage_input_does_not_raise(self):
        assert parse_listing("<<<>>> not html at all") == ()

    def test_unclosed_tags_do_not_raise(self):
        assert parse_listing("<div class='shm-thumb thumb' data-post-id='1'>") == ()


class TestUrlBuilding:
    def _source(self):
        # info() and _url_for() need no network, so a bare client is enough.
        from r34dl.net.client import HttpClient

        return PahealHtmlSource(HttpClient())

    def test_first_page_has_no_page_segment(self):
        src = self._source()
        assert src._url_for(TagSet.parse("cute"), 1) == (
            "https://rule34.paheal.net/post/list/cute"
        )

    def test_later_pages_are_suffixed(self):
        src = self._source()
        assert src._url_for(TagSet.parse("cute"), 3).endswith("/cute/3")

    def test_negated_tags_survive_encoding(self):
        src = self._source()
        url = src._url_for(TagSet.parse("cute -ai"), 1)
        assert "-ai" in url and " " not in url

    def test_empty_tagset_lists_everything(self):
        src = self._source()
        assert src._url_for(TagSet.parse(""), 1) == "https://rule34.paheal.net/post/list"

    def test_info_does_not_require_a_cookie(self):
        assert self._source().info().needs_cookie is False