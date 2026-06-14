import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fo_rules_engine import FORulesEngine


IST = ZoneInfo("Asia/Kolkata")


class FORulesEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = FORulesEngine()

    def _clean_entry_context(self, **overrides):
        at = datetime.datetime(2026, 5, 4, 11, 0, tzinfo=IST)
        base = {
            "has_hard_stop_loss": True,
            "uses_mental_stop": False,
            "broker_connected": True,
            "has_open_position": False,
            "consecutive_losses": 0,
            "seconds_since_last_loss": 9999,
            "trades_today": 1,
            "expected_slippage_bps": 5.0,
            "paper_live_fill_divergence_bps": 5.0,
            "is_paper_mode": True,
            "is_breakout_entry": True,
            "trend": "uptrend",
            "volatility": "normal",
            "adx_proxy": 30.0,
            "chop_score": 0.20,
            "rolling_expectancy": 1.0,
            "rolling_edge_trade_count": 0,
            "rolling_edge_sufficient": False,
            "rolling_edge_halt": False,
            "safe_trading_window": True,
            "is_safe_trading_window": True,
            "at": at,
        }
        base.update(overrides)
        return base

    def test_blocks_without_hard_stop_loss(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            {"has_hard_stop_loss": False, "uses_mental_stop": False, "broker_connected": True},
        )
        self.assertFalse(allowed)
        self.assertIn("FO_HARD_SL_REQUIRED", reason)
        self.assertEqual(mult, 1.0)

    def test_blocks_revenge_cooldown(self):
        allowed, reason, _ = self.engine.check_entry(
            "NIFTY26JUNFUT",
            {
                "has_hard_stop_loss": True,
                "uses_mental_stop": False,
                "broker_connected": True,
                "consecutive_losses": 1,
                "seconds_since_last_loss": 60,
                "trades_today": 0,
            },
        )
        self.assertFalse(allowed)
        self.assertIn("FO_REVENGE_TRADING_COOLDOWN", reason)

    def test_de_risks_high_slippage(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            {
                "has_hard_stop_loss": True,
                "uses_mental_stop": False,
                "broker_connected": True,
                "consecutive_losses": 0,
                "seconds_since_last_loss": 9999,
                "trades_today": 0,
                "expected_slippage_bps": 18.0,
                "is_safe_trading_window": True,
                "is_expiry_day": False,
                "is_paper_mode": True,
            },
        )
        self.assertTrue(allowed)
        self.assertIn("FO_SLIPPAGE_BUDGET", reason)
        self.assertLess(mult, 1.0)

    def test_blocks_within_pre_event_window(self):
        at = datetime.datetime(2026, 6, 5, 7, 0, tzinfo=IST)
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            {
                "has_hard_stop_loss": True,
                "uses_mental_stop": False,
                "broker_connected": True,
                "has_open_position": False,
                "consecutive_losses": 0,
                "seconds_since_last_loss": 9999,
                "trades_today": 1,
                "expected_slippage_bps": 5.0,
                "paper_live_fill_divergence_bps": 5.0,
                "is_paper_mode": True,
                "at": at,
            },
        )
        self.assertFalse(allowed)
        self.assertIn("FO_EVENT_CALENDAR", reason)
        self.assertEqual(mult, 1.0)

    def test_allows_outside_pre_event_window(self):
        at = datetime.datetime(2026, 5, 4, 11, 0, tzinfo=IST)
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            {
                "has_hard_stop_loss": True,
                "uses_mental_stop": False,
                "broker_connected": True,
                "has_open_position": False,
                "consecutive_losses": 0,
                "seconds_since_last_loss": 9999,
                "trades_today": 1,
                "expected_slippage_bps": 5.0,
                "paper_live_fill_divergence_bps": 5.0,
                "is_paper_mode": True,
                "safe_trading_window": True,
                "is_safe_trading_window": True,
                "at": at,
            },
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "")
        self.assertEqual(mult, 1.0)

    def test_allows_clean_context(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(),
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "")
        self.assertEqual(mult, 1.0)

    def test_blocks_chop_veto_on_ranging_low_adx(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(trend="ranging", adx_proxy=18.0, chop_score=0.35),
        )
        self.assertFalse(allowed)
        self.assertIn("FO_CHOP_VETO", reason)
        self.assertEqual(mult, 1.0)

    def test_blocks_chop_veto_on_ranging_low_volatility(self):
        allowed, reason, _ = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(
                trend="ranging",
                volatility="low",
                adx_proxy=28.0,
                chop_score=0.40,
            ),
        )
        self.assertFalse(allowed)
        self.assertIn("FO_CHOP_VETO", reason)

    def test_blocks_chop_veto_on_ranging_high_chop_score(self):
        allowed, reason, _ = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(
                trend="ranging",
                volatility="normal",
                adx_proxy=28.0,
                chop_score=0.72,
            ),
        )
        self.assertFalse(allowed)
        self.assertIn("FO_CHOP_VETO", reason)

    def test_de_risks_outside_safe_trading_window(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(
                safe_trading_window=False,
                is_safe_trading_window=False,
            ),
        )
        self.assertTrue(allowed)
        self.assertIn("FO_OPENING_AUCTION_WINDOW", reason)
        self.assertLess(mult, 1.0)

    def test_allows_ranging_when_directional_strength_present(self):
        allowed, reason, mult = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._clean_entry_context(
                trend="ranging",
                volatility="normal",
                adx_proxy=28.0,
                chop_score=0.45,
            ),
        )
        self.assertTrue(allowed)
        self.assertNotIn("FO_CHOP_VETO", reason)
        self.assertEqual(mult, 1.0)


if __name__ == "__main__":
    unittest.main()