"""
Emergency halt — kill switch backend.

Flattens open positions (paper + live), blocks new entries via state machine.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from .state_machine import state_machine, SystemState
from .audit_logger import audit_logger


def execute_emergency_halt(reason: str = "Kill switch triggered") -> Dict[str, Any]:
    """Halt trading and square off all open multi-symbol positions."""
    from .multi_symbol_risk import multi_risk_manager
    from .trade_ledger import trade_ledger

    state_machine.emergency_halt(reason)
    closed: List[Dict[str, Any]] = []

    force_dry = multi_risk_manager.config.force_dry_run
    kite = None
    if not force_dry:
        try:
            from kiteconnect import KiteConnect
            from config import KITE_API_KEY

            token = os.getenv("KITE_ACCESS_TOKEN", "")
            if token:
                kite = KiteConnect(api_key=KITE_API_KEY)
                kite.set_access_token(token)
        except Exception:
            pass

    for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        pos = multi_risk_manager.get_position(sym)
        qty = pos.quantity
        if qty == 0:
            continue

        exit_side = "SELL" if qty > 0 else "BUY"
        exit_qty = abs(qty)
        tradingsymbol = pos.symbol or sym
        ltp = pos.avg_price or 0.0

        try:
            from . import live_snapshots
            snap = live_snapshots.get_snapshot(sym) or {}
            ltp = snap.get("ltp") or ltp
        except Exception:
            pass

        result = multi_risk_manager.place_guarded_order(
            kite=kite,
            symbol=tradingsymbol,
            quantity=exit_qty,
            transaction_type=exit_side,
            price=ltp,
            is_exit=True,
            force_dry_run=force_dry or kite is None,
        )

        closed.append({
            "symbol": tradingsymbol,
            "index": sym,
            "quantity": exit_qty,
            "side": exit_side,
            "price": ltp,
            "success": result.get("success", False),
            "message": result.get("message"),
            "live": not force_dry and kite is not None,
        })

        trade_ledger.record("order.exit", {
            "symbol": tradingsymbol,
            "side": exit_side,
            "quantity": exit_qty,
            "price": ltp,
            "reason": "emergency_halt",
            "emergency": True,
        })

    audit_logger.record("emergency.halt", {
        "reason": reason,
        "closed_positions": closed,
        "ts": time.time(),
        "mode": "paper" if force_dry else "live",
    })

    trade_ledger.record("emergency.halt", {"reason": reason, "closed": len(closed)})

    return {
        "status": "halted",
        "state": state_machine.get_state().value,
        "reason": reason,
        "positions_closed": closed,
        "trading_allowed": state_machine.is_trading_allowed(),
    }