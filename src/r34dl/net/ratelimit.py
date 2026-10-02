"""A token-bucket rate limiter shared by every outgoing request.

The upstream sites are small and fan-hosted; hammering them gets you CAPTCHA'd
or IP-banned. This keeps the tool polite by construction rather than by
scattered ``asyncio.sleep`` calls.
"""

from __future__ import annotations

import asyncio
import random
import time


class RateLimiter:
    """Allow ``rate`` operations per ``period`` seconds, smoothly spaced.

    Args:
        rate: Operations permitted per period. ``<= 0`` disables limiting.
        period: Window length in seconds.
        jitter: Random extra delay added to each wait, to avoid a
            thundering-herd of requests from concurrent workers.
    """

    __slots__ = ("_interval", "_jitter", "_lock", "_next_slot")

    def __init__(self, rate: float, period: float = 1.0, jitter: float = 0.0) -> None:
        if rate <= 0:
            self._interval = 0.0
        else:
            self._interval = period / rate
        self._jitter = max(jitter, 0.0)
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        """Block until the caller may issue its request."""
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next_slot)
            self._next_slot = start + self._interval
            delay = start - now
        if self._jitter:
            delay += random.uniform(0, self._jitter)
        if delay > 0:
            await asyncio.sleep(delay)

    async def penalise(self, seconds: float) -> None:
        """Push the next request out after a 429/503.

        Lets a server that is actively complaining throttle the whole
        download, not just the request that was answered.
        """
        if seconds <= 0:
            return
        async with self._lock:
            self._next_slot = max(self._next_slot, time.monotonic() + seconds)