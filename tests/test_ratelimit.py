"""Rate limiter and retry backoff."""

from __future__ import annotations

import asyncio
import time

import pytest

from r34dl.net.client import RetryPolicy
from r34dl.net.ratelimit import RateLimiter


class TestRateLimiter:
    async def test_disabled_when_rate_is_zero(self):
        limiter = RateLimiter(rate=0)
        start = time.monotonic()
        for _ in range(20):
            await limiter.acquire()
        assert time.monotonic() - start < 0.2

    async def test_spaces_requests_out(self):
        limiter = RateLimiter(rate=20, period=1.0)  # 50ms apart
        start = time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # 3 gaps of 50ms = 150ms minimum.
        assert elapsed >= 0.13

    async def test_concurrent_callers_are_serialised(self):
        limiter = RateLimiter(rate=50, period=1.0)  # 20ms apart
        start = time.monotonic()
        await asyncio.gather(*(limiter.acquire() for _ in range(5)))
        assert time.monotonic() - start >= 0.07

    async def test_penalise_delays_the_next_request(self):
        limiter = RateLimiter(rate=1000, period=1.0)
        start = time.monotonic()
        await limiter.penalise(0.2)
        await limiter.acquire()
        assert time.monotonic() - start >= 0.19

    async def test_penalise_ignores_non_positive(self):
        limiter = RateLimiter(rate=1000)
        start = time.monotonic()
        await limiter.penalise(0)
        await limiter.penalise(-5)
        await limiter.acquire()
        assert time.monotonic() - start < 0.1


class TestRetryPolicy:
    def test_delay_grows_with_attempts(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=100)
        early = policy.delay_for(1)
        later = policy.delay_for(5)
        assert later > early

    def test_delay_is_capped(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=3)
        for _ in range(50):
            assert policy.delay_for(30) <= 3

    def test_retry_after_wins(self):
        policy = RetryPolicy(base_delay=10.0, max_delay=60)
        assert policy.delay_for(1, retry_after=2.0) == 2.0

    def test_retry_after_is_still_capped(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=30)
        assert policy.delay_for(1, retry_after=9999) == 30

    def test_jitter_stays_in_the_lower_half(self):
        # attempt 3 with base 4.0 doubles to a 16s ceiling, and jitter only
        # ever shortens the wait.
        policy = RetryPolicy(base_delay=4.0, max_delay=100)
        for _ in range(50):
            assert 8.0 <= policy.delay_for(3) <= 16.0

    @pytest.mark.parametrize("attempt", [0, 1, 2, 5])
    def test_never_negative(self, attempt):
        assert RetryPolicy().delay_for(attempt) >= 0