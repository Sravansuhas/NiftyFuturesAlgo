import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fo_guard_status import build_symbol_guard_snapshot
from app.fo_rules_engine import FORulesEngine


class FoGuardStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine = FORulesEngine()

    def test_chop_veto_shows_in_active_guards(self):
        ctx = {
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
            "trend": "ranging",
            "volatility": "low",
            "adx_proxy": 15.0,
            "chop_score": 0.75,
            "rolling_expectancy": 1.0,
            "rolling_edge_trade_count": 2,
            "rolling_edge_sufficient": False,
            "hours_to_high_impact_event": 100.0,
            "safe_trading_window": True,
        }
        snap = build_symbol_guard_snapshot("NIFTY", ctx, engine=self.engine)
        self.assertFalse(snap["allowed"])
        self.assertEqual(snap["blocked_rule"], "FO_CHOP_VETO")
        guard_ids = [g["id"] for g in snap["active_guards"]]
        self.assertIn("FO_CHOP_VETO", guard_ids)


if __name__ == "__main__":
    unittest.main()