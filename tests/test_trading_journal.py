import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading_journal import TradingJournal


class TradingJournalTests(unittest.TestCase):
    def test_generate_system_feedback_green_day(self):
        journal = TradingJournal()
        session = {
            "quality_score": 88,
            "event_metrics": {"rejection_rate": 0.12, "orders_placed": 3},
            "risk_snapshot": {"daily_pnl": 2500, "trades_today": 3},
        }
        fb = journal.generate_system_feedback(session, [], None, None)
        self.assertIn("headline", fb)
        self.assertTrue(fb["notes"])
        self.assertTrue(fb["actions"])

    def test_add_trader_note_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = TradingJournal(journal_dir=Path(tmp) / "journal")
            entry = j.add_trader_note("Good discipline today", date_ist="2026-06-11")
            self.assertEqual(len(entry["trader_notes"]), 1)
            loaded = j.load_journal("2026-06-11")
            self.assertEqual(loaded["trader_notes"][0]["text"], "Good discipline today")

    def test_build_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = TradingJournal(journal_dir=Path(tmp) / "journal")
            path = j.build_and_save("2026-06-11")
            self.assertTrue(path.exists())
            data = j.load_journal("2026-06-11")
            self.assertEqual(data["date_ist"], "2026-06-11")
            self.assertIn("system_feedback", data)


if __name__ == "__main__":
    unittest.main()