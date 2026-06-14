import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eod_flatten import (
    _STATE_FILE,
    already_flattened_today,
    eod_flatten_enabled,
    execute_eod_mis_flatten,
    maybe_run_eod_flatten,
)
from app.market_calendar import is_eod_flatten_window
from app.multi_symbol_risk import MultiSymbolRiskManager, SymbolPosition
from app.state_machine import SystemState, state_machine

IST = ZoneInfo("Asia/Kolkata")


class EodFlattenTests(unittest.TestCase):
    def setUp(self):
        state_machine.set_state(SystemState.PAPER_MODE)
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()

    def tearDown(self):
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()

    def test_eod_window_default_1510_to_1515(self):
        inside = datetime.datetime(2026, 6, 12, 15, 12, tzinfo=IST)
        outside = datetime.datetime(2026, 6, 12, 15, 5, tzinfo=IST)
        self.assertTrue(is_eod_flatten_window(inside))
        self.assertFalse(is_eod_flatten_window(outside))

    def test_eod_window_extended_session_1520_to_1525(self):
        inside = datetime.datetime(2026, 8, 10, 15, 22, tzinfo=IST)
        outside = datetime.datetime(2026, 8, 10, 15, 10, tzinfo=IST)
        self.assertTrue(is_eod_flatten_window(inside))
        self.assertFalse(is_eod_flatten_window(outside))

    def test_eod_disabled_via_env(self):
        inside = datetime.datetime(2026, 6, 12, 15, 12, tzinfo=IST)
        with patch.dict("os.environ", {"EOD_MIS_FLATTEN": "false"}):
            self.assertFalse(is_eod_flatten_window(inside))
            self.assertFalse(eod_flatten_enabled())

    def test_execute_flattens_paper_positions(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(True)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()
        mgr.positions["NIFTY"].quantity = 65
        mgr.positions["NIFTY"].symbol = "NIFTY26JUNFUT"
        mgr.positions["NIFTY"].avg_price = 25000.0

        fixed_now = datetime.datetime(2026, 6, 12, 15, 11, tzinfo=IST)
        with patch("app.eod_flatten.now_ist", return_value=fixed_now):
            result = execute_eod_mis_flatten(mgr, force_dry=True)

        self.assertTrue(result["flattened"])
        self.assertEqual(mgr.get_position_quantity("NIFTY"), 0)
        self.assertEqual(len(result["closed_positions"]), 1)
        self.assertTrue(result["closed_positions"][0]["success"])
        self.assertTrue(already_flattened_today(datetime.date(2026, 6, 12)))

    def test_maybe_run_only_once_per_day(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        mgr.set_force_dry_run(True)
        for key in mgr.positions:
            mgr.positions[key] = SymbolPosition()
        mgr.positions["NIFTY"].quantity = 65
        mgr.positions["NIFTY"].symbol = "NIFTY26JUNFUT"
        mgr.positions["NIFTY"].avg_price = 25000.0

        at = datetime.datetime(2026, 6, 12, 15, 11, tzinfo=IST)
        first = maybe_run_eod_flatten(mgr, at=at)
        second = maybe_run_eod_flatten(mgr, at=at)

        self.assertTrue(first["flattened"])
        self.assertFalse(first.get("skipped"))
        self.assertTrue(second.get("skipped"))
        self.assertEqual(second.get("reason"), "already_done_today")


if __name__ == "__main__":
    unittest.main()