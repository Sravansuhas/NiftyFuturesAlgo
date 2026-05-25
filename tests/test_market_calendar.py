import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from market_calendar import is_entry_window_open, is_market_open


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


if __name__ == "__main__":
    unittest.main()
