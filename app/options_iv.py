"""
Live implied volatility from option chain quotes.

Fetches ATM / near-ATM CE+PE via ``kite.quote``, solves IV with
``greeks.implied_volatility``, and returns a blended annualized IV for
iron condor proposal pricing.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union

from .greeks import DEFAULT_RISK_FREE_RATE, dividend_yield_for, implied_volatility
from .instruments_manager import instruments_manager, ltp_key

logger = logging.getLogger(__name__)


def _clamp_iv(iv: float, config: Dict[str, Any]) -> float:
    floor = float(config.get("iv_floor", 0.12))
    cap = float(config.get("iv_cap", 0.35))
    return max(floor, min(cap, iv))


def _vix_proxy_iv(
    config: Dict[str, Any],
    market_context: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """India VIX / 100 with configured default when VIX unavailable."""
    default_iv = float(config.get("default_iv", 0.16))
    vix_level = None
    if market_context:
        vix = market_context.get("india_vix") or market_context.get("vix") or {}
        if isinstance(vix, dict) and vix.get("available"):
            vix_level = float(vix.get("level") or 0)

    if vix_level and vix_level > 0:
        return vix_level / 100.0, "vix_proxy"
    return default_iv, "default_iv"


def _fetch_quotes(kite, quote_keys: List[str]) -> Dict[str, Dict[str, Any]]:
    if not kite or not quote_keys:
        return {}
    try:
        from .kite_rate_limit import quote_limiter

        quote_limiter.wait()
        return kite.quote(list(quote_keys)) or {}
    except Exception as exc:
        logger.debug("options_iv quote fetch failed: %s", exc)
        return {}


def _iv_from_premium(
    *,
    premium: float,
    spot: float,
    strike: float,
    option_type: str,
    time_to_expiry_years: float,
    underlying: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Optional[float]:
    if premium <= 0 or spot <= 0 or strike <= 0 or time_to_expiry_years <= 0:
        return None
    try:
        q = dividend_yield_for(underlying)
        return implied_volatility(
            premium,
            spot,
            strike,
            time_to_expiry_years,
            risk_free_rate,
            option_type,
            dividend_yield=q,
        )
    except (ValueError, ZeroDivisionError) as exc:
        logger.debug(
            "IV solve failed for %s K%s premium=%.2f: %s",
            option_type,
            strike,
            premium,
            exc,
        )
        return None


def fetch_atm_near_atm_iv(
    kite,
    *,
    underlying: str,
    expiry: Union[date, str],
    spot: float,
    time_to_expiry_years: float,
    near_atm_steps: int = 1,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """
    Quote ATM and near-ATM CE/PE and return weighted blended IV.

    ATM samples are weighted 2× vs adjacent strikes.
    """
    from .options_chain import options_chain_manager

    underlying_upper = underlying.upper()
    meta: Dict[str, Any] = {
        "source": "chain_atm",
        "samples": [],
        "quote_keys": [],
    }

    if not kite or spot <= 0 or time_to_expiry_years <= 0:
        return None, meta

    options_chain_manager.bind(kite)
    options_chain_manager.set_spot_price(underlying_upper, spot)

    atm = options_chain_manager.get_atm_strike(
        underlying_upper,
        spot_price=spot,
        expiry=expiry,
    )
    if atm is None:
        return None, meta

    strikes = options_chain_manager.get_strikes_near_atm(
        underlying_upper,
        n=near_atm_steps,
        spot_price=spot,
        expiry=expiry,
    )
    if not strikes:
        strikes = [atm]

    weights: Dict[float, float] = {float(atm): 2.0}
    for strike in strikes:
        weights.setdefault(float(strike), 1.0)

    instruments: List[Dict[str, Any]] = []
    for strike in sorted(weights):
        for option_type in ("CE", "PE"):
            inst = instruments_manager.get_option_instruments(
                underlying_upper,
                option_type,
                expiry,
                strike,
            )
            if inst and inst.get("tradingsymbol"):
                instruments.append(inst)

    if not instruments:
        return None, meta

    quote_keys = [
        ltp_key(inst["tradingsymbol"], inst.get("exchange") or "NFO")
        for inst in instruments
    ]
    meta["quote_keys"] = quote_keys
    meta["atm_strike"] = float(atm)

    quotes = _fetch_quotes(kite, quote_keys)
    weighted_sum = 0.0
    weight_total = 0.0

    for inst in instruments:
        key = ltp_key(inst["tradingsymbol"], inst.get("exchange") or "NFO")
        row = quotes.get(key) or {}
        premium = float(row.get("last_price") or 0)
        strike = float(inst.get("strike") or 0)
        option_type = str(inst.get("option_type") or "CE").upper()
        weight = weights.get(strike, 1.0)

        sample: Dict[str, Any] = {
            "strike": strike,
            "option_type": option_type,
            "tradingsymbol": inst.get("tradingsymbol"),
            "premium": premium,
            "weight": weight,
        }

        iv = _iv_from_premium(
            premium=premium,
            spot=spot,
            strike=strike,
            option_type=option_type,
            time_to_expiry_years=time_to_expiry_years,
            underlying=underlying_upper,
            risk_free_rate=risk_free_rate,
        )
        if iv is not None and iv > 0:
            sample["iv"] = round(iv, 6)
            weighted_sum += iv * weight
            weight_total += weight

        meta["samples"].append(sample)

    if weight_total <= 0:
        return None, meta

    blended = weighted_sum / weight_total
    meta["blended_iv_raw"] = round(blended, 6)
    return blended, meta


def resolve_blended_iv(
    kite,
    *,
    underlying: str,
    expiry: Union[date, str],
    spot: float,
    time_to_expiry_years: float,
    config: Dict[str, Any],
    market_context: Optional[Dict[str, Any]] = None,
    near_atm_steps: int = 1,
) -> Tuple[float, Dict[str, Any]]:
    """
    Resolve IV for proposal pricing: live chain blend when available, else VIX proxy.
    """
    live_iv, live_meta = fetch_atm_near_atm_iv(
        kite,
        underlying=underlying,
        expiry=expiry,
        spot=spot,
        time_to_expiry_years=time_to_expiry_years,
        near_atm_steps=near_atm_steps,
    )

    if live_iv is not None and live_iv > 0:
        clamped = _clamp_iv(live_iv, config)
        live_meta["source"] = "chain_atm"
        live_meta["iv_clamped"] = clamped
        return clamped, live_meta

    fallback_iv, source = _vix_proxy_iv(config, market_context)
    clamped = _clamp_iv(fallback_iv, config)
    return clamped, {
        "source": source,
        "fallback_iv_raw": round(fallback_iv, 6),
        "iv_clamped": clamped,
        "chain_meta": live_meta,
    }


def _recompute_economics(proposal: Dict[str, Any]) -> None:
    """Refresh net credit / max loss / breakevens from leg premiums."""
    legs = proposal.get("legs") or []
    if not legs:
        return

    lot_size = int(proposal.get("lot_size") or legs[0].get("quantity") or 1)
    buy_premium = sum(
        leg["premium"] * leg["quantity"]
        for leg in legs
        if leg.get("transaction_type", "").upper() == "BUY"
    )
    sell_premium = sum(
        leg["premium"] * leg["quantity"]
        for leg in legs
        if leg.get("transaction_type", "").upper() == "SELL"
    )
    net_credit = sell_premium - buy_premium
    net_credit_per_unit = net_credit / lot_size if lot_size else 0.0

    put_long = put_short = call_short = call_long = 0.0
    for leg in legs:
        opt = str(leg.get("option_type", "")).upper()
        side = str(leg.get("transaction_type", "")).upper()
        strike = float(leg.get("strike") or 0)
        if opt == "PE" and side == "BUY":
            put_long = strike
        elif opt == "PE" and side == "SELL":
            put_short = strike
        elif opt == "CE" and side == "SELL":
            call_short = strike
        elif opt == "CE" and side == "BUY":
            call_long = strike

    put_wing = put_short - put_long
    call_wing = call_long - call_short
    max_loss_put_side = (put_wing - net_credit_per_unit) * lot_size
    max_loss_call_side = (call_wing - net_credit_per_unit) * lot_size
    max_loss = max(max_loss_put_side, max_loss_call_side)
    lower_be = put_short - net_credit_per_unit
    upper_be = call_short + net_credit_per_unit

    underlying = proposal.get("underlying", "NIFTY")
    spot = float(proposal.get("spot") or 0)
    iv = float(proposal.get("iv") or 0.16)
    tte = float(proposal.get("time_to_expiry_years") or 7 / 365.0)

    from .strategies.iron_condor import _aggregate_greeks

    proposal["economics"] = {
        "net_credit": round(net_credit, 2),
        "net_credit_per_unit": round(net_credit_per_unit, 2),
        "max_profit": round(net_credit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": [round(lower_be, 2), round(upper_be, 2)],
        "put_wing_width": put_wing,
        "call_wing_width": call_wing,
    }
    proposal["greeks"] = _aggregate_greeks(
        spot=spot,
        iv=iv,
        legs=legs,
        time_to_expiry=tte,
        risk_free_rate=DEFAULT_RISK_FREE_RATE,
        dividend_yield=dividend_yield_for(underlying),
    )
    econ = proposal["economics"]
    proposal["message"] = (
        f"Iron condor proposal on {underlying} @ {spot:.0f} — "
        f"credit ₹{econ['net_credit']:,.0f}, BE [{lower_be:.0f}, {upper_be:.0f}]"
    )


def enrich_proposal_with_live_quotes(
    kite,
    proposal: Dict[str, Any],
    *,
    instruments_mgr=None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Resolve tradingsymbols, attach live LTP premiums, and refresh economics.

    Intended for the execution path before margin validation.
    """
    from copy import deepcopy

    mgr = instruments_mgr or instruments_manager
    out = deepcopy(proposal)
    legs_in = out.get("legs") or []
    underlying = out.get("underlying", "NIFTY").upper()
    expiry = out.get("expiry")
    if not expiry:
        return out, "Missing expiry on proposal"

    resolved_legs: List[Dict[str, Any]] = []
    quote_keys: List[str] = []

    for leg in legs_in:
        row = deepcopy(leg)
        if not row.get("tradingsymbol"):
            inst = mgr.get_option_instruments(
                underlying,
                row.get("option_type"),
                expiry,
                float(row.get("strike")),
            )
            if not inst or not inst.get("tradingsymbol"):
                return out, (
                    f"No instrument for {underlying} {row.get('option_type')} "
                    f"K{row.get('strike')} exp {expiry}"
                )
            row["tradingsymbol"] = inst["tradingsymbol"]
            row["exchange"] = inst.get("exchange") or (
                "BFO" if underlying == "SENSEX" else "NFO"
            )
            row["instrument_token"] = inst.get("instrument_token")
            row["lot_size"] = int(inst.get("lot_size") or row.get("quantity") or 1)
            if not row.get("quantity"):
                row["quantity"] = row["lot_size"]

        key = ltp_key(row["tradingsymbol"], row.get("exchange") or "NFO")
        quote_keys.append(key)
        resolved_legs.append(row)

    quotes = _fetch_quotes(kite, quote_keys)
    live_count = 0
    for row in resolved_legs:
        key = ltp_key(row["tradingsymbol"], row.get("exchange") or "NFO")
        quote_row = quotes.get(key) or {}
        ltp = float(quote_row.get("last_price") or 0)
        if ltp > 0:
            row["premium"] = round(ltp, 2)
            row["premium_source"] = "live_quote"
            live_count += 1
        else:
            row["premium_source"] = row.get("premium_source") or "model"

    out["legs"] = resolved_legs
    out["live_quote_legs"] = live_count
    if live_count:
        _recompute_economics(out)

    return out, None