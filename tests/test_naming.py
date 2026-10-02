"""Filename sanitising and collision handling."""

from __future__ import annotations

import pytest

from r34dl.errors import StorageError
from r34dl.naming import (
    MAX_PATH_LENGTH,
    filename_for,
    fit_to_directory,
    sanitize_component,
    unique_path,
)

CDN = "https://r34i.paheal-cdn.net/89/e1/89e109021ec0a151f3fbe17d767b2527"


class TestSanitize:
    @pytest.mark.parametrize(
        "raw",
        ['a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a<b', 'a>b', 'a|b', 'a"b'],
    )
    def test_path_hazardous_chars_replaced(self, raw):
        assert not set(sanitize_component(raw)) & set('/\\:*?<>|"')

    def test_control_chars_removed(self):
        assert "\x00" not in sanitize_component("a\x00b")

    def test_trailing_dots_and_spaces_trimmed(self):
        # Windows silently strips these, producing unreachable files.
        assert sanitize_component("name...  ") == "name"

    def test_empty_becomes_fallback(self):
        assert sanitize_component("   ") == "file"

    @pytest.mark.parametrize("reserved", ["CON", "nul", "Com1", "lpt9"])
    def test_windows_reserved_names_escaped(self, reserved):
        assert not sanitize_component(reserved).lower().startswith(reserved.lower())

    def test_long_names_are_truncated_within_byte_budget(self):
        name = sanitize_component("a" * 500 + ".png")
        assert len(name.encode()) <= 240
        assert name.endswith(".png")

    def test_multibyte_names_respected(self):
        name = sanitize_component("я" * 400 + ".jpg")
        assert len(name.encode("utf-8")) <= 240
        assert name.endswith(".jpg")


class TestFilenameFor:
    def test_plain_filename_from_url(self):
        assert filename_for("https://x.test/a/b/cute.png", "1") == "cute.png"

    def test_extensionless_cdn_url_uses_declared_ext(self):
        assert filename_for(CDN, "7451852", "png").endswith(".png")

    def test_extensionless_cdn_url_falls_back_to_post_id(self):
        assert filename_for(CDN, "7451852").startswith("7451852")

    def test_query_string_is_ignored(self):
        assert filename_for("https://x.test/a/cat.jpg?token=1&x=2", "1") == "cat.jpg"

    def test_fragment_is_ignored(self):
        assert filename_for("https://x.test/a/cat.jpg#frag", "1") == "cat.jpg"

    def test_declared_ext_wins_when_url_has_none(self):
        name = filename_for("https://x.test/files/abcdef", "42", "webp")
        assert name.endswith(".webp")

    def test_malicious_filename_is_neutralised(self):
        name = filename_for("https://x.test/..%2F..%2Fetc%2Fpasswd", "1")
        assert "/" not in name and ".." not in name


class TestUniquePath:
    def test_unused_name_returned_as_is(self, tmp_path):
        assert unique_path(tmp_path, "a.png") == tmp_path / "a.png"

    def test_collision_gets_suffix(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"x")
        assert unique_path(tmp_path, "a.png") == tmp_path / "a_1.png"

    def test_multiple_collisions_keep_counting(self, tmp_path):
        for index in range(3):
            (tmp_path / f"a{'' if index == 0 else f'_{index}'}.png").write_bytes(b"x")
        assert unique_path(tmp_path, "a.png").name == "a_3.png"

class TestFitToDirectory:
    """Windows caps the whole path at 260 chars, not just the name."""

    def test_short_name_is_untouched(self, tmp_path):
        assert fit_to_directory(tmp_path, "a.png") == "a.png"

    def test_long_name_in_deep_directory_is_shortened(self, tmp_path):
        deep = tmp_path.joinpath(*["d" * 40] * 4)
        name = fit_to_directory(deep, f"{'n' * 200}.png")
        assert len(str(deep / name)) <= MAX_PATH_LENGTH
        assert name.endswith(".png")

    def test_shortened_name_stays_under_the_byte_budget_too(self, tmp_path):
        deep = tmp_path.joinpath(*["d" * 40] * 4)
        name = fit_to_directory(deep, f"{'ы' * 200}.png")
        assert len(name.encode("utf-8")) <= 240
        assert len(str(deep / name)) <= MAX_PATH_LENGTH

    def test_stem_is_kept_readable(self, tmp_path):
        deep = tmp_path.joinpath(*["d" * 40] * 4)
        name = fit_to_directory(deep, f"{'n' * 200}.jpg")
        assert name.count("n") >= 8

    def test_directory_with_no_room_raises(self, tmp_path):
        absurd = tmp_path / ("d" * MAX_PATH_LENGTH)
        with pytest.raises(StorageError, match="too long"):
            fit_to_directory(absurd, "a.png")

    def test_unique_path_applies_the_clamp(self, tmp_path):
        deep = tmp_path.joinpath(*["d" * 40] * 4)
        deep.mkdir(parents=True)
        result = unique_path(deep, f"{'n' * 200}.png")
        assert len(str(result)) <= MAX_PATH_LENGTH
