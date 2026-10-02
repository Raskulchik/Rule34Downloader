"""A resilient HTTP client wrapping a single pooled ``aiohttp`` session.

Responsibilities kept here so that no other module has to think about retries,
backoff, cookies, rate limiting or bot-wall detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from ..errors import BotProtectedError, SourceUnavailableError
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

#: Statuses worth retrying: transient server trouble and explicit rate limits.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
#: Statuses that mean "we are being blocked", not "try again in a moment".
BLOCKED_STATUS = frozenset({401, 403, 407})
#: Substrings that identify an anti-bot interstitial even on a 200.
BOT_MARKERS = ("captcha", "captcha-container", "cf-browser-verification", "challenge-platform")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

DEFAULT_HEADERS: Mapping[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How hard to try before giving up on a request."""

    attempts: int = 5
    base_delay: float = 2.0
    max_delay: float = 60.0

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with full jitter, honouring ``Retry-After``."""
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self.max_delay)
        ceiling = min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)
        return random.uniform(ceiling * 0.5, ceiling)


class HttpClient:
    """Pooled HTTP access with retries, backoff and rate limiting.

    Use as an async context manager so the session is always closed::

        async with HttpClient() as client:
            data = await client.get_json(url)
    """

    def __init__(
        self,
        *,
        retry: RetryPolicy | None = None,
        limiter: RateLimiter | None = None,
        headers: Mapping[str, str] | None = None,
        cookie: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.retry = retry or RetryPolicy()
        self.limiter = limiter or RateLimiter(rate=0)
        self.headers: dict[str, str] = {**DEFAULT_HEADERS, **(headers or {})}
        self._cookie = cookie
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=15, sock_read=60)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> HttpClient:
        headers = dict(self.headers)
        if self._cookie:
            headers["Cookie"] = self._cookie
        # Pooled connections, no artificial cap: the downloader's own
        # semaphore is the real limit.
        connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=self._timeout,
            connector=connector,
            trust_env=True,
            raise_for_status=False,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            # Give aiohttp's transports a tick to shut down cleanly.
            await asyncio.sleep(0)
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("HttpClient must be used as an async context manager")
        return self._session

    async def get(
        self, url: str, *, accept: str | None = None, referer: str | None = None
    ) -> aiohttp.ClientResponse:
        """GET ``url``, retrying transient failures.

        The response context manager is *not* entered; the caller owns the body.
        Raises:
            BotProtectedError: the host served an anti-bot wall.
            SourceUnavailableError: retries exhausted, or a hard failure.
        """
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if referer:
            headers["Referer"] = referer
        host = urlsplit(url).hostname or "remote host"
        last_error: str = "no attempt made"

        for attempt in range(1, self.retry.attempts + 1):
            await self.limiter.acquire()
            try:
                resp = await self.session.get(url, headers=headers)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.debug(
                    "GET %s failed (attempt %d/%d): %s",
                    url, attempt, self.retry.attempts, last_error,
                )
                await self._sleep_between(attempt, None, url)
                continue

            # The caller owns the returned response, so it must stay open here.
            # On every error path we release it ourselves before retrying.
            if resp.status in BLOCKED_STATUS:
                body = await self._peek(resp)
                resp.release()
                if _looks_like_bot_wall(body):
                    raise BotProtectedError(host, resp.status)
                # A plain 403/401 is worth retrying: upstream transiently serves
                # these while under load.
                last_error = f"HTTP {resp.status}"
                log.debug("GET %s -> %s (attempt %d)", url, resp.status, attempt)
                await self._sleep_between(attempt, None, url)
                continue

            if resp.status in RETRYABLE_STATUS:
                retry_after = _retry_after(resp)
                resp.release()
                last_error = f"HTTP {resp.status}"
                log.debug("GET %s -> %s (attempt %d)", url, resp.status, attempt)
                await self._sleep_between(attempt, retry_after, url)
                continue

            if resp.status >= 400:
                status = resp.status
                resp.release()
                raise SourceUnavailableError(f"{url}: HTTP {status}", status=status)

            return resp

        raise SourceUnavailableError(
            f"{url}: giving up after {self.retry.attempts} attempts ({last_error})"
        )

    async def _sleep_between(
        self, attempt: int, retry_after: float | None, url: str
    ) -> None:
        delay = self.retry.delay_for(attempt, retry_after)
        # Make every in-flight worker wait too, not just this one.
        await self.limiter.penalise(delay)
        log.debug("backing off %.1fs before retry %d of %s", delay, attempt + 1, url)
        await asyncio.sleep(delay)

    @staticmethod
    async def _peek(resp: aiohttp.ClientResponse) -> str:
        """Read a small prefix of the body for inspection, without buffering."""
        try:
            chunk = await resp.content.read(4096)
        except (TimeoutError, aiohttp.ClientError):
            return ""
        return chunk.decode("utf-8", errors="replace").lower()

    async def get_json(self, url: str) -> Any:
        """GET ``url`` and decode JSON, failing loudly on an HTML answer."""
        resp = await self.get(url, accept="application/json")
        async with resp:
            text = await resp.text()
        if _looks_like_bot_wall(text.lower()):
            raise BotProtectedError(urlsplit(url).hostname or "remote host", resp.status)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise SourceUnavailableError(
                f"{url}: expected JSON, got {text[:80]!r}"
            ) from exc

    async def get_text(self, url: str) -> str:
        """GET ``url`` and decode the body as text."""
        resp = await self.get(url, accept="text/html,application/xhtml+xml")
        async with resp:
            text = await resp.text()
        if _looks_like_bot_wall(text.lower()):
            raise BotProtectedError(urlsplit(url).hostname or "remote host", resp.status)
        return text

    async def stream(
        self, url: str, chunk_size: int = 64 * 1024, referer: str | None = None
    ) -> AsyncIterator[bytes]:
        """Stream a response body in chunks."""
        resp = await self.get(
            url, accept="image/*,application/octet-stream,*/*", referer=referer
        )
        async with resp:
            async for chunk in resp.content.iter_chunked(chunk_size):
                yield chunk


def _looks_like_bot_wall(body: str) -> bool:
    return any(marker in body for marker in BOT_MARKERS)


def _retry_after(resp: aiohttp.ClientResponse) -> float | None:
    header = resp.headers.get("Retry-After")
    if not header:
        return None
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    try:
        delta = parsedate_to_datetime(header).timestamp()
    except (TypeError, ValueError):
        return None
    return max(delta - time.time(), 0.0)