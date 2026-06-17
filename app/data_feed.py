"""
DataFeed Abstraction — local-first market data for NIFTY / BANKNIFTY / SENSEX.

Uses KiteTicker in a background thread (official Zerodha threaded pattern) to avoid
Twisted reactor conflicts with uvicorn. REST polling remains the fallback when WS
is disabled, stale, or disconnected.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from kiteconnect import KiteTicker

if TYPE_CHECKING:
    from .candle_builder import CandleBuilder

logger = logging.getLogger(__name__)

STALE_PRICE_SECONDS = 15.0

_ACTIVE_WS_FEED: Optional["KiteWebSocketFeed"] = None


def register_ws_feed(feed: Optional["KiteWebSocketFeed"]) -> None:
    """Expose the engine WebSocket feed to dashboard / API helpers (same process)."""
    global _ACTIVE_WS_FEED
    _ACTIVE_WS_FEED = feed


def get_active_ws_feed() -> Optional["KiteWebSocketFeed"]:
    return _ACTIVE_WS_FEED


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

    def __init__(
        self,
        api_key: str,
        access_token: str,
        *,
        candle_builder: Optional["CandleBuilder"] = None,
        enable_quote_mode: Optional[bool] = None,
    ):
        self.api_key = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self._prices: Dict[int, float] = {}
        self._timestamps: Dict[int, float] = {}
        self._lock = threading.Lock()
        self._connected = False
        self._subscribed: List[int] = []
        if enable_quote_mode is None:
            enable_quote_mode = os.getenv("ENABLE_WS_QUOTE", "false").strip().lower() in {
                "1", "true", "yes", "on",
            }
        self._enable_quote_mode = bool(enable_quote_mode)
        self._candle_builder = candle_builder
        if self._enable_quote_mode and self._candle_builder is None:
            from .candle_builder import CandleBuilder

            self._candle_builder = CandleBuilder()

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
            mode = self.kws.MODE_QUOTE if self._enable_quote_mode else self.kws.MODE_LTP
            self.kws.set_mode(mode, tokens)
            mode_label = "QUOTE" if self._enable_quote_mode else "LTP"
            logger.info(f"Subscribed to {len(tokens)} tokens via WebSocket ({mode_label} mode)")
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

    def get_candle_builder(self) -> Optional["CandleBuilder"]:
        return self._candle_builder

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

        if self._candle_builder:
            for tick in ticks:
                self._dispatch_tick_to_builder(tick)

    def _dispatch_tick_to_builder(self, tick: dict) -> None:
        if not self._candle_builder:
            return
        token = tick.get("instrument_token")
        ltp = tick.get("last_price")
        if not token or ltp is None:
            return
        ts = tick.get("timestamp") or tick.get("exchange_timestamp")
        if isinstance(ts, datetime):
            tick_ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, (int, float)):
            tick_ts = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            tick_ts = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        volume = tick.get("volume")
        oi = tick.get("oi")
        try:
            self._candle_builder.on_tick(
                int(token),
                float(ltp),
                tick_ts,
                volume=int(volume) if volume is not None else None,
                oi=int(oi) if oi is not None else None,
            )
        except Exception as exc:
            logger.debug("CandleBuilder tick dispatch skipped: %s", exc)

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