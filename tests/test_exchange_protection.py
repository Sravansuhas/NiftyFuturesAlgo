import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.exchange_protection import ExchangeProtectionManager


class FakeKite:
    def __init__(self, ltp=25000.0):
        self.ltp_price = ltp
        self.orders = []
        self.cancelled = []

    def ltp(self, keys):
        return {keys[0]: {"last_price": self.ltp_price}}

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return f"SLM-{len(self.orders)}"

    def cancel_order(self, **kwargs):
        self.cancelled.append(kwargs)
        return True


class ExchangeProtectionTests(unittest.TestCase):
    def setUp(self):
        self.mgr = ExchangeProtectionManager()
        self.mgr.active_protections.clear()
        self.mgr._kite = None
        os.environ["FORCE_DRY_RUN"] = "false"
        os.environ["ENABLE_EXCHANGE_SLM"] = "true"

    def tearDown(self):
        os.environ["FORCE_DRY_RUN"] = "true"
        os.environ.pop("ENABLE_EXCHANGE_SLM", None)

    def test_sl_m_params_for_long(self):
        kite = FakeKite(ltp=25000.0)
        result = self.mgr.place_protective_sl(
            kite=kite,
            symbol="NIFTY26JULFUT",
            quantity=65,
            side="LONG",
            trigger_price=24800.0,
            exchange="NFO",
            force_dry_run=False,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["order_id"], "SLM-1")
        self.assertEqual(len(kite.orders), 1)

        order = kite.orders[0]
        self.assertEqual(order["order_type"], "SL-M")
        self.assertEqual(order["transaction_type"], "SELL")
        self.assertEqual(order["trigger_price"], 24800.0)
        self.assertEqual(order["quantity"], 65)
        self.assertEqual(order["exchange"], "NFO")
        self.assertEqual(order["tradingsymbol"], "NIFTY26JULFUT")

    def test_skips_in_dry_run(self):
        kite = FakeKite(ltp=25000.0)
        result = self.mgr.place_protective_sl(
            kite=kite,
            symbol="NIFTY26JULFUT",
            quantity=65,
            side="LONG",
            trigger_price=24800.0,
            exchange="NFO",
            force_dry_run=True,
        )

        self.assertFalse(result["success"])
        self.assertIn("dry-run", result["message"].lower())
        self.assertEqual(len(kite.orders), 0)

    def test_cancel_on_exit_fill(self):
        kite = FakeKite(ltp=25000.0)
        self.mgr.active_protections["NIFTY"] = {
            "order_id": "SLM-99",
            "symbol": "NIFTY26JULFUT",
            "trigger": 24800.0,
            "qty": 65,
            "exchange": "NFO",
            "side": "LONG",
        }
        self.mgr._kite = kite

        result = self.mgr.on_exit_fill("NIFTY")

        self.assertTrue(result["success"])
        self.assertNotIn("NIFTY", self.mgr.active_protections)
        self.assertEqual(len(kite.cancelled), 1)
        self.assertEqual(kite.cancelled[0]["order_id"], "SLM-99")

    def test_rejects_invalid_trigger(self):
        kite = FakeKite(ltp=25000.0)

        # LONG trigger above LTP — invalid
        result = self.mgr.place_protective_sl(
            kite=kite,
            symbol="NIFTY26JULFUT",
            quantity=65,
            side="LONG",
            trigger_price=25100.0,
            exchange="NFO",
            force_dry_run=False,
        )
        self.assertFalse(result["success"])
        self.assertIn("below", result["message"].lower())

        # LONG trigger too close to LTP (< 5 points and < 0.05%)
        result2 = self.mgr.place_protective_sl(
            kite=kite,
            symbol="NIFTY26JULFUT",
            quantity=65,
            side="LONG",
            trigger_price=24998.0,
            exchange="NFO",
            force_dry_run=False,
        )
        self.assertFalse(result2["success"])
        self.assertIn("too close", result2["message"].lower())
        self.assertEqual(len(kite.orders), 0)

    def test_on_entry_fill_places_and_tracks_protection(self):
        kite = FakeKite(ltp=25000.0)
        fill_meta = {
            "symbol": "NIFTY26JULFUT",
            "quantity": 65,
            "transaction_type": "BUY",
            "avg_price": 25010.0,
            "stop_price": 24800.0,
            "exchange": "NFO",
            "index_key": "NIFTY",
        }

        result = self.mgr.on_entry_fill(kite, fill_meta)

        self.assertTrue(result["success"])
        self.assertIn("NIFTY", self.mgr.active_protections)
        self.assertEqual(self.mgr.active_protections["NIFTY"]["trigger"], 24800.0)


if __name__ == "__main__":
    unittest.main()