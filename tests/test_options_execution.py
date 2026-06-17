from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.options_execution_engine import OptionsExecutionEngine
from app.options_positions import OptionsPositionStore
from app.options_strategy_runner import (
    check_regime_gates,
    evaluate_structure_exit,
    futures_trading_enabled,
    options_trading_enabled,
)
from app.strategies.iron_condor import default_strikes_from_spot, propose_iron_condor


def _proposal():
    strikes = default_strikes_from_spot(24500, "NIFTY")
    return propose_iron_condor(
        spot=24500,
        iv=0.16,
        strikes=strikes,
        underlying="NIFTY",
        expiry=date(2026, 6, 26),
        research_only=False,
    )


def _resolved_legs(proposal):
    legs = []
    for i, leg in enumerate(proposal["legs"]):
        legs.append({
            **leg,
            "tradingsymbol": f"NIFTY26JUN{int(leg['strike'])}{leg['option_type']}",
            "exchange": "NFO",
            "lot_size": 65,
            "instrument_token": 1000 + i,
        })
    out = dict(proposal)
    out["legs"] = legs
    return out


def test_propose_iron_condor_execution_mode():
    signal = _proposal()
    assert signal["research_only"] is False


def test_options_trading_requires_env_and_config(monkeypatch):
    monkeypatch.delenv("OPTIONS_TRADING_ENABLED", raising=False)
    with patch("app.trading_controls.effective_options_trading_enabled", return_value=False):
        assert options_trading_enabled() is False

    monkeypatch.setenv("OPTIONS_TRADING_ENABLED", "true")
    with patch("app.options_strategy_runner.get_options_config") as mock_cfg:
        mock_cfg.return_value = {"trading_enabled": True}
        with patch("app.trading_controls.effective_options_trading_enabled", return_value=True):
            assert options_trading_enabled() is True


def test_futures_trading_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FUTURES_TRADING_ENABLED", raising=False)
    assert futures_trading_enabled() is False


def test_check_regime_gates_blocks_high_vix():
    cfg = {"regime_gates": {"max_vix": 20.0, "min_vix": 10.0, "block_expiry_day_entries": False}}
    with patch("app.options_strategy_runner.is_real_market_open", return_value=True):
        ok, reasons = check_regime_gates(
            cfg,
            market_context={"india_vix": {"available": True, "level": 25.0}},
        )
    assert ok is False
    assert any("max_vix" in r for r in reasons)


def test_check_regime_gates_allows_expiry_morning_window():
    """Expiry day should not hard-block before cutoff (mirrors futures discipline)."""
    cfg = {
        "regime_gates": {
            "block_expiry_day_entries": False,
            "expiry_day_entry_cutoff_hour": 12,
        }
    }
    morning = __import__("datetime").datetime(2026, 6, 16, 10, 30)
    with patch("app.options_strategy_runner.is_real_market_open", return_value=True), patch(
        "app.expiry_risk.now_ist", return_value=morning
    ), patch("app.expiry_risk.is_expiry_day", return_value=True):
        ok, reasons = check_regime_gates(cfg, underlying="NIFTY")
    assert ok is True
    assert reasons == []


def test_check_regime_gates_blocks_expiry_after_cutoff():
    cfg = {
        "regime_gates": {
            "block_expiry_day_entries": False,
            "expiry_day_entry_cutoff_hour": 12,
        }
    }
    afternoon = __import__("datetime").datetime(2026, 6, 16, 12, 15)
    with patch("app.options_strategy_runner.is_real_market_open", return_value=True), patch(
        "app.expiry_risk.now_ist", return_value=afternoon
    ), patch("app.expiry_risk.is_expiry_day", return_value=True):
        ok, reasons = check_regime_gates(cfg, underlying="NIFTY")
    assert ok is False
    assert any("gamma caution" in r for r in reasons)


