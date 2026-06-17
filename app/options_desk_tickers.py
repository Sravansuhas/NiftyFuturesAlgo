"""
Live Options Desk — ATM CE/PE tickers for NIFTY, BANKNIFTY, SENSEX.

Batch ``kite.quote`` for spot + option keys; prefer WebSocket LTP when the
engine feed already has a fresh tick for the leg token.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .data_feed import STALE_PRICE_SECONDS
from .instruments_manager import INDEX_SPOT_LTP_KEYS, instruments_manager, ltp_key
from .market_calendar import now_ist
from .options_chain import options_chain_manager

logger = logging.getLogger(__name__)

_ticker_cache: Dict[str, Any] = {"payload": None, "ts": 0.0}
TICKER_CACHE_SEC = 20.0

SUPPORTED_INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

INDEX_LABELS = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "BANK NIFTY",
    "SENSEX": "SENSEX",
}


def _fetch_quotes_batch(kite, quote_keys: List[str]) -> Dict[str, Dict[str, Any]]:
    if not kite or not quote_keys:
        return {}
    unique = list(dict.fromkeys(quote_keys))
    try:
        from .kite_rate_limit import quote_limiter

        quote_limiter.wait()
        return kite.quote(unique) or {}
    except Exception as exc:
        logger.debug("options_desk_tickers quote batch failed: %s", exc)
        return {}


def _quote_change(row: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    if not row:
        return None, None
    net = row.get("net_change")
    last = float(row.get("last_price") or 0)
    close = float((row.get("ohlc") or {}).get("close") or 0)
    if net is not None:
        change = float(net)
    elif last > 0 and close > 0:
        change = last - close
    else:
        return None, None
    change_pct = (change / close) * 100.0 if close else None
    return change, change_pct


def _ws_ltp(ws_feed, token: Optional[int]) -> Tuple[Optional[float], Optional[float], bool]:
    if not ws_feed or not token:
        return None, None, False
    try:
        price, age = ws_feed.get_last_price_with_age(int(token))
    except Exception:
        return None, None, False
    if price is None or price <= 0 or age > STALE_PRICE_SECONDS:
        return None, age if age != float("inf") else None, False
    return float(price), age, True


def _build_leg(
    inst: Optional[Dict[str, Any]],
    quote_row: Dict[str, Any],
    *,
    option_type: str,
    expiry: Optional[date],
    ws_feed=None,
) -> Optional[Dict[str, Any]]:
    if not inst:
        return None

    token = inst.get("instrument_token")
    symbol = inst.get("tradingsymbol")
    strike = float(inst.get("strike") or 0)
    exchange = inst.get("exchange") or "NFO"

    ws_price, ws_age, ws_live = _ws_ltp(ws_feed, token)
    rest_ltp = float(quote_row.get("last_price") or 0) if quote_row else 0.0
    ltp = ws_price if ws_live else (rest_ltp if rest_ltp > 0 else None)
    data_source = "WS" if ws_live else ("REST" if ltp is not None else "none")

    change, change_pct = _quote_change(quote_row)
    oi = quote_row.get("oi")
    if oi is not None:
        try:
            oi = int(oi)
        except (TypeError, ValueError):
            oi = None

    return {
        "option_type": option_type,
        "strike": strike,
        "ltp": round(ltp, 2) if ltp is not None else None,
        "tradingsymbol": symbol,
        "symbol": symbol,
        "exchange": exchange,
        "change": round(change, 2) if change is not None else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "prev_close": float((quote_row.get("ohlc") or {}).get("close") or 0) or None,
        "oi": oi,
        "instrument_token": int(token) if token is not None else None,
        "expiry": str(expiry) if expiry else None,
        "data_source": data_source,
        "live": data_source == "WS",
        "data_age_seconds": round(ws_age, 2) if ws_age is not None else None,
    }


def _empty_index_row(index: str) -> Dict[str, Any]:
    return {
        "underlying": index,
        "label": INDEX_LABELS.get(index, index),
        "spot": None,
        "spot_change": None,
        "spot_change_pct": None,
        "atm_strike": None,
        "expiry": None,
        "live": False,
        "data_source": "none",
        "ce": None,
        "pe": None,
    }


def get_index_option_tickers(kite=None, ws_feed=None) -> Dict[str, Any]:
    """
    Return ATM CE/PE LTP for NIFTY, BANKNIFTY, and SENSEX.

    Spot comes from ``INDEX_SPOT_LTP_KEYS`` via a single batched ``kite.quote``.
    Option metadata (change, OI) uses the same batch; LTP prefers WS when fresh.
    """
    now = time.time()
    cached = _ticker_cache.get("payload")
    if cached and now - float(_ticker_cache.get("ts") or 0) < TICKER_CACHE_SEC:
        return cached

    payload: Dict[str, Any] = {
        "available": False,
        "timestamp": now_ist().isoformat(),
        "session_date": now_ist().date().isoformat(),
        "indices": {},
        "subscribed_tokens": 0,
        "error": None,
    }

    if not kite:
        payload["error"] = "kite_unavailable"
        for index in SUPPORTED_INDICES:
            payload["indices"][index] = _empty_index_row(index)
        return payload

    instruments_manager.bind(kite)
    options_chain_manager.bind(kite)

    spot_keys = [INDEX_SPOT_LTP_KEYS[idx] for idx in SUPPORTED_INDICES]
    spot_quotes = _fetch_quotes_batch(kite, spot_keys)

    plans: Dict[str, Dict[str, Any]] = {}
    option_keys: List[str] = []

    for index in SUPPORTED_INDICES:
        spot_key = INDEX_SPOT_LTP_KEYS[index]
        spot_row = spot_quotes.get(spot_key) or {}
        spot = float(spot_row.get("last_price") or 0)
        if spot <= 0:
            plans[index] = {"spot_row": spot_row}
            continue

        options_chain_manager.set_spot_price(index, spot)
        expiry = options_chain_manager.resolve_expiry(index)
        atm = options_chain_manager.get_atm_strike(index, spot_price=spot, expiry=expiry)

        ce_inst = (
            instruments_manager.get_option_instruments(index, "CE", expiry, atm)
            if expiry and atm is not None
            else None
        )
        pe_inst = (
            instruments_manager.get_option_instruments(index, "PE", expiry, atm)
            if expiry and atm is not None
            else None
        )

        ce_key = (
            ltp_key(ce_inst["tradingsymbol"], ce_inst.get("exchange") or "NFO")
            if ce_inst and ce_inst.get("tradingsymbol")
            else None
        )
        pe_key = (
            ltp_key(pe_inst["tradingsymbol"], pe_inst.get("exchange") or "NFO")
            if pe_inst and pe_inst.get("tradingsymbol")
            else None
        )

        if ce_key:
            option_keys.append(ce_key)
        if pe_key:
            option_keys.append(pe_key)

        plans[index] = {
            "spot": spot,
            "spot_row": spot_row,
            "expiry": expiry,
            "atm": atm,
            "ce_inst": ce_inst,
            "pe_inst": pe_inst,
            "ce_key": ce_key,
            "pe_key": pe_key,
        }

    option_quotes = _fetch_quotes_batch(kite, option_keys) if option_keys else {}

    token_count = 0
    indices: Dict[str, Dict[str, Any]] = {}

    for index in SUPPORTED_INDICES:
        plan = plans.get(index) or {}
        spot = plan.get("spot")
        if not spot or spot <= 0:
            indices[index] = _empty_index_row(index)
            continue

        expiry = plan.get("expiry")
        ce_leg = _build_leg(
            plan.get("ce_inst"),
            option_quotes.get(plan.get("ce_key") or "", {}),
            option_type="CE",
            expiry=expiry,
            ws_feed=ws_feed,
        )
        pe_leg = _build_leg(
            plan.get("pe_inst"),
            option_quotes.get(plan.get("pe_key") or "", {}),
            option_type="PE",
            expiry=expiry,
            ws_feed=ws_feed,
        )

        leg_sources = [
            (ce_leg or {}).get("data_source"),
            (pe_leg or {}).get("data_source"),
        ]
        data_source = "WS" if "WS" in leg_sources else ("REST" if "REST" in leg_sources else "none")

        spot_change, spot_change_pct = _quote_change(plan.get("spot_row") or {})

        for leg in (ce_leg, pe_leg):
            if leg and leg.get("instrument_token"):
                token_count += 1

        indices[index] = {
            "underlying": index,
            "label": INDEX_LABELS.get(index, index),
            "spot": round(float(spot), 2),
            "spot_change": round(spot_change, 2) if spot_change is not None else None,
            "spot_change_pct": round(spot_change_pct, 2) if spot_change_pct is not None else None,
            "atm_strike": plan.get("atm"),
            "expiry": str(expiry) if expiry else None,
            "live": data_source == "WS",
            "data_source": data_source,
            "ce": ce_leg,
            "pe": pe_leg,
        }

    payload["indices"] = indices
    payload["subscribed_tokens"] = token_count
    payload["available"] = any(
        (row.get("ce") or {}).get("ltp") or (row.get("pe") or {}).get("ltp")
        for row in indices.values()
    )
    _ticker_cache["payload"] = payload
    _ticker_cache["ts"] = now
    return payload