"""
Lightweight state persistence for live trading reliability.

Purpose:
- Allow the strategy to survive restarts without losing important context
  (entry price, best price for trailing, entry time, etc.)

This is critical for production reliability. We only persist what is
absolutely necessary and always reconcile with the broker on startup.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
import time


STATE_FILE = Path("data/strategy_state.json")


def save_strategy_state(state: Dict[str, Any]):
    """Save critical strategy state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["saved_at"] = time.time()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_strategy_state() -> Optional[Dict[str, Any]]:
    """Load previously saved strategy state."""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        return state
    except Exception as e:
        print(f"[STATE] Failed to load strategy state: {e}")
        return None


def clear_strategy_state():
    """Clear saved state (called after successful reconciliation or flat position)."""
    if STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
        except Exception:
            pass