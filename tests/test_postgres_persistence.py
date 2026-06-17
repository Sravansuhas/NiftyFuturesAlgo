import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit_logger import AuditLogger
from app.db.connection import is_db_enabled
from app.persistence.composite import persist_audit_event, persist_ledger_event
from app.persistence.postgres_backend import insert_audit_event, insert_ledger_event
from app.trade_ledger import TradeLedger


def _has_database_url() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def _mock_psycopg_connect(mock_conn: MagicMock) -> MagicMock:
    """Inject a fake psycopg module so tests work without a live driver."""
    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = mock_conn
    mock_psycopg.types.json.Json.side_effect = lambda value: value
    return mock_psycopg


class PostgresPersistenceTests(unittest.TestCase):
    def test_is_db_enabled_requires_backend_and_url(self):
        with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "jsonl", "DATABASE_URL": "postgresql://x"}, clear=False):
            self.assertFalse(is_db_enabled())
        with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": ""}, clear=False):
            self.assertFalse(is_db_enabled())
        with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
            self.assertTrue(is_db_enabled())
        with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "postgres", "DATABASE_URL": "postgresql://x"}, clear=False):
            self.assertTrue(is_db_enabled())

    def test_insert_audit_event_no_url_is_noop(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            insert_audit_event(ts_epoch=1.0, event_type="test", payload={"k": "v"})

    def test_insert_ledger_event_no_url_is_noop(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            insert_ledger_event(
                ts_epoch=1.0,
                event_type="order.placed",
                date_ist="2026-06-15",
                payload={"symbol": "NIFTY"},
            )

    def test_insert_audit_event_executes_sql(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg = _mock_psycopg_connect(mock_conn)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test/db"}, clear=False):
            with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.types": mock_psycopg.types}):
                insert_audit_event(ts_epoch=123.45, event_type="kite.postback", payload={"ok": True})

        mock_psycopg.connect.assert_called_once()
        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        self.assertIn("INSERT INTO audit_logs", sql)
        self.assertEqual(params[0], 123.45)
        self.assertEqual(params[1], "kite.postback")
        mock_conn.commit.assert_called_once()

    def test_insert_ledger_event_executes_sql(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg = _mock_psycopg_connect(mock_conn)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test/db"}, clear=False):
            with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.types": mock_psycopg.types}):
                insert_ledger_event(
                    ts_epoch=99.0,
                    event_type="trade.closed",
                    date_ist="2026-06-15",
                    session_id="sess-1",
                    payload={"pnl": 100},
                )

        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        self.assertIn("INSERT INTO trade_ledger", sql)
        self.assertEqual(params[0], 99.0)
        self.assertEqual(params[1], "trade.closed")
        self.assertEqual(params[2], "sess-1")
        self.assertEqual(params[3], "2026-06-15")

    def test_persist_ledger_dual_writes_jsonl_and_postgres(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(path))
            event = {
                "ts": 1.0,
                "event_type": "order.placed",
                "payload": {"symbol": "NIFTY"},
                "session_id": "s1",
                "date_ist": "2026-06-15",
            }
            with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
                with patch("app.persistence.composite.insert_ledger_event") as insert:
                    persist_ledger_event(ledger, event)
                    insert.assert_called_once()

            self.assertTrue(path.exists())
            self.assertIn("order.placed", path.read_text(encoding="utf-8"))

    def test_persist_audit_dual_writes_jsonl_and_postgres(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path=str(path))
            event = {"ts": 2.0, "event_type": "emergency.halt", "payload": {"reason": "test"}}
            with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
                with patch("app.persistence.composite.insert_audit_event") as insert:
                    persist_audit_event(logger, event)
                    insert.assert_called_once()

            self.assertTrue(path.exists())
            self.assertIn("emergency.halt", path.read_text(encoding="utf-8"))

    def test_persist_ledger_postgres_failure_does_not_block_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(path))
            event = {
                "ts": 3.0,
                "event_type": "session.start",
                "payload": {},
                "date_ist": "2026-06-15",
            }
            with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
                with patch("app.persistence.composite.insert_ledger_event", side_effect=RuntimeError("db down")):
                    persist_ledger_event(ledger, event)

            self.assertIn("session.start", path.read_text(encoding="utf-8"))

    def test_trade_ledger_record_uses_composite_when_db_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger = TradeLedger(path=str(path))
            with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
                with patch("app.persistence.composite.insert_ledger_event"):
                    ledger.record("order.placed", {"symbol": "BANKNIFTY"})
            self.assertIn("order.placed", path.read_text(encoding="utf-8"))

    def test_audit_logger_record_uses_composite_when_db_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path=str(path))
            with patch.dict(os.environ, {"PERSISTENCE_BACKEND": "dual", "DATABASE_URL": "postgresql://x"}, clear=False):
                with patch("app.persistence.composite.insert_audit_event"):
                    logger.record("recon.mismatch_halt", {"count": 1})
            self.assertIn("recon.mismatch_halt", path.read_text(encoding="utf-8"))


@unittest.skipUnless(_has_database_url(), "DATABASE_URL not set")
class PostgresIntegrationTests(unittest.TestCase):
    def test_live_insert_roundtrip(self):
        from app.db.connection import ping_database

        if not ping_database():
            self.skipTest("Postgres not reachable")

        ts = 1718448000.0
        insert_audit_event(ts_epoch=ts, event_type="integration.test", payload={"probe": True})
        insert_ledger_event(
            ts_epoch=ts,
            event_type="integration.test",
            date_ist="2026-06-15",
            session_id="integration",
            payload={"probe": True},
        )


if __name__ == "__main__":
    unittest.main()