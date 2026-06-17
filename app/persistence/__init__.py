"""Dual-write persistence: JSONL (default) + optional Postgres mirror."""

from .composite import persist_audit_event, persist_ledger_event
from .postgres_backend import insert_audit_event, insert_ledger_event

__all__ = [
    "insert_audit_event",
    "insert_ledger_event",
    "persist_audit_event",
    "persist_ledger_event",
]