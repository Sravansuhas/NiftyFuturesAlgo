import os
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.multi_symbol_risk import MultiSymbolRiskManager, SymbolPosition


class MultiSymbolRiskTests(unittest.TestCase):
    def setUp(self):
        self.mgr = MultiSymbolRiskManager(capital=1_000_000.0)

    def test_paper_exit_records_pnl(self):
        self.mgr._update_paper_position("NIFTY", 65, "BUY", 25000.0, is_exit=False)
        pnl = self.mgr._update_paper_position("NIFTY", 65, "SELL", 25100.0, is_exit=True)
        self.assertEqual(pnl, 6500.0)
        self.assertEqual(self.mgr.daily_pnl, 6500.0)

    def test_fo_rules_blocks_overtrading(self):
        self.mgr.symbol_daily_trades["NIFTY"] = 3
        self.assertFalse(self.mgr.can_place_order("NIFTY", is_exit=False))

    def test_loss_streak_sets_cooldown_timestamp(self):
        self.mgr._record_realized_pnl("NIFTY", -500.0)
        self.assertGreater(self.mgr.last_loss_timestamp, 0)
        self.assertLess(self.mgr.seconds_since_last_loss(), 5.0)

    def test_sync_with_broker_skips_paper_mode(self):
        self.mgr.positions["NIFTY"] = SymbolPosition(symbol="NIFTY26JUNFUT", quantity=65, avg_price=25000.0)
        self.mgr.sync_with_broker([])
        self.assertEqual(self.mgr.positions["NIFTY"].quantity, 65)

    def test_fo_context_includes_chop_regime_fields(self):
        self.mgr.set_market_regime(
            "NIFTY",
            {"trend": "ranging", "volatility": "low", "adx_proxy": 17.0, "chop_score": 0.71},
        )
        ctx = self.mgr.build_fo_rules_context("NIFTY26JUNFUT")
        self.assertEqual(ctx["trend"], "ranging")
        self.assertEqual(ctx["volatility"], "low")
        self.assertEqual(ctx["adx_proxy"], 17.0)
        self.assertEqual(ctx["chop_score"], 0.71)
        self.assertTrue(ctx["is_breakout_entry"])

    def test_live_fo_context_has_hard_sl_when_slm_enabled(self):
        self.mgr.set_force_dry_run(False)
        prev = os.environ.get("ENABLE_EXCHANGE_SLM")
        try:
            os.environ["ENABLE_EXCHANGE_SLM"] = "true"
            ctx = self.mgr.build_fo_rules_context("NIFTY26JUNFUT")
            self.assertTrue(ctx["has_hard_stop_loss"])
            self.assertFalse(ctx["is_paper_mode"])
        finally:
            if prev is None:
                os.environ.pop("ENABLE_EXCHANGE_SLM", None)
            else:
                os.environ["ENABLE_EXCHANGE_SLM"] = prev

    def test_sync_with_broker_updates_live_positions(self):
        self.mgr.set_force_dry_run(False)
        self.mgr.sync_with_broker([
            {"tradingsymbol": "BANKNIFTY26JUNFUT", "quantity": -30, "average_price": 52000.0},
        ])
        self.assertEqual(self.mgr.positions["BANKNIFTY"].quantity, -30)
        self.assertEqual(self.mgr.positions["BANKNIFTY"].avg_price, 52000.0)


if __name__ == "__main__":
    unittest.main()