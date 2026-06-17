#!/usr/bin/env python3
"""Apply SQL migrations when DATABASE_URL is configured."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIGRATIONS_DIR = ROOT / "migrations"


def main() -> int:
    from app.db.connection import get_database_url, ping_database

    url = get_database_url()
    if not url:
        print("[DB] DATABASE_URL not set — skipping migrations")
        return 0

    if not ping_database():
        print("[DB] Cannot connect to Postgres")
        return 1

    import psycopg

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("[DB] No migration files found")
        return 0

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for path in sql_files:
                print(f"[DB] Applying {path.name}...")
                cur.execute(path.read_text(encoding="utf-8"))
        conn.commit()

    print(f"[DB] Applied {len(sql_files)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())