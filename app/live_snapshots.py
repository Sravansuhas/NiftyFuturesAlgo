"""
Simple in-memory store for live 3-index snapshots.
Used by main trading loop and read by dashboard.
"""

from typing import Dict

# symbol -> snapshot dict
_snapshots: Dict[str, dict] = {}

def update_snapshot(symbol: str, snapshot: dict):
    _snapshots[symbol] = snapshot

def get_all_snapshots() -> Dict[str, dict]:
    return dict(_snapshots)

def get_snapshot(symbol: str) -> dict:
    return _snapshots.get(symbol, {})