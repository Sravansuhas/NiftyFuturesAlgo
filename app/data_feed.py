"""
DataFeed Abstraction

This module will eventually become the single source of truth for live market data
for all three indices (NIFTY, BANKNIFTY, SENSEX).

Current status (June 2026):
- Primary path is still REST polling (with rich diagnostics) because of Twisted + uvicorn reactor conflicts.
- Kite best practice: WebSocket + MODE_LTP / MODE_QUOTE is strongly preferred for live data.
- This file has the skeleton. Real migration will happen once diagnostics (from diagnostic_logger) prove polling is the bottleneck (especially for SENSEX).

Design goals:
- Clean interface (start, stop, subscribe, get_last_price)
- Support both Real (WebSocket) and Simulated (Paper) modes
- Easy to swap without touching strategy code
"""

import logging
import threading
from typing import Callable, Dict, Optional, List
from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)


class BaseDataFeed:
    """Abstract base class."""

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def subscribe(self, tokens: List[int]):
        raise NotImplementedError

    def get_last_price(self, token: int) -> Optional[float]:
        raise NotImplementedError


class KiteWebSocketFeed(BaseDataFeed):
    """
    Real-time data feed using KiteTicker (WebSocket).

    This is the recommended way according to official Kite documentation.
    """

    def __init__(self, kite, api_key: str, access_token: str):
        self.kite = kite
        self.api_key = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self._prices: Dict[int, float] = {}
        self._lock = threading.Lock()
        self._connected = False

    def start(self):
        if self.kws:
            logger.warning("WebSocket already started")
            return

        logger.info("Starting Kite WebSocket (KiteTicker)...")

        self.kws = KiteTicker(self.api_key, self.access_token)

        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

        # Run in background thread
        thread = threading.Thread(target=self.kws.connect, daemon=True)
        thread.start()

    def stop(self):
        if self.kws:
            logger.info("Stopping Kite WebSocket...")
            self.kws.close()
            self.kws = None

    def subscribe(self, tokens: List[int]):
        if not self.kws:
            logger.warning("WebSocket not started. Cannot subscribe.")
            return

        self.kws.subscribe(tokens)
        self.kws.set_mode(self.kws.MODE_LTP, tokens)
        logger.info(f"Subscribed to {len(tokens)} tokens via WebSocket (LTP mode)")

    def get_last_price(self, token: int) -> Optional[float]:
        with self._lock:
            return self._prices.get(token)

    # --- Internal callbacks ---

    def _on_ticks(self, ws, ticks):
        with self._lock:
            for tick in ticks:
                token = tick.get("instrument_token")
                ltp = tick.get("last_price")
                if token and ltp is not None:
                    self._prices[token] = float(ltp)

    def _on_connect(self, ws, response):
        self._connected = True
        logger.info("Kite WebSocket connected successfully.")

    def _on_close(self, ws, code, reason):
        self._connected = False
        logger.warning(f"WebSocket closed: {code} - {reason}")

    def _on_error(self, ws, code, reason):
        logger.error(f"WebSocket error: {code} - {reason}")


# Placeholder for future simulated feed
class SimulatedDataFeed(BaseDataFeed):
    """Used for pure paper trading with generated movement when real data is unavailable."""
    pass  # Will implement properly later if needed