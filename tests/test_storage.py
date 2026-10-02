"""Storage: destination fallback and atomic writes."""

from __future__ import annotations

import asyncio
import os
import stat

import pytest

from r34dl.errors import StorageError
from r34dl.storage import OutputLayout, atomic_write, atomic_write_async


class TestOutputLayout:
    def test_creates_the_tag_subfolder(self, tmp_path):
        layout = OutputLayout(tmp_path, "cute")
        assert layout.resolve() == tmp_path / "cute"
        assert (tmp_path / "cute").is_dir()

    def test_is_idempotent(self, tmp_path):
        OutputLayout(tmp_path, "cute").resolve()
        assert OutputLayout(tmp_path, "cute").resolve() == tmp_path / "cute"

    def test_falls_back_when_base_is_unusable(self, tmp_path):
        """A read-only base directory must not be fatal."""
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o500)
        try:
            layout = OutputLayout(blocked, "cute")
            resolved = layout.resolve()
            assert resolved != blocked / "cute"
            assert resolved.name == "cute"
            assert layout.fallback_used == resolved
            assert resolved.is_dir()
        finally:
            blocked.chmod(0o700)

    def test_falls_back_when_base_is_a_file(self, tmp_path):
        not_a_dir = tmp_path / "afile"
        not_a_dir.write_text("x")
        layout = OutputLayout(not_a_dir, "cute")
        resolved = layout.resolve()
        assert resolved.name == "cute"
        assert resolved.is_dir()

    def test_raises_only_when_nowhere_is_writable(self, tmp_path, monkeypatch):
        """Every candidate fails -> a clear typed error, not a traceback."""
        # Each candidate is a *file*, so mkdir can never succeed.
        base = tmp_path / "base_is_a_file"
        base.write_text("x")
        blocked_downloads = tmp_path / "downloads_is_a_file"
        blocked_downloads.write_text("x")
        monkeypatch.setattr(
            "r34dl.storage.default_downloads_dir", lambda: blocked_downloads
        )
        home = tmp_path / "home_is_a_file"
        home.write_text("x")
        monkeypatch.setenv("HOME", str(home))

        layout = OutputLayout(base, "cute")
        with pytest.raises(StorageError, match="no writable"):
            layout.resolve()


class TestAtomicWrite:
    def test_writes_and_renames(self, tmp_path):
        target = tmp_path / "a.bin"
        assert atomic_write(target, [b"abc", b"def"]) == 6
        assert target.read_bytes() == b"abcdef"

    def test_no_temp_file_remains(self, tmp_path):
        atomic_write(tmp_path / "a.bin", [b"x"])
        assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "a.bin"
        target.write_bytes(b"old")
        atomic_write(target, [b"new"])
        assert target.read_bytes() == b"new"

    def test_failure_leaves_original_intact(self, tmp_path):
        target = tmp_path / "a.bin"
        target.write_bytes(b"original")

        def exploding():
            yield b"partial"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            atomic_write(target, exploding())
        assert target.read_bytes() == b"original"
        assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "a.bin"
        atomic_write(target, [b"x"])
        assert target.exists()


class TestAtomicWriteAsync:
    async def test_writes_from_async_iterable(self, tmp_path):
        async def chunks():
            for part in (b"ab", b"cd"):
                yield part

        target = tmp_path / "a.bin"
        assert await atomic_write_async(target, chunks()) == 4
        assert target.read_bytes() == b"abcd"

    async def test_empty_stream_creates_empty_file(self, tmp_path):
        async def nothing():
            return
            yield  # pragma: no cover

        target = tmp_path / "a.bin"
        assert await atomic_write_async(target, nothing()) == 0
        assert target.exists() and target.stat().st_size == 0

    async def test_concurrent_writes_to_same_target_do_not_interleave(self, tmp_path):
        """The race the unique .part name exists to prevent."""
        target = tmp_path / "a.bin"

        async def body(byte: bytes, count: int):
            for _ in range(count):
                yield byte * 4096

        await asyncio.gather(
            atomic_write_async(target, body(b"\xaa", 50)),
            atomic_write_async(target, body(b"\xbb", 50)),
        )
        data = target.read_bytes()
        # Whichever won, the file must be internally consistent - never a mix.
        assert set(data) in ({0xAA}, {0xBB})
        assert len(data) == 50 * 4096
        assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]

    async def test_failure_leaves_original_and_cleans_temp(self, tmp_path):
        target = tmp_path / "a.bin"
        target.write_bytes(b"original")

        async def exploding():
            yield b"partial"
            raise OSError("disk full")

        with pytest.raises(StorageError):
            await atomic_write_async(target, exploding())
        assert target.read_bytes() == b"original"
        assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]

    async def test_cancellation_cleans_temp_file(self, tmp_path):
        target = tmp_path / "a.bin"
        started = asyncio.Event()

        async def slow():
            yield b"partial"
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(atomic_write_async(target, slow()))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [p.name for p in tmp_path.iterdir()] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
class TestPermissions:
    def test_unwritable_directory_raises(self, tmp_path):
        blocked = tmp_path / "ro"
        blocked.mkdir()
        blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises(StorageError):
                atomic_write(blocked / "a.bin", [b"x"])
        finally:
            blocked.chmod(0o700)