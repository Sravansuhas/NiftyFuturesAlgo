import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fill_learning import (
    analyze_fills,
    build_calibration_notes,
    load_latest_fill_learning,
    record_fill_learning_snapshot,
)


class FillLearningTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fill_dir = Path(self._tmpdir.name) / "fill_learning"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _sample_trades(self):
        return [
            {
                "tradingsymbol": "NIFTY26JUNFUT",
                "quantity": 65,
                "average_price": 24500.0,
                "transaction_type": "BUY",
                "order_timestamp": "2026-06-10T10:30:00+05:30",
            },
            {
                "tradingsymbol": "BANKNIFTY26JUNFUT",
                "quantity": 30,
                "average_price": 52000.0,
                "transaction_type": "SELL",
                "order_timestamp": "2026-06-10T11:00:00+05:30",
            },
            {
                "tradingsymbol": "SENSEX26JUNFUT",
                "quantity": 20,
                "average_price": 81000.0,
                "transaction_type": "BUY",
                "order_timestamp": "2026-06-10T12:00:00+05:30",
            },
            {
                "tradingsymbol": "RELIANCE",
                "quantity": 10,
                "average_price": 2800.0,
                "transaction_type": "BUY",
            },
        ]

    def test_analyze_fills_filters_by_underlying(self):
        all_analysis = analyze_fills(self._sample_trades(), underlying=None, limit=10)
        nifty_analysis = analyze_fills(self._sample_trades(), underlying="NIFTY", limit=10)

        self.assertEqual(all_analysis["summary"]["fills_analyzed"], 3)
        self.assertEqual(nifty_analysis["summary"]["fills_analyzed"], 1)
        self.assertEqual(nifty_analysis["fills"][0]["underlying"], "NIFTY")
        self.assertIn("fills_by_underlying", all_analysis["summary"])
        self.assertEqual(all_analysis["summary"]["fills_by_underlying"]["BANKNIFTY"], 1)

    def test_build_calibration_notes_small_vs_large_sample(self):
        small = build_calibration_notes({
            "summary": {"fills_analyzed": 2, "est_total_cost_rs": 500.0},
            "fills": [],
        })
        large = build_calibration_notes({
            "summary": {
                "fills_analyzed": 10,
                "est_total_cost_rs": 5000.0,
                "fills_by_underlying": {"NIFTY": 10},
            },
            "fills": [{"fill_hour": 11.5}],
        })

        self.assertTrue(any("Small sample" in n for n in small))
        self.assertTrue(any("Sufficient real fills" in n for n in large))
        self.assertFalse(any("Sufficient real fills" in n for n in small))

    def test_record_and_load_fill_learning_snapshot(self):
        analysis = analyze_fills(self._sample_trades(), underlying="NIFTY", limit=5)
        with patch("app.fill_learning.FILL_LEARNING_DIR", self._fill_dir):
            path = record_fill_learning_snapshot(analysis)
            self.assertTrue(path.exists())

            loaded = load_latest_fill_learning()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["summary"]["nifty_fills_analyzed"], 1)
            self.assertIn("saved_at", loaded)

    def test_analyze_fills_uses_lot_sizes_for_cost(self):
        with patch("app.fill_learning._get_lot_size", return_value=30):
            analysis = analyze_fills(
                [{
                    "tradingsymbol": "BANKNIFTY26JUNFUT",
                    "quantity": 30,
                    "average_price": 52000.0,
                    "transaction_type": "SELL",
                }],
                underlying="BANKNIFTY",
            )
        fill = analysis["fills"][0]
        self.assertEqual(fill["lot_size"], 30)
        self.assertEqual(fill["lots"], 1)
        self.assertGreater(fill["est_cost_round_turn_rs"], 0)


if __name__ == "__main__":
    unittest.main()