"""
Dashboard ops API tests — TestClient against web.dashboard with mocked ops_hub.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_HAS_TEST_CLIENT = False
_TEST_CLIENT = None
try:
    from fastapi.testclient import TestClient
    from web.dashboard import app as _dashboard_app

    _TEST_CLIENT = TestClient(_dashboard_app)
    _HAS_TEST_CLIENT = True
except Exception:
    pass


class OpsApiRouteTests(unittest.TestCase):
    def test_dashboard_ops_route_functions_exist(self):
        import web.dashboard as dashboard

        for name in (
            "get_ops_preflight",
            "get_ops_status",
            "get_ops_compliance",
        ):
            self.assertTrue(callable(getattr(dashboard, name, None)), f"missing {name}")

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    @patch("app.ops_hub.run_preflight")
    def test_preflight_endpoint_returns_mock_payload(self, mock_preflight):
        mock_preflight.return_value = {
            "ready": True,
            "mode": "paper",
            "status": {"healthy": True},
            "compliance": {"passed": True, "automated_passed": 20, "automated_total": 20},
            "data_health": {"healthy": True},
            "wfo": {"any_passed": False},
            "blockers": [],
            "warnings": ["sparse cache: NIFTY"],
        }

        res = _TEST_CLIENT.get("/api/ops/preflight?days=2&skip_token=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ready"])
        self.assertEqual(data["mode"], "paper")
        self.assertIn("warnings", data)
        mock_preflight.assert_called_once_with(validate_token=False, audit_days=2)

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    @patch("app.ops_hub.build_status_report")
    def test_status_endpoint_returns_mock_payload(self, mock_status):
        mock_status.return_value = {
            "healthy": True,
            "mode": "paper",
            "algo_id": "TESTOPS",
            "state": "BOOTING",
            "checks": [],
            "blockers": [],
            "warnings": [],
        }

        res = _TEST_CLIENT.get("/api/ops/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["healthy"])
        self.assertEqual(data["algo_id"], "TESTOPS")
        self.assertIn("timestamp", data)
        mock_status.assert_called_once_with(validate_token=True)

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    @patch("app.ops_hub.run_compliance_checks")
    def test_compliance_endpoint_returns_mock_payload(self, mock_compliance):
        mock_compliance.return_value = {
            "passed": True,
            "automated_passed": 18,
            "automated_total": 18,
            "failed_ids": [],
            "blockers": [],
            "checks": [{"id": "algo_id_resolved", "passed": True}],
            "manual_checks": ["extended_paper_trading"],
        }

        res = _TEST_CLIENT.get("/api/ops/compliance")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["passed"])
        self.assertEqual(data["automated_passed"], 18)
        self.assertIn("timestamp", data)
        mock_compliance.assert_called_once()

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    @patch("app.ops_hub.run_preflight")
    def test_preflight_endpoint_handles_exception(self, mock_preflight):
        mock_preflight.side_effect = RuntimeError("ops hub unavailable")

        res = _TEST_CLIENT.get("/api/ops/preflight")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["ready"])
        self.assertIn("error", data)
        self.assertIn("ops hub unavailable", data["error"])


if __name__ == "__main__":
    unittest.main()