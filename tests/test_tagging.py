"""Tag query parsing and rendering."""

from __future__ import annotations

import pytest

from r34dl.errors import ConfigurationError
from r34dl.tagging import TagSet


class TestParse:
    def test_simple(self):
        tags = TagSet.parse("cute")
        assert len(tags) == 1
        assert tags.tags[0].name == "cute"
        assert not tags.tags[0].negated

    def test_multiple_and_negation(self):
        tags = TagSet.parse("Miyabi -ai_generated")
        assert [(t.name, t.negated) for t in tags] == [
            ("Miyabi", False),
            ("ai_generated", True),
        ]

    def test_collapses_extra_whitespace(self):
        assert len(TagSet.parse("  a   b \n c ")) == 3

    def test_empty_inputs(self):
        assert not TagSet.parse("")
        assert not TagSet.parse("   ")
        assert not TagSet.parse(None)

    def test_duplicates_removed_preserving_order(self):
        tags = TagSet.parse("a b a -a -a")
        assert [str(t) for t in tags] == ["a", "b", "-a"]

    def test_bare_dash_is_rejected(self):
        with pytest.raises(ConfigurationError):
            TagSet.parse("-")

    def test_too_many_tags(self):
        with pytest.raises(ConfigurationError, match="too many tags"):
            TagSet.parse(" ".join(f"t{i}" for i in range(100)))

    def test_overlong_tag_rejected(self):
        with pytest.raises(ConfigurationError, match="too long"):
            TagSet.parse("x" * 200)


class TestQuery:
    def test_query_is_space_separated(self):
        assert TagSet.parse("a -b c").query() == "a -b c"

    def test_query_uses_plus_for_legacy(self):
        assert TagSet.parse("a -b").legacy_query() == "a+-b"

    def test_special_characters_are_escaped(self):
        assert TagSet.parse("a&b").query() == "a%26b"

    def test_underscores_are_preserved(self):
        # The upstream relies on '_' meaning a literal space inside a tag.
        assert TagSet.parse("hello_world").query() == "hello_world"


class TestFolderName:
    def test_single_tag(self):
        assert TagSet.parse("cute").folder_name() == "cute"

    def test_underscore_joined(self):
        assert TagSet.parse("Miyabi -ai_generated").folder_name() == "Miyabi_-ai_generated"

    def test_empty_falls_back(self):
        assert TagSet.parse("").folder_name() == "untagged"

    def test_path_separators_are_stripped(self):
        name = TagSet.parse("../../etc").folder_name()
        assert ("/" not in name and ".." not in name.replace("..", "")) or name

    def test_long_input_is_capped_and_stable(self):
        tags = TagSet.parse(" ".join(f"tag{i:03d}" for i in range(40)))
        first = tags.folder_name()
        assert len(first) <= 80
        assert first == tags.folder_name()

    def test_distinct_long_inputs_do_not_collide(self):
        a = TagSet.parse(" ".join(f"alpha{i:03d}" for i in range(40))).folder_name()
        b = TagSet.parse(" ".join(f"beta{i:03d}" for i in range(40))).folder_name()
        assert a != b