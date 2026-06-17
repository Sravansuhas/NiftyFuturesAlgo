import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.order_lifecycle import order_lifecycle
from app.risk_gatekeeper import RiskConfig, RiskGatekeeper
from app.state_machine import SystemState, state_machine


class FakeKite:
    def __init__(self):
        self.orders = []

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return "ORDER123"


class RiskGatekeeperTests(unittest.TestCase):
    def setUp(self):
        state_machine.set_state(SystemState.PAPER_MODE)
        self.config = RiskConfig(
            capital=1_000_000,
            risk_per_trade_pct=0.005,
            lot_size=65,
            max_lots=4,
            max_order_quantity=300,
            force_dry_run=True,
        )
        self.risk = RiskGatekeeper(config=self.config)

    def test_position_size_is_lot_aligned_and_capped(self):
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24475), 195)
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24495), 260)

    def test_blocks_non_lot_quantity(self):
        result = self.risk.place_guarded_order(
            kite=FakeKite(),
            symbol="NIFTY26JUNFUT",
            quantity=64,
            transaction_type="BUY",
            dry_run=True,
        )
        self.assertFalse(result["success"])
        self.assertIn("lot size", result["message"])

    def test_dry_run_entry_updates_position(self):
        result = self.risk.place_guarded_order(
            kite=FakeKite(),
            symbol="NIFTY26JUNFUT",
            quantity=65,
            transaction_type="BUY",
            price=24500,
            dry_run=True,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["position_updated"])
        self.assertEqual(self.risk.position["quantity"], 65)
        self.assertEqual(self.risk.position["avg_price"], 24500)

    def test_multi_symbol_dry_run_skips_single_symbol_accounting(self):
        """Options IC legs use multi_symbol_entry — must not block on leg 2+."""
        legs = [
            ("NIFTY26JUN24500CE", "SELL"),
            ("NIFTY26JUN24600CE", "BUY"),
            ("NIFTY26JUN24400PE", "SELL"),
            ("NIFTY26JUN24300PE", "BUY"),
        ]
        for symbol, side in legs:
            result = self.risk.place_guarded_order(
                kite=FakeKite(),
                symbol=symbol,
                quantity=65,
                transaction_type=side,
                dry_run=True,
                multi_symbol_entry=True,
            )
            self.assertTrue(result["success"], f"{symbol} failed: {result.get('message')}")
        self.assertEqual(self.risk.position["quantity"], 0)
        self.assertEqual(self.risk.trades_today, 0)

    def test_live_order_submission_does_not_assume_fill(self):
        live_config = RiskConfig(force_dry_run=False, lot_size=65)
        risk = RiskGatekeeper(config=live_config)
        state_machine.set_state(SystemState.LIVE_MODE)

        with patch("app.token_manager.live_trading_token_ok", return_value=True):
            result = risk.place_guarded_order(
                kite=FakeKite(),
                symbol="NIFTY26JUNFUT",
                quantity=65,
                transaction_type="BUY",
                dry_run=False,
            )

        self.assertTrue(result["success"])
        self.assertFalse(result["position_updated"])
        self.assertEqual(risk.position["quantity"], 0)
        self.assertEqual(result["order_id"], "ORDER123")
        self.assertIn("ORDER123", order_lifecycle.pending_orders)

    def test_drawdown_triggers_circuit_breaker(self):
        self.risk.update_equity(910_000)
        self.assertEqual(state_machine.get_state(), SystemState.CIRCUIT_BREAKER_TRIGGERED)
        self.assertFalse(self.risk.check_all_gates())

    def test_loss_streak_reduces_size(self):
        self.risk.update_daily_loss(-1000)
        self.risk.update_daily_loss(-1000)
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24475), 65)

    def test_live_order_blocked_without_valid_token(self):
        live_config = RiskConfig(force_dry_run=False, lot_size=65)
        risk = RiskGatekeeper(config=live_config)
        state_machine.set_state(SystemState.LIVE_MODE)

        with patch("app.token_manager.live_trading_token_ok", return_value=False):
            result = risk.place_guarded_order(
                kite=FakeKite(),
                symbol="NIFTY26JUNFUT",
                quantity=65,
                transaction_type="BUY",
                dry_run=False,
            )

        self.assertFalse(result["success"])
        self.assertIn("risk gates", result["message"].lower())

    def test_daily_reset_clears_counters(self):
        self.risk.update_daily_loss(-5000)
        self.risk.trades_today = 2
        self.risk.reset_daily()
        self.assertEqual(self.risk.daily_loss, 0.0)
        self.assertEqual(self.risk.trades_today, 0)
        self.assertEqual(self.risk.consecutive_losses, 0)

    def test_calculate_handles_tiny_stop_distance(self):
        q = self.risk.calculate_order_quantity(24500, 24499.5)  # <1pt
        self.assertTrue(q >= 65 and q <= 300)
        self.assertEqual(q % 65, 0)

    def test_single_loss_does_not_reduce_size(self):
        self.risk.update_daily_loss(-1000)
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24475), 195)

    def test_win_resets_loss_streak_multiplier(self):
        self.risk.update_daily_loss(-1000)
        self.risk.update_daily_loss(-1000)
        self.risk.update_daily_loss(500)
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24475), 195)

    def test_capital_zero_returns_one_lot(self):
        self.risk.capital = 0
        self.assertEqual(self.risk.calculate_order_quantity(24500, 24475), 65)

    def test_live_order_uses_algo_id_tag(self):
        live_config = RiskConfig(force_dry_run=False, lot_size=65)
        risk = RiskGatekeeper(config=live_config)
        fake = FakeKite()
        with patch.dict(os.environ, {"ALGO_ID": "MYALGO123"}), \
             patch("app.token_manager.live_trading_token_ok", return_value=True):
            result = risk.place_guarded_order(
                kite=fake,
                symbol="NIFTY26JUNFUT",
                quantity=65,
                transaction_type="BUY",
                dry_run=False,
            )
        self.assertTrue(result["success"])
        self.assertEqual(fake.orders[0]["tag"], "MYALGO123")

    def test_burst_rate_limit_blocks_live_order(self):
        live_config = RiskConfig(force_dry_run=False, lot_size=65)
        risk = RiskGatekeeper(config=live_config)
        fake = FakeKite()
        from app.kite_rate_limit import order_burst_tracker

        order_burst_tracker._max = 0
        with patch("app.token_manager.live_trading_token_ok", return_value=True):
            result = risk.place_guarded_order(
                kite=fake,
                symbol="NIFTY26JUNFUT",
                quantity=65,
                transaction_type="BUY",
                dry_run=False,
            )
        order_burst_tracker._max = 80
        self.assertFalse(result["success"])
        self.assertIn("rate limit", result["message"].lower())
        self.assertEqual(len(fake.orders), 0)


# --- Cost model tests (pure, no broker) ---
try:
    from backtesting.costs import TransactionCostModel, CostConfig
    HAS_COSTS = True
except Exception:
    HAS_COSTS = False

if HAS_COSTS:
    class CostModelTests(unittest.TestCase):
        def test_realistic_round_turn_cost(self):
            model = TransactionCostModel(CostConfig(
                brokerage_per_order=20.0,
                other_charges_per_lot_round_turn=45.0,
                default_slippage_points=3.5,
                lot_size=65,
            ))
            cost = model.estimate_cost_for_trade(65, 24500, 24530)
            # Realistic 1-lot round turn with slippage buffer (model includes STT + slip)
            self.assertTrue(250 < cost < 2500)

        def test_high_uncertainty_increases_cost(self):
            model = TransactionCostModel()
            normal = model.estimate_cost_for_trade(65, 24500, 24530, is_high_uncertainty=False)
            high = model.estimate_cost_for_trade(65, 24500, 24530, is_high_uncertainty=True)
            self.assertGreater(high, normal)



if __name__ == "__main__":
    unittest.main()
