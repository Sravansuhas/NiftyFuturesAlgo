import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_calendar import IST
from app.session_tracker import SessionTracker, _compute_session_quality


class SessionTrackerTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.tracker = SessionTracker(
            sessions_dir=base / "sessions",
            ledger_path=base / "trade_ledger.jsonl",
            audit_path=base / "audit_events.json",
        )
        self.target_date = "2026-06-10"
        self._ts = datetime(2026, 6, 10, 11, 30, 0, tzinfo=IST).timestamp()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_ledger(self, events):
        lines = [json.dumps(e) for e in events]
        self.tracker.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_audit(self, events):
        lines = [json.dumps(e) for e in events]
        self.tracker.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_build_daily_session_report_parses_events(self):
        self._write_ledger([
            {
                "ts": self._ts,
                "event_type": "order.placed",
                "payload": {"symbol": "NIFTY", "side": "BUY", "quantity": 65, "dry_run": True},
            },
            {
                "ts": self._ts + 60,
                "event_type": "order.exit",
                "payload": {"symbol": "NIFTY", "side": "SELL", "quantity": 65},
            },
            {
                "ts": self._ts + 120,
                "event_type": "emergency.halt",
                "payload": {"reason": "test"},
            },
        ])
        self._write_audit([
            {
                "ts": self._ts + 30,
                "event_type": "order.blocked",
                "payload": {"symbol": "NIFTY", "reason": "risk_gate"},
            },
            {
                "ts": self._ts + 90,
                "event_type": "order.paper_multi",
                "payload": {
                    "symbol": "NIFTY",
                    "is_exit": True,
                    "realized_pnl": 1500.0,
                },
            },
        ])

        report = self.tracker.build_daily_session_report(self.target_date)

        self.assertEqual(report["date_ist"], self.target_date)
        self.assertEqual(report["trades"]["placed"], 1)
        self.assertEqual(report["trades"]["exited"], 1)
        self.assertEqual(report["blockers"]["count"], 1)
        self.assertEqual(len(report["halts"]), 1)
        self.assertEqual(report["per_symbol_pnl"]["NIFTY"], 1500.0)
        self.assertLess(report["session_quality"], 100)

    def test_save_and_load_daily_report(self):
        report = {
            "date_ist": self.target_date,
            "session_quality": 88,
            "trades": {"placed": 2, "exited": 1},
        }
        path = self.tracker.save_daily_report(report)
        self.assertTrue(path.exists())

        loaded = self.tracker.load_daily_report(self.target_date)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["session_quality"], 88)
        self.assertIsNone(self.tracker.load_daily_report("2099-01-01"))

    def test_get_session_streak_counts_consecutive_days(self):
        for offset in (0, 1, 2):
            day = f"2026-06-{10 - offset:02d}"
            self.tracker.save_daily_report({
                "date_ist": day,
                "session_quality": 90 - offset,
                "trades": {"placed": 1},
                "blockers": {"count": 0},
            })

        with patch("app.session_tracker.now_ist") as mock_now:
            mock_now.return_value = datetime(2026, 6, 10, 18, 0, 0, tzinfo=IST)
            streak = self.tracker.get_session_streak()

        self.assertEqual(streak["consecutive_days_with_reports"], 3)
        self.assertEqual(len(streak["last_7_days"]), 7)
        self.assertTrue(streak["last_7_days"][0]["has_report"])

    def test_session_quality_heuristic(self):
        self.assertEqual(_compute_session_quality(0, 0), 100)
        self.assertLess(_compute_session_quality(3, 1), 70)


if __name__ == "__main__":
    unittest.main()