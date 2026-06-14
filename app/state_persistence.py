"""
Per-symbol state persistence for live trading reliability.

Each index (NIFTY, BANKNIFTY, SENSEX) gets its own state file so multi-symbol
paper/live runs survive restarts without cross-contamination.

Broker reconciliation remains authoritative on startup; persisted state only
restores strategy context (entry price, trailing, timers).
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

STATE_DIR = Path("data/state")
LEGACY_STATE_FILE = Path("data/strategy_state.json")


def _normalize_key(symbol_or_key: str) -> str:
    s = (symbol_or_key or "").upper()
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "SENSEX" in s:
        return "SENSEX"
    return "NIFTY"


def _state_path(key: str) -> Path:
    return STATE_DIR / f"{_normalize_key(key)}.json"


def save_symbol_state(symbol_or_key: str, state: Dict[str, Any]) -> None:
    """Save critical strategy state for one index."""
    key = _normalize_key(symbol_or_key)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["symbol_key"] = key
    payload["saved_at"] = time.time()
    path = _state_path(key)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def load_symbol_state(symbol_or_key: str) -> Optional[Dict[str, Any]]:
    """Load persisted state for one index."""
    path = _state_path(symbol_or_key)
    if not path.exists():
        return _migrate_legacy_state(symbol_or_key)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        print(f"[STATE] Failed to load state for {symbol_or_key}: {exc}")
        return None


def load_all_symbol_states() -> Dict[str, Dict[str, Any]]:
    """Load all per-symbol state files (plus legacy migration)."""
    states: Dict[str, Dict[str, Any]] = {}
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        loaded = load_symbol_state(key)
        if loaded:
            states[key] = loaded
    return states


def clear_symbol_state(symbol_or_key: str) -> None:
    """Clear saved state for one index."""
    path = _state_path(symbol_or_key)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def clear_all_symbol_states() -> None:
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        clear_symbol_state(key)


def _migrate_legacy_state(symbol_or_key: str) -> Optional[Dict[str, Any]]:
    """One-time migration from single-file strategy_state.json."""
    if not LEGACY_STATE_FILE.exists():
        return None
    try:
        with open(LEGACY_STATE_FILE, "r", encoding="utf-8") as handle:
            legacy = json.load(handle)
    except Exception:
        return None

    key = _normalize_key(symbol_or_key)
    legacy_key = _normalize_key(str(legacy.get("symbol", legacy.get("symbol_key", "NIFTY"))))
    if legacy_key != key:
        return None

    save_symbol_state(key, legacy)
    try:
        LEGACY_STATE_FILE.unlink()
    except OSError:
        pass
    return legacy


# Backward-compatible aliases (deprecated — prefer per-symbol API)
def save_strategy_state(state: Dict[str, Any]) -> None:
    symbol = state.get("symbol_key") or state.get("symbol") or "NIFTY"
    save_symbol_state(symbol, state)


def load_strategy_state() -> Optional[Dict[str, Any]]:
    states = load_all_symbol_states()
    if not states:
        return None
    if len(states) == 1:
        return next(iter(states.values()))
    return {"multi_symbol": True, "symbols": states}


def clear_strategy_state() -> None:
    clear_all_symbol_states()