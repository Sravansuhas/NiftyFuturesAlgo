"""
app/trade_ledger.py

Lightweight, append-only trade & signal ledger.
Uses JSONL for simplicity and durability (easy to later migrate to Postgres).

This gives us basic persistence for:
- All entries/exits with gross + net P&L
- Risk decisions
- Reconciliation events

Future: swap the backend for SQLite or Postgres while keeping the same interface.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict


class TradeLedger:
    def __init__(self, path: str = "data/trade_ledger.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, payload: Dict[str, Any]):
        event = {
            "ts": time.time(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def tail(self, n: int = 50):
        if not self.path.exists():
            return []
        lines = self.path.read_text().strip().splitlines()[-n:]
        return [json.loads(l) for l in lines]


trade_ledger = TradeLedger()
