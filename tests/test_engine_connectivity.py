"""
Engine ↔ dashboard connectivity — status, health, and SSE smoke tests.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient
    from web.dashboard import app as dashboard_app

    _CLIENT = TestClient(dashboard_app)
    _HAS_CLIENT = True
except Exception:
    _CLIENT = None
    _HAS_CLIENT = False


@unittest.skipUnless(_HAS_CLIENT, "FastAPI TestClient not available")
class EngineConnectivityTests(unittest.TestCase):
    def test_health_reports_engine_ready(self):
        res = _CLIENT.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("engine_ready", data)

    def test_status_includes_engine_ready_when_loaded(self):
        res = _CLIENT.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        if data.get("engine_ready"):
            self.assertNotIn("error", data)
            self.assertIn("options_legs", data)
            self.assertIn("state", data)
        else:
            self.assertEqual(data.get("error"), "Trading engine not loaded")
            self.assertIn("options_legs", data)

    def test_status_stream_route_registered(self):
        from web.dashboard import status_stream

        self.assertTrue(callable(status_stream))

    def test_status_quick_returns_fast_payload(self):
        res = _CLIENT.get("/api/status/quick")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("engine_ready", data)
        self.assertIn("timestamp", data)
        self.assertIn("market", data)
        if data.get("engine_ready"):
            self.assertNotIn("error", data)
            self.assertIn("state", data)
            self.assertIn("fo_mood", data)