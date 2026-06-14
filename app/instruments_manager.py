"""
InstrumentsManager

Central place to load and query instruments from both NSE (NFO) and BSE (BFO).
This is the foundation for reliable multi-index (NIFTY, BANKNIFTY, SENSEX) support.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, List, Any

from .market_calendar import now_ist

logger = logging.getLogger(__name__)

# Kite LTP keys for index spot (compare against futures LTP in UI / diagnostics)
INDEX_SPOT_LTP_KEYS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX": "BSE:SENSEX",
}

_MONTH_CODES = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()

# Lot sizes when Kite instruments are unavailable
_FALLBACK_LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "SENSEX": 20,
}


def _fallback_tradingsymbol(index_name: str, ref_date: Optional[datetime] = None) -> str:
    """Nearest-month future symbol when Kite instrument dump is unavailable."""
    ref = ref_date or now_ist()
    code = _MONTH_CODES[ref.month - 1]
    year = str(ref.year)[2:]
    return f"{index_name.upper()}{year}{code}FUT"


def ltp_key(tradingsymbol: str, exchange: str = "NFO") -> str:
    """Kite REST quote/LTP API requires exchange:tradingsymbol (not raw token)."""
    return f"{exchange}:{tradingsymbol}"


class InstrumentsManager:
    def __init__(self, kite=None):
        self.kite = kite
        self._instruments: Dict[str, List[Dict]] = {}
        self._last_loaded: Optional[datetime] = None
        self._no_kite_warned = False

    def bind(self, kite, force: bool = False) -> bool:
        """
        Attach the shared Kite client and load instruments when possible.
        Call this as early as main() creates KiteConnect + TokenManager.
        """
        changed = self.kite is not kite
        self.kite = kite
        if changed:
            self._last_loaded = None
            self._no_kite_warned = False
        return self.load(force=force or changed)

    def is_ready(self) -> bool:
        return bool(self.kite and (self._instruments.get("NFO") or self._instruments.get("BFO")))

    def should_refresh(self) -> bool:
        """FAQ: download fresh instrument dump before each trading session."""
        if not self._last_loaded:
            return True
        today = now_ist().date()
        if self._last_loaded.date() != today:
            return True
        try:
            from .kite_connect_rules import is_safe_to_generate_token
            if is_safe_to_generate_token() and self._last_loaded.hour < 7:
                return True
        except Exception:
            pass
        return False

    def load(self, force: bool = False) -> bool:
        """Load instruments for NFO and BFO. Refreshes each trading morning."""
        if self._last_loaded and not force and not self.should_refresh():
            return self.is_ready()

        if not self.kite:
            if not self._no_kite_warned:
                logger.warning(
                    "InstrumentsManager: no Kite client yet — using fallback contracts until engine binds."
                )
                self._no_kite_warned = True
            return False

        try:
            from .kite_rate_limit import default_limiter
            default_limiter.wait()
            logger.info("Loading instruments from Kite (NFO + BFO)...")
            self._instruments["NFO"] = self.kite.instruments("NFO") or []
            default_limiter.wait()
            self._instruments["BFO"] = self.kite.instruments("BFO") or []
            self._last_loaded = now_ist()
            logger.info(
                f"Loaded {len(self._instruments['NFO'])} NFO + "
                f"{len(self._instruments['BFO'])} BFO instruments."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load instruments from Kite: {e}")
            self._instruments = {"NFO": [], "BFO": []}
            return False

    def _filter_unexpired_futures(self, futures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        today = now_ist().date()
        live = []
        for inst in futures:
            exp = self._normalize_expiry(inst.get("expiry"))
            if exp and exp.date() >= today:
                live.append(inst)
        return live

    def _fallback_future(self, index_upper: str) -> Dict[str, Any]:
        exchange = "BFO" if index_upper == "SENSEX" else "NFO"
        tradingsymbol = _fallback_tradingsymbol(index_upper)
        token = self.get_instrument_token(tradingsymbol, exchange) if self.kite else None
        return {
            "tradingsymbol": tradingsymbol,
            "instrument_token": token,
            "lot_size": _FALLBACK_LOT_SIZES.get(index_upper, 1),
            "expiry": None,
            "exchange": exchange,
            "segment": "BFO-FUT" if index_upper == "SENSEX" else "NFO-FUT",
            "source": "fallback",
        }

    def get_active_future(self, index_name: str) -> Optional[Dict[str, Any]]:
        """
        Returns the front-month (nearest expiry) active future for the given index.
        Falls back to static contract names when Kite data is unavailable.
        """
        index_upper = index_name.upper()

        if not self.kite:
            return self._fallback_future(index_upper)

        self.load()

        if index_upper == "SENSEX":
            segment = "BFO"
            instruments = self._instruments.get(segment, [])
            futures = [
                inst for inst in instruments
                if str(inst.get("tradingsymbol", "")).upper().startswith("SENSEX")
                and inst.get("instrument_type") == "FUT"
                and inst.get("expiry")
            ]
        else:
            segment = "NFO"
            instruments = self._instruments.get(segment, [])
            futures = [
                inst for inst in instruments
                if inst.get("name") == index_upper
                and inst.get("instrument_type") == "FUT"
                and inst.get("expiry")
            ]

        futures = self._filter_unexpired_futures(futures)

        if not futures:
            logger.debug(f"Using fallback future for {index_name} (no unexpired futures in {segment})")
            return self._fallback_future(index_upper)

        futures.sort(key=lambda x: x.get("expiry"))
        active = futures[0]
        logger.info(
            f"[Instruments] Selected active future for {index_name}: "
            f"{active['tradingsymbol']} (expiry {active.get('expiry')})"
        )

        return {
            "tradingsymbol": active["tradingsymbol"],
            "instrument_token": active["instrument_token"],
            "lot_size": active.get("lot_size", _FALLBACK_LOT_SIZES.get(index_upper, 0)),
            "expiry": active.get("expiry"),
            "exchange": active.get("exchange") or segment,
            "segment": active.get("segment"),
            "source": "kite",
        }

    def fetch_ltp(self, tradingsymbol: str, exchange: str = "NFO") -> Optional[float]:
        """Fetch LTP via Kite REST using the documented exchange:tradingsymbol key."""
        if not self.kite or not tradingsymbol:
            return None
        key = ltp_key(tradingsymbol, exchange)
        try:
            from .kite_rate_limit import quote_limiter
            quote_limiter.wait()
            data = self.kite.ltp([key])
            if key in data and data[key].get("last_price"):
                return float(data[key]["last_price"])
        except Exception as exc:
            logger.debug("fetch_ltp failed for %s: %s", key, exc)
        return None

    def fetch_index_spot_ltp(self, index_name: str) -> Optional[float]:
        key = INDEX_SPOT_LTP_KEYS.get(index_name.upper())
        if not key or not self.kite:
            return None
        try:
            from .kite_rate_limit import quote_limiter
            quote_limiter.wait()
            data = self.kite.ltp([key])
            if key in data and data[key].get("last_price"):
                return float(data[key]["last_price"])
        except Exception as exc:
            logger.debug("fetch_index_spot_ltp failed for %s: %s", key, exc)
        return None

    def get_instrument_token(self, tradingsymbol: str, exchange: str = "NFO") -> Optional[int]:
        """Helper to get token by tradingsymbol."""
        if not self.kite:
            return None
        self.load()
        instruments = self._instruments.get(exchange, [])
        for inst in instruments:
            if inst.get("tradingsymbol") == tradingsymbol:
                return inst.get("instrument_token")
        return None

    def _segment_for_index(self, index_name: str) -> str:
        return "BFO" if index_name.upper() == "SENSEX" else "NFO"

    def _normalize_expiry(self, expiry) -> Optional[datetime]:
        if expiry is None:
            return None
        if isinstance(expiry, datetime):
            return expiry
        if hasattr(expiry, "year") and hasattr(expiry, "month"):
            return datetime(expiry.year, expiry.month, expiry.day)
        try:
            return datetime.fromisoformat(str(expiry)[:10])
        except (TypeError, ValueError):
            return None

    def get_option_instruments(
        self,
        underlying: str,
        option_type: str,
        expiry,
        strike: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the matching CE/PE instrument for an index underlying.

        Args:
            underlying: NIFTY, BANKNIFTY, or SENSEX
            option_type: CE or PE
            expiry: expiry date (date, datetime, or YYYY-MM-DD string)
            strike: strike price

        Returns instrument dict with tradingsymbol, instrument_token, lot_size, etc.
        """
        index_upper = underlying.upper()
        opt_type_upper = option_type.upper()
        if opt_type_upper not in {"CE", "PE"}:
            logger.warning(f"Invalid option_type: {option_type}")
            return None

        if not self.kite:
            logger.debug("get_option_instruments: no Kite client")
            return None

        self.load()
        segment = self._segment_for_index(index_upper)
        instruments = self._instruments.get(segment, [])
        target_expiry = self._normalize_expiry(expiry)
        target_strike = float(strike)

        for inst in instruments:
            if inst.get("instrument_type") != opt_type_upper:
                continue

            inst_expiry = self._normalize_expiry(inst.get("expiry"))
            if target_expiry and inst_expiry and inst_expiry.date() != target_expiry.date():
                continue

            inst_strike = inst.get("strike")
            if inst_strike is None or float(inst_strike) != target_strike:
                continue

            if index_upper == "SENSEX":
                if "SENSEX" not in str(inst.get("tradingsymbol", "")).upper():
                    continue
            elif inst.get("name") != index_upper:
                continue

            return {
                "tradingsymbol": inst.get("tradingsymbol"),
                "instrument_token": inst.get("instrument_token"),
                "lot_size": inst.get("lot_size"),
                "expiry": inst.get("expiry"),
                "strike": float(inst_strike),
                "option_type": opt_type_upper,
                "underlying": index_upper,
                "exchange": inst.get("exchange", segment),
                "segment": inst.get("segment"),
                "tick_size": inst.get("tick_size"),
                "source": "kite",
            }

        logger.debug(
            f"No option instrument for {index_upper} {opt_type_upper} "
            f"strike={target_strike} expiry={expiry}"
        )
        return None


# Global singleton (simple for now)
instruments_manager = InstrumentsManager()