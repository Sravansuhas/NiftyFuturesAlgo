"""
Indian market context — India VIX, FII/DII flows, open-bias session hints.

Unified pre-open block for morning brief and regime orchestrator.
All fetchers fail gracefully — trading never depends on NSE HTTP or Kite quotes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .market_calendar import now_ist
from .nse_data import _vix_zone, fetch_fii_dii_flow

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKET_CONTEXT_FILE = PROJECT_ROOT / "data" / "market_context.json"

_KITE_VIX_KEYS = ("NSE:INDIA VIX", "NSE:India VIX")


def fetch_india_vix(kite=None, *, timeout: float = 12.0) -> Dict[str, Any]:
    """
    India VIX level — NSE allIndices first, Kite quote fallback if available.
    """
    sources: List[str] = []

    try:
        from .nse_data import fetch_india_vix as _nse_vix

        payload = _nse_vix(timeout=timeout)
        if payload.get("available"):
            payload["source"] = "nse_all_indices"
            return payload
    except Exception as exc:
        logger.debug("NSE India VIX fetch failed: %s", exc)

    if kite is None:
        try:
            from config import KITE_API_KEY, KITE_ACCESS_TOKEN
            from kiteconnect import KiteConnect

            if KITE_API_KEY and KITE_ACCESS_TOKEN:
                kite = KiteConnect(api_key=KITE_API_KEY)
                kite.set_access_token(KITE_ACCESS_TOKEN)
        except Exception:
            kite = None

    if kite is not None:
        try:
            quotes = kite.quote(list(_KITE_VIX_KEYS))
            for key in _KITE_VIX_KEYS:
                row = quotes.get(key) or {}
                last = float(row.get("last_price") or 0)
                if last <= 0:
                    continue
                ohlc = row.get("ohlc") or {}
                prev = float(ohlc.get("close") or last)
                chg_pct = ((last - prev) / prev * 100) if prev else 0.0
                sources.append("kite_quote")
                return {
                    "available": True,
                    "level": round(last, 2),
                    "previous_close": round(prev, 2),
                    "change_pct": round(chg_pct, 2),
                    "zone": _vix_zone(last),
                    "source": "kite_quote",
                    "fetched_at": now_ist().isoformat(),
                }
        except Exception as exc:
            logger.debug("Kite India VIX fetch failed: %s", exc)

    return {"available": False, "fetched_at": now_ist().isoformat()}


def fetch_fii_dii_flows(*, timeout: float = 12.0) -> Dict[str, Any]:
    """FII/FPI vs DII provisional cash-market flows (₹ Cr) — latest trading day."""
    payload = fetch_fii_dii_flow(timeout=timeout)
    if payload.get("available"):
        payload["source"] = "nse_fiidii_trade"
    return payload


def _open_bias(flow_bias: str, vix_zone: str) -> str:
    if flow_bias in ("risk_off", "fii_selling"):
        return "bearish_open"
    if flow_bias in ("risk_on", "dii_support"):
        return "bullish_open"
    if vix_zone in ("extreme", "elevated"):
        return "volatile_caution"
    return "neutral"


def _session_hints(
    vix: Dict[str, Any],
    fii_dii: Dict[str, Any],
) -> Dict[str, Any]:
    """De-risk only — never increases size or aggression."""
    hints: Dict[str, Any] = {
        "open_bias": "neutral",
        "open_risk": "normal",
        "breakout_buffer_mult": 1.0,
        "posture_floor": "normal",
        "max_trades_delta": 0,
        "reasons": [],
    }

    vix_zone = str(vix.get("zone") or "normal")
    flow_bias = str(fii_dii.get("flow_bias") or "neutral")
    hints["open_bias"] = _open_bias(flow_bias, vix_zone)

    if vix_zone == "extreme":
        hints.update({
            "open_risk": "extreme",
            "breakout_buffer_mult": 1.30,
            "posture_floor": "contingency",
            "max_trades_delta": -2,
            "reasons": [f"vix_{vix.get('level')}_extreme"],
        })
    elif vix_zone == "elevated":
        hints.update({
            "open_risk": "elevated",
            "breakout_buffer_mult": 1.20,
            "posture_floor": "defensive",
            "max_trades_delta": -1,
            "reasons": [f"vix_{vix.get('level')}_elevated"],
        })
    elif vix_zone == "low":
        hints["reasons"].append(f"vix_{vix.get('level')}_low")

    if flow_bias == "risk_off":
        hints["open_risk"] = "elevated" if hints["open_risk"] == "normal" else hints["open_risk"]
        hints["posture_floor"] = "defensive" if hints["posture_floor"] == "normal" else hints["posture_floor"]
        hints["max_trades_delta"] = min(int(hints["max_trades_delta"]), -1)
        hints["reasons"].append("fii_dii_risk_off")
    elif flow_bias == "fii_selling":
        hints["open_risk"] = "elevated" if hints["open_risk"] == "normal" else hints["open_risk"]
        hints["reasons"].append("fii_net_selling")
    elif flow_bias in ("risk_on", "dii_support"):
        hints["reasons"].append(f"flows_{flow_bias}")

    if not hints["reasons"]:
        hints["reasons"].append("macro_neutral")

    return hints


def build_market_context(kite=None, *, force_refresh: bool = False) -> Dict[str, Any]:
    """Build or return cached Indian market context for today."""
    today = now_ist().strftime("%Y-%m-%d")
    if not force_refresh and MARKET_CONTEXT_FILE.exists():
        try:
            cached = json.loads(MARKET_CONTEXT_FILE.read_text(encoding="utf-8"))
            if cached.get("date_ist") == today and cached.get("available"):
                return cached
        except Exception:
            pass

    now = now_ist()
    vix = fetch_india_vix(kite=kite)
    fii_dii = fetch_fii_dii_flows()
    sources: List[str] = []
    if vix.get("available"):
        sources.append(str(vix.get("source") or "nse_all_indices"))
    if fii_dii.get("available"):
        sources.append(str(fii_dii.get("source") or "nse_fiidii_trade"))

    available = bool(vix.get("available") or fii_dii.get("available"))
    payload: Dict[str, Any] = {
        "date_ist": today,
        "fetched_at": now.isoformat(),
        "sources": sources,
        "available": available,
        "india_vix": vix,
        "fii_dii": fii_dii,
        "session_hints": _session_hints(vix, fii_dii),
    }

    if available:
        save_market_context(payload)
    return payload


def save_market_context(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = path or MARKET_CONTEXT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_market_context(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = path or MARKET_CONTEXT_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None