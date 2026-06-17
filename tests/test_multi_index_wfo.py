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
    DEFAULT_INDICES,
    build_multi_index_report,
    find_latest_multi_index_report,
    load_multi_index_report,
    parse_indices,
    run_multi_index_wfo,
    write_multi_index_report,
)


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


class MultiIndexWfoHelpersTests(unittest.TestCase):
    def test_parse_indices_defaults(self):
        self.assertEqual(parse_indices(None), list(DEFAULT_INDICES))
        self.assertEqual(parse_indices("NIFTY,BANKNIFTY"), ["NIFTY", "BANKNIFTY"])

    def test_parse_indices_rejects_unknown(self):
        with self.assertRaises(ValueError):
            parse_indices("NIFTY,FOO")

    def test_build_multi_index_report_structure(self):
        results = {
            "NIFTY": _mock_wfo_summary("NIFTY", passed=True),
            "BANKNIFTY": _mock_wfo_summary("BANKNIFTY", passed=False),
            "SENSEX": {"underlying": "SENSEX", "error": "no cache", "promotion": {"passed": False}},
        }
        report = build_multi_index_report(
            indices=DEFAULT_INDICES,
            results=results,
            config={"days": 180, "indices": list(DEFAULT_INDICES), "cache_only": True},
            started_at=1000.0,
            finished_at=1100.0,
            run_id="multi_index_test",
        )

        self.assertEqual(report["run_id"], "multi_index_test")
        self.assertIn("indices", report)
        self.assertIn("summary", report)
        self.assertTrue(report["indices"]["NIFTY"]["passed"])
        self.assertFalse(report["indices"]["BANKNIFTY"]["passed"])
        self.assertEqual(report["indices"]["SENSEX"]["exchange"], "BFO")
        self.assertEqual(report["indices"]["NIFTY"]["exchange"], "NFO")
        self.assertEqual(report["summary"]["passed_indices"], ["NIFTY"])
        self.assertIn("BANKNIFTY", report["summary"]["failed_indices"])
        self.assertIn("SENSEX", report["summary"]["errored_indices"])

    def test_write_and_load_report_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "wfo_runs"
            report = build_multi_index_report(
                indices=["NIFTY"],
                results={"NIFTY": _mock_wfo_summary("NIFTY", passed=True)},
                config={"days": 90, "indices": ["NIFTY"], "cache_only": True},
                started_at=1.0,
                finished_at=2.0,
                run_id="multi_index_roundtrip",
            )
            path = write_multi_index_report(report, runs_dir=runs_dir)
            self.assertTrue(path.exists())
            loaded = load_multi_index_report(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["run_id"], "multi_index_roundtrip")
            self.assertTrue(loaded["indices"]["NIFTY"]["passed"])

    def test_find_latest_multi_index_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            older = runs_dir / "multi_index_20260101_100000.json"
            newer = runs_dir / "multi_index_20260615_120000.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            older.touch()
            newer.touch()
            # Ensure newer mtime
            import os
            import time

            time.sleep(0.01)
            os.utime(newer, None)
            latest = find_latest_multi_index_report(runs_dir=runs_dir)
            self.assertEqual(latest, newer)


class MultiIndexWfoRunTests(unittest.TestCase):
    def test_run_multi_index_wfo_mocked(self):
        kite = MagicMock()

        def fake_run_index(**kwargs):
            underlying = kwargs["underlying"]
            return _mock_wfo_summary(underlying, passed=(underlying == "NIFTY"))

        with patch("scripts.run_multi_index_wfo._run_index", side_effect=fake_run_index):
            with patch("scripts.run_multi_index_wfo.index_lot_size", side_effect=lambda u: {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}[u]):
                with patch("scripts.run_multi_index_wfo.index_exchange", side_effect=lambda u: "BFO" if u == "SENSEX" else "NFO"):
                    with tempfile.TemporaryDirectory() as tmp:
                        runs_dir = Path(tmp)
                        with patch("scripts.run_multi_index_wfo.WFO_RUNS_DIR", runs_dir):
                            report = run_multi_index_wfo(
                                indices=["NIFTY", "BANKNIFTY"],
                                days=180,
                                cache_only=True,
                                kite=kite,
                            )
                        self.assertEqual(report["summary"]["passed_indices"], ["NIFTY"])
                        self.assertFalse(report["summary"]["all_passed"])
                        self.assertTrue(any(runs_dir.glob("multi_index_*.json")))


class MultiIndexWfoOpsHubTests(unittest.TestCase):
    def test_run_multi_index_wfo_status_reads_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            payload = build_multi_index_report(
                indices=list(DEFAULT_INDICES),
                results={idx: _mock_wfo_summary(idx, passed=(idx == "NIFTY")) for idx in DEFAULT_INDICES},
                config={"days": 180, "indices": list(DEFAULT_INDICES), "cache_only": True},
                started_at=datetime.now().timestamp(),
                run_id="multi_index_ops_test",
            )
            write_multi_index_report(payload, runs_dir=runs_dir)

            with patch.object(hub, "ROOT", ROOT):
                with patch.object(hub, "_latest_multi_index_report_path", return_value=runs_dir / "multi_index_ops_test.json"):
                    status = hub.run_multi_index_wfo_status()

            self.assertTrue(status["has_report"])
            self.assertEqual(status["run_id"], "multi_index_ops_test")
            self.assertTrue(status["per_index"]["NIFTY"]["passed"])
            self.assertFalse(status["per_index"]["BANKNIFTY"]["passed"])
            self.assertEqual(status["per_index"]["SENSEX"]["exchange"], "BFO")

    def test_run_multi_index_wfo_status_empty(self):
        with patch.object(hub, "_latest_multi_index_report_path", return_value=None):
            status = hub.run_multi_index_wfo_status()
        self.assertFalse(status["has_report"])


class MultiIndexWfoCacheIntegrationTests(unittest.TestCase):
    def test_skip_when_no_cache_available(self):
        from backtesting.data_loader import list_available_cached_datasets

        datasets = list_available_cached_datasets()
        has_any = any(
            (ds.get("symbol") or "").upper().startswith(prefix)
            for ds in datasets
            for prefix in ("NIFTY", "BANKNIFTY", "SENSEX")
        )
        if has_any:
            self.skipTest("historical_cache present — offline integration left to manual WFO run")

        with patch("scripts.run_multi_index_wfo.validate_access_token", return_value=(False, None, "no token")):
            with patch("scripts.run_multi_index_wfo._run_index") as mock_run:
                from scripts.run_multi_index_wfo import main

                mock_run.return_value = {
                    "underlying": "NIFTY",
                    "error": "no cache",
                    "promotion": {"passed": False},
                    "data_meta": {"error": "no cache"},
                }
                with patch("scripts.run_multi_index_wfo.instruments_manager.bind"):
                    with tempfile.TemporaryDirectory() as tmp:
                        with patch("scripts.run_multi_index_wfo.WFO_RUNS_DIR", Path(tmp)):
                            code = main(["--days", "30", "--indices", "NIFTY", "--cache-only"])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()