def test_check_regime_gates_legacy_full_day_block():
    cfg = {"regime_gates": {"block_expiry_day_entries": True}}
    morning = __import__("datetime").datetime(2026, 6, 16, 10, 30)
    with patch("app.options_strategy_runner.is_real_market_open", return_value=True), patch(
        "app.expiry_risk.now_ist", return_value=morning
    ), patch("app.expiry_risk.is_expiry_day", return_value=True):
        ok, reasons = check_regime_gates(cfg, underlying="NIFTY")
    assert ok is False
    assert any("Expiry day entry blocked" in r for r in reasons)


def test_evaluate_structure_exit_profit_target():
    class Struct:
        entry_credit = 10_000
        max_loss = 50_000

    cfg = {"iron_condor": {"exit_profit_pct": 0.50, "exit_loss_pct": 1.0}}
    reason = evaluate_structure_exit(Struct(), cfg, current_mtm=6_000)
    assert reason is not None
    assert "profit_target" in reason


def test_execute_structure_paper_success(tmp_path):
    store = OptionsPositionStore(path=tmp_path / "structures.json")
    engine = OptionsExecutionEngine(position_store=store)
    proposal = _resolved_legs(_proposal())

    mock_rg = MagicMock()
    mock_rg.place_guarded_order.return_value = {
        "success": True,
        "order_id": "DRY-RUN",
        "dry_run": True,
    }

    with patch("app.risk_gatekeeper.risk_gatekeeper", mock_rg):
        result = engine.execute_structure(
            MagicMock(),
            proposal,
            force_dry_run=True,
        )

    assert result["success"] is True
    assert result["structure_id"].startswith("OPT-")
    assert len(store.list_open()) == 1


def test_execute_structure_all_legs_via_real_gatekeeper(tmp_path):
    """Regression: leg 2+ must not fail single-symbol dry-run accounting."""
    from app.risk_gatekeeper import RiskConfig, RiskGatekeeper
    from app.state_machine import SystemState, state_machine

    state_machine.set_state(SystemState.PAPER_MODE)
    store = OptionsPositionStore(path=tmp_path / "structures.json")
    engine = OptionsExecutionEngine(position_store=store)
    proposal = _resolved_legs(_proposal())

    gatekeeper = RiskGatekeeper(config=RiskConfig(force_dry_run=True, lot_size=65))
    with patch("app.risk_gatekeeper.risk_gatekeeper", gatekeeper):
        result = engine.execute_structure(MagicMock(), proposal, force_dry_run=True)

    assert result["success"] is True
    assert len(store.list_open()) == 1
    assert len(result.get("leg_results") or []) == 4


def test_execute_structure_rolls_back_on_leg_failure(tmp_path):
    store = OptionsPositionStore(path=tmp_path / "structures.json")
    engine = OptionsExecutionEngine(position_store=store)
    proposal = _resolved_legs(_proposal())

    responses = [
        {"success": True, "order_id": "DRY-1"},
        {"success": False, "message": "blocked"},
    ]
    mock_rg = MagicMock()
    mock_rg.place_guarded_order.side_effect = responses + [
        {"success": True, "order_id": "DRY-RB"},
    ]

    with patch("app.risk_gatekeeper.risk_gatekeeper", mock_rg):
        result = engine.execute_structure(MagicMock(), proposal, force_dry_run=True)

    assert result["success"] is False
    assert len(store.list_open()) == 0
    assert mock_rg.place_guarded_order.call_count >= 3


def test_validate_proposal_structure_loss():
    engine = OptionsExecutionEngine()
    proposal = _resolved_legs(_proposal())
    proposal["economics"]["max_loss"] = 500_000

    result = engine.validate_proposal(
        proposal,
        capital=1_000_000,
        max_margin_pct=0.15,
        max_structure_loss=100_000,
        kite=None,
        dry_run_fallback_margin=True,
    )
    assert result["approved"] is False
    assert result["stage"] == "structure_loss"