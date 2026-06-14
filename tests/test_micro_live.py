import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.micro_live import (
    MicroLiveConfig,
    cap_order_quantity,
    load_micro_live_config,
    validate_micro_live_ready,
)
from app.multi_symbol_risk import MultiSymbolRiskManager
from app.state_machine import SystemState, state_machine


class MicroLiveTests(unittest.TestCase):
    def setUp(self):
        state_machine.set_state(SystemState.PAPER_MODE)
        self.mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        self.enabled_config = MicroLiveConfig(
            enabled=True,
            max_lots=1,
            max_open_positions=1,
            allowed_symbols=("NIFTY",),
            require_promotion=True,
        )

    def test_requires_double_confirmation(self):
        env = {
            "MICRO_LIVE_ENABLED": "true",
            "FORCE_DRY_RUN": "false",
            "LIVE_TRADING_CONFIRMED": "true",
        }
        deploy_ok = {
            "ready": True,
            "blockers": [],
            "warnings": [],
            "checks": [],
            "mode": "live",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch(
                "app.intelligence_loop.intelligence_loop.run_safe_deploy_checklist",
                return_value=deploy_ok,
            ):
                with patch(
                    "app.intelligence_loop.intelligence_loop._get_promotion_for",
                    return_value={"passed": True, "status": "promoted"},
                ):
                    blocked = validate_micro_live_ready()
                    self.assertFalse(blocked["ready"])
                    self.assertTrue(
                        any("MICRO_LIVE_CONFIRMED" in b for b in blocked["blockers"])
                    )

                    with patch.dict(os.environ, {"MICRO_LIVE_CONFIRMED": "true"}, clear=False):
                        ready = validate_micro_live_ready()
                        self.assertTrue(ready["ready"])

    def test_caps_quantity_to_one_lot(self):
        capped = cap_order_quantity("NIFTY", 300, 75, 0, self.enabled_config)
        self.assertEqual(capped, 75)

        self.mgr.set_micro_live_config(self.enabled_config)
        result = self.mgr.place_guarded_order(
            kite=None,
            symbol="NIFTY",
            quantity=300,
            transaction_type="BUY",
            price=25000.0,
            is_exit=False,
        )
        self.assertTrue(result["success"])
        lot_size = self.mgr._get_lot_size("NIFTY")
        self.assertEqual(self.mgr.get_position("NIFTY").quantity, lot_size)

    def test_blocks_second_position_when_max_open_is_one(self):
        self.mgr.set_micro_live_config(self.enabled_config)
        first = self.mgr.place_guarded_order(
            kite=None,
            symbol="NIFTY",
            quantity=75,
            transaction_type="BUY",
            price=25000.0,
            is_exit=False,
        )
        self.assertTrue(first["success"])

        second = self.mgr.place_guarded_order(
            kite=None,
            symbol="BANKNIFTY",
            quantity=30,
            transaction_type="BUY",
            price=52000.0,
            is_exit=False,
        )
        self.assertFalse(second["success"])
        self.assertIn("max open positions", second["message"].lower())

    def test_allows_when_disabled(self):
        disabled = MicroLiveConfig(enabled=False)
        self.assertEqual(cap_order_quantity("NIFTY", 300, 75, 0, disabled), 300)

        self.mgr.set_micro_live_config(disabled)
        result = self.mgr.place_guarded_order(
            kite=None,
            symbol="NIFTY",
            quantity=65,
            transaction_type="BUY",
            price=25000.0,
            is_exit=False,
        )
        self.assertTrue(result["success"])
        self.assertEqual(self.mgr.get_position("NIFTY").quantity, 65)

    def test_load_config_requires_both_flags(self):
        with patch.dict(os.environ, {"MICRO_LIVE_ENABLED": "true"}, clear=False):
            cfg = load_micro_live_config()
            self.assertFalse(cfg.enabled)
        with patch.dict(
            os.environ,
            {"MICRO_LIVE_ENABLED": "true", "MICRO_LIVE_CONFIRMED": "true"},
            clear=False,
        ):
            cfg = load_micro_live_config()
            self.assertTrue(cfg.enabled)


if __name__ == "__main__":
    unittest.main()