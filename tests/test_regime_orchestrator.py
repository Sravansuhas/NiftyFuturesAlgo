import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.regime_orchestrator import (
    apply_overnight_macro_hints,
    assess_live_posture,
    classify_market_color,
    exit_overrides_for_posture,
    posture_for_symbol,
)
from app.breakout_core import ExitConfig, ExitState, should_exit_position


class RegimeOrchestratorTests(unittest.TestCase):
    def test_green_uptrend_classified(self):
        color = classify_market_color({
            "trend": "uptrend",
            "volatility": "normal",
            "htf_bias": "bullish",
            "chop_score": 0.2,
        })
        self.assertEqual(color, "green")

    def test_sideways_chop_classified(self):
        color = classify_market_color({
            "trend": "ranging",
            "volatility": "low",
            "chop_score": 0.7,
        })
        self.assertEqual(color, "sideways")

    def test_aggressive_posture_in_green_validated_session(self):
        posture = assess_live_posture(
            {"trend": "uptrend", "volatility": "normal", "htf_bias": "bullish"},
            {
                "daily_pnl": 5000,
                "params_promoted": True,
                "learning_mult": 1.0,
                "capital": 1_000_000,
            },
        )
        self.assertEqual(posture["posture"], "aggressive")
        self.assertGreaterEqual(posture["recommended_max_trades_per_day"], 5)

    def test_contingency_near_daily_limit(self):
        posture = assess_live_posture(
            {"trend": "uptrend", "volatility": "normal"},
            {
                "daily_pnl": -17_500,
                "capital": 1_000_000,
                "max_daily_loss_pct": 0.02,
                "params_promoted": True,
            },
        )
        self.assertEqual(posture["posture"], "contingency")
        self.assertLessEqual(posture["recommended_max_trades_per_day"], 1)

    def test_chop_profit_defense_exits_green_trade(self):
        cfg = ExitConfig(
            profit_target_pts=100,
            stop_loss_pts=50,
            chop_profit_defense=True,
            time_exit_profit_fraction=0.4,
            chop_min_hold_seconds=60,
        )
        state = ExitState(best_price=24050, entry_time=None)
        import datetime as dt
        bar_time = dt.datetime(2026, 6, 12, 11, 0, 0)
        entry_time = dt.datetime(2026, 6, 12, 9, 0, 0)
        state.entry_time = entry_time
        exit_now, _, reason = should_exit_position(
            24020,
            24000,
            True,
            12.0,
            cfg,
            state,
            bar_time=bar_time,
            regime={"trend": "ranging"},
        )
        self.assertTrue(exit_now)
        self.assertEqual(reason, "chop_profit_defense")

    def test_breakeven_stop_locks_profit(self):
        cfg = ExitConfig(
            profit_target_pts=100,
            stop_loss_pts=50,
            breakeven_activation_mult=0.8,
        )
        state = ExitState(best_price=24045, entry_time=None)
        exit_now, _, reason = should_exit_position(
            23999,
            24000,
            True,
            12.0,
            cfg,
            state,
            regime={"trend": "uptrend"},
        )
        self.assertTrue(exit_now)
        self.assertEqual(reason, "breakeven_stop")

    def test_defensive_exit_tighter_than_aggressive(self):
        defensive = exit_overrides_for_posture("defensive")
        aggressive = exit_overrides_for_posture("aggressive")
        self.assertLess(
            defensive["profit_lock_retrace_pct"],
            aggressive["profit_lock_retrace_pct"],
        )

    def test_overnight_gap_floors_aggressive_posture(self):
        base = assess_live_posture(
            {"trend": "uptrend", "volatility": "normal", "htf_bias": "bullish"},
            {"daily_pnl": 1000, "params_promoted": True, "learning_mult": 1.0, "capital": 1_000_000},
        )
        self.assertEqual(base["posture"], "aggressive")
        adjusted = apply_overnight_macro_hints(
            base,
            overnight={
                "available": True,
                "session_hints": {
                    "posture_floor": "defensive",
                    "max_trades_delta": -1,
                    "breakout_buffer_mult": 1.25,
                    "reasons": ["gift_gap_-0.60pct_large"],
                },
            },
        )
        self.assertEqual(adjusted["posture"], "defensive")
        self.assertEqual(adjusted["breakout_buffer_mult"], 1.25)
        self.assertEqual(adjusted["recommended_max_trades_per_day"], 4)

    def test_macro_vix_de_risks_multiplier(self):
        base = assess_live_posture(
            {"trend": "uptrend", "volatility": "normal"},
            {"daily_pnl": 0, "params_promoted": True, "learning_mult": 1.0, "capital": 1_000_000},
        )
        adjusted = apply_overnight_macro_hints(
            base,
            macro={"vix": {"available": True, "zone": "extreme", "level": 22}},
        )
        self.assertLess(adjusted["risk_multiplier_hint"], base["risk_multiplier_hint"])


if __name__ == "__main__":
    unittest.main()