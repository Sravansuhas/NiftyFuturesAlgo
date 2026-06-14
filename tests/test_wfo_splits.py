"""Unit tests for institutional walk-forward split geometry."""

import math
import statistics
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.wfo_splits import (
    build_anchored_purged_folds,
    parameter_stability,
    score_train_result,
)


class TestAnchoredPurgedFolds(unittest.TestCase):
    def setUp(self):
        self.n_bars = 2000
        self.n_folds = 4
        self.test_fraction = 0.4
        self.embargo_bars = 78
        self.folds = build_anchored_purged_folds(
            n_bars=self.n_bars,
            n_folds=self.n_folds,
            test_fraction=self.test_fraction,
            embargo_bars=self.embargo_bars,
            min_train_bars=500,
            min_test_bars=100,
        )

    def test_returns_expected_fold_count(self):
        self.assertEqual(len(self.folds), self.n_folds)

    def test_train_and_test_slices_never_overlap(self):
        for train_slice, test_slice in self.folds:
            train_end = train_slice.stop
            test_start = test_slice.start
            self.assertLess(train_end, test_start)
            self.assertEqual(train_slice.start, 0)

    def test_embargo_gap_between_train_end_and_test_start(self):
        for train_slice, test_slice in self.folds:
            gap = test_slice.start - train_slice.stop
            self.assertGreaterEqual(gap, self.embargo_bars)

    def test_folds_cover_sequential_test_regions(self):
        oos_start = int(self.n_bars * (1.0 - self.test_fraction))
        test_slices = [test for _, test in self.folds]

        self.assertEqual(test_slices[0].start, oos_start)
        for prev, curr in zip(test_slices, test_slices[1:]):
            self.assertEqual(prev.stop, curr.start)

        self.assertEqual(test_slices[-1].stop, self.n_bars)

    def test_insufficient_data_returns_empty_list(self):
        folds = build_anchored_purged_folds(
            n_bars=400,
            n_folds=5,
            test_fraction=0.4,
            embargo_bars=78,
            min_train_bars=500,
            min_test_bars=100,
        )
        self.assertEqual(folds, [])


class TestScoreTrainResult(unittest.TestCase):
    def test_calmar_objective(self):
        result = {
            "total_return_pct": 12.0,
            "max_drawdown_pct": 4.0,
            "trades": [{"pnl": 1}] * 8,
        }
        self.assertAlmostEqual(score_train_result(result, objective="calmar"), 3.0)

    def test_sharpe_penalized_objective(self):
        result = {
            "total_return_pct": 10.0,
            "max_drawdown_pct": 4.0,
            "total_trades": 16,
        }
        expected = (10.0 * math.sqrt(16)) / (1.0 + 4.0)
        self.assertAlmostEqual(
            score_train_result(result, objective="sharpe_penalized"),
            expected,
        )

    def test_returns_negative_infinity_when_trades_below_minimum(self):
        result = {
            "total_return_pct": 20.0,
            "max_drawdown_pct": 2.0,
            "trades": [{"pnl": 1}] * 3,
        }
        self.assertEqual(score_train_result(result, min_trades=5), float("-inf"))


class TestParameterStability(unittest.TestCase):
    def test_parameter_variance_stats(self):
        fold_results = [
            {"best_params": {"breakout_atr_mult": 0.75, "risk_per_trade_pct": 0.004}},
            {"best_params": {"breakout_atr_mult": 0.78, "risk_per_trade_pct": 0.0045}},
            {"best_params": {"breakout_atr_mult": 0.76, "risk_per_trade_pct": 0.0042}},
        ]
        stats = parameter_stability(fold_results)

        self.assertEqual(stats["n_folds"], 3)
        self.assertIn("breakout_atr_mult", stats["parameters"])
        atr_stats = stats["parameters"]["breakout_atr_mult"]
        self.assertEqual(atr_stats["min"], 0.75)
        self.assertEqual(atr_stats["max"], 0.78)
        self.assertAlmostEqual(atr_stats["mean"], statistics.fmean([0.75, 0.78, 0.76]))


if __name__ == "__main__":
    unittest.main()