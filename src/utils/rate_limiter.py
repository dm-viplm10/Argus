"""Token bucket rate limiter backed by Redis for distributed limiting."""

from __future__ import annotations

import asyncio
import time

from src.utils.logging import get_logger

logger = get_logger(__name__)


class TokenBucketRateLimiter:
    """In-process token bucket rate limiter.

    For distributed limiting across Celery workers, a Redis-backed
    implementation can replace this. This version is suitable for
    single-process or development use.
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate  # tokens per second
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now
