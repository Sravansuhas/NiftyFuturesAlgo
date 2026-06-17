"""Dashboard options event wiring — recent_execution mapping and /api/trades filter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.trade_ledger import TradeLedger


class DashboardOptionsEventMappingTests(unittest.TestCase):
    def test_map_options_structure_open(self):
        from web.dashboard import _map_ledger_event_to_recent_exec

        event = {
            "ts": 1710000000.0,
            "event_type": "options.structure.open",
            "payload": {
                "structure_id": "IC-NIFTY-001",
                "underlying": "NIFTY",
                "credit": 12500.0,
                "legs": 4,
            },
        }
        mapped = _map_ledger_event_to_recent_exec(event)
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped["type"], "options.structure.open")
        self.assertEqual(mapped["symbol"], "NIFTY")
        self.assertEqual(mapped["side"], "OPEN")
        self.assertEqual(mapped["price"], 12500.0)
        self.assertEqual(mapped["structure_id"], "IC-NIFTY-001")

    def test_map_options_cycle_skip_with_details(self):
        from web.dashboard import _map_ledger_event_to_recent_exec

        event = {
            "ts": 1710000001.0,
            "event_type": "options.cycle.skip",
            "payload": {
                "reason": "regime_gate",
                "details": ["VIX above max_vix", "expiry caution"],
                "underlying": "NIFTY",
            },
        }
        mapped = _map_ledger_event_to_recent_exec(event)
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped["side"], "SKIP")
        self.assertIn("regime_gate", mapped["reason"] or "")
        self.assertIn("VIX above max_vix", mapped["reason"] or "")

    def test_build_recent_execution_includes_options_events(self):
        from web.dashboard import _build_recent_execution_from_ledger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            ledger.record("signal.accepted", {"side": "BUY", "symbol": "NIFTY", "price": 24500})
            ledger.record("options.cycle.skip", {"reason": "open_structure_exists", "underlying": "NIFTY"})
            ledger.record("options.structure.open", {
                "structure_id": "IC-1",
                "underlying": "NIFTY",
                "credit": 9000,
                "legs": 4,
            })

            with patch("app.trade_ledger.trade_ledger", ledger):
                recent = _build_recent_execution_from_ledger(5)

        types = [e["type"] for e in recent]
        self.assertIn("options.structure.open", types)
        self.assertIn("options.cycle.skip", types)


class OptionsCycleFailLedgerTests(unittest.TestCase):
    def test_finish_options_cycle_records_fail(self):
        from app.options_strategy_runner import _finish_options_cycle

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            with patch("app.trade_ledger.trade_ledger", ledger):
                with patch("app.options_strategy_runner.get_options_config") as mock_cfg:
                    mock_cfg.return_value = {"underlying": "NIFTY"}
                    _finish_options_cycle({
                        "action": "open",
                        "success": False,
                        "message": "Dry run rejected by position accounting",
                        "stage": "order",
                        "leg_results": [
                            {"tradingsymbol": "NIFTY26JUN24500CE", "success": True},
                            {"tradingsymbol": "NIFTY26JUN24600CE", "success": False},
                        ],
                    })

            events = ledger.tail(5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "options.cycle.fail")
        self.assertEqual(events[0]["payload"]["action"], "open")
        self.assertIn("position accounting", events[0]["payload"]["message"])

    def test_map_options_cycle_fail(self):
        from web.dashboard import _map_ledger_event_to_recent_exec

        event = {
            "ts": 1710000002.0,
            "event_type": "options.cycle.fail",
            "payload": {
                "action": "open",
                "message": "Leg order failed",
                "underlying": "NIFTY",
            },
        }
        mapped = _map_ledger_event_to_recent_exec(event)
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertEqual(mapped["side"], "FAIL")
        self.assertIn("open", mapped["reason"] or "")


class OptionsCycleSkipLedgerTests(unittest.TestCase):
    def test_finish_options_cycle_records_skip(self):
        from app.options_strategy_runner import _finish_options_cycle

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            with patch("app.trade_ledger.trade_ledger", ledger):
                with patch("app.options_strategy_runner.get_options_config") as mock_cfg:
                    mock_cfg.return_value = {"underlying": "NIFTY"}
                    _finish_options_cycle({
                        "skipped": True,
                        "reason": "regime_gate",
                        "details": ["Expiry day — no new entries after 12:00 IST (gamma caution)"],
                        "trigger_type": "calendar_hard",
                        "gamma_caution_level": 2,
                        "expiry_triggers": ["calendar_hard"],
                        "is_expiry_day": True,
                        "expiry_caution": False,
                        "underlying": "NIFTY",
                    })

            events = ledger.tail(5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "options.cycle.skip")
        self.assertEqual(events[0]["payload"]["reason"], "regime_gate")
        self.assertEqual(events[0]["payload"]["trigger_type"], "calendar_hard")
        self.assertEqual(events[0]["payload"]["gamma_caution_level"], 2)
        self.assertEqual(events[0]["payload"]["expiry_triggers"], ["calendar_hard"])
        self.assertTrue(events[0]["payload"]["is_expiry_day"])


_HAS_TEST_CLIENT = False
_TEST_CLIENT = None
try:
    from fastapi.testclient import TestClient
    from web.dashboard import app as _dashboard_app

    _TEST_CLIENT = TestClient(_dashboard_app)
    _HAS_TEST_CLIENT = True
except Exception:
    pass


@unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
class DashboardTradesApiOptionsTests(unittest.TestCase):
    def test_trades_api_includes_options_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            ledger.record("options.structure.close", {
                "structure_id": "IC-9",
                "underlying": "NIFTY",
                "credit": 8000,
                "legs": 4,
                "reason": "profit_target",
            })
            with patch("app.trade_ledger.trade_ledger", ledger):
                res = _TEST_CLIENT.get("/api/trades?limit=10")

        self.assertEqual(res.status_code, 200)
        data = res.json()
        types = [e.get("event_type") for e in data.get("trades", [])]
        self.assertIn("options.structure.close", types)


class DashboardTodayFilterTests(unittest.TestCase):
    def test_trades_api_filters_by_today(self):
        from app.market_calendar import now_ist

        today = now_ist().strftime("%Y-%m-%d")
        from datetime import timedelta
        yesterday = (now_ist().date() - timedelta(days=1)).isoformat()

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            ledger.record("options.cycle.skip", {"reason": "old", "underlying": "NIFTY"})
            with ledger.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    '{"ts": 1710000000.0, "event_type": "options.structure.open", '
                    f'"date_ist": "{yesterday}", '
                    '"payload": {"structure_id": "IC-OLD", "underlying": "NIFTY", "credit": 1000, "legs": 4}}\n'
                )
            ledger.record("options.structure.close", {
                "structure_id": "IC-TODAY",
                "underlying": "NIFTY",
                "credit": 2000,
                "legs": 4,
                "reason": "profit_target",
            })

            with patch("app.trade_ledger.trade_ledger", ledger):
                if not _HAS_TEST_CLIENT:
                    events = ledger.read_events_today(limit=10, event_types=["options.structure.close"])
                    types = [e.get("event_type") for e in events]
                    self.assertIn("options.structure.close", types)
                    self.assertNotIn("options.structure.open", types)
                    return
                res = _TEST_CLIENT.get(f"/api/trades?limit=10&date={today}")

        self.assertEqual(res.status_code, 200)
        data = res.json()
        types = [e.get("event_type") for e in data.get("trades", [])]
        self.assertIn("options.structure.close", types)
        self.assertNotIn("options.structure.open", types)


if __name__ == "__main__":
    unittest.main()