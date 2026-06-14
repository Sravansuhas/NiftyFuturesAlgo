"""
DataFeed Abstraction — local-first market data for NIFTY / BANKNIFTY / SENSEX.

Uses KiteTicker in a background thread (official Zerodha threaded pattern) to avoid
Twisted reactor conflicts with uvicorn. REST polling remains the fallback when WS
is disabled, stale, or disconnected.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)

STALE_PRICE_SECONDS = 15.0


class BaseDataFeed:
    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def subscribe(self, tokens: List[int]):
        raise NotImplementedError

    def get_last_price(self, token: int) -> Optional[float]:
        raise NotImplementedError

    def get_last_price_with_age(self, token: int) -> Tuple[Optional[float], float]:
        price = self.get_last_price(token)
        return price, float("inf")

    def is_connected(self) -> bool:
        return False


class KiteWebSocketFeed(BaseDataFeed):
    """
    Real-time LTP feed via KiteTicker (threaded mode — compatible with FastAPI main thread).
    Reference: zerodha/pykiteconnect examples/threaded_ticker.py
    """

    def __init__(self, api_key: str, access_token: str):
        self.api_key = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self._prices: Dict[int, float] = {}
        self._timestamps: Dict[int, float] = {}
        self._lock = threading.Lock()
        self._connected = False
        self._subscribed: List[int] = []

    def start(self) -> None:
        if self.kws is not None:
            logger.warning("WebSocket already started")
            return

        logger.info("Starting Kite WebSocket (KiteTicker threaded=True)...")
        self.kws = KiteTicker(self.api_key, self.access_token)

        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error
        self.kws.on_reconnect = self._on_reconnect
        self.kws.on_noreconnect = self._on_noreconnect
        # FAQ: order updates via WebSocket when postback URL unavailable (local dev)
        self.kws.on_order_update = self._on_order_update

        self.kws.connect(threaded=True)

    def update_access_token(self, access_token: str) -> None:
        """Restart ticker after dashboard/CLI token refresh without full engine restart."""
        token = (access_token or "").strip()
        if not token or token == self.access_token:
            return
        subscribed = list(self._subscribed)
        self.stop()
        self.access_token = token
        self.start()
        if subscribed:
            self.subscribe(subscribed)

    def stop(self) -> None:
        if self.kws is not None:
            logger.info("Stopping Kite WebSocket...")
            try:
                self.kws.close()
            except Exception as exc:
                logger.debug(f"WS close note: {exc}")
            self.kws = None
            self._connected = False

    def subscribe(self, tokens: List[int]) -> None:
        if not tokens:
            return
        self._subscribed = list(tokens)
        if not self.kws:
            logger.warning("WebSocket not started yet; tokens queued for on_connect")
            return
        if self._connected:
            self._do_subscribe(tokens)

    def _do_subscribe(self, tokens: List[int]) -> None:
        if not self.kws:
            return
        try:
            self.kws.subscribe(tokens)
            self.kws.set_mode(self.kws.MODE_LTP, tokens)
            logger.info(f"Subscribed to {len(tokens)} tokens via WebSocket (LTP mode)")
        except Exception as exc:
            logger.warning(f"WebSocket subscribe failed: {exc}")

    def get_last_price(self, token: int) -> Optional[float]:
        with self._lock:
            return self._prices.get(token)

    def get_last_price_with_age(self, token: int) -> Tuple[Optional[float], float]:
        with self._lock:
            price = self._prices.get(token)
            ts = self._timestamps.get(token)
        if price is None or ts is None:
            return None, float("inf")
        return price, max(0.0, time.time() - ts)

    def is_stale(self, token: int, max_age_seconds: float = STALE_PRICE_SECONDS) -> bool:
        _, age = self.get_last_price_with_age(token)
        return age > max_age_seconds

    def is_connected(self) -> bool:
        if self.kws is not None and hasattr(self.kws, "is_connected"):
            try:
                return bool(self.kws.is_connected())
            except Exception:
                pass
        return self._connected

    def _on_ticks(self, ws, ticks):
        now = time.time()
        with self._lock:
            for tick in ticks:
                token = tick.get("instrument_token")
                ltp = tick.get("last_price")
                if token and ltp is not None:
                    self._prices[int(token)] = float(ltp)
                    self._timestamps[int(token)] = now

    def _on_connect(self, ws, response):
        self._connected = True
        logger.info("Kite WebSocket connected successfully.")
        if self._subscribed:
            self._do_subscribe(self._subscribed)

    def _on_close(self, ws, code, reason):
        self._connected = False
        logger.warning(f"WebSocket closed: {code} - {reason}")

    def _on_error(self, ws, code, reason):
        logger.error(f"WebSocket error: {code} - {reason}")

    def _on_reconnect(self, ws, attempts_count):
        logger.info(f"WebSocket reconnecting (attempt {attempts_count})...")

    def _on_noreconnect(self, ws):
        self._connected = False
        logger.error("WebSocket reconnect exhausted — falling back to REST polling")

    def _on_order_update(self, ws, data):
        """Kite WS order update — FAQ recommends this over postback for local apps."""
        try:
            from .instruments_manager import instruments_manager
            from .order_lifecycle import order_lifecycle
            if instruments_manager.kite:
                order_lifecycle.bind_kite(instruments_manager.kite)
            order_lifecycle.handle_postback(data)
        except Exception as exc:
            logger.debug("WS order update handler skipped: %s", exc)


class SimulatedDataFeed(BaseDataFeed):
    """Placeholder for pure offline simulation."""

    pass