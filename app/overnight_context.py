"""
Overnight / pre-open context — GIFT Nifty gap regime for Indian F&O.

Fetched ~08:55–09:00 IST via Kite quote. De-risk only — never increases size.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .market_calendar import IST, now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERNIGHT_FILE = PROJECT_ROOT / "data" / "overnight_context.json"

GAP_LARGE_PCT = 0.50
GAP_EXTREME_PCT = 1.00


def _gap_regime(gap_pct: float) -> str:
    if gap_pct >= GAP_EXTREME_PCT:
        return "extreme_up"
    if gap_pct >= GAP_LARGE_PCT:
        return "large_up"
    if gap_pct <= -GAP_EXTREME_PCT:
        return "extreme_down"
    if gap_pct <= -GAP_LARGE_PCT:
        return "large_down"
    if gap_pct > 0.15:
        return "moderate_up"
    if gap_pct < -0.15:
        return "moderate_down"
    return "flat"


def _session_hints(gap_pct: float, gap_regime: str) -> Dict[str, Any]:
    hints: Dict[str, Any] = {
        "open_risk": "normal",
        "breakout_buffer_mult": 1.0,
        "posture_floor": "normal",
        "max_trades_delta": 0,
        "reasons": [],
    }
    if gap_regime in ("extreme_up", "extreme_down"):
        hints.update({
            "open_risk": "extreme",
            "breakout_buffer_mult": 1.35,
            "posture_floor": "contingency",
            "max_trades_delta": -2,
            "reasons": [f"gift_gap_{gap_pct:+.2f}pct_extreme"],
        })
    elif gap_regime in ("large_up", "large_down"):
        hints.update({
            "open_risk": "elevated",
            "breakout_buffer_mult": 1.25,
            "posture_floor": "defensive",
            "max_trades_delta": -1,
            "reasons": [f"gift_gap_{gap_pct:+.2f}pct_large"],
        })
    elif gap_regime in ("moderate_up", "moderate_down"):
        hints.update({
            "open_risk": "moderate",
            "breakout_buffer_mult": 1.10,
            "posture_floor": "defensive",
            "reasons": [f"gift_gap_{gap_pct:+.2f}pct"],
        })
    else:
        hints["reasons"].append("gift_gap_flat")
    return hints


def fetch_overnight_from_kite(kite) -> Dict[str, Any]:
    """Pull GIFT Nifty + NIFTY 50 spot for implied gap."""
    gift_key = "NSEIX:GIFT NIFTY"
    nifty_key = "NSE:NIFTY 50"
    quotes = kite.quote([gift_key, nifty_key])
    gift = quotes.get(gift_key) or {}
    nifty = quotes.get(nifty_key) or {}

    gift_last = float(gift.get("last_price") or 0)
    nse_prev = float(nifty.get("ohlc", {}).get("close") or nifty.get("last_price") or 0)
    if gift_last <= 0 or nse_prev <= 0:
        return {"available": False, "error": "invalid_quote"}

    gap_pts = gift_last - nse_prev
    gap_pct = gap_pts / nse_prev * 100
    regime = _gap_regime(gap_pct)
    now = now_ist()

    payload: Dict[str, Any] = {
        "date_ist": now.strftime("%Y-%m-%d"),
        "fetched_at": now.isoformat(),
        "sources": ["kite_quote"],
        "available": True,
        "NIFTY": {
            "nse_prev_close": round(nse_prev, 2),
            "gift_last": round(gift_last, 2),
            "implied_gap_pts": round(gap_pts, 2),
            "implied_gap_pct": round(gap_pct, 3),
            "gap_regime": regime,
        },
        "session_hints": _session_hints(gap_pct, regime),
    }
    return payload


def build_overnight_context(kite=None) -> Dict[str, Any]:
    """Build or return cached overnight context for today."""
    today = now_ist().strftime("%Y-%m-%d")
    if OVERNIGHT_FILE.exists():
        try:
            cached = json.loads(OVERNIGHT_FILE.read_text(encoding="utf-8"))
            if cached.get("date_ist") == today and cached.get("available"):
                return cached
        except Exception:
            pass

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
            payload = fetch_overnight_from_kite(kite)
            if payload.get("available"):
                save_overnight_context(payload)
                return payload
        except Exception as exc:
            logger.warning("Overnight context fetch failed: %s", exc)

    return {
        "date_ist": today,
        "fetched_at": now_ist().isoformat(),
        "available": False,
        "session_hints": _session_hints(0.0, "flat"),
    }


def save_overnight_context(payload: Dict[str, Any], path: Path = OVERNIGHT_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_overnight_context(path: Path = OVERNIGHT_FILE) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None