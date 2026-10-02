"""JSON dapi source: payload coercion and URL shape handling."""

from __future__ import annotations

import pytest

from r34dl.errors import SourceError
from r34dl.models import coerce_post, first_url
from r34dl.sources.json_api import JsonApiSource, _as_records
from r34dl.tagging import TagSet


class TestFirstUrl:
    """The upstream PHP serialises URL fields inconsistently."""

    def test_plain_string(self):
        assert first_url("https://x.test/a.png") == "https://x.test/a.png"

    def test_json_array(self):
        assert first_url(["https://x.test/a.png"]) == "https://x.test/a.png"

    def test_php_style_object(self):
        assert first_url({"0": "https://x.test/a.png"}) == "https://x.test/a.png"

    def test_nested_array_of_objects(self):
        payload = [{"0": "https://x.test/a.png"}]
        assert first_url(payload) == "https://x.test/a.png"

    def test_none_and_empty(self):
        assert first_url(None) is None
        assert first_url("") is None
        assert first_url([]) is None
        assert first_url({}) is None

    def test_skips_empty_entries(self):
        assert first_url(["", None, "https://x.test/a.png"]) == "https://x.test/a.png"


class TestCoercePost:
    def test_numeric_strings(self):
        post = coerce_post(
            {"id": "123", "file_url": "https://x/a.png", "width": "800", "height": "600"},
            "json",
        )
        assert post.width == 800 and post.height == 600

    def test_tags_from_string_and_list(self):
        assert coerce_post({"tags": "a b"}, "json").tags == ("a", "b")
        assert coerce_post({"tags": ["a", "b"]}, "json").tags == ("a", "b")

    def test_deleted_post_has_no_url(self):
        post = coerce_post({"id": "1", "file_url": None, "status": "deleted"}, "json")
        assert post.file_url is None

    def test_garbage_numbers_do_not_raise(self):
        assert coerce_post({"id": "1", "width": "wide"}, "json").width is None

    def test_rejects_non_object(self):
        with pytest.raises(SourceError):
            coerce_post("not a post", "json")

    def test_missing_id_becomes_empty_string(self):
        assert coerce_post({"file_url": "https://x/a.png"}, "json").id == ""


class TestAsRecords:
    def test_bare_list(self):
        assert _as_records([{"id": "1"}]) == [{"id": "1"}]

    @pytest.mark.parametrize("key", ["posts", "data", "results"])
    def test_wrapped_list(self, key):
        assert _as_records({key: [{"id": "1"}]}) == [{"id": "1"}]

    def test_error_object_raises(self):
        with pytest.raises(SourceError):
            _as_records({"error": "nope"})

    def test_scalar_raises(self):
        with pytest.raises(SourceError):
            _as_records(42)


class TestJsonApiSource:
    def _source(self):
        from r34dl.net.client import HttpClient

        return JsonApiSource(HttpClient())

    def test_needs_cookie_flag(self):
        assert self._source().info().needs_cookie is True

    def test_pagination_params(self):
        src = self._source()
        # The upstream is 0-based and wants '+' joined tags.
        assert src.page_size == 100

    def test_tagset_renders_for_the_api(self):
        assert TagSet.parse("a -b").legacy_query() == "a+-b"
