"""
Lightweight tests for Phase 4C improvement APIs and core modules.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ImprovementModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.session_tracker_mod = __import__("app.session_tracker", fromlist=["SessionTracker"])
        self.improvement_loop_mod = __import__("app.improvement_loop", fromlist=["ImprovementLoop"])
        self.tracker = self.session_tracker_mod.SessionTracker(reports_dir=base / "sessions")
        self.loop = self.improvement_loop_mod.ImprovementLoop(
            earn_dir=base / "weekly",
            proposals_dir=base / "proposals",
            applied_dir=base / "proposals" / "applied",
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_build_daily_session_report_has_expected_keys(self):
        report = self.tracker.build_daily_session_report()
        for key in (
            "date_ist",
            "quality_score",
            "quality_grade",
            "quality_components",
            "event_metrics",
            "risk_snapshot",
            "founder_actions",
        ):
            self.assertIn(key, report)
        self.assertGreaterEqual(report["quality_score"], 0)
        self.assertLessEqual(report["quality_score"], 100)

    def test_save_and_load_daily_report(self):
        report = self.tracker.build_daily_session_report()
        path = self.tracker.save_daily_session_report(report)
        self.assertTrue(path.exists())
        loaded = self.tracker.load_daily_session_report(report["date_ist"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["quality_score"], report["quality_score"])

    def test_weekly_report_has_expected_keys(self):
        report = self.loop.build_weekly_report(days=7)
        for key in (
            "session_summary",
            "wfa_summary",
            "fill_calibration",
            "founder_actions",
            "improvement_proposals",
            "week_start",
            "week_end",
        ):
            self.assertIn(key, report)

    def test_list_pending_proposals(self):
        proposal_path = self.loop.proposals_dir / "test_prop.json"
        proposal_path.write_text(
            json.dumps({"proposal_id": "test_prop", "status": "pending", "title": "Test"}),
            encoding="utf-8",
        )
        pending = self.loop.list_pending_proposals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_id"], "test_prop")

    def test_apply_proposal_requires_confirmation(self):
        proposal_path = self.loop.proposals_dir / "gate_test.json"
        proposal_path.write_text(
            json.dumps({"proposal_id": "gate_test", "status": "pending"}),
            encoding="utf-8",
        )
        result = self.loop.apply_proposal_manifest("gate_test", human_confirmed=False)
        self.assertFalse(result["success"])
        self.assertIn("human_confirmation", result.get("error", ""))

    def test_apply_proposal_manifest_on_confirmed(self):
        proposal_path = self.loop.proposals_dir / "apply_test.json"
        proposal_path.write_text(
            json.dumps({"proposal_id": "apply_test", "status": "pending", "title": "Apply me"}),
            encoding="utf-8",
        )
        result = self.loop.apply_proposal_manifest("apply_test", human_confirmed=True)
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["manifest_path"]).exists())
        updated = json.loads(proposal_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["status"], "applied")


_HAS_TEST_CLIENT = False
_TEST_CLIENT = None
try:
    from fastapi.testclient import TestClient
    from web.dashboard import app as _dashboard_app

    _TEST_CLIENT = TestClient(_dashboard_app)
    _HAS_TEST_CLIENT = True
except Exception:
    pass


class ImprovementDashboardRouteTests(unittest.TestCase):
    def test_dashboard_route_functions_exist(self):
        import web.dashboard as dashboard

        for name in (
            "get_improvement_daily",
            "get_improvement_weekly",
            "get_improvement_proposals",
            "apply_improvement_proposal",
            "get_improvement_fill_learning",
        ):
            self.assertTrue(callable(getattr(dashboard, name, None)), f"missing {name}")

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_daily_endpoint_returns_expected_keys(self):
        res = _TEST_CLIENT.get("/api/improvement/daily")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("report", data)
        if data.get("report"):
            self.assertIn("quality_score", data["report"])

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_proposals_endpoint_returns_count(self):
        res = _TEST_CLIENT.get("/api/improvement/proposals")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("proposals", data)
        self.assertIn("pending_count", data)

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_fill_learning_endpoint_returns_snapshot(self):
        res = _TEST_CLIENT.get("/api/improvement/fill-learning")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("snapshot", data)
        self.assertIn("has_data", data)


if __name__ == "__main__":
    unittest.main()