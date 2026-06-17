"""
NSE public macro data — holidays, India VIX, FII/DII flows.

Uses official NSE JSON endpoints (session cookie priming required).
All fetchers fail gracefully — trading never depends on NSE HTTP.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .market_calendar import IST, now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOLIDAYS_FILE = PROJECT_ROOT / "data" / "market_holidays.json"
NSE_HOME = "https://www.nseindia.com"

# Ad-hoc closures not always in annual circular early releases
_EXTRA_HOLIDAYS: Dict[str, str] = {
    "2026-01-15": "Maharashtra Municipal Corporation Elections",
}


def _nse_session(timeout: float = 12.0) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{NSE_HOME}/",
    })
    session.get(NSE_HOME, timeout=timeout)
    return session


def _parse_nse_date(raw: str) -> Optional[date]:
    text = (raw or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _vix_zone(level: float) -> str:
    if level >= 20:
        return "extreme"
    if level >= 15:
        return "elevated"
    if level >= 12:
        return "normal"
    return "low"


def _flow_bias(fii_net: float, dii_net: float) -> str:
    if fii_net < -1500 and dii_net < 0:
        return "risk_off"
    if fii_net < -800:
        return "fii_selling"
    if fii_net > 800 and dii_net >= 0:
        return "risk_on"
    if dii_net > 1500 and fii_net <= 0:
        return "dii_support"
    return "neutral"


def sync_holidays_from_nse(
    *,
    segments: Tuple[str, ...] = ("FO", "CM"),
    save: bool = True,
) -> Dict[str, Any]:
    """
    Fetch NSE holiday-master and persist to data/market_holidays.json.
    Returns sync metadata + holiday list.
    """
    holidays: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    try:
        session = _nse_session()
        url = f"{NSE_HOME}/api/holiday-master?type=trading"
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            errors.append(f"holiday-master: HTTP {resp.status_code}")
        else:
            payload = resp.json()
            segment_rows: List[Tuple[str, List[Any]]] = []
            if isinstance(payload, list):
                segment_rows.append(("ALL", payload))
            elif isinstance(payload, dict):
                for seg in segments:
                    rows = payload.get(seg) or payload.get(seg.lower()) or []
                    if rows:
                        segment_rows.append((seg, rows))
                if not segment_rows:
                    for key, rows in payload.items():
                        if isinstance(rows, list):
                            segment_rows.append((str(key), rows))
            for seg, rows in segment_rows:
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    d = _parse_nse_date(str(row.get("tradingDate") or row.get("date") or ""))
                    if d is None:
                        continue
                    key = d.isoformat()
                    holidays[key] = {
                        "date": key,
                        "description": row.get("description") or row.get("holiday") or "Holiday",
                        "segment": seg,
                    }
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("NSE holiday sync failed: %s", exc)

    for key, desc in _EXTRA_HOLIDAYS.items():
        holidays[key] = {"date": key, "description": desc, "segment": "ADHOC"}

    result: Dict[str, Any] = {
        "synced_at": now_ist().isoformat(),
        "source": "nse_holiday_master",
        "holiday_count": len(holidays),
        "holidays": sorted(holidays.values(), key=lambda h: h["date"]),
        "errors": errors,
    }

    if save:
        HOLIDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with HOLIDAYS_FILE.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        logger.info("Holiday sync saved %d dates to %s", len(holidays), HOLIDAYS_FILE)

    return result


def load_synced_holidays(path: Path = HOLIDAYS_FILE) -> Dict[str, Any]:
    if not path.exists():
        return {"holidays": [], "holiday_count": 0}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.debug("Holiday file read failed: %s", exc)
        return {"holidays": [], "holiday_count": 0}


def holiday_dates_from_file(path: Path = HOLIDAYS_FILE) -> List[date]:
    payload = load_synced_holidays(path)
    dates: List[date] = []
    for row in payload.get("holidays") or []:
        try:
            dates.append(date.fromisoformat(str(row["date"])))
        except Exception:
            continue
    return dates


def fetch_india_vix(timeout: float = 12.0) -> Dict[str, Any]:
    """India VIX from NSE allIndices snapshot."""
    try:
        session = _nse_session(timeout)
        resp = session.get(f"{NSE_HOME}/api/allIndices", timeout=timeout)
        resp.raise_for_status()
        for row in resp.json().get("data") or []:
            name = str(row.get("index") or row.get("indexSymbol") or "").upper()
            if "VIX" not in name:
                continue
            last = float(row.get("last") or row.get("lastPrice") or 0)
            prev = float(row.get("previousClose") or row.get("previous_close") or last)
            chg_pct = ((last - prev) / prev * 100) if prev else 0.0
            return {
                "available": True,
                "level": round(last, 2),
                "previous_close": round(prev, 2),
                "change_pct": round(chg_pct, 2),
                "zone": _vix_zone(last),
                "fetched_at": now_ist().isoformat(),
            }
    except Exception as exc:
        logger.debug("India VIX fetch failed: %s", exc)
    return {"available": False, "fetched_at": now_ist().isoformat()}


def _parse_fii_dii_rows(rows: Any) -> Optional[Dict[str, Any]]:
    """
    Parse NSE fiidiiTradeReact payload.

    Supports:
    - New array: [{"category":"DII","netValue":"5341.29","date":"12-Jun-2026"}, ...]
    - Legacy row/list: {"fiiNetValue": ..., "diiNetValue": ..., "date": ...}
    """
    if not rows:
        return None

    if isinstance(rows, dict):
        if any(rows.get(k) is not None for k in ("fiiNetValue", "diiNetValue", "fii_net", "dii_net")):
            fii = float(rows.get("fiiNetValue") or rows.get("fii_net") or 0)
            dii = float(rows.get("diiNetValue") or rows.get("dii_net") or 0)
            return {
                "fii": fii,
                "dii": dii,
                "trade_date": str(rows.get("date") or rows.get("tradeDate") or ""),
            }
        rows = rows.get("data") or [rows]

    if not isinstance(rows, list) or not rows:
        return None

    first = rows[0] if rows else {}
    if isinstance(first, dict) and any(
        first.get(k) is not None for k in ("fiiNetValue", "diiNetValue", "fii_net", "dii_net")
    ):
        fii = float(first.get("fiiNetValue") or first.get("fii_net") or 0)
        dii = float(first.get("diiNetValue") or first.get("dii_net") or 0)
        return {
            "fii": fii,
            "dii": dii,
            "trade_date": str(first.get("date") or first.get("tradeDate") or ""),
        }

    by_date: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "").upper().strip()
        if not category:
            continue
        trade_date = str(row.get("date") or row.get("tradeDate") or "")
        try:
            net = float(row.get("netValue") or row.get("net_value") or 0)
        except (TypeError, ValueError):
            net = 0.0
        bucket = by_date.setdefault(
            trade_date,
            {"fii": 0.0, "dii": 0.0, "trade_date": trade_date},
        )
        if "DII" in category:
            bucket["dii"] = net
        elif "FII" in category or "FPI" in category:
            bucket["fii"] = net

    if not by_date:
        return None

    dated_rows = [
        (_parse_nse_date(trade_date) or date.min, trade_date, bucket)
        for trade_date, bucket in by_date.items()
    ]
    dated_rows.sort(key=lambda item: item[0], reverse=True)
    _, _, latest = dated_rows[0]
    return {
        "fii": float(latest.get("fii") or 0),
        "dii": float(latest.get("dii") or 0),
        "trade_date": str(latest.get("trade_date") or ""),
    }


def fetch_fii_dii_flow(timeout: float = 12.0) -> Dict[str, Any]:
    """FII/FPI vs DII net cash market flows (₹ Cr) — latest trading day."""
    try:
        session = _nse_session(timeout)
        resp = session.get(f"{NSE_HOME}/api/fiidiiTradeReact", timeout=timeout)
        resp.raise_for_status()
        parsed = _parse_fii_dii_rows(resp.json())
        if not parsed:
            return {"available": False}
        fii = float(parsed["fii"])
        dii = float(parsed["dii"])
        return {
            "available": True,
            "trade_date": parsed["trade_date"],
            "fii_net_crores": round(fii, 2),
            "dii_net_crores": round(dii, 2),
            "flow_bias": _flow_bias(fii, dii),
            "fetched_at": now_ist().isoformat(),
        }
    except Exception as exc:
        logger.debug("FII/DII fetch failed: %s", exc)
    return {"available": False, "fetched_at": now_ist().isoformat()}


def fetch_macro_context(*, include_vix: bool = True, include_fii: bool = True) -> Dict[str, Any]:
    """Combined macro block for morning brief and journal."""
    ctx: Dict[str, Any] = {"fetched_at": now_ist().isoformat()}
    if include_vix:
        ctx["vix"] = fetch_india_vix()
    if include_fii:
        ctx["fii_dii"] = fetch_fii_dii_flow()
    return ctx