import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.multi_symbol_risk import MultiSymbolRiskManager
from app.risk_state_persistence import (
    RISK_STATE_FILE,
    clear_risk_state,
    load_risk_state,
    restore_risk_manager,
    save_risk_state,
)
from app.trade_ledger import TradeLedger


class PersistenceTests(unittest.TestCase):
    def test_trade_ledger_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(path))
            ledger.set_session_id("test_session")
            ledger.record("order.placed", {"symbol": "NIFTY"})
            ledger.record("trade.closed", {"realized_pnl": 500})

            ledger2 = TradeLedger(path=str(path))
            events = ledger2.tail(10)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event_type"], "order.placed")
            self.assertGreater(len(path.read_text().strip()), 0)

    def test_trade_ledger_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(path))
            ledger.record("session.start", {})
            archived = ledger.archive_current()
            self.assertIsNotNone(archived)
            self.assertFalse(path.exists())

    def test_risk_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "risk_state.json"
            mgr = MultiSymbolRiskManager()
            mgr._update_paper_position("NIFTY", 65, "BUY", 24000.0, is_exit=False)
            mgr.daily_pnl = 1200.0
            mgr.trades_today = 2
            save_risk_state(mgr, path=state_path)

            fresh = MultiSymbolRiskManager()
            restored = restore_risk_manager(fresh, load_risk_state(state_path))
            self.assertTrue(restored)
            self.assertEqual(fresh.positions["NIFTY"].quantity, 65)
            self.assertEqual(fresh.daily_pnl, 1200.0)
            self.assertEqual(fresh.trades_today, 2)

    def test_risk_state_not_restored_on_wrong_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "risk_state.json"
            payload = {
                "date_ist": "2000-01-01",
                "capital": 1_000_000,
                "daily_pnl": 99,
                "positions": {"NIFTY": {"quantity": 65, "avg_price": 24000}},
                "symbol_daily_trades": {},
                "symbol_daily_pnl": {},
                "symbol_daily_loss": {},
                "force_dry_run": True,
            }
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            mgr = MultiSymbolRiskManager()
            self.assertFalse(restore_risk_manager(mgr, payload))
            self.assertEqual(mgr.positions["NIFTY"].quantity, 0)


if __name__ == "__main__":
    unittest.main()