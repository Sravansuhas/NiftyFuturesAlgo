"""
Straddle proposal skeleton — long / short ATM straddle.

Research only: returns signal dicts for future RiskGatekeeper / OptionsRiskChecker routing.
Short straddles are flagged as naked short volatility (blocked by options_risk by default).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Union

from ..greeks import DEFAULT_RISK_FREE_RATE, black_scholes_price, compute_greeks, dividend_yield_for
from ..options_pnl import get_index_lot_size

RESEARCH_ONLY = True


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
        "tradingsymbol": "",
    }


def propose_straddle(
    spot: float,
    iv: float,
    strike: float,
    direction: str = "long",
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
    Propose a long or short ATM straddle (buy/sell CE + PE at same strike).

    Parameters
    ----------
    direction : str
        "long" — buy call + buy put (defined risk = premium paid)
        "short" — sell call + sell put (naked short vol; risk checker blocks by default)
    """
    if not research_only:
        raise ValueError("straddle proposals are research_only only")

    direction_lower = direction.lower()
    if direction_lower not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")

    underlying_upper = underlying.upper()
    q = dividend_yield_for(underlying_upper, dividend_yield)
    lot_size = quantity or get_index_lot_size(underlying_upper)
    strike_f = float(strike)

    call_premium = black_scholes_price(
        spot, strike_f, time_to_expiry_years, risk_free_rate, iv, "CE", q
    )
    put_premium = black_scholes_price(
        spot, strike_f, time_to_expiry_years, risk_free_rate, iv, "PE", q
    )

    txn = "BUY" if direction_lower == "long" else "SELL"
    legs: List[Dict[str, Any]] = [
        _build_leg(
            underlying=underlying_upper,
            option_type="CE",
            strike=strike_f,
            transaction_type=txn,
            quantity=lot_size,
            premium=call_premium,
            expiry=expiry,
        ),
        _build_leg(
            underlying=underlying_upper,
            option_type="PE",
            strike=strike_f,
            transaction_type=txn,
            quantity=lot_size,
            premium=put_premium,
            expiry=expiry,
        ),
    ]

    total_premium = (call_premium + put_premium) * lot_size
    aggregate_greeks = _aggregate_greeks(
        spot=spot,
        iv=iv,
        legs=legs,
        time_to_expiry=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        dividend_yield=q,
    )

    if direction_lower == "long":
        max_loss = total_premium
        max_profit = None  # theoretically unlimited on large moves
        net_debit = total_premium
        net_credit = 0.0
        lower_be = strike_f - (call_premium + put_premium)
        upper_be = strike_f + (call_premium + put_premium)
        action = "PROPOSE_STRADDLE_LONG"
        risk_flags: List[str] = []
    else:
        max_profit = total_premium
        max_loss = None  # undefined — naked short
        net_credit = total_premium
        net_debit = 0.0
        lower_be = strike_f - (call_premium + put_premium)
        upper_be = strike_f + (call_premium + put_premium)
        action = "PROPOSE_STRADDLE_SHORT"
        risk_flags = ["naked_short_volatility"]

    return {
        "action": action,
        "structure": f"straddle_{direction_lower}",
        "research_only": True,
        "underlying": underlying_upper,
        "spot": float(spot),
        "iv": float(iv),
        "strike": strike_f,
        "expiry": str(expiry)[:10] if expiry is not None else None,
        "lot_size": lot_size,
        "legs": legs,
        "economics": {
            "net_debit": round(net_debit, 2),
            "net_credit": round(net_credit, 2),
            "total_premium_per_unit": round(call_premium + put_premium, 2),
            "max_profit": round(max_profit, 2) if max_profit is not None else None,
            "max_loss": round(max_loss, 2) if max_loss is not None else None,
            "breakevens": [round(lower_be, 2), round(upper_be, 2)],
        },
        "greeks": aggregate_greeks,
        "risk_flags": risk_flags,
        "routing_hint": "options_risk_checker",
        "message": (
            f"{direction_lower.title()} straddle on {underlying_upper} @ strike {strike_f:.0f} — "
            f"premium/unit {call_premium + put_premium:.2f}"
        ),
    }


def propose_long_straddle(
    spot: float,
    iv: float,
    strike: Optional[float] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience: long straddle at ATM (rounded) or explicit strike."""
    underlying = kwargs.pop("underlying", "NIFTY")
    atm = strike if strike is not None else _atm_strike(spot, underlying)
    return propose_straddle(spot, iv, atm, direction="long", underlying=underlying, **kwargs)


def propose_short_straddle(
    spot: float,
    iv: float,
    strike: Optional[float] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience: short straddle at ATM (rounded) or explicit strike."""
    underlying = kwargs.pop("underlying", "NIFTY")
    atm = strike if strike is not None else _atm_strike(spot, underlying)
    return propose_straddle(spot, iv, atm, direction="short", underlying=underlying, **kwargs)


def _atm_strike(spot: float, underlying: str) -> float:
    steps = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
    step = steps.get(underlying.upper(), 50)
    return round(spot / step) * step


def _aggregate_greeks(
    spot: float,
    iv: float,
    legs: List[Dict[str, Any]],
    time_to_expiry: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> Dict[str, float]:
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