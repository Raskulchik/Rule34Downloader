"""HTTP client behaviour: bot-wall detection and error classification.

Uses a stub client so no network access is required.
"""

from __future__ import annotations

import pytest

from r34dl.errors import BotProtectedError, SourceUnavailableError
from r34dl.net.client import HttpClient, RetryPolicy, _looks_like_bot_wall


class _Content:
    """Stands in for aiohttp's response content reader."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, _size: int) -> bytes:
        return self._body


class StubResponse:
    """Minimal aiohttp.ClientResponse stand-in."""

    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.released = False
        self.content = _Content(body)

    async def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def release(self) -> None:
        self.released = True


class FakeSession:
    """Stands in for aiohttp.ClientSession at the transport level.

    Substituting here (rather than overriding HttpClient.get) keeps the retry,
    backoff and error-classification logic under test.
    """

    def __init__(self, script):
        self.script = list(script)
        self.requested: list[str] = []
        self.closed = False
        self.last = StubResponse(200, b"{}")

    async def get(self, url, *, headers=None):
        self.requested.append(url)
        if not self.script:
            # Repeat the last entry so a test can script a permanent failure
            # and still exhaust its retries.
            return self.last
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.last = item
        return item

    async def close(self):
        self.closed = True


class ScriptedClient(HttpClient):
    """An HttpClient whose transport replays a queued list of responses."""

    def __init__(self, script, *, attempts: int = 2, **kwargs):
        super().__init__(
            retry=RetryPolicy(attempts=attempts, base_delay=0.001, max_delay=0.002),
            **kwargs,
        )
        self.script = list(script)
        self.fake = FakeSession([])

    async def __aenter__(self) -> ScriptedClient:
        self.fake = FakeSession(self.script)
        self._session = self.fake  # type: ignore[assignment]
        return self

    @property
    def urls(self) -> list[str]:
        """URLs requested, still readable after the client has closed."""
        return self.fake.requested


class TestBotWallDetection:
    @pytest.mark.parametrize(
        "body",
        [
            b"<html><title>Rule34.xxx CAPTCHA</title></html>",
            b"<div class='captcha-container'>prove it</div>",
            b"<script src='//cdn.cloudflare.com/cdn-cgi/challenge-platform/x'></script>",
        ],
    )
    def test_markers_detected(self, body):
        assert _looks_like_bot_wall(body.decode().lower())

    @pytest.mark.parametrize("body", [b'{"id": "1"}', b"binary\x89PNG", b""])
    def test_normal_bodies_pass(self, body):
        assert not _looks_like_bot_wall(body.decode("utf-8", errors="replace").lower())


class TestErrorClassification:
    async def test_captcha_page_raises_bot_protected(self, captcha_html):
        client = ScriptedClient([StubResponse(403, captcha_html.encode())])
        async with client:
            with pytest.raises(BotProtectedError) as exc:
                await client.get_text("https://rule34.xxx/x")
        assert exc.value.status == 403
        assert "PHPSESSID" in str(exc.value), "message should tell the user what to do"

    async def test_captcha_on_200_still_detected(self, captcha_html):
        client = ScriptedClient([StubResponse(200, captcha_html.encode())])
        async with client:
            with pytest.raises(BotProtectedError):
                await client.get_text("https://rule34.xxx/x")

    async def test_404_carries_status(self):
        client = ScriptedClient([StubResponse(404)])
        async with client:
            with pytest.raises(SourceUnavailableError) as exc:
                await client.get_text("https://rule34.paheal.net/post/list/nope")
        assert exc.value.status == 404

    async def test_plain_403_is_retried_then_given_up(self):
        client = ScriptedClient(
            [StubResponse(403, b"forbidden")],
            attempts=2,
        )
        async with client:
            with pytest.raises(SourceUnavailableError):
                await client.get_text("https://x.test/y")
        assert len(client.urls) == 2

    async def test_retryable_status_is_retried_then_succeeds(self):
        client = ScriptedClient(
            [StubResponse(503), StubResponse(200, b'{"ok": true}')],
            attempts=3,
        )
        async with client:
            assert await client.get_json("https://x.test/y") == {"ok": True}
        assert len(client.urls) == 2

    async def test_retry_after_header_is_respected(self):
        resp = StubResponse(429, headers={"Retry-After": "0"})
        client = ScriptedClient([resp, StubResponse(200, b"{}")])
        async with client:
            assert await client.get_json("https://x.test/y") == {}

    async def test_giving_up_reports_the_last_status(self):
        client = ScriptedClient([StubResponse(503)], attempts=2)
        async with client:
            with pytest.raises(SourceUnavailableError, match="HTTP 503"):
                await client.get_json("https://x.test/y")

    async def test_non_json_body_is_reported_clearly(self):
        client = ScriptedClient([StubResponse(200, b"<html>hello</html>")])
        async with client:
            with pytest.raises(SourceUnavailableError, match="expected JSON"):
                await client.get_json("https://x.test/y")


class TestNotFoundMeansEmpty:
    """A search that matches nothing 404s; that is not a failure."""

    async def test_paheal_404_yields_an_empty_page(self):
        from r34dl.sources.html_list import PahealHtmlSource
        from r34dl.tagging import TagSet

        client = ScriptedClient([StubResponse(404)])
        async with client:
            source = PahealHtmlSource(client)
            page = await source.fetch_page(TagSet.parse("nosuchtag"), 1)
        assert page.posts == ()
        assert page.has_more is False

    async def test_paheal_500_still_raises(self):
        from r34dl.sources.html_list import PahealHtmlSource
        from r34dl.tagging import TagSet

        client = ScriptedClient([StubResponse(500)], attempts=1)
        async with client:
            with pytest.raises(SourceUnavailableError):
                await PahealHtmlSource(client).fetch_page(TagSet.parse("x"), 1)


class TestSessionLifecycle:
    async def test_use_outside_context_manager_is_an_error(self):
        with pytest.raises(RuntimeError, match="async context manager"):
            _ = HttpClient().session

    async def test_close_is_idempotent(self):
        client = HttpClient()
        async with client:
            pass
        await client.close()  # must not raise