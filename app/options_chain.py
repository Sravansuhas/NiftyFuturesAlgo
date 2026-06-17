"""
OptionsChainManager

Phase 0B options infrastructure: fetch, cache, and query index option chains
(NIFTY / BANKNIFTY on NFO, SENSEX on BFO).

No live order placement — chain data only.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from .instruments_manager import instruments_manager

logger = logging.getLogger(__name__)

SUPPORTED_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})

_STRIKE_STEPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}

CHAIN_CACHE_DIR = Path("data/options_chain")
CHAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_expiry(expiry: Union[date, datetime, str, None]) -> Optional[date]:
    if expiry is None:
        return None
    if isinstance(expiry, datetime):
        return expiry.date()
    if isinstance(expiry, date):
        return expiry
    return pd.to_datetime(expiry).date()


def _expiry_cache_token(expiry: date) -> str:
    return expiry.strftime("%Y-%m-%d")


def _segment_for(underlying: str) -> str:
    return "BFO" if underlying.upper() == "SENSEX" else "NFO"


def _strike_step(underlying: str) -> int:
    return _STRIKE_STEPS.get(underlying.upper(), 50)


class OptionsChainManager:
    """
    Load and cache option chains for supported index underlyings.

    Mirrors InstrumentsManager patterns: bind/load, daily instrument cache,
    segment-aware NFO/BFO filtering.
    """

    def __init__(self, kite=None, instruments_mgr=None):
        self.kite = kite
        self.instruments_mgr = instruments_mgr or instruments_manager
        self._chains: Dict[str, pd.DataFrame] = {}
        self._spot_prices: Dict[str, float] = {}
        self._no_kite_warned = False

    def bind(self, kite, force: bool = False) -> bool:
        """Attach Kite client and ensure instruments are loaded."""
        changed = self.kite is not kite
        self.kite = kite
        if changed:
            self._no_kite_warned = False
            self._chains.clear()
        if self.kite:
            self.instruments_mgr.bind(self.kite, force=force or changed)
        return bool(self.kite)

    def is_ready(self) -> bool:
        return bool(self.kite and self.instruments_mgr.is_ready())

    def set_spot_price(self, underlying: str, spot: float) -> None:
        """Cache latest underlying spot for ATM helpers."""
        underlying_upper = underlying.upper()
        if spot and spot > 0:
            self._spot_prices[underlying_upper] = float(spot)

    def get_spot_price(self, underlying: str) -> Optional[float]:
        return self._spot_prices.get(underlying.upper())

    def _cache_path(self, underlying: str, expiry: date) -> Path:
        token = _expiry_cache_token(expiry)
        return CHAIN_CACHE_DIR / f"{underlying.upper()}_{token}.parquet"

    def _filter_option_instruments(
        self,
        underlying: str,
        expiry: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        underlying_upper = underlying.upper()
        if underlying_upper not in SUPPORTED_UNDERLYINGS:
            raise ValueError(f"Unsupported underlying: {underlying}")

        segment = _segment_for(underlying_upper)
        self.instruments_mgr.load()
        instruments = self.instruments_mgr._instruments.get(segment, [])

        options: List[Dict[str, Any]] = []
        for inst in instruments:
            if inst.get("instrument_type") not in {"CE", "PE"}:
                continue

            inst_expiry = _normalize_expiry(inst.get("expiry"))
            if not inst_expiry:
                continue

            if underlying_upper == "SENSEX":
                symbol_match = "SENSEX" in str(inst.get("tradingsymbol", "")).upper()
            else:
                symbol_match = inst.get("name") == underlying_upper

            if not symbol_match:
                continue

            if expiry and inst_expiry != expiry:
                continue

            row = dict(inst)
            row["underlying"] = underlying_upper
            row["segment"] = segment
            row["expiry"] = inst_expiry
            options.append(row)

        return options

    def _available_expiries(self, underlying: str) -> List[date]:
        options = self._filter_option_instruments(underlying)
        expiries = sorted({opt["expiry"] for opt in options if opt.get("expiry")})
        return expiries

    def resolve_expiry(
        self,
        underlying: str,
        expiry: Optional[Union[date, datetime, str]] = None,
    ) -> Optional[date]:
        """Pick explicit expiry or nearest upcoming chain expiry."""
        if expiry is not None:
            return _normalize_expiry(expiry)

        expiries = self._available_expiries(underlying)
        if not expiries:
            return None

        today = datetime.now().date()
        upcoming = [e for e in expiries if e >= today]
        return upcoming[0] if upcoming else expiries[-1]

    def fetch_and_cache_chain(
        self,
        kite=None,
        underlying: str = "NIFTY",
        expiry: Optional[Union[date, datetime, str]] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Filter NFO/BFO option instruments and cache to parquet.

        Cache path: data/options_chain/{SYMBOL}_{YYYY-MM-DD}.parquet
        """
        if kite is not None:
            self.bind(kite)

        underlying_upper = underlying.upper()
        if underlying_upper not in SUPPORTED_UNDERLYINGS:
            raise ValueError(f"Unsupported underlying: {underlying_upper}")

        if not self.kite:
            if not self._no_kite_warned:
                logger.warning(
                    "OptionsChainManager: no Kite client — cannot fetch option chain."
                )
                self._no_kite_warned = True
            return pd.DataFrame()

        resolved_expiry = self.resolve_expiry(underlying_upper, expiry)
        if not resolved_expiry:
            logger.warning(f"No option expiries found for {underlying_upper}")
            return pd.DataFrame()

        cache_key = f"{underlying_upper}_{_expiry_cache_token(resolved_expiry)}"
        cache_file = self._cache_path(underlying_upper, resolved_expiry)

        if cache_file.exists() and not force_refresh:
            try:
                df = pd.read_parquet(cache_file)
                self._chains[cache_key] = df
                logger.info(
                    f"[OptionsChain] Loaded {len(df)} rows from cache: {cache_file.name}"
                )
                return df
            except Exception as exc:
                logger.warning(f"[OptionsChain] Cache read failed ({exc}); rebuilding.")

        options = self._filter_option_instruments(underlying_upper, resolved_expiry)
        if not options:
            logger.warning(
                f"[OptionsChain] No {underlying_upper} options for expiry {resolved_expiry}"
            )
            return pd.DataFrame()

        df = pd.DataFrame(options)
        if "strike" in df.columns:
            df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
            df = df.dropna(subset=["strike"])
            df["strike"] = df["strike"].astype(float)
            df = df.sort_values(["strike", "instrument_type"]).reset_index(drop=True)

        try:
            df.to_parquet(cache_file, compression="snappy")
            logger.info(
                f"[OptionsChain] Cached {len(df)} instruments → {cache_file.name}"
            )
        except Exception as exc:
            csv_file = cache_file.with_suffix(".csv")
            df.to_csv(csv_file, index=False)
            logger.warning(
                f"[OptionsChain] Parquet unavailable ({exc}); saved CSV → {csv_file.name}"
            )

        self._chains[cache_key] = df
        return df

    def get_chain(
        self,
        underlying: str,
        expiry: Optional[Union[date, datetime, str]] = None,
    ) -> pd.DataFrame:
        """Return in-memory or on-disk chain for underlying + expiry."""
        underlying_upper = underlying.upper()
        resolved_expiry = self.resolve_expiry(underlying_upper, expiry)
        if not resolved_expiry:
            return pd.DataFrame()

        cache_key = f"{underlying_upper}_{_expiry_cache_token(resolved_expiry)}"
        if cache_key in self._chains:
            return self._chains[cache_key]

        cache_file = self._cache_path(underlying_upper, resolved_expiry)
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                self._chains[cache_key] = df
                return df
            except Exception as exc:
                logger.warning(f"[OptionsChain] Failed to read {cache_file.name}: {exc}")

        return self.fetch_and_cache_chain(
            kite=self.kite,
            underlying=underlying_upper,
            expiry=resolved_expiry,
        )

    def get_atm_strike(
        self,
        underlying: str,
        spot_price: Optional[float] = None,
        expiry: Optional[Union[date, datetime, str]] = None,
    ) -> Optional[float]:
        """Round spot to nearest exchange strike step for the underlying."""
        underlying_upper = underlying.upper()
        spot = spot_price if spot_price is not None else self.get_spot_price(underlying_upper)
        if spot is None or spot <= 0:
            logger.debug(f"[OptionsChain] No spot price for {underlying_upper} ATM strike")
            return None

        step = _strike_step(underlying_upper)
        atm = round(spot / step) * step

        chain = self.get_chain(underlying_upper, expiry)
        if chain.empty or "strike" not in chain.columns:
            return float(atm)

        strikes = sorted(chain["strike"].dropna().unique())
        if not strikes:
            return float(atm)

        nearest = min(strikes, key=lambda s: abs(s - atm))
        return float(nearest)

    def get_strikes_near_atm(
        self,
        underlying: str,
        n: int = 5,
        spot_price: Optional[float] = None,
        expiry: Optional[Union[date, datetime, str]] = None,
    ) -> List[float]:
        """
        Return up to (2*n + 1) strikes centered on ATM: n below, ATM, n above.
        """
        underlying_upper = underlying.upper()
        chain = self.get_chain(underlying_upper, expiry)
        if chain.empty or "strike" not in chain.columns:
            return []

        atm = self.get_atm_strike(underlying_upper, spot_price=spot_price, expiry=expiry)
        if atm is None:
            return []

        unique_strikes = sorted(chain["strike"].dropna().unique())
        if atm not in unique_strikes:
            unique_strikes.append(atm)
            unique_strikes = sorted(unique_strikes)

        atm_idx = min(
            range(len(unique_strikes)),
            key=lambda i: abs(unique_strikes[i] - atm),
        )
        start = max(0, atm_idx - n)
        end = min(len(unique_strikes), atm_idx + n + 1)
        return [float(s) for s in unique_strikes[start:end]]

    def resolve_strikes_from_chain(
        self,
        strikes: Dict[str, float],
        underlying: str,
        expiry: Optional[Union[date, datetime, str]] = None,
    ) -> Dict[str, float]:
        """
        Snap iron-condor strikes to chain-listed values while preserving order.

        Each target is mapped to the nearest available strike strictly above
        the previous leg (put_long < put_short < call_short < call_long).
        """
        required = ("put_long", "put_short", "call_short", "call_long")
        missing = [k for k in required if k not in strikes]
        if missing:
            raise ValueError(f"strikes missing required keys: {missing}")

        underlying_upper = underlying.upper()
        chain = self.get_chain(underlying_upper, expiry)
        if chain.empty or "strike" not in chain.columns:
            return {k: float(strikes[k]) for k in required}

        available = sorted({float(s) for s in chain["strike"].dropna().unique()})
        if len(available) < 4:
            logger.warning(
                "[OptionsChain] Fewer than 4 strikes for %s — using raw strikes",
                underlying_upper,
            )
            return {k: float(strikes[k]) for k in required}

        resolved: Dict[str, float] = {}
        prev = float("-inf")
        for key in required:
            target = float(strikes[key])
            candidates = [s for s in available if s > prev]
            if not candidates:
                logger.warning(
                    "[OptionsChain] Could not preserve strike order for %s at %s",
                    underlying_upper,
                    key,
                )
                return {k: float(strikes[k]) for k in required}
            nearest = min(candidates, key=lambda s: abs(s - target))
            resolved[key] = nearest
            prev = nearest

        pl, ps, cs, cl = (
            resolved["put_long"],
            resolved["put_short"],
            resolved["call_short"],
            resolved["call_long"],
        )
        if not (pl < ps < cs < cl):
            logger.warning(
                "[OptionsChain] Strike ordering invalid after snap for %s",
                underlying_upper,
            )
            return {k: float(strikes[k]) for k in required}

        return resolved


# Global singleton (mirrors instruments_manager)
options_chain_manager = OptionsChainManager()