import json
from pathlib import Path

from app.external_signals import (
    ExternalSignalsStore,
    _normalize_sheet,
    build_journal_rows,
    evaluate_side,
)


def test_save_and_load_sheet(tmp_path):
    store = ExternalSignalsStore(path=tmp_path / "signals.json")
    sheet = {
        "date": "2026-06-11",
        "notes": "brother sheet",
        "indices": {
            "NIFTY": {
                "call": {"entry": 180, "target": 230, "stop_loss": 7, "strike": 23100, "status": "Ready"},
                "put": {"entry": 160, "target": 210, "stop_loss": 7, "strike": 23100, "status": "Ready"},
            },
        },
    }
    saved = store.save(sheet)
    assert saved["indices"]["NIFTY"]["call"]["entry"] == 180
    loaded = store.get("2026-06-11")
    assert loaded["indices"]["NIFTY"]["call"]["strike"] == 23100
    assert loaded["notes"] == "brother sheet"
    dates = store.list_dates()
    assert "2026-06-11" in dates


def test_normalize_coerces_numbers():
    raw = {
        "date": "2026-06-11",
        "indices": {
            "SENSEX": {
                "call": {"entry": "225", "strike": "73600"},
                "put": {},
            },
        },
    }
    norm = _normalize_sheet(raw)
    assert norm["indices"]["SENSEX"]["call"]["entry"] == 225.0
    assert norm["indices"]["SENSEX"]["call"]["strike"] == 73600.0


def _leg(entry=180, target=230, stop_loss=7, strike=23100):
    return {
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "strike": strike,
        "journal_status": "watching",
    }


def test_evaluate_side_watching():
    side = _leg()
    out = evaluate_side(side, 195.0, "2026-06-11T10:00:00")
    assert out["journal_status"] == "watching"
    assert out["last_ltp"] == 195.0
    assert "Watching" in out["outcome_note"]


def test_evaluate_side_enters_within_2pct():
    side = _leg(entry=180)
    out = evaluate_side(side, 183.0, "2026-06-11T10:05:00")
    assert out["journal_status"] == "entered"
    assert out["entry_fill"] == 183.0
    assert out["entered_at"] == "2026-06-11T10:05:00"


def test_evaluate_side_target_met():
    side = _leg()
    side["journal_status"] = "entered"
    side["entry_fill"] = 180.0
    side["session_high"] = 228.0
    side["session_low"] = 175.0
    out = evaluate_side(side, 232.0, "2026-06-11T11:00:00")
    assert out["journal_status"] == "target_met"
    assert out["session_high"] == 232.0
    assert out["target_met_at"] == "2026-06-11T11:00:00"


def test_evaluate_side_stop_hit():
    side = _leg(stop_loss=7)
    side["journal_status"] = "entered"
    side["entry_fill"] = 180.0
    side["session_high"] = 190.0
    side["session_low"] = 172.0
    out = evaluate_side(side, 172.0, "2026-06-11T11:30:00")
    assert out["journal_status"] == "stop_hit"
    assert out["stop_hit_at"] == "2026-06-11T11:30:00"


def test_build_journal_rows_skips_empty():
    sheet = {
        "date": "2026-06-11",
        "indices": {
            "NIFTY": {
                "call": _leg(),
                "put": {"entry": None, "strike": None},
            },
            "BANKNIFTY": {"call": {}, "put": {}},
            "SENSEX": {"call": {}, "put": {}},
        },
    }
    rows = build_journal_rows(sheet)
    assert len(rows) == 1
    assert rows[0]["option_type"] == "CE"
    assert rows[0]["strike"] == 23100


def test_build_journal_rows_includes_partial_levels():
    sheet = {
        "date": "2026-06-12",
        "indices": {
            "NIFTY": {
                "call": {"entry": None, "target": 230, "stop_loss": 7, "strike": 23100},
                "put": {"entry": 160, "target": None, "stop_loss": None, "strike": None},
            },
            "BANKNIFTY": {"call": {}, "put": {}},
            "SENSEX": {"call": {}, "put": {}},
        },
    }
    rows = build_journal_rows(sheet)
    assert len(rows) == 2
    assert {r["option_type"] for r in rows} == {"CE", "PE"}


def test_journal_for_date_all_indices(tmp_path):
    store = ExternalSignalsStore(path=tmp_path / "signals.json")
    sheet = {
        "date": "2026-06-12",
        "indices": {
            "NIFTY": {"call": _leg(strike=23100), "put": _leg(entry=160, strike=23098)},
            "BANKNIFTY": {"call": _leg(entry=1024, strike=54800), "put": _leg(entry=856, strike=54800)},
            "SENSEX": {"call": _leg(entry=225, strike=73600), "put": _leg(entry=206, strike=73597)},
        },
    }
    store.save(sheet)
    rows = store.journal_for_date("2026-06-12")
    assert len(rows) == 6
    assert {r["index"] for r in rows} == {"NIFTY", "BANKNIFTY", "SENSEX"}
    assert store.journal(trade_date="2026-06-12") == rows


def test_delete_sheet(tmp_path):
    store = ExternalSignalsStore(path=tmp_path / "signals.json")
    store.save({"date": "2026-06-12", "indices": {"NIFTY": {"call": _leg(), "put": {}}}})
    assert store.delete("2026-06-12")
    assert store.list_dates() == []
    assert not store.delete("2026-06-12")


def test_journal_filter_by_date(tmp_path):
    store = ExternalSignalsStore(path=tmp_path / "signals.json")
    store.save({"date": "2026-06-11", "indices": {"NIFTY": {"call": _leg(), "put": {}}}})
    store.save({
        "date": "2026-06-12",
        "indices": {
            "BANKNIFTY": {"call": _leg(entry=1024, strike=54800), "put": {}},
            "NIFTY": {"call": {}, "put": {}},
            "SENSEX": {"call": {}, "put": {}},
        },
    })
    rows = store.journal(trade_date="2026-06-12")
    assert len(rows) == 1
    assert rows[0]["index"] == "BANKNIFTY"