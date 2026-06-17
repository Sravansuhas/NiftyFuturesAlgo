"""
Iron condor proposal — defined-risk short-vol structure.

Builds a 4-leg iron condor from spot + IV + strikes. When ``research_only=False``,
the proposal may be routed through OptionsExecutionEngine (config + env gated).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Union

from ..greeks import DEFAULT_RISK_FREE_RATE, black_scholes_price, compute_greeks, dividend_yield_for
from ..instruments_manager import _FALLBACK_LOT_SIZES
from ..options_pnl import get_index_lot_size

RESEARCH_ONLY = True


def _price_leg(
    spot: float,
    strike: float,
    iv: float,
    option_type: str,
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> float:
    return black_scholes_price(
        spot=spot,
        strike=strike,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        volatility=iv,
        option_type=option_type,
        dividend_yield=dividend_yield,
    )


def _build_leg(
    *,
    underlying: str,
    option_type: str,
    strike: float,
    transaction_type: str,
    quantity: int,
    premium: float,
    expiry: Optional[Union[date, str]],
) -> Dict[str, Any]:
    expiry_str = str(expiry)[:10] if expiry is not None else None
    return {
        "underlying": underlying.upper(),
        "option_type": option_type.upper(),
        "strike": float(strike),
        "transaction_type": transaction_type.upper(),
        "quantity": int(quantity),
        "premium": round(float(premium), 2),
        "expiry": expiry_str,
        "tradingsymbol": "",  # resolved later via OptionsChainManager
    }


def propose_iron_condor(
    spot: float,
    iv: float,
    strikes: Dict[str, float],
    *,
    underlying: str = "NIFTY",
    expiry: Optional[Union[date, str]] = None,
    time_to_expiry_years: float = 7 / 365.0,
    quantity: Optional[int] = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: Optional[float] = None,
    research_only: bool = True,
) -> Dict[str, Any]:
    """
    Propose a short iron condor (sell body, buy wings).

    Parameters
    ----------
    spot : float
        Current underlying spot/index level.
    iv : float
        Annualized implied volatility (e.g. 0.15 for 15%).
    strikes : dict
        Required keys: put_long, put_short, call_short, call_long
        (put_long < put_short < call_short < call_long).

    Returns
    -------
    dict
        Signal payload with legs, max_profit, max_loss, breakevens.
        Always tagged research_only — no order placement.
    """
    required = ("put_long", "put_short", "call_short", "call_long")
    missing = [k for k in required if k not in strikes]
    if missing:
        raise ValueError(f"strikes missing required keys: {missing}")

    put_long = float(strikes["put_long"])
    put_short = float(strikes["put_short"])
    call_short = float(strikes["call_short"])
    call_long = float(strikes["call_long"])

    if not (put_long < put_short < call_short < call_long):
        raise ValueError(
            "Strikes must satisfy put_long < put_short < call_short < call_long"
        )

    underlying_upper = underlying.upper()
    q = dividend_yield_for(underlying_upper, dividend_yield)
    lot_size = quantity or get_index_lot_size(underlying_upper)

    premiums = {
        "put_long": _price_leg(spot, put_long, iv, "PE", time_to_expiry_years, risk_free_rate, q),
        "put_short": _price_leg(spot, put_short, iv, "PE", time_to_expiry_years, risk_free_rate, q),
        "call_short": _price_leg(spot, call_short, iv, "CE", time_to_expiry_years, risk_free_rate, q),
        "call_long": _price_leg(spot, call_long, iv, "CE", time_to_expiry_years, risk_free_rate, q),
    }

    legs: List[Dict[str, Any]] = [
        _build_leg(
            underlying=underlying_upper,
            option_type="PE",
            strike=put_long,
            transaction_type="BUY",
            quantity=lot_size,
            premium=premiums["put_long"],
            expiry=expiry,
        ),
        _build_leg(
            underlying=underlying_upper,
            option_type="PE",
            strike=put_short,
            transaction_type="SELL",
            quantity=lot_size,
            premium=premiums["put_short"],
            expiry=expiry,
        ),
        _build_leg(
            underlying=underlying_upper,
            option_type="CE",
            strike=call_short,
            transaction_type="SELL",
            quantity=lot_size,
            premium=premiums["call_short"],
            expiry=expiry,
        ),
        _build_leg(
            underlying=underlying_upper,
            option_type="CE",
            strike=call_long,
            transaction_type="BUY",
            quantity=lot_size,
            premium=premiums["call_long"],
            expiry=expiry,
        ),
    ]

    buy_premium = sum(leg["premium"] * leg["quantity"] for leg in legs if leg["transaction_type"] == "BUY")
    sell_premium = sum(leg["premium"] * leg["quantity"] for leg in legs if leg["transaction_type"] == "SELL")
    net_credit_per_unit = (sell_premium - buy_premium) / lot_size
    net_credit = sell_premium - buy_premium

    put_wing = put_short - put_long
    call_wing = call_long - call_short
    max_loss_put_side = (put_wing - net_credit_per_unit) * lot_size
    max_loss_call_side = (call_wing - net_credit_per_unit) * lot_size
    max_loss = max(max_loss_put_side, max_loss_call_side)
    max_profit = net_credit

    lower_breakeven = put_short - net_credit_per_unit
    upper_breakeven = call_short + net_credit_per_unit

    aggregate_greeks = _aggregate_greeks(
        spot=spot,
        iv=iv,
        legs=legs,
        time_to_expiry=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        dividend_yield=q,
    )

    return {
        "action": "PROPOSE_IRON_CONDOR",
        "structure": "iron_condor",
        "research_only": bool(research_only),
        "underlying": underlying_upper,
        "spot": float(spot),
        "iv": float(iv),
        "expiry": str(expiry)[:10] if expiry is not None else None,
        "lot_size": lot_size,
        "legs": legs,
        "economics": {
            "net_credit": round(net_credit, 2),
            "net_credit_per_unit": round(net_credit_per_unit, 2),
            "max_profit": round(max_profit, 2),
            "max_loss": round(max_loss, 2),
            "breakevens": [round(lower_breakeven, 2), round(upper_breakeven, 2)],
            "put_wing_width": put_wing,
            "call_wing_width": call_wing,
        },
        "greeks": aggregate_greeks,
        "routing_hint": "options_risk_checker",
        "message": (
            f"Iron condor proposal on {underlying_upper} @ {spot:.0f} — "
            f"credit ₹{net_credit:,.0f}, BE [{lower_breakeven:.0f}, {upper_breakeven:.0f}]"
        ),
    }


def _aggregate_greeks(
    spot: float,
    iv: float,
    legs: List[Dict[str, Any]],
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> Dict[str, float]:
    """Net Greeks across all legs (signed by buy/sell)."""
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for leg in legs:
        g = compute_greeks(
            spot=spot,
            strike=leg["strike"],
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            volatility=iv,
            option_type=leg["option_type"],
            dividend_yield=dividend_yield,
        )
        sign = 1.0 if leg["transaction_type"] == "BUY" else -1.0
        qty = leg["quantity"]
        totals["delta"] += sign * g.delta * qty
        totals["gamma"] += sign * g.gamma * qty
        totals["theta"] += sign * g.theta * qty
        totals["vega"] += sign * g.vega * qty
        totals["rho"] += sign * g.rho * qty

    return {k: round(v, 4) for k, v in totals.items()}


def default_strikes_from_spot(
    spot: float,
    underlying: str = "NIFTY",
    wing_width: Optional[float] = None,
    body_width: Optional[float] = None,
) -> Dict[str, float]:
    """
    Helper: derive iron condor strikes from spot using exchange strike steps.

    body_width : distance from ATM to short strikes (each side)
    wing_width : width of protective long strikes beyond shorts
    """
    steps = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
    step = steps.get(underlying.upper(), 50)
    atm = round(spot / step) * step
    body = body_width if body_width is not None else step * 2
    wing = wing_width if wing_width is not None else step * 2

    return {
        "put_long": atm - body - wing,
        "put_short": atm - body,
        "call_short": atm + body,
        "call_long": atm + body + wing,
    }