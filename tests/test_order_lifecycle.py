import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.branding import DEFAULT_ALGO_ID
from app.multi_symbol_risk import MultiSymbolRiskManager, SymbolPosition
from app.order_lifecycle import OrderLifecycleManager, order_lifecycle
from app.risk_gatekeeper import RiskConfig, RiskGatekeeper
from app.state_machine import SystemState, state_machine


class FakeKite:
    def __init__(self, order_id="ORDER-LIVE-1"):
        self.order_id = order_id
        self.orders = []

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return self.order_id


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        state_machine.set_state(SystemState.LIVE_MODE)
        order_lifecycle.pending_orders.clear()
        from app.risk_gatekeeper import risk_gatekeeper

        risk_gatekeeper.pending_orders.clear()
        risk_gatekeeper.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None,
        }

    def test_live_entry_does_not_update_position_until_complete(self):
        live_config = RiskConfig(force_dry_run=False, lot_size=65)
        risk = RiskGatekeeper(config=live_config)
        risk.pending_orders.clear()

        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()

        with patch("app.token_manager.live_trading_token_ok", return_value=True), patch(
            "app.multi_symbol_risk.multi_risk_manager", mgr
        ):
            result = risk.place_guarded_order(
                kite=FakeKite("ORD-100"),
                symbol="NIFTY26JUNFUT",
                quantity=65,
                transaction_type="BUY",
                dry_run=False,
            )

        self.assertTrue(result["success"])
        self.assertFalse(result.get("position_updated", True))
        self.assertEqual(risk.position["quantity"], 0)
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 0)
        self.assertIn("ORD-100", order_lifecycle.pending_orders)

    def test_complete_fill_updates_position(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()

        from app.risk_gatekeeper import risk_gatekeeper

        risk_gatekeeper.pending_orders.clear()
        risk_gatekeeper.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None,
        }

        lifecycle = OrderLifecycleManager()
        lifecycle.register_submitted_order("ORD-200", {
            "symbol": "NIFTY26JUNFUT",
            "quantity": 65,
            "transaction_type": "BUY",
            "is_exit": False,
            "exchange": "NFO",
            "tag": DEFAULT_ALGO_ID,
            "index_key": "NIFTY",
        })

        with patch("app.multi_symbol_risk.multi_risk_manager", mgr):
            result = lifecycle.handle_broker_update("ORD-200", {
                "status": "COMPLETE",
                "filled_quantity": 65,
                "average_price": 25000.0,
            })

        self.assertTrue(result["processed"])
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["fill_price"], 25000.0)
        self.assertEqual(result["filled_qty"], 65)
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 65)
        self.assertEqual(mgr.get_position("NIFTY").avg_price, 25000.0)
        self.assertEqual(risk_gatekeeper.position["quantity"], 65)
        self.assertNotIn("ORD-200", lifecycle.pending_orders)
        self.assertNotIn("ORD-200", risk_gatekeeper.pending_orders)

    def test_rejected_does_not_update_position(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)

        from app.risk_gatekeeper import risk_gatekeeper

        lifecycle = OrderLifecycleManager()
        lifecycle.register_submitted_order("ORD-REJ", {
            "symbol": "NIFTY26JUNFUT",
            "quantity": 65,
            "transaction_type": "BUY",
            "is_exit": False,
            "exchange": "NFO",
            "index_key": "NIFTY",
        })

        with patch("app.multi_symbol_risk.multi_risk_manager", mgr):
            result = lifecycle.handle_broker_update("ORD-REJ", {
                "status": "REJECTED",
                "filled_quantity": 0,
                "average_price": 0,
                "status_message": "Insufficient margin",
            })

        self.assertTrue(result["processed"])
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 0)
        self.assertEqual(risk_gatekeeper.position["quantity"], 0)
        self.assertNotIn("ORD-REJ", lifecycle.pending_orders)

    def test_postback_routes_to_handler(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()

        lifecycle = OrderLifecycleManager()
        lifecycle.register_submitted_order("ORD-PB", {
            "symbol": "BANKNIFTY26JUNFUT",
            "quantity": 30,
            "transaction_type": "SELL",
            "is_exit": False,
            "exchange": "NFO",
            "index_key": "BANKNIFTY",
        })

        payload = {
            "order_id": "ORD-PB",
            "status": "COMPLETE",
            "filled_quantity": 30,
            "average_price": 52000.0,
        }

        with patch("app.multi_symbol_risk.multi_risk_manager", mgr):
            result = lifecycle.handle_postback(payload)

        self.assertTrue(result["processed"])
        self.assertEqual(result["filled_qty"], 30)
        self.assertEqual(mgr.get_position_quantity("BANKNIFTY"), -30)

    def test_terminal_update_is_idempotent(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()

        from app.risk_gatekeeper import risk_gatekeeper

        risk_gatekeeper.pending_orders.clear()
        risk_gatekeeper.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None,
        }

        lifecycle = OrderLifecycleManager()
        lifecycle.register_submitted_order("ORD-IDEM", {
            "symbol": "NIFTY26JUNFUT",
            "quantity": 65,
            "transaction_type": "BUY",
            "is_exit": False,
            "exchange": "NFO",
            "index_key": "NIFTY",
        })

        payload = {"status": "COMPLETE", "filled_quantity": 65, "average_price": 25000.0}
        with patch("app.multi_symbol_risk.multi_risk_manager", mgr):
            first = lifecycle.handle_broker_update("ORD-IDEM", payload)
            second = lifecycle.handle_broker_update("ORD-IDEM", payload)

        self.assertTrue(first["processed"])
        self.assertFalse(second["processed"])
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 65)

    def test_update_applies_partial_fill_incrementally(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(False)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()

        from app.risk_gatekeeper import risk_gatekeeper

        risk_gatekeeper.pending_orders.clear()
        risk_gatekeeper.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None,
        }

        lifecycle = OrderLifecycleManager()
        lifecycle.register_submitted_order("ORD-PART", {
            "symbol": "NIFTY26JUNFUT",
            "quantity": 130,
            "transaction_type": "BUY",
            "is_exit": False,
            "exchange": "NFO",
            "index_key": "NIFTY",
        })

        with patch("app.multi_symbol_risk.multi_risk_manager", mgr):
            first = lifecycle.handle_broker_update("ORD-PART", {
                "status": "UPDATE",
                "filled_quantity": 65,
                "average_price": 25000.0,
            })
            self.assertTrue(first["processed"])
            self.assertEqual(mgr.get_position_quantity("NIFTY"), 65)

            second = lifecycle.handle_broker_update("ORD-PART", {
                "status": "COMPLETE",
                "filled_quantity": 130,
                "average_price": 25010.0,
            })

        self.assertTrue(second["processed"])
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 130)
        self.assertNotIn("ORD-PART", lifecycle.pending_orders)


if __name__ == "__main__":
    unittest.main()