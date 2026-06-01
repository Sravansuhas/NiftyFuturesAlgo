"""
InstrumentsManager

Central place to load and query instruments from both NSE (NFO) and BSE (BFO).
This is the foundation for reliable multi-index (NIFTY, BANKNIFTY, SENSEX) support.

Features:
- Loads instruments for NFO + BFO (cached daily)
- Provides clean methods to get the current active future for any index
- Handles proper expiry sorting
- Designed to be used by both live engine and backtesting
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


class InstrumentsManager:
    def __init__(self, kite=None):
        self.kite = kite
        self._instruments: Dict[str, List[Dict]] = {}   # "NFO" and "BFO"
        self._last_loaded: Optional[datetime] = None

    def load(self, force: bool = False):
        """Load instruments for NFO and BFO. Caches for the day."""
        if self._last_loaded and not force:
            today = datetime.now().date()
            if self._last_loaded.date() == today:
                return

        if not self.kite:
            logger.warning("No Kite client provided to InstrumentsManager. Using empty instruments.")
            self._instruments = {"NFO": [], "BFO": []}
            return

        try:
            logger.info("Loading instruments from Kite (NFO + BFO)...")
            self._instruments["NFO"] = self.kite.instruments("NFO") or []
            self._instruments["BFO"] = self.kite.instruments("BFO") or []
            self._last_loaded = datetime.now()
            logger.info(f"Loaded {len(self._instruments['NFO'])} NFO + {len(self._instruments['BFO'])} BFO instruments.")
        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            self._instruments = {"NFO": [], "BFO": []}

    def get_active_future(self, index_name: str) -> Optional[Dict[str, Any]]:
        """
        Returns the front-month (nearest expiry) active future for the given index.

        index_name: "NIFTY", "BANKNIFTY", or "SENSEX"
        """
        self.load()

        index_upper = index_name.upper()
        if index_upper == "SENSEX":
            segment = "BFO"
            instruments = self._instruments.get(segment, [])
            # Very broad search for any active Sensex future
            futures = [
                inst for inst in instruments
                if "SENSEX" in str(inst.get("tradingsymbol", "")).upper()
                and inst.get("instrument_type") == "FUT"
                and inst.get("expiry")
            ]
        else:
            segment = "NFO"
            name_filter = index_upper
            instruments = self._instruments.get(segment, [])
            futures = [
                inst for inst in instruments
                if inst.get("name") == name_filter
                and inst.get("instrument_type") == "FUT"
                and inst.get("expiry")
            ]

        if not futures:
            logger.warning(f"No active futures found for {index_name} in {segment}")
            return None

        # Pick the one with the nearest future expiry (most important)
        futures.sort(key=lambda x: x.get("expiry"))
        active = futures[0]

        # Log the chosen contract for transparency
        logger.info(f"[Instruments] Selected active future for {index_name}: {active['tradingsymbol']} (expiry {active.get('expiry')})")

        return {
            "tradingsymbol": active["tradingsymbol"],
            "instrument_token": active["instrument_token"],
            "lot_size": active.get("lot_size", 0),
            "expiry": active.get("expiry"),
            "exchange": active.get("exchange"),
            "segment": active.get("segment"),
        }

    def get_instrument_token(self, tradingsymbol: str, exchange: str = "NFO") -> Optional[int]:
        """Helper to get token by tradingsymbol."""
        self.load()
        instruments = self._instruments.get(exchange, [])
        for inst in instruments:
            if inst.get("tradingsymbol") == tradingsymbol:
                return inst.get("instrument_token")
        return None


# Global singleton (simple for now)
instruments_manager = InstrumentsManager()