from collections import deque
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.external_signals import evaluate_side, leg_key, iter_legs
from app.options_legs_engine import OptionsLegsEngine, SPARKLINE_LEN


def _sample_sheet():
    return {
        "date": "2026-06-15",
        "indices": {
            "NIFTY": {
                "call": {"entry": 180, "target": 230, "stop_loss": 7, "strike": 23100},
                "put": {"entry": 160, "target": 210, "stop_loss": 7, "strike": 23050},
            },
            "BANKNIFTY": {
                "call": {"entry": 1024, "target": 1200, "stop_loss": 40, "strike": 54800},
                "put": {},
            },
            "SENSEX": {"call": {}, "put": {}},
        },
    }


def test_leg_key_and_iter_legs_order():
    assert leg_key("NIFTY", "call") == "NIFTY_CE"
    assert leg_key("BANKNIFTY", "put") == "BANKNIFTY_PE"

    sheet = _sample_sheet()
    legs = list(iter_legs(sheet))
    assert len(legs) == 6
    assert legs[0][0] == "SENSEX_CE"
    assert legs[1][0] == "SENSEX_PE"
    assert legs[2][0] == "NIFTY_CE"
    assert legs[3][0] == "NIFTY_PE"
    assert legs[4][0] == "BANKNIFTY_CE"
    assert legs[5][0] == "BANKNIFTY_PE"


def test_resolve_instruments_mocked():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()
    mock_inst = {
        "tradingsymbol": "NIFTY2561223100CE",
        "instrument_token": 111,
        "exchange": "NFO",
    }

    with patch("app.instruments_manager.instruments_manager") as mgr, patch(
        "app.options_chain.options_chain_manager"
    ) as chain:
        mgr.kite = MagicMock()
        chain.bind = MagicMock()
        chain.resolve_expiry.return_value = date(2026, 6, 12)
        mgr.get_option_instruments.return_value = mock_inst

        resolved = engine.resolve_instruments(sheet, force=True)

    assert len(resolved) == 3
    assert resolved[0]["leg_id"] == "NIFTY_CE"
    assert resolved[0]["token"] == 111
    assert resolved[0]["strike"] == 23100.0
    assert resolved[0]["option_type"] == "CE"


def test_tick_from_ws_updates_ltp_and_sparkline():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()

    engine._resolved = [{
        "leg_id": "NIFTY_CE",
        "index": "NIFTY",
        "leg": "call",
        "option_type": "CE",
        "strike": 23100.0,
        "token": 111,
        "tradingsymbol": "NIFTY2561223100CE",
        "exchange": "NFO",
        "expiry": "2026-06-12",
    }]
    engine._resolve_cache_key = engine._sheet_resolve_key(sheet)
    engine._token_by_leg = {"NIFTY_CE": 111}

    ws = MagicMock()
    ws.get_last_price_with_age.return_value = (195.5, 1.2)

    out = engine.tick_from_ws(ws, sheet)
    side = out["indices"]["NIFTY"]["call"]
    assert side["last_ltp"] == 195.5
    assert side["session_high"] == 195.5
    assert side["session_low"] == 195.5
    assert list(engine._sparklines["NIFTY_CE"]) == [195.5]
    assert engine._data_source["NIFTY_CE"] == "WS"


def test_build_snapshots_shape():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()
    engine._data_source["NIFTY_CE"] = "WS"
    engine._data_age["NIFTY_CE"] = 2.0
    engine._sparklines["NIFTY_CE"] = deque([190.0, 195.0], maxlen=SPARKLINE_LEN)

    with patch.object(engine, "resolve_instruments", return_value=[{
        "leg_id": "NIFTY_CE",
        "tradingsymbol": "NIFTY2561223100CE",
        "token": 111,
        "expiry": "2026-06-12",
        "exchange": "NFO",
    }]), patch("app.options_legs_engine.apply_pnl_to_sheet", side_effect=lambda s: s):
        snaps = engine.build_snapshots(sheet)

    assert "NIFTY_CE" in snaps
    snap = snaps["NIFTY_CE"]
    assert snap["leg_id"] == "NIFTY_CE"
    assert snap["option_type"] == "CE"
    assert snap["strike"] == 23100
    assert snap["data_source"] == "WS"
    assert snap["sparkline"] == [190.0, 195.0]
    assert snap["tradingsymbol"] == "NIFTY2561223100CE"


