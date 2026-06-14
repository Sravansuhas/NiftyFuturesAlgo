"""
Lightweight client-side rate limiter aligned with Kite Connect API limits.
"""

from __future__ import annotations

import threading
import time

from .kite_connect_rules import (
    RATE_LIMIT_DEFAULT_RPS,
    RATE_LIMIT_HISTORICAL_RPS,
    RATE_LIMIT_ORDER_RPS,
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


quote_limiter = RateLimiter(RATE_LIMIT_QUOTE_RPS)
historical_limiter = RateLimiter(RATE_LIMIT_HISTORICAL_RPS)
order_limiter = RateLimiter(RATE_LIMIT_ORDER_RPS)
default_limiter = RateLimiter(RATE_LIMIT_DEFAULT_RPS)