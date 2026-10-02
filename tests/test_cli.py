"""CLI argument handling and configuration assembly."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from r34dl.cli import build_parser, settings_from_args
from r34dl.config import Settings
from r34dl.errors import ConfigurationError
from r34dl.ui import Console


def parse(*argv: str):
    return build_parser().parse_args(list(argv))


def settings(*argv: str, **kwargs) -> Settings:
    console = Console(stream=io.StringIO(), quiet=True)
    return settings_from_args(parse(*argv), console)


class TestParser:
    def test_tags_short_and_long(self):
        assert parse("-t", "cute").tags == "cute"
        assert parse("--tags", "cute").tags == "cute"

    def test_negated_tag_survives_the_shell(self):
        assert parse("-t", "cute -ai").tags == "cute -ai"

    def test_output_dir_is_a_path(self):
        assert isinstance(parse("-o", "/tmp/x").output_dir, Path)

    def test_limit(self):
        assert parse("-n", "12").limit == 12

    def test_defaults(self):
        args = parse("-t", "x")
        assert args.concurrency == 5
        assert args.source == "auto"
        assert args.yes is False

    def test_unknown_source_rejected(self):
        with pytest.raises(SystemExit):
            parse("-t", "x", "-s", "nope")

    def test_version_exits_cleanly(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse("--version")
        assert exc.value.code == 0


class TestSettings:
    def test_tags_and_limit_flow_through(self):
        cfg = settings("-t", "cute", "-n", "5")
        assert cfg.tags == "cute" and cfg.limit == 5

    def test_zero_limit_means_unlimited(self):
        assert settings("-t", "cute", "-n", "0").limit is None

    def test_output_dir_is_resolved(self, tmp_path):
        assert settings("-t", "c", "-o", str(tmp_path)).output_dir == tmp_path.resolve()

    def test_output_dir_expands_tilde(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert settings("-t", "c").output_dir.is_absolute()

    def test_concurrency_is_clamped(self):
        assert settings("-t", "c", "-c", "999").concurrency == 16
        assert settings("-t", "c", "-c", "0").concurrency == 1

    def test_attempts_are_clamped(self):
        cfg = Settings(max_attempts=99).clamp()
        assert cfg.max_attempts == 10

    def test_rate_is_clamped(self):
        assert settings("-t", "c", "-r", "0").request_rate == 0.1
        assert settings("-t", "c", "-r", "9999").request_rate == 30.0

    def test_max_pages_at_least_one(self):
        assert Settings(max_pages=0).clamp().max_pages == 1

    def test_flags_are_carried(self):
        cfg = settings("-t", "c", "-y", "--flat", "--overwrite", "-q")
        assert cfg.assume_yes and cfg.flat and cfg.overwrite and cfg.quiet


class TestInteractiveFallbacks:
    def test_missing_tags_without_a_tty_is_an_error(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        with pytest.raises(ConfigurationError, match="--tags"):
            settings()

    def test_empty_tagset_without_a_tty_is_an_error(self, monkeypatch):
        # Whitespace-only input is no input at all.
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        with pytest.raises(ConfigurationError, match="no tags given"):
            settings("-t", "   ")

    def test_invalid_tags_raise_a_typed_error(self):
        with pytest.raises(ConfigurationError):
            settings("-t", "-")

    def test_yes_flag_skips_the_count_prompt(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        assert settings("-t", "cute", "-y").limit is None


class TestListSources:
    def test_prints_and_exits_zero(self):
        console = Console(stream=io.StringIO())
        with pytest.raises(SystemExit) as exc:
            settings_from_args(parse("--list-sources"), console)
        assert exc.value.code == 0
        output = console.stream.getvalue()
        assert "paheal" in output and "json" in output


class TestConsole:
    def test_quiet_suppresses_info(self):
        buf = io.StringIO()
        Console(stream=buf, quiet=True).info("hello")
        assert buf.getvalue() == ""

    def test_errors_go_to_the_stream_anyway(self, capsys):
        Console(stream=io.StringIO(), quiet=True).error("bad")
        assert "bad" in capsys.readouterr().err

    def test_confirm_accepts_shorthand(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "y")
        assert Console(stream=io.StringIO()).confirm("go?") is True

    def test_confirm_rejects_nonsense(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "maybe")
        assert Console(stream=io.StringIO()).confirm("go?") is False

    def test_confirm_honours_default_on_enter(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "")
        assert Console(stream=io.StringIO()).confirm("go?", default=True) is True

    def test_prompt_survives_closed_stdin(self, monkeypatch):
        def raise_eof(_=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert Console(stream=io.StringIO()).ask("tags?") == ""

    def test_ask_int_reprompts_on_garbage(self, monkeypatch):
        answers = iter(["abc", "-3", "7"])
        monkeypatch.setattr("builtins.input", lambda _="": next(answers))
        assert Console(stream=io.StringIO()).ask_int("n? ", 0) == 7


class TestProgressBar:
    def test_degrades_quietly_without_tqdm(self, monkeypatch):
        import r34dl.ui as ui

        monkeypatch.setattr(ui, "HAS_TQDM", False)
        bar = Console(stream=io.StringIO()).progress(10, "x")
        bar.tick()
        bar.close()  # must not raise

    def test_zero_total_is_safe(self):
        bar = Console(stream=io.StringIO()).progress(0, "x")
        bar.tick()
        bar.close()