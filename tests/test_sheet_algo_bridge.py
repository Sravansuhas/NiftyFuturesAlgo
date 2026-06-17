from copy import deepcopy

from app.external_signals import DEFAULT_SIDE, _empty_sheet
from app.sheet_algo_bridge import (
    build_sheet_vs_algo_scoreboard,
    check_sheet_allows_futures_entry,
)


def _inactive_side():
    side = deepcopy(DEFAULT_SIDE)
    side["journal_status"] = "skipped"
    side["status"] = "skipped"
    return side


def _sheet_ce_only():
    sheet = _empty_sheet()
    sheet["indices"]["NIFTY"]["call"] = {
        **deepcopy(DEFAULT_SIDE),
        "strike": 24000,
        "entry": 100,
        "target": 150,
        "stop_loss": 10,
        "journal_status": "watching",
    }
    sheet["indices"]["NIFTY"]["put"] = _inactive_side()
    return sheet


def _sheet_pe_only():
    sheet = _empty_sheet()
    sheet["indices"]["NIFTY"]["put"] = {
        **deepcopy(DEFAULT_SIDE),
        "strike": 24000,
        "entry": 100,
        "target": 150,
        "stop_loss": 10,
        "journal_status": "entered",
    }
    sheet["indices"]["NIFTY"]["call"] = _inactive_side()
    return sheet


def test_skipped_strike_does_not_affect_bias():
    """Skipped PE with leftover strike must not block CE-only bullish bias."""
    cfg = {"enabled": True, "mode": "filter", "block_on_mismatch": True, "allow_when_empty": True}
    sheet = _sheet_ce_only()
    sheet["indices"]["NIFTY"]["put"] = {
        **_inactive_side(),
        "strike": 24000,
        "entry": 100,
    }
    long_ok = check_sheet_allows_futures_entry("NIFTY", "BUY", sheet=sheet, config=cfg)
    short_ok = check_sheet_allows_futures_entry("NIFTY", "SELL", sheet=sheet, config=cfg)
    assert long_ok.bias == "bullish"
    assert long_ok.allowed is True
    assert short_ok.allowed is False


def test_filter_blocks_short_on_bullish_sheet():
    cfg = {"enabled": True, "mode": "filter", "block_on_mismatch": True, "allow_when_empty": True}
    sheet = _sheet_ce_only()
    long_ok = check_sheet_allows_futures_entry("NIFTY", "BUY", sheet=sheet, config=cfg)
    short_ok = check_sheet_allows_futures_entry("NIFTY", "SELL", sheet=sheet, config=cfg)
    assert long_ok.allowed is True
    assert short_ok.allowed is False
    assert "CE-only" in short_ok.reason or "blocks SHORT" in short_ok.reason


def test_filter_blocks_long_on_bearish_sheet():
    cfg = {"enabled": True, "mode": "filter", "block_on_mismatch": True, "allow_when_empty": True}
    sheet = _sheet_pe_only()
    long_ok = check_sheet_allows_futures_entry("NIFTY", "BUY", sheet=sheet, config=cfg)
    short_ok = check_sheet_allows_futures_entry("NIFTY", "SELL", sheet=sheet, config=cfg)
    assert long_ok.allowed is False
    assert short_ok.allowed is True


def test_advisory_never_blocks():
    cfg = {"enabled": True, "mode": "advisory", "block_on_mismatch": True, "allow_when_empty": True}
    sheet = _sheet_pe_only()
    result = check_sheet_allows_futures_entry("NIFTY", "BUY", sheet=sheet, config=cfg)
    assert result.allowed is True
    assert result.advisory_only is True


def test_confirm_requires_alignment():
    cfg = {"enabled": True, "mode": "confirm", "block_on_mismatch": False, "allow_when_empty": False}
    sheet = _sheet_ce_only()
    assert check_sheet_allows_futures_entry("NIFTY", "BUY", sheet=sheet, config=cfg).allowed is True
    assert check_sheet_allows_futures_entry("NIFTY", "SELL", sheet=sheet, config=cfg).allowed is False


def test_scoreboard_shape():
    board = build_sheet_vs_algo_scoreboard(sheet=_sheet_ce_only())
    assert "per_index" in board
    assert len(board["per_index"]) == 3
    assert board["per_index"][1]["symbol"] == "NIFTY"
    assert board["per_index"][1]["sheet_bias"] == "bullish"