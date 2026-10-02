"""Downloader: outcomes, resume, atomicity and failure isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from r34dl.download import Downloader
from r34dl.errors import DownloadError, SourceUnavailableError, StorageError
from r34dl.models import Outcome, Post
from r34dl.net.client import HttpClient

from .conftest import requires_posix_permissions
from .helpers import make_post

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512


class FakeStreamClient(HttpClient):
    """An HttpClient stand-in whose ``stream`` yields canned bytes."""

    def __init__(self, body: bytes = PNG, *, fail: Exception | None = None,
                 status: int = 200):
        self.body = body
        self.fail = fail
        self.status = status
        self.requested: list[str] = []

    async def stream(self, url, chunk_size=65536, referer=None):
        self.requested.append(url)
        if self.fail is not None:
            raise self.fail
        if self.status >= 400:
            raise SourceUnavailableError(f"{url}: HTTP {self.status}")
        for i in range(0, len(self.body), 128):
            yield self.body[i : i + 128]


def make_downloader(client, **kwargs) -> Downloader:
    return Downloader(client, **kwargs)


class TestSingleDownload:
    async def test_writes_file(self, tmp_path):
        client = FakeStreamClient()
        outcome, detail = await make_downloader(client).download_one(
            make_post("1"), tmp_path
        )
        assert outcome is Outcome.DOWNLOADED
        assert "bytes" in detail

    async def test_uses_declared_extension(self, tmp_path):
        # An extension-less CDN URL is named after the post, with the
        # extension the listing source declared.
        client = FakeStreamClient()
        post = make_post("1", "https://x.test/abcdef", ext="webp")
        outcome, _ = await make_downloader(client).download_one(post, tmp_path)
        assert outcome is Outcome.DOWNLOADED
        assert (tmp_path / "1.webp").exists()

    async def test_url_filename_wins_over_post_id(self, tmp_path):
        client = FakeStreamClient()
        post = make_post("1", "https://x.test/named_photo.jpg")
        await make_downloader(client).download_one(post, tmp_path)
        assert (tmp_path / "named_photo.jpg").exists()

    async def test_skips_existing(self, tmp_path):
        (tmp_path / "1.png").write_bytes(b"old")
        client = FakeStreamClient()
        outcome, _ = await make_downloader(client).download_one(make_post("1"), tmp_path)
        assert outcome is Outcome.SKIPPED_EXISTING
        assert not client.requested

    async def test_overwrite_replaces(self, tmp_path):
        (tmp_path / "1.png").write_bytes(b"old")
        client = FakeStreamClient()
        outcome, _ = await make_downloader(client, overwrite=True).download_one(
            make_post("1"), tmp_path
        )
        assert outcome is Outcome.DOWNLOADED
        assert (tmp_path / "1.png").read_bytes() == PNG

    async def test_post_without_url_is_skipped(self, tmp_path):
        outcome, _ = await make_downloader(FakeStreamClient()).download_one(
            Post(id="1", file_url=None), tmp_path
        )
        assert outcome is Outcome.SKIPPED_NO_SOURCE

    async def test_concurrent_duplicates_do_not_clobber(self, tmp_path):
        """Two posts resolving to one filename must both survive."""
        shared_url = "https://x.test/shared.png"
        posts = [make_post("dup-a", shared_url), make_post("dup-b", shared_url)]
        downloader = make_downloader(FakeStreamClient(), concurrency=2)

        # Neutralise the id-based naming so both targets really are equal.
        import r34dl.download as dl

        original = dl.filename_for
        try:
            dl.filename_for = lambda url, fallback_id, declared_ext=None: "shared.png"
            report = await downloader.download_all(posts, tmp_path)
        finally:
            dl.filename_for = original

        names = sorted(p.name for p in tmp_path.iterdir())
        assert report.downloaded == 2
        assert names == ["shared.png", "shared_1.png"]
        # Each file must be a complete image, not interleaved bytes.
        for path in tmp_path.iterdir():
            assert path.read_bytes() == PNG


class TestFailureIsolation:
    async def test_one_failure_does_not_stop_the_batch(self, tmp_path):
        posts = [make_post("1"), make_post("2"), make_post("3")]

        class Flaky(FakeStreamClient):
            async def stream(self, url, chunk_size=65536, referer=None):
                if url.endswith("2.png"):
                    raise SourceUnavailableError("boom")
                async for chunk in super().stream(url, chunk_size, referer):
                    yield chunk

        report = await make_downloader(Flaky()).download_all(posts, tmp_path)
        assert report.downloaded == 2
        assert report.failed == 1
        assert len(report.failures) == 1

    async def test_failures_are_recorded_with_detail(self, tmp_path):
        client = FakeStreamClient(fail=SourceUnavailableError("upstream 503"))
        report = await make_downloader(client).download_all([make_post("1")], tmp_path)
        assert report.failed == 1
        assert "upstream 503" in report.failures[0][1]

    async def test_html_response_is_rejected(self, tmp_path):
        client = FakeStreamClient(b"<html><body>Not found</body></html>")
        report = await make_downloader(client).download_all([make_post("1")], tmp_path)
        assert report.failed == 1
        assert not list(tmp_path.iterdir()), "no file should be written"

    async def test_captcha_response_is_rejected(self, tmp_path):
        client = FakeStreamClient(b"<html><div class='captcha-container'>prove it</div></html>")
        report = await make_downloader(client).download_all([make_post("1")], tmp_path)
        assert report.failed == 1
        assert not list(tmp_path.iterdir())


class TestAtomicity:
    async def test_partial_file_is_not_left_on_failure(self, tmp_path):
        class Exploding(FakeStreamClient):
            async def stream(self, url, chunk_size=65536, referer=None):
                yield PNG[:128]
                raise DownloadError("connection reset")

        report = await make_downloader(Exploding()).download_all([make_post("1")], tmp_path)
        assert report.failed == 1
        leftovers = [p.name for p in tmp_path.iterdir()]
        assert leftovers == [], f"stray files: {leftovers}"

    async def test_no_part_file_survives_success(self, tmp_path):
        await make_downloader(FakeStreamClient()).download_all([make_post("1")], tmp_path)
        assert [p.suffix for p in tmp_path.iterdir()] == [".png"]

    async def test_cancellation_removes_partial_file(self, tmp_path):
        started = asyncio.Event()

        class Slow(FakeStreamClient):
            async def stream(self, url, chunk_size=65536, referer=None):
                yield PNG[:128]
                started.set()
                await asyncio.sleep(30)
                yield PNG[128:]

        task = asyncio.create_task(
            make_downloader(Slow()).download_all([make_post("1")], tmp_path)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [p.name for p in tmp_path.iterdir()] == []


class TestConcurrency:
    async def test_batches_respect_the_size(self, tmp_path):
        client = FakeStreamClient()
        downloader = make_downloader(client, batch_size=2)
        posts = [make_post(str(i)) for i in range(5)]
        report = await downloader.download_all(posts, tmp_path)
        assert report.downloaded == 5

    async def test_report_totals_add_up(self, tmp_path):
        posts = [make_post("1"), Post(id="2", file_url=None)]
        report = await make_downloader(FakeStreamClient()).download_all(posts, tmp_path)
        assert report.total == 2
        assert report.downloaded == 1 and report.skipped == 1
        assert "1 downloaded" in report.summary()


class TestProgress:
    async def test_progress_is_ticked_once_per_file(self, tmp_path):
        class Bar:
            def __init__(self):
                self.ticks = 0
                self.closed = False

            def tick(self, amount=1):
                self.ticks += amount

            def close(self):
                self.closed = True

        bar = Bar()
        posts = [make_post(str(i)) for i in range(3)]
        await make_downloader(FakeStreamClient(), on_progress=bar).download_all(posts, tmp_path)
        assert bar.ticks == 3
        assert bar.closed, "progress bar must be closed deterministically"

    async def test_progress_closed_even_on_failure(self, tmp_path):
        class Bar:
            closed = False

            def tick(self, amount=1):
                pass

            def close(self):
                type(self).closed = True

        client = FakeStreamClient(fail=SourceUnavailableError("x"))
        await make_downloader(client, on_progress=Bar()).download_all([make_post("1")], tmp_path)
        assert Bar.closed


class TestStorageErrors:
    @requires_posix_permissions
    async def test_unwritable_directory_is_reported(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o500)
        try:
            client = FakeStreamClient()
            report = await make_downloader(client).download_all([make_post("1")], blocked)
            assert report.failed == 1
        finally:
            blocked.chmod(0o700)

    async def test_storage_error_is_typed(self, tmp_path):
        client = FakeStreamClient(fail=StorageError("read-only filesystem"))
        report = await make_downloader(client).download_all([make_post("1")], tmp_path)
        assert report.failed == 1
        assert "read-only" in report.failures[0][1]


@pytest.mark.parametrize("body", [PNG, b"\xff\xd8\xff" + b"\x00" * 200])
async def test_binary_content_survives_intact(tmp_path, body):
    client = FakeStreamClient(body)
    await make_downloader(client).download_all([make_post("1")], tmp_path)
    assert (tmp_path / "1.png").read_bytes() == body


def test_target_paths_stay_inside_the_directory(tmp_path: Path):
    """A hostile URL must not be able to escape the destination directory."""
    post = make_post("1", "https://x.test/../../../../etc/passwd")
    outcome, _ = _sync(make_downloader(FakeStreamClient()).download_one(post, tmp_path))
    assert outcome in {Outcome.DOWNLOADED, Outcome.SKIPPED_EXISTING}
    assert list(tmp_path.iterdir())


def _sync(coro):
    return asyncio.run(coro)