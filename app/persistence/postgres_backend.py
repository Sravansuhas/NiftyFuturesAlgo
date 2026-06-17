"""
Best-effort Postgres inserts for audit + trade ledger mirrors.

Never raises to callers — trading must not depend on DB availability.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.db.connection import get_database_url

logger = logging.getLogger(__name__)


def insert_audit_event(
    *,
    ts_epoch: float,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """Insert one row into audit_logs. Raises on failure (caller should catch)."""
    url = get_database_url()
    if not url:
        return

    import psycopg

    with psycopg.connect(url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_logs (ts_epoch, event_type, payload)
                VALUES (%s, %s, %s)
                """,
                (ts_epoch, event_type, psycopg.types.json.Json(payload)),
            )
        conn.commit()


def insert_ledger_event(
    *,
    ts_epoch: float,
    event_type: str,
    date_ist: str,
    payload: Dict[str, Any],
    session_id: Optional[str] = None,
) -> None:
    """Insert one row into trade_ledger. Raises on failure (caller should catch)."""
    url = get_database_url()
    if not url:
        return

    import psycopg

    with psycopg.connect(url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trade_ledger (ts_epoch, event_type, session_id, date_ist, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    ts_epoch,
                    event_type,
                    session_id,
                    date_ist,
                    psycopg.types.json.Json(payload),
                ),
            )
        conn.commit()