def test_run_evaluation_prefers_ws_then_evaluates():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()
    engine._token_by_leg = {"NIFTY_CE": 111}

    ws = MagicMock()
    ws.get_last_price_with_age.return_value = (183.0, 0.5)

    with patch("app.options_legs_engine.fetch_live_premiums") as fetch_prem, patch(
        "app.options_legs_engine.external_signals_store"
    ) as store:
        fetch_prem.return_value = {
            "available": True,
            "indices": {
                "NIFTY": {"call_ltp": 180.0, "put_ltp": 160.0, "expiry": "2026-06-12"},
            },
        }
        store.save.side_effect = lambda s: s

        updated, premiums = engine.run_evaluation(sheet, ws_feed=ws, persist=True)

    call = updated["indices"]["NIFTY"]["call"]
    assert call["journal_status"] == "entered"
    assert call["last_ltp"] == 183.0
    assert engine._data_source["NIFTY_CE"] == "WS"
    assert premiums["available"] is True


def test_run_evaluation_skips_rest_when_ws_covers_all_journal_legs():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()
    engine._token_by_leg = {
        "NIFTY_CE": 111,
        "NIFTY_PE": 112,
        "BANKNIFTY_CE": 113,
    }

    ws = MagicMock()
    ws.get_last_price_with_age.return_value = (183.0, 0.5)

    with patch("app.options_legs_engine.fetch_live_premiums") as fetch_prem:
        updated, premiums = engine.run_evaluation(sheet, ws_feed=ws, persist=False)

    fetch_prem.assert_not_called()
    assert premiums.get("source") == "ws"
    assert updated["indices"]["NIFTY"]["call"]["journal_status"] == "entered"


def test_run_evaluation_rest_fallback():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()

    with patch("app.options_legs_engine.fetch_live_premiums") as fetch_prem:
        fetch_prem.return_value = {
            "available": True,
            "indices": {
                "NIFTY": {"call_ltp": 183.0, "put_ltp": None, "expiry": "2026-06-12"},
            },
        }
        updated, _ = engine.run_evaluation(sheet, ws_feed=None, persist=False)

    call = updated["indices"]["NIFTY"]["call"]
    assert call["journal_status"] == "entered"
    assert engine._data_source["NIFTY_CE"] == "REST"


def test_on_sheet_saved_busts_cache():
    engine = OptionsLegsEngine()
    engine._resolved = [{"leg_id": "NIFTY_CE", "token": 1}]
    engine._resolve_cache_key = "cached"

    with patch.object(engine, "refresh_from_sheet") as refresh:
        engine.on_sheet_saved(_sample_sheet())

    assert engine._resolved == []
    assert engine._resolve_cache_key is None
    refresh.assert_called_once()


def test_get_status_payload_returns_defensive_copy():
    from app.options_legs_engine import options_legs_engine

    engine = options_legs_engine
    sheet = _sample_sheet()
    sheet["indices"]["NIFTY"]["call"]["last_ltp"] = 195.0
    engine._data_source["NIFTY_CE"] = "WS"
    engine.update_all_snapshots(sheet)

    payload = engine.get_status_payload()
    payload["legs"]["NIFTY_CE"]["last_ltp"] = 999.0

    fresh = engine.get_status_payload()
    assert fresh["legs"]["NIFTY_CE"]["last_ltp"] == 195.0


def test_refresh_ws_subscriptions_merges_tokens():
    engine = OptionsLegsEngine()
    sheet = _sample_sheet()
    ws = MagicMock()

    with patch.object(engine, "resolve_instruments", return_value=[
        {"token": 201}, {"token": 202},
    ]):
        merged = engine.refresh_ws_subscriptions(ws, sheet, futures_tokens=[101, 102, 201])

    assert merged == [101, 102, 201, 202]
    ws.subscribe.assert_called_once_with([101, 102, 201, 202])