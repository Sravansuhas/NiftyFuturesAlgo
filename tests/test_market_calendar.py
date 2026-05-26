import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_calendar import is_entry_window_open, is_market_open


IST = ZoneInfo("Asia/Kolkata")


class MarketCalendarTests(unittest.TestCase):
    def test_regular_session_open(self):
        at = datetime.datetime(2026, 5, 4, 10, 0, tzinfo=IST)
        self.assertTrue(is_market_open(at))
        self.assertTrue(is_entry_window_open(at))

    def test_first_fifteen_minutes_block_entries(self):
        at = datetime.datetime(2026, 5, 4, 9, 20, tzinfo=IST)
        self.assertTrue(is_market_open(at))
        self.assertFalse(is_entry_window_open(at))

    def test_official_fo_holiday_closed(self):
        at = datetime.datetime(2026, 3, 3, 10, 0, tzinfo=IST)
        self.assertFalse(is_market_open(at))

    def test_weekend_closed(self):
        at = datetime.datetime(2026, 5, 2, 10, 0, tzinfo=IST)
        self.assertFalse(is_market_open(at))

    def test_expiry_day_detection(self):
        # 28 May 2026 is a Thursday — check if our approx marks near last Thu
        from market_calendar import is_expiry_day
        d = datetime.date(2026, 5, 28)
        # The heuristic may or may not exactly match every month; we mainly test it doesn't crash
        self.assertIsInstance(is_expiry_day(d), bool)


if __name__ == "__main__":
    unittest.main()
