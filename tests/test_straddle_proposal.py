from datetime import date

import pytest

from app.options_risk import options_risk_checker
from app.strategies.straddle_proposal import (
    propose_long_straddle,
    propose_short_straddle,
    propose_straddle,
)


def test_propose_long_straddle():
    signal = propose_long_straddle(
        spot=24500,
        iv=0.15,
        strike=24500,
        underlying="NIFTY",
        expiry=date(2026, 6, 26),
    )

    assert signal["action"] == "PROPOSE_STRADDLE_LONG"
    assert signal["research_only"] is True
    assert signal["structure"] == "straddle_long"
    assert len(signal["legs"]) == 2
    assert all(leg["transaction_type"] == "BUY" for leg in signal["legs"])
    assert signal["economics"]["max_loss"] > 0
    assert signal["economics"]["max_profit"] is None
    assert signal["risk_flags"] == []


def test_propose_short_straddle():
    signal = propose_short_straddle(24500, 0.15, strike=24500)

    assert signal["action"] == "PROPOSE_STRADDLE_SHORT"
    assert signal["research_only"] is True
    assert all(leg["transaction_type"] == "SELL" for leg in signal["legs"])
    assert signal["economics"]["max_profit"] > 0
    assert signal["economics"]["max_loss"] is None
    assert "naked_short_volatility" in signal["risk_flags"]


def test_straddle_same_strike_on_both_legs():
    signal = propose_straddle(24500, 0.15, 24500, direction="long")
    strikes = {leg["option_type"]: leg["strike"] for leg in signal["legs"]}
    assert strikes["CE"] == strikes["PE"] == 24500


def test_straddle_breakevens_symmetric():
    signal = propose_long_straddle(24500, 0.15, strike=24500)
    lower, upper = signal["economics"]["breakevens"]
    mid = (lower + upper) / 2
    assert mid == pytest.approx(24500, abs=0.01)
    assert upper - mid == pytest.approx(mid - lower, abs=0.01)


def test_long_straddle_passes_options_risk():
    signal = propose_long_straddle(24500, 0.15, strike=24500)
    result = options_risk_checker.validate_dicts(signal["legs"])
    assert result.approved is True


def test_short_straddle_blocked_by_options_risk():
    signal = propose_short_straddle(24500, 0.15, strike=24500)
    result = options_risk_checker.validate_dicts(signal["legs"])
    assert result.approved is False
    assert result.has_naked_short_vol is True


def test_atm_strike_rounding():
    signal = propose_long_straddle(24523, 0.15, underlying="NIFTY")
    assert signal["strike"] == 24500


def test_invalid_direction_raises():
    with pytest.raises(ValueError, match="direction"):
        propose_straddle(24500, 0.15, 24500, direction="neutral")


def test_research_only_cannot_be_disabled():
    with pytest.raises(ValueError, match="research_only"):
        propose_straddle(24500, 0.15, 24500, research_only=False)