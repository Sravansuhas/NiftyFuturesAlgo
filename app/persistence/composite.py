"""
Composite persistence: JSONL always, Postgres best-effort when enabled.

PERSISTENCE_BACKEND=jsonl  -> JSONL only (default)
PERSISTENCE_BACKEND=dual     -> JSONL + Postgres mirror
PERSISTENCE_BACKEND=postgres -> same as dual (JSONL remains source of truth)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from app.db.connection import is_db_enabled
from app.persistence.postgres_backend import insert_audit_event, insert_ledger_event

if TYPE_CHECKING:
    from app.audit_logger import AuditLogger
    from app.trade_ledger import TradeLedger

logger = logging.getLogger(__name__)


def persist_ledger_event(ledger: "TradeLedger", event: Dict[str, Any]) -> None:
    """Write ledger event to JSONL, then mirror to Postgres when configured."""
    ledger._append_event(event)
    if not is_db_enabled():
        return
    try:
        insert_ledger_event(
            ts_epoch=float(event["ts"]),
            event_type=str(event["event_type"]),
            date_ist=str(event["date_ist"]),
            payload=dict(event.get("payload") or {}),
            session_id=event.get("session_id"),
        )
    except Exception as exc:
        logger.warning("Postgres ledger insert failed (JSONL ok): %s", exc)


def persist_audit_event(logger_instance: "AuditLogger", event: Dict[str, Any]) -> None:
    """Write audit event to JSONL, then mirror to Postgres when configured."""
    logger_instance._append_event(event)
    if not is_db_enabled():
        return
    try:
        insert_audit_event(
            ts_epoch=float(event["ts"]),
            event_type=str(event["event_type"]),
            payload=dict(event.get("payload") or {}),
        )
    except Exception as exc:
        logger.warning("Postgres audit insert failed (JSONL ok): %s", exc)