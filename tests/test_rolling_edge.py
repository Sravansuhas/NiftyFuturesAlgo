import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fo_rules_engine import FORulesEngine
from app.multi_symbol_risk import MultiSymbolRiskManager
from app.rolling_edge import (
    assess_rolling_edge,
    extract_recent_pnls,
    rolling_expectancy,
)
from app.trade_ledger import TradeLedger


def _closed_trade(pnl: float, symbol: str = "NIFTY") -> dict:
    return {
        "event_type": "trade.closed",
        "payload": {"symbol": symbol, "realized_pnl": pnl},
    }


class RollingEdgeHelperTests(unittest.TestCase):
    def test_extract_recent_pnls_skips_events_without_pnl(self):
        events = [
            {"event_type": "order.placed", "payload": {"symbol": "NIFTY"}},
            _closed_trade(100.0),
            {"event_type": "order.exit", "payload": {"symbol": "NIFTY"}},
            _closed_trade(-50.0),
        ]
        self.assertEqual(extract_recent_pnls(events), [100.0, -50.0])

    def test_rolling_expectancy_mean_of_last_window(self):
        pnls = [200.0, -100.0, 50.0, -25.0, 75.0]
        self.assertEqual(rolling_expectancy(pnls, window=3), 100.0 / 3)

    def test_assess_allows_sparse_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            ledger.record("trade.closed", {"symbol": "NIFTY", "realized_pnl": -500.0})

            result = assess_rolling_edge(ledger, window=10, min_trades=10)
            self.assertFalse(result["rolling_edge_sufficient"])
            self.assertFalse(result["rolling_edge_halt"])
            self.assertEqual(result["rolling_edge_trade_count"], 1)

    def test_assess_halts_on_negative_expectancy(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            for pnl in [-100, -50, -25, -10, -5, -5, -5, -5, -5, -5]:
                ledger.record("trade.closed", {"symbol": "NIFTY", "realized_pnl": pnl})

            result = assess_rolling_edge(ledger, window=10, min_trades=10)
            self.assertTrue(result["rolling_edge_sufficient"])
            self.assertTrue(result["rolling_edge_halt"])
            self.assertLess(result["rolling_expectancy"], 0.0)

    def test_assess_allows_positive_expectancy(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = TradeLedger(path=str(Path(tmp) / "ledger.jsonl"))
            for pnl in [100, 50, 25, 10, 5, 5, 5, 5, 5, 5]:
                ledger.record("trade.closed", {"symbol": "NIFTY", "realized_pnl": pnl})

            result = assess_rolling_edge(ledger, window=10, min_trades=10)
            self.assertTrue(result["rolling_edge_sufficient"])
            self.assertFalse(result["rolling_edge_halt"])
            self.assertGreater(result["rolling_expectancy"], 0.0)


class RollingEdgeRulesIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = FORulesEngine()

    def _base_ctx(self, **overrides):
        ctx = {
            "has_hard_stop_loss": True,
            "uses_mental_stop": False,
            "broker_connected": True,
            "consecutive_losses": 0,
            "seconds_since_last_loss": 9999,
            "trades_today": 0,
            "is_paper_mode": True,
            "rolling_expectancy": 0.0,
            "rolling_edge_trade_count": 0,
        }
        ctx.update(overrides)
        return ctx

    def test_fo_rule_blocks_negative_rolling_expectancy(self):
        allowed, reason, _ = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._base_ctx(
                rolling_expectancy=-21.5,
                rolling_edge_trade_count=10,
            ),
        )
        self.assertFalse(allowed)
        self.assertIn("FO_ROLLING_EDGE_HALT", reason)

    def test_fo_rule_allows_insufficient_history(self):
        allowed, reason, _ = self.engine.check_entry(
            "NIFTY26JUNFUT",
            self._base_ctx(
                rolling_expectancy=-500.0,
                rolling_edge_trade_count=3,
            ),
        )
        self.assertTrue(allowed)
        self.assertNotIn("FO_ROLLING_EDGE_HALT", reason)

    def test_build_fo_rules_context_includes_rolling_edge(self):
        mgr = MultiSymbolRiskManager(capital=1_000_000.0)
        ctx = mgr.build_fo_rules_context("NIFTY26JUNFUT")
        for key in (
            "rolling_expectancy",
            "rolling_edge_trade_count",
            "rolling_edge_sufficient",
            "rolling_edge_halt",
        ):
            self.assertIn(key, ctx)

    def test_record_trade_closed_writes_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(ledger_path))
            mgr = MultiSymbolRiskManager(capital=1_000_000.0)

            with mock.patch("app.trade_ledger.trade_ledger", ledger):
                mgr._record_trade_closed("NIFTY", 65, "SELL", 6500.0, paper=True)

            lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(event["event_type"], "trade.closed")
            self.assertEqual(event["payload"]["realized_pnl"], 6500.0)
            self.assertTrue(event["payload"]["paper"])


if __name__ == "__main__":
    unittest.main()