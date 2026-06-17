import math

import pytest

from app.greeks import (
    DEFAULT_DIVIDEND_YIELDS,
    black_scholes_price,
    compute_greeks,
    dividend_yield_for,
    greeks_for_underlying,
    implied_volatility,
)


# Hull "Options, Futures, and Other Derivatives" reference (Ch. 15):
# S=42, K=40, r=10%, T=0.5yr, sigma=20%, q=0 → call ≈ 4.76
HULL_CALL = 4.76
HULL_PUT = 0.81

# ATM reference: S=K=100, r=5%, T=1yr, sigma=20%, q=0
ATM_CALL = 10.4506
ATM_PUT = 5.5735
ATM_DELTA_CALL = 0.6368
ATM_DELTA_PUT = -0.3632


def test_black_scholes_hull_example():
    price = black_scholes_price(
        spot=42,
        strike=40,
        time_to_expiry=0.5,
        risk_free_rate=0.10,
        volatility=0.20,
        option_type="CE",
        dividend_yield=0.0,
    )
    assert price == pytest.approx(HULL_CALL, abs=0.01)

    put = black_scholes_price(
        spot=42,
        strike=40,
        time_to_expiry=0.5,
        risk_free_rate=0.10,
        volatility=0.20,
        option_type="PE",
        dividend_yield=0.0,
    )
    assert put == pytest.approx(HULL_PUT, abs=0.01)


def test_black_scholes_atm_call_put():
    call = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "CE", 0.0)
    put = black_scholes_price(100, 100, 1.0, 0.05, 0.20, "PE", 0.0)
    assert call == pytest.approx(ATM_CALL, abs=0.001)
    assert put == pytest.approx(ATM_PUT, abs=0.001)
    assert call - put == pytest.approx(100 - 100 * math.exp(-0.05), abs=0.01)


def test_greeks_atm_call():
    g = compute_greeks(100, 100, 1.0, 0.05, 0.20, "CE", dividend_yield=0.0)
    assert g.delta == pytest.approx(ATM_DELTA_CALL, abs=0.001)
    assert g.gamma > 0
    assert g.vega > 0
    assert g.theta < 0
    assert g.rho > 0
    assert g.price == pytest.approx(ATM_CALL, abs=0.001)


def test_greeks_atm_put():
    g = compute_greeks(100, 100, 1.0, 0.05, 0.20, "PE", dividend_yield=0.0)
    assert g.delta == pytest.approx(ATM_DELTA_PUT, abs=0.001)


def test_nifty_dividend_yield_default():
    assert dividend_yield_for("NIFTY") == pytest.approx(0.012)
    assert DEFAULT_DIVIDEND_YIELDS["NIFTY"] == 0.012


def test_dividend_yield_lowers_call_price():
    no_div = black_scholes_price(24500, 24500, 7 / 365, 0.065, 0.14, "CE", 0.0)
    with_div = black_scholes_price(24500, 24500, 7 / 365, 0.065, 0.14, "CE", 0.012)
    assert with_div < no_div


def test_greeks_for_underlying_applies_nifty_yield():
    g = greeks_for_underlying("NIFTY", 24500, 24500, 7 / 365, 0.14, "CE")
    g_zero = compute_greeks(24500, 24500, 7 / 365, 0.065, 0.14, "CE", dividend_yield=0.0)
    assert g.price < g_zero.price


def test_implied_volatility_round_trip_newton():
    sigma = 0.18
    price = black_scholes_price(100, 100, 0.25, 0.05, sigma, "CE", 0.0)
    recovered = implied_volatility(price, 100, 100, 0.25, 0.05, "CE", method="newton")
    assert recovered == pytest.approx(sigma, abs=1e-4)


def test_implied_volatility_round_trip_bisection_fallback():
    sigma = 0.35
    price = black_scholes_price(100, 105, 0.1, 0.05, sigma, "PE", 0.012)
    recovered = implied_volatility(price, 100, 105, 0.1, 0.05, "PE", dividend_yield=0.012)
    assert recovered == pytest.approx(sigma, abs=1e-3)


def test_implied_vol_below_intrinsic_raises():
    with pytest.raises(ValueError, match="intrinsic"):
        implied_volatility(1.0, 100, 90, 0.1, 0.05, "CE")


def test_invalid_option_type_raises():
    with pytest.raises(ValueError):
        black_scholes_price(100, 100, 1.0, 0.05, 0.2, "INVALID")