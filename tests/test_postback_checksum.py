"""
Postback checksum validation — unit + dashboard integration tests.

Kite formula: SHA-256(order_id + order_timestamp + api_secret)
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.postback_checksum import compute_postback_checksum, verify_postback_checksum

# Official docs sample (checksum only; api_secret is app-specific).
KITE_DOCS_ORDER_ID = "220303000308932"
KITE_DOCS_ORDER_TIMESTAMP = "2022-03-03 09:24:25"
KITE_DOCS_CHECKSUM = (
    "2011845d9348bd6795151bf4258102a03431e3bb12a79c0df73fcb4b7fde4b5d"
)

TEST_API_SECRET = "unit_test_api_secret_9f3a"


def _expected_checksum(order_id: str, order_timestamp: str, api_secret: str) -> str:
    return hashlib.sha256(f"{order_id}{order_timestamp}{api_secret}".encode()).hexdigest()


class PostbackChecksumUnitTests(unittest.TestCase):
    def test_compute_matches_sha256_concatenation(self):
        order_id = "ORD-1001"
        order_timestamp = "2026-06-12 10:15:30"
        expected = _expected_checksum(order_id, order_timestamp, TEST_API_SECRET)
        self.assertEqual(
            compute_postback_checksum(order_id, order_timestamp, TEST_API_SECRET),
            expected,
        )

    def test_kite_docs_sample_vector_with_test_secret(self):
        """Formula uses order_id + order_timestamp + api_secret (no separators)."""
        checksum = compute_postback_checksum(
            KITE_DOCS_ORDER_ID,
            KITE_DOCS_ORDER_TIMESTAMP,
            TEST_API_SECRET,
        )
        self.assertEqual(len(checksum), 64)
        self.assertEqual(
            checksum,
            _expected_checksum(
                KITE_DOCS_ORDER_ID,
                KITE_DOCS_ORDER_TIMESTAMP,
                TEST_API_SECRET,
            ),
        )
        self.assertNotEqual(checksum, KITE_DOCS_CHECKSUM)

    def test_verify_accepts_valid_payload(self):
        payload = {
            "order_id": KITE_DOCS_ORDER_ID,
            "order_timestamp": KITE_DOCS_ORDER_TIMESTAMP,
            "checksum": _expected_checksum(
                KITE_DOCS_ORDER_ID,
                KITE_DOCS_ORDER_TIMESTAMP,
                TEST_API_SECRET,
            ),
            "status": "COMPLETE",
        }
        ok, reason, computed = verify_postback_checksum(payload, api_secret=TEST_API_SECRET)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIsNotNone(computed)

    def test_verify_rejects_checksum_mismatch(self):
        payload = {
            "order_id": KITE_DOCS_ORDER_ID,
            "order_timestamp": KITE_DOCS_ORDER_TIMESTAMP,
            "checksum": "deadbeef" * 8,
            "status": "COMPLETE",
        }
        ok, reason, computed = verify_postback_checksum(payload, api_secret=TEST_API_SECRET)
        self.assertFalse(ok)
        self.assertEqual(reason, "checksum_mismatch")
        self.assertNotEqual(computed, payload["checksum"])

    def test_verify_rejects_missing_fields(self):
        ok, reason, computed = verify_postback_checksum(
            {"order_id": "1", "order_timestamp": "t"},
            api_secret=TEST_API_SECRET,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_fields")
        self.assertIsNone(computed)

    def test_verify_rejects_missing_api_secret(self):
        payload = {
            "order_id": "1",
            "order_timestamp": "t",
            "checksum": "abc",
        }
        with patch.dict("os.environ", {"KITE_API_SECRET": ""}, clear=False):
            ok, reason, computed = verify_postback_checksum(payload, api_secret=None)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_api_secret")
        self.assertIsNone(computed)


_HAS_TEST_CLIENT = False
_TEST_CLIENT = None
try:
    from fastapi.testclient import TestClient
    from web.dashboard import app as _dashboard_app

    _TEST_CLIENT = TestClient(_dashboard_app)
    _HAS_TEST_CLIENT = True
except Exception:
    pass


class PostbackDashboardRouteTests(unittest.TestCase):
    def _valid_payload(self) -> dict:
        return {
            "user_id": "AB1234",
            "order_id": KITE_DOCS_ORDER_ID,
            "order_timestamp": KITE_DOCS_ORDER_TIMESTAMP,
            "checksum": _expected_checksum(
                KITE_DOCS_ORDER_ID,
                KITE_DOCS_ORDER_TIMESTAMP,
                TEST_API_SECRET,
            ),
            "status": "COMPLETE",
            "filled_quantity": 1,
            "average_price": 470.0,
        }

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_postback_endpoint_accepts_valid_checksum(self):
        payload = self._valid_payload()
        with patch.dict("os.environ", {"KITE_API_SECRET": TEST_API_SECRET}, clear=False):
            with patch("app.order_lifecycle.order_lifecycle.handle_postback", return_value={"processed": True}):
                with patch("app.trade_ledger.trade_ledger.record"):
                    res = _TEST_CLIENT.post("/api/kite/postback", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_postback_endpoint_rejects_invalid_checksum_with_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit_events.json"
            payload = self._valid_payload()
            payload["checksum"] = "0" * 64
            with patch.dict("os.environ", {"KITE_API_SECRET": TEST_API_SECRET}, clear=False):
                with patch("web.dashboard.audit_logger") as mock_audit:
                    res = _TEST_CLIENT.post("/api/kite/postback", json=payload)
                    self.assertEqual(res.status_code, 200)
                    self.assertEqual(res.json(), {"status": "invalid_checksum"})
                    mock_audit.record.assert_called_once()
                    args = mock_audit.record.call_args[0]
                    self.assertEqual(args[0], "kite.postback.rejected")
                    self.assertEqual(args[1]["reason"], "checksum_mismatch")
                    self.assertEqual(args[1]["order_id"], KITE_DOCS_ORDER_ID)

    @unittest.skipUnless(_HAS_TEST_CLIENT, "FastAPI TestClient not available")
    def test_postback_endpoint_rejects_missing_checksum(self):
        payload = self._valid_payload()
        del payload["checksum"]
        with patch.dict("os.environ", {"KITE_API_SECRET": TEST_API_SECRET}, clear=False):
            with patch("web.dashboard.audit_logger") as mock_audit:
                res = _TEST_CLIENT.post("/api/kite/postback", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "invalid"})
        mock_audit.record.assert_called_once()
        self.assertEqual(mock_audit.record.call_args[0][1]["reason"], "missing_fields")


if __name__ == "__main__":
    unittest.main()