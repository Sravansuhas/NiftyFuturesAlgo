"""
Lazy Postgres connection helpers.

Trading never depends on DB availability — JSONL remains default source of truth.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def get_database_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def is_db_enabled() -> bool:
    backend = os.getenv("PERSISTENCE_BACKEND", "jsonl").strip().lower()
    return backend in {"dual", "postgres"} and bool(get_database_url())


def ping_database() -> bool:
    """Return True if DATABASE_URL is set and Postgres accepts a connection."""
    url = get_database_url()
    if not url:
        return False
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("Postgres ping failed: %s", exc)
        return False