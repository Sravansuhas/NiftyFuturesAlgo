import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.promotion_gates import (
    PromotionGateConfig,
    evaluate_wfo_summary,
    write_candidate,
)


def _good_fold(fold_num: int = 1) -> dict:
    return {
        "fold": fold_num,
        "test_pf": 1.5,
        "test_dd": 4.0,
        "test_return": 2.5,
        "trades": 12,
        "best_params": {"breakout_atr_mult": 0.75, "risk_per_trade_pct": 0.004},
        "monte_carlo": {"final_return_5th_percentile": 0.5},
    }


class PromotionGateTests(unittest.TestCase):
    def test_passes_with_two_good_folds(self):
        summary = {
            "folds": [_good_fold(1), _good_fold(2)],
            "avg_return": 2.5,
            "avg_pf": 1.5,
            "total_folds_run": 2,
            "regime_aggregate": {
                "high": {"trades": 6, "total_pnl": 500.0},
            },
            "trend_aggregate": {
                "ranging": {"trades": 6, "total_pnl": 200.0},
            },
            "parameter_stability": {"stable": True, "parameters": {}},
        }
        result = evaluate_wfo_summary(summary, underlying="NIFTY", cost_multiplier=2.0)
        self.assertTrue(result.passed)
        self.assertEqual(result.fold_pass_count, 2)
        self.assertIn("breakout_atr_mult", result.best_params)

    def test_rejects_low_profit_factor(self):
        bad = _good_fold()
        bad["test_pf"] = 0.8
        summary = {"folds": [bad], "parameter_stability": {"stable": True, "parameters": {}}}
        result = evaluate_wfo_summary(summary, underlying="BANKNIFTY", cost_multiplier=2.0)
        self.assertFalse(result.passed)
        self.assertEqual(result.fold_pass_count, 0)
        fold_reports = (result.summary or {}).get("fold_reports") or []
        self.assertTrue(fold_reports and not fold_reports[0]["passed"])
        self.assertTrue(any("PF" in r for r in fold_reports[0]["reasons"]))

    def test_rejects_negative_high_vol_regime(self):
        summary = {
            "folds": [_good_fold(1), _good_fold(2)],
            "regime_aggregate": {
                "high": {"trades": 12, "total_pnl": -5000.0},
            },
            "trend_aggregate": {
                "ranging": {"trades": 8, "total_pnl": 100.0},
            },
            "parameter_stability": {"stable": True, "parameters": {}},
        }
        result = evaluate_wfo_summary(summary, underlying="NIFTY", cost_multiplier=2.0)
        self.assertFalse(result.passed)
        self.assertTrue(any("high-vol" in r for r in result.reasons))

    def test_write_candidate_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            summary = {"folds": [_good_fold()], "parameter_stability": {"stable": True, "parameters": {}}}
            result = evaluate_wfo_summary(summary, underlying="SENSEX", cost_multiplier=2.0)
            write_candidate(result, path=path)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("SENSEX", content)


if __name__ == "__main__":
    unittest.main()