"""
Restart / Recovery test — verifies per-symbol state survives simulated process kill.
"""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tempfile

from app.strategy import PreviousCandleBreakoutStrategy
from app.paper_trading_params import DEFAULT_PAPER_PARAMS
from app.multi_symbol_risk import MultiSymbolRiskManager
from app.state_persistence import (
    save_symbol_state,
    load_symbol_state,
    clear_symbol_state,
    STATE_DIR,
)


class RestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import app.state_persistence as sp
        self._sp = sp
        self._orig_dir = sp.STATE_DIR
        sp.STATE_DIR = Path(self._tmpdir.name) / "state"

    def tearDown(self):
        self._sp.STATE_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_restart_with_open_trade(self):
        save_symbol_state("NIFTY", {
            "entry_price": 24000.0,
            "entry_time": time.time() - 30 * 60,
            "best_price": 24150.0,
            "symbol": "NIFTY26JUNFUT",
        })

        mgr = MultiSymbolRiskManager()
        mgr._update_paper_position("NIFTY", 65, "BUY", 24000.0, is_exit=False)

        new_strategy = PreviousCandleBreakoutStrategy(
            kite=None, paper_params=DEFAULT_PAPER_PARAMS, risk_manager=mgr
        )
        new_strategy.symbol = "NIFTY26JUNFUT"
        new_strategy.restore_from_persistence("NIFTY")

        persisted = load_symbol_state("NIFTY")
        self.assertIsNotNone(persisted)
        self.assertEqual(new_strategy.entry_price, 24000.0)
        self.assertEqual(new_strategy._best_price_in_trade, 24150.0)

        clear_symbol_state("NIFTY")
        self.assertIsNone(load_symbol_state("NIFTY"))


if __name__ == "__main__":
    unittest.main()