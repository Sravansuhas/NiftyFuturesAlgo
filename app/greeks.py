"""
Black-Scholes Greeks for Indian index options (NIFTY / BANKNIFTY / SENSEX).

Research / analysis layer only — no order placement.
Uses continuous dividend yield (q) for index options; NIFTY default ~1.2% p.a.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Union

OptionType = Literal["CE", "PE", "C", "P"]

# Approximate continuous dividend yield for index underlyings (annualized)
DEFAULT_DIVIDEND_YIELDS = {
    "NIFTY": 0.012,
    "BANKNIFTY": 0.010,
    "SENSEX": 0.011,
}

DEFAULT_RISK_FREE_RATE = 0.065  # ~6.5% INR money-market proxy


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _normalize_option_type(option_type: Union[OptionType, str]) -> str:
    ot = str(option_type).upper()
    if ot in {"CE", "C", "CALL"}:
        return "CE"
    if ot in {"PE", "P", "PUT"}:
        return "PE"
    raise ValueError(f"Invalid option_type: {option_type}")


def _d1_d2(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    dividend_yield: float,
) -> tuple[float, float]:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if time_to_expiry <= 0:
        raise ValueError("time_to_expiry must be positive")
    if volatility <= 0:
        raise ValueError("volatility must be positive")

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiry
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    return d1, d2


def dividend_yield_for(
    underlying: Optional[str] = None,
    dividend_yield: Optional[float] = None,
) -> float:
    if dividend_yield is not None:
        return float(dividend_yield)
    if underlying:
        return DEFAULT_DIVIDEND_YIELDS.get(underlying.upper(), 0.0)
    return 0.0


def black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    option_type: Union[OptionType, str],
    dividend_yield: float = 0.0,
) -> float:
    """European option price with continuous dividend yield."""
    ot = _normalize_option_type(option_type)
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, risk_free_rate, volatility, dividend_yield)
    disc_s = math.exp(-dividend_yield * time_to_expiry)
    disc_k = math.exp(-risk_free_rate * time_to_expiry)

    if ot == "CE":
        return disc_s * spot * _norm_cdf(d1) - disc_k * strike * _norm_cdf(d2)
    return disc_k * strike * _norm_cdf(-d2) - disc_s * spot * _norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float   # per 1% IV move
    rho: float    # per 1% rate move
    price: float
    implied_vol: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "rho": self.rho,
            "price": self.price,
            "implied_vol": self.implied_vol,
        }


def compute_greeks(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    option_type: Union[OptionType, str],
    dividend_yield: float = 0.0,
    market_price: Optional[float] = None,
) -> Greeks:
    """Full Greeks set for a European index option."""
    ot = _normalize_option_type(option_type)
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, risk_free_rate, volatility, dividend_yield)
    sqrt_t = math.sqrt(time_to_expiry)
    disc_s = math.exp(-dividend_yield * time_to_expiry)
    disc_k = math.exp(-risk_free_rate * time_to_expiry)
    pdf_d1 = _norm_pdf(d1)

    price = black_scholes_price(
        spot, strike, time_to_expiry, risk_free_rate, volatility, ot, dividend_yield
    )

    if ot == "CE":
        delta = disc_s * _norm_cdf(d1)
        rho = (
            strike
            * time_to_expiry
            * disc_k
            * _norm_cdf(d2)
            / 100.0
        )
        theta_annual = (
            -disc_s * spot * pdf_d1 * volatility / (2.0 * sqrt_t)
            - risk_free_rate * strike * disc_k * _norm_cdf(d2)
            + dividend_yield * spot * disc_s * _norm_cdf(d1)
        )
    else:
        delta = disc_s * (_norm_cdf(d1) - 1.0)
        rho = (
            -strike
            * time_to_expiry
            * disc_k
            * _norm_cdf(-d2)
            / 100.0
        )
        theta_annual = (
            -disc_s * spot * pdf_d1 * volatility / (2.0 * sqrt_t)
            + risk_free_rate * strike * disc_k * _norm_cdf(-d2)
            - dividend_yield * spot * disc_s * _norm_cdf(-d1)
        )

    gamma = disc_s * pdf_d1 / (spot * volatility * sqrt_t)
    vega = disc_s * spot * pdf_d1 * sqrt_t / 100.0
    theta = theta_annual / 365.0

    iv = None
    if market_price is not None and market_price > 0:
        iv = implied_volatility(
            market_price,
            spot,
            strike,
            time_to_expiry,
            risk_free_rate,
            ot,
            dividend_yield=dividend_yield,
        )

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        price=price,
        implied_vol=iv,
    )


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    option_type: Union[OptionType, str],
    dividend_yield: float = 0.0,
    *,
    initial_guess: float = 0.20,
    tolerance: float = 1e-6,
    max_iterations: int = 50,
    method: str = "newton",
) -> float:
    """
    Solve for implied volatility given market premium.

    Uses Newton-Raphson with vega; falls back to bisection if unstable.
    """
    ot = _normalize_option_type(option_type)
    if market_price <= 0:
        raise ValueError("market_price must be positive")

    intrinsic = max(0.0, spot - strike) if ot == "CE" else max(0.0, strike - spot)
    if market_price < intrinsic - 1e-6:
        raise ValueError("market_price below intrinsic value")

    def _price_at(vol: float) -> float:
        return black_scholes_price(
            spot, strike, time_to_expiry, risk_free_rate, vol, ot, dividend_yield
        )

    def _vega_raw(vol: float) -> float:
        d1, _ = _d1_d2(spot, strike, time_to_expiry, risk_free_rate, vol, dividend_yield)
        disc_s = math.exp(-dividend_yield * time_to_expiry)
        return disc_s * spot * _norm_pdf(d1) * math.sqrt(time_to_expiry)

    # Newton-Raphson
    sigma = max(initial_guess, 1e-4)
    for _ in range(max_iterations):
        model = _price_at(sigma)
        diff = model - market_price
        if abs(diff) < tolerance:
            return sigma

        vega = _vega_raw(sigma)
        if vega < 1e-12:
            break

        sigma_next = sigma - diff / vega
        if sigma_next <= 0 or sigma_next > 5.0:
            break
        sigma = sigma_next

    # Bisection fallback
    lo, hi = 1e-6, 5.0
    price_lo = _price_at(lo) - market_price
    price_hi = _price_at(hi) - market_price
    if price_lo * price_hi > 0:
        raise ValueError("Could not bracket implied volatility")

    for _ in range(max_iterations):
        mid = 0.5 * (lo + hi)
        price_mid = _price_at(mid) - market_price
        if abs(price_mid) < tolerance:
            return mid
        if price_lo * price_mid <= 0:
            hi = mid
            price_hi = price_mid
        else:
            lo = mid
            price_lo = price_mid

    return 0.5 * (lo + hi)


def greeks_for_underlying(
    underlying: str,
    spot: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    option_type: Union[OptionType, str],
    *,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: Optional[float] = None,
    market_price: Optional[float] = None,
) -> Greeks:
    """Convenience wrapper applying index-specific dividend yield defaults."""
    q = dividend_yield_for(underlying, dividend_yield)
    return compute_greeks(
        spot=spot,
        strike=strike,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        volatility=volatility,
        option_type=option_type,
        dividend_yield=q,
        market_price=market_price,
    )