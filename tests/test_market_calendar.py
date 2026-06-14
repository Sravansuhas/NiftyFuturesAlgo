import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_calendar import (
    NSE_FO_EXTENDED_SESSION_EFFECTIVE_DATE,
    get_eod_flatten_defaults,
    get_entry_window_end,
    get_event_calendar_status,
    get_hours_to_high_impact_event,
    get_market_status,
    get_next_high_impact_event,
    get_nse_fo_market_close,
    get_safe_trading_window_end,
    is_eod_flatten_window,
    is_entry_window_open,
    is_market_open,
    is_real_market_open,
    is_safe_trading_window,
    is_within_pre_event_block_window,
    load_market_events,
    uses_extended_nse_fo_session,
)


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

    def test_adhoc_holiday_jan_15_2026(self):
        from app.market_calendar import is_trading_holiday, next_trading_day
        d = datetime.date(2026, 1, 15)
        self.assertTrue(is_trading_holiday(d))
        nxt = next_trading_day(d)
        self.assertGreater(nxt, d)

    def test_weekend_closed(self):
        at = datetime.datetime(2026, 5, 2, 10, 0, tzinfo=IST)
        self.assertFalse(is_market_open(at))

    def test_weekly_expiry_tuesday_nifty(self):
        from app.market_calendar import is_weekly_expiry_day, is_monthly_expiry_day

        tue = datetime.date(2026, 6, 9)  # Tuesday, not monthly expiry
        self.assertEqual(tue.weekday(), 1)
        self.assertTrue(is_weekly_expiry_day(tue, underlying="NIFTY"))
        self.assertFalse(is_weekly_expiry_day(tue, underlying="BANKNIFTY"))
        self.assertFalse(is_monthly_expiry_day(tue, underlying="NIFTY"))

    def test_expiry_day_detection(self):
        from app.market_calendar import get_monthly_expiry_for_month, is_expiry_day

        nifty_june = get_monthly_expiry_for_month(2026, 6, "NIFTY")
        self.assertEqual(nifty_june.weekday(), 1)  # last Tuesday
        self.assertTrue(is_expiry_day(nifty_june, underlying="NIFTY"))
        self.assertFalse(is_expiry_day(nifty_june - datetime.timedelta(days=1), underlying="NIFTY"))

        sensex_june = get_monthly_expiry_for_month(2026, 6, "SENSEX")
        self.assertEqual(sensex_june.weekday(), 3)  # last Thursday
        self.assertTrue(is_expiry_day(sensex_june, underlying="SENSEX"))
        self.assertFalse(is_expiry_day(sensex_june, underlying="NIFTY"))

        # May 28 2026 (Bakri Id) shifts SENSEX monthly expiry to prior session
        sensex_may = get_monthly_expiry_for_month(2026, 5, "SENSEX")
        self.assertLess(sensex_may, datetime.date(2026, 5, 28))
        self.assertTrue(is_expiry_day(sensex_may, underlying="SENSEX"))

    def test_dev_fixed_sim_time_ignored_in_live_mode(self):
        import os
        from app.market_calendar import now_ist
        from app.state_machine import SystemState, state_machine

        prev_fixed = os.environ.get("DEV_FIXED_SIM_TIME")
        prev_dev = os.environ.get("DEV_MODE")
        prev_dry = os.environ.get("FORCE_DRY_RUN")
        try:
            os.environ["DEV_FIXED_SIM_TIME"] = "2026-06-02 11:45:00"
            os.environ["DEV_MODE"] = "true"
            os.environ["FORCE_DRY_RUN"] = "false"
            state_machine.set_state(SystemState.LIVE_MODE)
            now = now_ist()
            self.assertNotEqual(now.strftime("%Y-%m-%d %H:%M:%S"), "2026-06-02 11:45:00")
        finally:
            state_machine.set_state(SystemState.PAPER_MODE)
            if prev_fixed is None:
                os.environ.pop("DEV_FIXED_SIM_TIME", None)
            else:
                os.environ["DEV_FIXED_SIM_TIME"] = prev_fixed
            if prev_dev is None:
                os.environ.pop("DEV_MODE", None)
            else:
                os.environ["DEV_MODE"] = prev_dev
            if prev_dry is None:
                os.environ.pop("FORCE_DRY_RUN", None)
            else:
                os.environ["FORCE_DRY_RUN"] = prev_dry

    def test_safe_trading_window(self):
        # Inside safe window
        at = datetime.datetime(2026, 5, 4, 11, 0, tzinfo=IST)
        self.assertTrue(is_safe_trading_window(at))
        # Too early
        at_early = datetime.datetime(2026, 5, 4, 9, 20, tzinfo=IST)
        self.assertFalse(is_safe_trading_window(at_early))
        # Legacy regime: safe window ends 15:15
        at_late_legacy = datetime.datetime(2026, 5, 4, 15, 20, tzinfo=IST)
        self.assertFalse(is_safe_trading_window(at_late_legacy))

    def test_legacy_session_close_before_aug_2026(self):
        pre = datetime.date(2026, 7, 31)  # Friday before effective date
        self.assertFalse(uses_extended_nse_fo_session(pre))
        self.assertEqual(get_nse_fo_market_close(pre), datetime.time(15, 30))
        at_open = datetime.datetime(2026, 7, 31, 15, 30, tzinfo=IST)
        at_closed = datetime.datetime(2026, 7, 31, 15, 31, tzinfo=IST)
        self.assertTrue(is_real_market_open(at_open))
        self.assertFalse(is_real_market_open(at_closed))

    def test_extended_session_close_from_aug_3_2026(self):
        self.assertEqual(NSE_FO_EXTENDED_SESSION_EFFECTIVE_DATE, datetime.date(2026, 8, 3))
        post = datetime.date(2026, 8, 3)
        self.assertTrue(uses_extended_nse_fo_session(post))
        self.assertEqual(get_nse_fo_market_close(post), datetime.time(15, 40))
        at_open = datetime.datetime(2026, 8, 3, 15, 40, tzinfo=IST)
        at_closed = datetime.datetime(2026, 8, 3, 15, 41, tzinfo=IST)
        self.assertTrue(is_real_market_open(at_open))
        self.assertFalse(is_real_market_open(at_closed))

    def test_safe_and_entry_windows_shift_with_extended_session(self):
        legacy = datetime.date(2026, 6, 10)
        extended = datetime.date(2026, 8, 10)
        self.assertEqual(get_safe_trading_window_end(legacy), datetime.time(15, 15))
        self.assertEqual(get_safe_trading_window_end(extended), datetime.time(15, 25))
        self.assertEqual(get_entry_window_end(legacy), datetime.time(15, 0))
        self.assertEqual(get_entry_window_end(extended), datetime.time(15, 10))

        self.assertTrue(is_entry_window_open(datetime.datetime(2026, 6, 10, 15, 0, tzinfo=IST)))
        self.assertFalse(is_entry_window_open(datetime.datetime(2026, 6, 10, 15, 5, tzinfo=IST)))
        self.assertTrue(is_entry_window_open(datetime.datetime(2026, 8, 10, 15, 10, tzinfo=IST)))
        self.assertFalse(is_entry_window_open(datetime.datetime(2026, 8, 10, 15, 15, tzinfo=IST)))

    def test_eod_flatten_window_regime_defaults(self):
        legacy_day = datetime.date(2026, 6, 10)
        extended_day = datetime.date(2026, 8, 10)
        self.assertEqual(get_eod_flatten_defaults(legacy_day), (datetime.time(15, 10), datetime.time(15, 15)))
        self.assertEqual(
            get_eod_flatten_defaults(extended_day),
            (datetime.time(15, 20), datetime.time(15, 25)),
        )

        import os

        prev_start = os.environ.pop("EOD_FLATTEN_START", None)
        prev_end = os.environ.pop("EOD_FLATTEN_END", None)
        try:
            at_legacy = datetime.datetime(2026, 6, 10, 15, 12, tzinfo=IST)
            at_extended = datetime.datetime(2026, 8, 10, 15, 22, tzinfo=IST)
            self.assertTrue(is_eod_flatten_window(at_legacy))
            self.assertFalse(is_eod_flatten_window(at_legacy.replace(hour=15, minute=20)))
            self.assertTrue(is_eod_flatten_window(at_extended))
            self.assertFalse(is_eod_flatten_window(at_extended.replace(hour=15, minute=10)))
        finally:
            if prev_start is None:
                os.environ.pop("EOD_FLATTEN_START", None)
            else:
                os.environ["EOD_FLATTEN_START"] = prev_start
            if prev_end is None:
                os.environ.pop("EOD_FLATTEN_END", None)
            else:
                os.environ["EOD_FLATTEN_END"] = prev_end

    def test_get_market_status_reflects_extended_session(self):
        import os

        prev_fixed = os.environ.get("DEV_FIXED_SIM_TIME")
        prev_dev = os.environ.get("DEV_MODE")
        try:
            os.environ["DEV_FIXED_SIM_TIME"] = "2026-08-10 11:00:00"
            os.environ["DEV_MODE"] = "true"
            status = get_market_status()
            self.assertTrue(status["uses_extended_nse_fo_session"])
            self.assertEqual(status["session_close"], "15:40")
            self.assertEqual(status["safe_window_end"], "15:25")
            self.assertTrue(status["is_market_open"])
        finally:
            if prev_fixed is None:
                os.environ.pop("DEV_FIXED_SIM_TIME", None)
            else:
                os.environ["DEV_FIXED_SIM_TIME"] = prev_fixed
            if prev_dev is None:
                os.environ.pop("DEV_MODE", None)
            else:
                os.environ["DEV_MODE"] = prev_dev

    def test_hours_to_high_impact_event_before_rbi_policy(self):
        at = datetime.datetime(2026, 6, 5, 7, 0, tzinfo=IST)
        hours = get_hours_to_high_impact_event(at)
        self.assertAlmostEqual(hours, 3.0, places=2)
        self.assertTrue(is_within_pre_event_block_window(at, block_hours=4))

    def test_hours_to_high_impact_event_far_from_macro_events(self):
        at = datetime.datetime(2026, 5, 4, 11, 0, tzinfo=IST)
        hours = get_hours_to_high_impact_event(at)
        self.assertGreater(hours, 24.0)
        self.assertFalse(is_within_pre_event_block_window(at, block_hours=4))

    def test_next_high_impact_event_metadata(self):
        at = datetime.datetime(2026, 6, 4, 11, 0, tzinfo=IST)
        nxt = get_next_high_impact_event(at)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt["id"], "RBI_MPC_JUN_2026")
        self.assertEqual(nxt["category"], "rbi_policy")

    def test_event_calendar_status_in_market_status(self):
        from app.market_calendar import get_market_status

        at = datetime.datetime(2026, 6, 5, 8, 30, tzinfo=IST)
        status = get_event_calendar_status(at)
        self.assertTrue(status["within_pre_event_block_window"])
        self.assertLess(status["hours_to_high_impact_event"], 4.0)
        self.assertEqual(status["next_high_impact_event"]["id"], "RBI_MPC_JUN_2026")

    def test_market_events_json_loads(self):
        payload = load_market_events(reload=True)
        self.assertIn("events", payload)
        self.assertGreaterEqual(len(payload["events"]), 6)
        categories = {event["category"] for event in payload["events"]}
        self.assertIn("rbi_policy", categories)
        self.assertIn("union_budget", categories)

    def test_real_market_closed_after_hours_even_with_dev_force(self):
        import os
        at = datetime.datetime(2026, 6, 10, 17, 12, tzinfo=IST)
        self.assertFalse(is_real_market_open(at))
        prev = os.environ.get("DEV_FORCE_MARKET_OPEN")
        prev_dry = os.environ.get("FORCE_DRY_RUN")
        try:
            os.environ["DEV_FORCE_MARKET_OPEN"] = "true"
            os.environ["FORCE_DRY_RUN"] = "true"
            self.assertTrue(is_market_open(at))
            self.assertFalse(is_real_market_open(at))
        finally:
            if prev is None:
                os.environ.pop("DEV_FORCE_MARKET_OPEN", None)
            else:
                os.environ["DEV_FORCE_MARKET_OPEN"] = prev
            if prev_dry is None:
                os.environ.pop("FORCE_DRY_RUN", None)
            else:
                os.environ["FORCE_DRY_RUN"] = prev_dry


if __name__ == "__main__":
    unittest.main()
