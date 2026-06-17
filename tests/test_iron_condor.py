from datetime import date

import pytest

from app.options_risk import options_risk_checker
from app.strategies.iron_condor import (
    default_strikes_from_spot,
    propose_iron_condor,
)


def _sample_strikes():
    return {
        "put_long": 23800,
        "put_short": 24000,
        "call_short": 25000,
        "call_long": 25200,
    }


def test_propose_iron_condor_structure():
    signal = propose_iron_condor(
        spot=24500,
        iv=0.14,
        strikes=_sample_strikes(),
        underlying="NIFTY",
        expiry=date(2026, 6, 26),
    )

    assert signal["action"] == "PROPOSE_IRON_CONDOR"
    assert signal["research_only"] is True
    assert signal["routing_hint"] == "options_risk_checker"
    assert len(signal["legs"]) == 4
    assert signal["underlying"] == "NIFTY"
    assert signal["lot_size"] == 65


def test_propose_iron_condor_legs_order_and_sides():
    signal = propose_iron_condor(24500, 0.14, _sample_strikes())
    legs = signal["legs"]

    assert legs[0]["option_type"] == "PE" and legs[0]["transaction_type"] == "BUY"
    assert legs[1]["option_type"] == "PE" and legs[1]["transaction_type"] == "SELL"
    assert legs[2]["option_type"] == "CE" and legs[2]["transaction_type"] == "SELL"
    assert legs[3]["option_type"] == "CE" and legs[3]["transaction_type"] == "BUY"

    strikes = [leg["strike"] for leg in legs]
    assert strikes == [23800, 24000, 25000, 25200]


def test_propose_iron_condor_economics():
    signal = propose_iron_condor(24500, 0.14, _sample_strikes())
    econ = signal["economics"]

    assert econ["net_credit"] > 0
    assert econ["max_profit"] == econ["net_credit"]
    assert econ["max_loss"] > 0
    assert len(econ["breakevens"]) == 2
    lower, upper = econ["breakevens"]
    assert lower < 24500 < upper
    assert lower == pytest.approx(24000 - econ["net_credit_per_unit"], abs=0.01)
    assert upper == pytest.approx(25000 + econ["net_credit_per_unit"], abs=0.01)


def test_propose_iron_condor_greeks_present():
    signal = propose_iron_condor(24500, 0.14, _sample_strikes())
    greeks = signal["greeks"]
    assert "delta" in greeks
    assert "gamma" in greeks
    assert "theta" in greeks
    assert "vega" in greeks


def test_propose_iron_condor_passes_options_risk_checker():
    signal = propose_iron_condor(24500, 0.14, _sample_strikes())
    result = options_risk_checker.validate_dicts(signal["legs"])
    assert result.approved is True
    assert result.has_naked_short_vol is False


def test_invalid_strike_order_raises():
    bad = {
        "put_long": 24000,
        "put_short": 23800,
        "call_short": 25000,
        "call_long": 25200,
    }
    with pytest.raises(ValueError, match="put_long"):
        propose_iron_condor(24500, 0.14, bad)


def test_missing_strikes_raises():
    with pytest.raises(ValueError, match="missing"):
        propose_iron_condor(24500, 0.14, {"put_long": 23800})


def test_research_only_can_be_disabled_for_execution():
    signal = propose_iron_condor(24500, 0.14, _sample_strikes(), research_only=False)
    assert signal["research_only"] is False


def test_default_strikes_from_spot():
    strikes = default_strikes_from_spot(24523, "NIFTY")
    assert strikes["put_long"] < strikes["put_short"]
    assert strikes["put_short"] < strikes["call_short"]
    assert strikes["call_short"] < strikes["call_long"]
    assert strikes["put_short"] == 24400  # ATM 24500 - 100 body
    assert strikes["call_short"] == 24600