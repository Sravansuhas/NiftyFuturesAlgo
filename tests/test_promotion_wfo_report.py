import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ops_hub as hub
from scripts.run_multi_index_wfo import (
    find_latest_multi_index_report,
    load_multi_index_report,
    write_multi_index_report,
)
from scripts.run_promotion_wfo import INDICES, main
from scripts.run_multi_index_wfo import build_multi_index_report


def _mock_wfo_summary(underlying: str, *, passed: bool) -> dict:
    return {
        "underlying": underlying,
        "avg_return": 1.2 if passed else -0.5,
        "avg_pf": 1.4 if passed else 0.9,
        "total_folds_run": 3,
        "total_trades": 18,
        "data_meta": {
            "rows": 2500,
            "from": "2025-12-01",
            "to": "2026-06-01",
            "exchange": "BFO" if underlying == "SENSEX" else "NFO",
            "lot_size": 20 if underlying == "SENSEX" else (30 if underlying == "BANKNIFTY" else 65),
            "cache_only": True,
        },
        "promotion": {
            "passed": passed,
            "underlying": underlying,
            "fold_pass_count": 2 if passed else 0,
            "reasons": [] if passed else ["only 0/3 folds passed (need 2)"],
            "summary": {
                "fold_reports": [
                    {"fold": 1, "passed": passed, "reasons": [] if passed else ["PF=0.90 < 1.2"]},
                    {"fold": 2, "passed": passed, "reasons": []},
                ],
            },
        },
    }


class PromotionWfoReportTests(unittest.TestCase):
    def test_main_writes_multi_index_report(self):
        kite = MagicMock()

        def fake_run_index(**kwargs):
            underlying = kwargs["underlying"]
            return _mock_wfo_summary(underlying, passed=(underlying == "NIFTY"))

        with patch("scripts.run_promotion_wfo.validate_access_token", return_value=(True, None, "ok")):
            with patch("scripts.run_promotion_wfo.instruments_manager.bind"):
                with patch("scripts.run_promotion_wfo.KiteConnect", return_value=kite):
                    with patch("scripts.run_promotion_wfo.load_dotenv"):
                        with patch("scripts.run_promotion_wfo.load_candidates", return_value=[]):
                            with patch(
                                "scripts.run_promotion_wfo._run_index",
                                side_effect=fake_run_index,
                            ) as mock_run_index:
                                with tempfile.TemporaryDirectory() as tmp:
                                    runs_dir = Path(tmp)
                                    with patch("scripts.run_multi_index_wfo.WFO_RUNS_DIR", runs_dir):
                                        with patch(
                                            "sys.argv",
                                            [
                                                "run_promotion_wfo.py",
                                                "--cache-only",
                                                "--months",
                                                "5",
                                                "--cost-multiplier",
                                                "2.5",
                                            ],
                                        ):
                                            code = main()

                                    self.assertEqual(code, 1)
                                    self.assertEqual(mock_run_index.call_count, len(INDICES))
                                    called_indices = {
                                        call.kwargs["underlying"] for call in mock_run_index.call_args_list
                                    }
                                    self.assertEqual(called_indices, set(INDICES))

                                    report_files = list(runs_dir.glob("multi_index_*.json"))
                                    self.assertEqual(len(report_files), 1)

                                    latest = find_latest_multi_index_report(runs_dir=runs_dir)
                                    self.assertIsNotNone(latest)
                                    loaded = load_multi_index_report(latest)
                                    self.assertIsNotNone(loaded)

                                    config = loaded["config"]
                                    self.assertEqual(config["source"], "run_promotion_wfo")
                                    self.assertEqual(config["cost_multiplier"], 2.5)
                                    self.assertTrue(config["cache_only"])
                                    self.assertEqual(config["months"], 5)
                                    self.assertEqual(config["indices"], list(INDICES))

                                    self.assertTrue(loaded["indices"]["NIFTY"]["passed"])
                                    self.assertFalse(loaded["indices"]["BANKNIFTY"]["passed"])
                                    self.assertFalse(loaded["indices"]["SENSEX"]["passed"])
                                    self.assertEqual(loaded["summary"]["passed_indices"], ["NIFTY"])

                                    with latest.open("r", encoding="utf-8") as handle:
                                        raw = json.load(handle)
                                    self.assertEqual(raw["run_id"], loaded["run_id"])


class PromotionWfoOpsHubTests(unittest.TestCase):
    def test_run_multi_index_wfo_status_reads_promotion_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            results = {
                idx: _mock_wfo_summary(idx, passed=(idx == "NIFTY"))
                for idx in INDICES
            }
            payload = build_multi_index_report(
                indices=list(INDICES),
                results=results,
                config={
                    "source": "run_promotion_wfo",
                    "months": 5,
                    "indices": list(INDICES),
                    "cache_only": True,
                    "cost_multiplier": 2.0,
                },
                started_at=datetime.now().timestamp(),
                run_id="multi_index_promotion_ops_test",
            )
            report_path = write_multi_index_report(payload, runs_dir=runs_dir)

            with patch.object(hub, "ROOT", ROOT):
                with patch.object(hub, "_latest_multi_index_report_path", return_value=report_path):
                    status = hub.run_multi_index_wfo_status()

            self.assertTrue(status["has_report"])
            self.assertEqual(status["run_id"], "multi_index_promotion_ops_test")
            self.assertEqual(status["source"], "run_promotion_wfo")
            self.assertEqual(status["config"]["cost_multiplier"], 2.0)
            self.assertEqual(status["config"]["source"], "run_promotion_wfo")
            self.assertTrue(status["per_index"]["NIFTY"]["passed"])
            self.assertFalse(status["per_index"]["BANKNIFTY"]["passed"])
            self.assertEqual(status["per_index"]["SENSEX"]["exchange"], "BFO")


if __name__ == "__main__":
    unittest.main()