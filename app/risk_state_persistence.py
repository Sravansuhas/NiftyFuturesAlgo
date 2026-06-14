"""
Persist multi-symbol risk manager state across restarts.

Saves paper positions, daily P&L counters, and trade counts to data/risk_state.json.
Restored only when date_ist matches today (IST) — new trading day starts fresh counters
but open positions from prior day would need manual review (not auto-restored跨日).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .market_calendar import now_ist

RISK_STATE_FILE = Path("data/risk_state.json")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def snapshot_risk_manager(mgr) -> Dict[str, Any]:
    """Serialize recoverable risk manager fields."""
    positions = {}
    for sym in INDICES:
        pos = mgr.positions.get(sym)
        if pos is None:
            continue
        positions[sym] = {
            "quantity": int(pos.quantity),
            "avg_price": float(pos.avg_price or 0),
            "symbol": pos.symbol,
            "last_updated": pos.last_updated,
        }
    return {
        "date_ist": now_ist().strftime("%Y-%m-%d"),
        "saved_at": time.time(),
        "capital": float(mgr.capital),
        "daily_pnl": float(mgr.daily_pnl),
        "daily_loss": float(mgr.daily_loss),
        "current_equity": float(mgr.current_equity),
        "peak_equity": float(mgr.peak_equity),
        "consecutive_losses": int(mgr.consecutive_losses),
        "trades_today": int(mgr.trades_today),
        "symbol_daily_trades": dict(mgr.symbol_daily_trades),
        "symbol_daily_pnl": {k: float(v) for k, v in mgr.symbol_daily_pnl.items()},
        "symbol_daily_loss": {k: float(v) for k, v in mgr.symbol_daily_loss.items()},
        "last_loss_timestamp": float(mgr.last_loss_timestamp or 0),
        "positions": positions,
        "force_dry_run": bool(mgr.config.force_dry_run),
    }


def save_risk_state(mgr, path: Path = RISK_STATE_FILE) -> Path:
    payload = snapshot_risk_manager(mgr)
    _atomic_write(path, payload)
    return path


def load_risk_state(path: Path = RISK_STATE_FILE) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def restore_risk_manager(mgr, data: Optional[Dict[str, Any]] = None) -> bool:
    """
    Restore paper risk state for today's session.
    Returns True if state was applied.
    """
    data = data if data is not None else load_risk_state()
    if not data:
        return False

    today = now_ist().strftime("%Y-%m-%d")
    if data.get("date_ist") != today:
        return False

    if not data.get("force_dry_run", True) and mgr.config.force_dry_run:
        pass  # paper restore only when manager is in dry-run
    if not mgr.config.force_dry_run:
        return False  # live mode: broker reconciliation is authoritative

    mgr.capital = float(data.get("capital", mgr.capital))
    mgr.daily_pnl = float(data.get("daily_pnl", 0))
    mgr.daily_loss = float(data.get("daily_loss", 0))
    mgr.current_equity = float(data.get("current_equity", mgr.capital))
    mgr.peak_equity = float(data.get("peak_equity", mgr.capital))
    mgr.consecutive_losses = int(data.get("consecutive_losses", 0))
    mgr.trades_today = int(data.get("trades_today", 0))
    mgr.symbol_daily_trades = dict(data.get("symbol_daily_trades") or {})
    mgr.symbol_daily_pnl = {k: float(v) for k, v in (data.get("symbol_daily_pnl") or {}).items()}
    mgr.symbol_daily_loss = {k: float(v) for k, v in (data.get("symbol_daily_loss") or {}).items()}
    mgr.last_loss_timestamp = float(data.get("last_loss_timestamp", 0))

    for sym in INDICES:
        row = (data.get("positions") or {}).get(sym) or {}
        pos = mgr.positions[sym]
        pos.quantity = int(row.get("quantity", 0))
        pos.avg_price = float(row.get("avg_price", 0))
        pos.symbol = row.get("symbol") or sym
        pos.last_updated = row.get("last_updated")

    try:
        from .risk_gatekeeper import risk_gatekeeper
        risk_gatekeeper.daily_pnl = mgr.daily_pnl
        risk_gatekeeper.daily_loss = mgr.daily_loss
    except Exception:
        pass

    return True


def clear_risk_state(path: Path = RISK_STATE_FILE) -> None:
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass