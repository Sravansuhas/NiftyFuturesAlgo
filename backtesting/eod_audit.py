"""
EOD data audit — compare Kite parquet cache vs NSE/BSE official bhavcopy.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from app.market_calendar import MARKET_HOLIDAYS, now_ist

logger = logging.getLogger(__name__)

REPORT_DIR = Path("data/eod_audit_reports")
CACHE_DIR = Path("data/historical_cache")
PRICE_TOL = 0.10
VOLUME_TOL_PCT = 0.05


def previous_trading_day(at: Optional[date] = None) -> date:
    d = at or now_ist().date()
    probe = d - timedelta(days=1)
    for _ in range(10):
        if probe.weekday() < 5 and probe not in MARKET_HOLIDAYS:
            return probe
        probe -= timedelta(days=1)
    return d - timedelta(days=1)


def cache_daily_ohlc(
    trade_date: date,
    underlying: str,
    *,
    cache_dir: Path = CACHE_DIR,
) -> Optional[Dict[str, Any]]:
    """Aggregate 5m parquet bars into daily OHLC for front-month contract."""
    files = sorted(cache_dir.glob(f"{underlying}*5minute.parquet"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        return None

    frames = []
    for path in files[:4]:
        try:
            df = pd.read_parquet(path)
            if df.empty:
                continue
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            day_mask = df.index.date == trade_date
            day_df = df.loc[day_mask]
            if len(day_df) >= 10:
                frames.append(day_df)
        except Exception:
            continue

    if not frames:
        return None

    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    if combined.empty:
        return None

    sym = underlying
    if "symbol" in combined.columns:
        sym = str(combined["symbol"].iloc[-1])

    return {
        "tradingsymbol": sym,
        "open": float(combined["open"].iloc[0]),
        "high": float(combined["high"].max()),
        "low": float(combined["low"].min()),
        "close": float(combined["close"].iloc[-1]),
        "volume": int(combined["volume"].sum()) if "volume" in combined.columns else 0,
        "bar_count": len(combined),
        "source": "historical_cache",
    }


def official_daily_ohlc(trade_date: date, underlying: str) -> Optional[Dict[str, Any]]:
    key = underlying.upper()
    try:
        if key == "SENSEX":
            from backtesting.bse_eod_client import bse_eod_client
            row = bse_eod_client.get_front_month_eod(trade_date, key)
            source = "bse_bhavcopy"
        else:
            from backtesting.nse_eod_client import nse_eod_client
            row = nse_eod_client.get_front_month_eod(trade_date, key)
            source = "nse_bhavcopy"
        if row is None:
            return None
        return {
            "tradingsymbol": row.tradingsymbol,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "settle": row.settle or row.close,
            "volume": row.volume,
            "open_interest": row.open_interest,
            "source": source,
        }
    except Exception as exc:
        logger.debug("official_daily_ohlc failed: %s", exc)
        return None


def compare_eod_bars(cache: Dict[str, Any], official: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    deltas: Dict[str, float] = {}
    for field in ("open", "high", "low", "close"):
        c = float(cache.get(field, 0) or 0)
        o = float(official.get(field, 0) or official.get("settle", 0) or 0)
        deltas[field] = round(c - o, 2)
        if abs(deltas[field]) > PRICE_TOL:
            issues.append(f"{field}_mismatch_{deltas[field]:+.2f}")

    cv = float(cache.get("volume", 0) or 0)
    ov = float(official.get("volume", 0) or 0)
    if ov > 0:
        vol_delta_pct = abs(cv - ov) / ov
        deltas["volume_pct"] = round(vol_delta_pct * 100, 2)
        if vol_delta_pct > VOLUME_TOL_PCT:
            issues.append(f"volume_mismatch_{deltas['volume_pct']:.1f}pct")

    status = "match" if not issues else "mismatch"
    if int(cache.get("bar_count", 0) or 0) < 50:
        status = "partial"
        issues.append("thin_bar_count")

    return {"status": status, "deltas": deltas, "issues": issues}


def run_eod_audit(
    trade_date: Optional[date] = None,
    underlyings: Sequence[str] = ("NIFTY", "BANKNIFTY", "SENSEX"),
    *,
    save: bool = True,
) -> Dict[str, Any]:
    trade_date = trade_date or previous_trading_day()
    if trade_date.weekday() >= 5 or trade_date in MARKET_HOLIDAYS:
        return {
            "date": trade_date.isoformat(),
            "overall": "skipped",
            "reason": "holiday_or_weekend",
        }

    indices: Dict[str, Any] = {}
    mismatch_count = 0

    for underlying in underlyings:
        key = underlying.upper()
        cache = cache_daily_ohlc(trade_date, key)
        official = official_daily_ohlc(trade_date, key)

        if cache is None and official is None:
            indices[key] = {"status": "unavailable", "issues": ["no_cache_and_no_official"]}
            continue
        if cache is None:
            indices[key] = {"status": "missing_cache", "official": official}
            mismatch_count += 1
            continue
        if official is None:
            indices[key] = {"status": "missing_official", "cache": cache}
            mismatch_count += 1
            continue

        cmp = compare_eod_bars(cache, official)
        indices[key] = {
            "status": cmp["status"],
            "cache": cache,
            "official": official,
            "deltas": cmp["deltas"],
            "issues": cmp["issues"],
        }
        if cmp["status"] != "match":
            mismatch_count += 1

    overall = "healthy"
    if mismatch_count:
        overall = "mismatch" if any(
            indices.get(u, {}).get("status") == "mismatch" for u in underlyings
        ) else "partial"

    macro_context: Dict[str, Any] = {}
    try:
        brief_path = Path("data/briefs") / f"{trade_date.isoformat()}.json"
        if brief_path.exists():
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            macro_context = brief.get("macro_context") or {}
    except Exception:
        pass
    if not macro_context:
        try:
            from app.nse_data import fetch_macro_context
            macro_context = fetch_macro_context()
        except Exception:
            pass

    report = {
        "date": trade_date.isoformat(),
        "checked_at": now_ist().isoformat(),
        "overall": overall,
        "indices": indices,
        "macro_context": macro_context,
        "recommendation": (
            "Cache aligns with official NSE/BSE EOD bhavcopy."
            if overall == "healthy"
            else "Review mismatched indices; re-fetch promotion data or check rollover."
        ),
    }

    if save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"{trade_date.isoformat()}.json"
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    return report


def load_eod_audit_report(trade_date: str) -> Optional[Dict[str, Any]]:
    path = REPORT_DIR / f"{trade_date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None