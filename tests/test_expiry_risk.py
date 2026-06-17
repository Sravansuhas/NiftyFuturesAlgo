"""Tests for app/expiry_risk.py — expiry calendar + gamma proxy gates."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.expiry_risk import (
    GAMMA_CAUTION_CLEAR,
    GAMMA_CAUTION_HARD,
    GAMMA_CAUTION_SOFT,
    TRIGGER_CALENDAR_HARD,
    TRIGGER_CALENDAR_SOFT,
    TRIGGER_GAMMA_PROXY_HARD,
    TRIGGER_NONE,
    evaluate_expiry_gates,
    expiry_gate_blocks_entries,
)

IST = ZoneInfo("Asia/Kolkata")


def _cfg(**gate_overrides):
    gates = {
        "block_expiry_day_entries": False,
        "expiry_day_entry_cutoff_hour": 12,
        "enable_gamma_proxy": False,
    }
    gates.update(gate_overrides)
    return {"regime_gates": gates, "default_iv": 0.16, "iv_floor": 0.12, "iv_cap": 0.35}


def test_clear_on_non_expiry_day():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=False):
        ev = evaluate_expiry_gates(_cfg(), underlying="NIFTY", now=now)
    assert ev.gamma_caution_level == GAMMA_CAUTION_CLEAR
    assert ev.trigger_type == TRIGGER_NONE
    assert ev.is_expiry_day is False
    assert ev.expiry_caution is False
    assert not expiry_gate_blocks_entries(ev)


def test_calendar_soft_expiry_morning():
    now = datetime(2026, 6, 16, 10, 30, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(_cfg(), underlying="NIFTY", now=now)
    assert ev.gamma_caution_level == GAMMA_CAUTION_SOFT
    assert ev.trigger_type == TRIGGER_CALENDAR_SOFT
    assert ev.is_expiry_day is True
    assert ev.expiry_caution is True
    assert "calendar_soft" in ev.triggers
    assert not expiry_gate_blocks_entries(ev)


def test_calendar_hard_after_cutoff():
    now = datetime(2026, 6, 16, 12, 15, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(_cfg(), underlying="NIFTY", now=now)
    assert ev.gamma_caution_level == GAMMA_CAUTION_HARD
    assert ev.trigger_type == TRIGGER_CALENDAR_HARD
    assert expiry_gate_blocks_entries(ev)
    assert any("gamma caution" in r for r in ev.reasons)


def test_legacy_full_day_block():
    now = datetime(2026, 6, 16, 9, 45, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(
            _cfg(block_expiry_day_entries=True),
            underlying="NIFTY",
            now=now,
        )
    assert ev.gamma_caution_level == GAMMA_CAUTION_HARD
    assert ev.trigger_type == TRIGGER_CALENDAR_HARD
    assert any("blocked" in r for r in ev.reasons)


def test_gamma_proxy_hard_on_high_atm_gamma():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    mctx = {"spot": 24500, "india_vix": {"available": True, "level": 18.0}}
    with patch("app.expiry_risk.is_expiry_day", return_value=True), patch(
        "app.expiry_risk.greeks_for_underlying"
    ) as mock_greeks:
        ce = type("G", (), {"gamma": 0.00025})()
        pe = type("G", (), {"gamma": 0.00020})()
        mock_greeks.side_effect = [ce, pe]
        ev = evaluate_expiry_gates(
            _cfg(enable_gamma_proxy=True, gamma_proxy_hard_threshold=0.00035),
            underlying="NIFTY",
            market_context=mctx,
            now=now,
        )
    assert ev.gamma_caution_level == GAMMA_CAUTION_HARD
    assert ev.trigger_type == TRIGGER_GAMMA_PROXY_HARD
    assert ev.gamma_proxy is not None
    assert ev.gamma_proxy["combined_atm_gamma"] == pytest.approx(0.00045, rel=1e-3)


def test_gamma_proxy_oi_threshold():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    mctx = {
        "spot": 24500,
        "index_tickers": {
            "indices": {
                "NIFTY": {
                    "atm_strike": 24500,
                    "ce": {"oi": 5_000_000},
                    "pe": {"oi": 4_000_000},
                }
            }
        },
    }
    with patch("app.expiry_risk.is_expiry_day", return_value=True), patch(
        "app.expiry_risk.greeks_for_underlying"
    ) as mock_greeks:
        low = type("G", (), {"gamma": 0.00001})()
        mock_greeks.side_effect = [low, low]
        ev = evaluate_expiry_gates(
            _cfg(enable_gamma_proxy=True, gamma_proxy_oi_threshold=8_000_000),
            underlying="NIFTY",
            market_context=mctx,
            now=now,
        )
    assert ev.gamma_caution_level == GAMMA_CAUTION_HARD
    assert ev.trigger_type == TRIGGER_GAMMA_PROXY_HARD
    assert any("OI" in r for r in ev.reasons)


def test_gamma_proxy_disabled():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    mctx = {"spot": 24500}
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(_cfg(), underlying="NIFTY", market_context=mctx, now=now)
    assert ev.gamma_caution_level == GAMMA_CAUTION_SOFT
    assert ev.trigger_type == TRIGGER_CALENDAR_SOFT


def test_audit_dict_fields():
    now = datetime(2026, 6, 16, 12, 30, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(_cfg(), underlying="BANKNIFTY", now=now)
    audit = ev.to_audit_dict()
    assert audit["gamma_caution_level"] == GAMMA_CAUTION_HARD
    assert audit["trigger_type"] == TRIGGER_CALENDAR_HARD
    assert audit["is_expiry_day"] is True
    assert "reasons" in audit


def test_unpack_tuple_style():
    now = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    with patch("app.expiry_risk.is_expiry_day", return_value=True):
        ev = evaluate_expiry_gates(_cfg(), now=now)
        level, reasons, trigger = ev.gamma_caution_level, ev.reasons, ev.trigger_type
    assert level == GAMMA_CAUTION_SOFT
    assert trigger == TRIGGER_CALENDAR_SOFT
    assert reasons


def test_status_payload_exposes_expiry_fields():
    from app.options_strategy_runner import get_options_algo_status_payload

    morning = datetime(2026, 6, 16, 10, 0, tzinfo=IST)
    cfg = {
        "underlying": "NIFTY",
        "regime_gates": _cfg()["regime_gates"],
        "allowed_structures": ["iron_condor"],
    }
    with patch("app.options_strategy_runner.get_options_config", return_value=cfg), patch(
        "app.options_strategy_runner.is_real_market_open", return_value=True
    ), patch("app.expiry_risk.is_expiry_day", return_value=True), patch(
        "app.expiry_risk.now_ist", return_value=morning
    ), patch("app.options_strategy_runner.now_ist", return_value=morning), patch(
        "app.options_strategy_runner.options_trading_enabled", return_value=False
    ), patch("app.options_strategy_runner.futures_trading_enabled", return_value=False):
        payload = get_options_algo_status_payload(fast=True)

    assert payload["gamma_caution_level"] == GAMMA_CAUTION_SOFT
    assert payload["is_expiry_day"] is True
    assert payload["expiry_caution"] is True
    assert "calendar_soft" in payload["expiry_triggers"]
    assert payload["regime_gates"]["gamma_caution_level"] == GAMMA_CAUTION_SOFT