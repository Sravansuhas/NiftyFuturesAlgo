"""
Lightweight client-side rate limiter aligned with Kite Connect API limits.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from .kite_connect_rules import (
    RATE_LIMIT_DEFAULT_RPS,
    RATE_LIMIT_HISTORICAL_RPS,
    RATE_LIMIT_ORDER_RPS,
    RATE_LIMIT_ORDERS_PER_10S,
    RATE_LIMIT_QUOTE_RPS,
)


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / max(requests_per_second, 0.01)
        self._lock = threading.Lock()
        self._last_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_at
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_at = time.time()


class OrderBurstTracker:
    """Rolling 10-second order counter — reject before broker burst limits."""

    def __init__(self, max_per_10s: int = RATE_LIMIT_ORDERS_PER_10S) -> None:
        self._max = max_per_10s
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - 10.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    @property
    def orders_last_10s(self) -> int:
        with self._lock:
            self._prune(time.time())
            return len(self._timestamps)

    @property
    def max_per_10s(self) -> int:
        return self._max

    def try_acquire(self) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            self._prune(now)
            count = len(self._timestamps)
            if count >= self._max:
                return False, count
            self._timestamps.append(now)
            return True, count + 1


quote_limiter = RateLimiter(RATE_LIMIT_QUOTE_RPS)
historical_limiter = RateLimiter(RATE_LIMIT_HISTORICAL_RPS)
order_limiter = RateLimiter(RATE_LIMIT_ORDER_RPS)
default_limiter = RateLimiter(RATE_LIMIT_DEFAULT_RPS)
order_burst_tracker = OrderBurstTracker()