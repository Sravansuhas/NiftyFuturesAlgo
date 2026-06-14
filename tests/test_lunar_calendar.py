import datetime
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.lunar_calendar import (
    astronomical_phase,
    build_lunar_context,
    build_lunar_day,
    build_lunar_range,
    list_lunar_events,
    mumbai_sunrise,
    panchang_at_sunrise,
)


class LunarCalendarTests(unittest.TestCase):
    def test_mumbai_sunrise_is_ist_morning(self):
        sr = mumbai_sunrise(datetime.date(2026, 6, 15))
        self.assertEqual(sr.tzinfo.key, "Asia/Kolkata")
        self.assertGreaterEqual(sr.hour, 5)
        self.assertLessEqual(sr.hour, 7)

    def test_panchang_fields_present(self):
        p = panchang_at_sunrise(datetime.date(2026, 6, 15))
        self.assertIn(p["paksha"], ("shukla", "krishna"))
        self.assertGreaterEqual(p["tithi_num"], 1)
        self.assertLessEqual(p["tithi_num"], 30)
        self.assertIn("caveat", p)

    def test_astronomical_phase_range(self):
        a = astronomical_phase(datetime.date(2026, 6, 15))
        self.assertGreaterEqual(a["illumination_pct"], 0.0)
        self.assertLessEqual(a["illumination_pct"], 100.0)
        self.assertIn(a["dichev_bucket"], ("near_new", "near_full"))

    def test_build_lunar_day_trading_context(self):
        day = build_lunar_day(datetime.date(2026, 3, 3))  # Holi holiday
        tc = day["trading_context"]
        self.assertFalse(tc["is_trading_session"])
        self.assertTrue(tc["is_trading_holiday"])
        self.assertTrue(day["is_analysis_excluded"])

    def test_muhurat_trading_flagged(self):
        day = build_lunar_day(datetime.date(2026, 11, 8))
        self.assertTrue(day["trading_context"]["is_muhurat_trading"])
        self.assertTrue(day["is_analysis_excluded"])

    def test_session_hints_research_only(self):
        day = build_lunar_day(datetime.date(2026, 6, 15))
        hints = day["session_hints"]
        self.assertTrue(hints["research_only"])
        self.assertIn("folklore_tag", hints)

    def test_event_windows_keys(self):
        payload = build_lunar_range(
            datetime.date(2026, 1, 1),
            datetime.date(2026, 3, 31),
        )
        self.assertGreater(len(payload["days"]), 0)
        sample = payload["days"][10]
        ew = sample.get("event_windows", {})
        self.assertIn("in_new_moon_window_3d", ew)
        self.assertIn("in_full_moon_window_3d", ew)
        self.assertIn("in_new_moon_window_5d", ew)

    def test_list_lunar_events_in_range(self):
        events = list_lunar_events(
            datetime.date(2026, 1, 1),
            datetime.date(2026, 12, 31),
        )
        phases = {e["phase"] for e in events}
        self.assertIn("synodic_new_moon", phases)
        self.assertIn("synodic_full_moon", phases)
        self.assertGreater(len(events), 20)

    def test_build_lunar_context_available(self):
        payload = build_lunar_context(
            for_date=datetime.date(2026, 6, 15),
            refresh=True,
        )
        self.assertTrue(payload["available"])
        self.assertEqual(payload["date_ist"], "2026-06-15")
        self.assertIn("panchang", payload)
        self.assertIn("astronomical", payload)


if __name__ == "__main__":
    unittest.main()