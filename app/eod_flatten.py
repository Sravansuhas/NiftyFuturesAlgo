"""
EOD MIS flatten — square off open MIS positions before Zerodha auto square-off (~15:15 IST).

NSE F&O session closes 15:30 IST (15:40 from 2026-08-03). MIS positions are
auto-squared by the broker before close. This module flattens proactively during
the regime-aware window (15:10–15:15 legacy; 15:20–15:25 extended — configurable)
so exits are controlled by our risk layer, not a broker auction.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audit_logger import audit_logger
from .market_calendar import IST, is_eod_flatten_window, now_ist

logger = logging.getLogger(__name__)

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
_STATE_FILE = Path("data/eod_flatten_state.json")


def eod_flatten_enabled() -> bool:
    return os.getenv("EOD_MIS_FLATTEN", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _load_state() -> Dict[str, Any]:
    if not _STATE_FILE.exists():
        return {}
    try:
        with _STATE_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _save_state(payload: Dict[str, Any]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, _STATE_FILE)


def already_flattened_today(d: Optional[date] = None) -> bool:
    day = d or now_ist().date()
    state = _load_state()
    return str(state.get("last_flatten_date_ist", "")) == day.isoformat()


def _mark_flattened(d: Optional[date] = None) -> None:
    day = d or now_ist().date()
    _save_state({
        "last_flatten_date_ist": day.isoformat(),
        "flattened_at": time.time(),
    })


def _resolve_kite(kite=None):
    if kite is not None:
        return kite
    try:
        from kiteconnect import KiteConnect
        from config import KITE_API_KEY
        from .token_manager import get_token_manager

        mgr = get_token_manager()
        if mgr and mgr.access_token:
            mgr.kite.set_access_token(mgr.access_token)
            return mgr.kite
        token = os.getenv("KITE_ACCESS_TOKEN", "")
        if token and KITE_API_KEY:
            client = KiteConnect(api_key=KITE_API_KEY)
            client.set_access_token(token)
            return client
    except Exception:
        pass
    return None


def execute_eod_mis_flatten(
    multi_risk_manager,
    *,
    kite=None,
    strategies: Optional[Dict[str, Any]] = None,
    force_dry: Optional[bool] = None,
    reason: str = "eod_mis_flatten",
    for_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Flatten all open multi-symbol MIS positions (paper or live)."""
    from .multi_symbol_risk import multi_risk_manager as default_mgr
    from .trade_ledger import trade_ledger

    mgr = multi_risk_manager or default_mgr
    use_dry = mgr.config.force_dry_run if force_dry is None else force_dry
    kite_client = None if use_dry else _resolve_kite(kite)

    if not use_dry:
        from .token_manager import live_trading_token_ok

        if not live_trading_token_ok():
            return {
                "flattened": False,
                "skipped": True,
                "reason": "kite_token_invalid",
                "closed_positions": [],
            }

    mark_day = for_date or now_ist().date()

    open_before = mgr.count_open_positions()
    if open_before == 0:
        _mark_flattened(mark_day)
        return {
            "flattened": True,
            "skipped": True,
            "reason": "no_open_positions",
            "closed_positions": [],
        }

    closed: List[Dict[str, Any]] = []

    try:
        from .exchange_protection import exchange_protection

        for sym in INDICES:
            if mgr.has_open_position(sym):
                try:
                    exchange_protection.on_exit_fill(sym, kite=kite_client)
                except Exception as exc:
                    logger.debug("[EOD] SL-M cancel note for %s: %s", sym, exc)
    except Exception:
        pass

    for sym in INDICES:
        pos = mgr.get_position(sym)
        qty = int(pos.quantity or 0)
        if qty == 0:
            continue

        exit_side = "SELL" if qty > 0 else "BUY"
        exit_qty = abs(qty)
        tradingsymbol = pos.symbol or sym
        ltp = float(pos.avg_price or 0.0)

        try:
            from . import live_snapshots

            snap = live_snapshots.get_snapshot(sym) or {}
            ltp = float(snap.get("ltp") or ltp)
        except Exception:
            pass

        result = mgr.place_guarded_order(
            kite=kite_client,
            symbol=tradingsymbol,
            quantity=exit_qty,
            transaction_type=exit_side,
            price=ltp,
            product="MIS",
            is_exit=True,
            force_dry_run=use_dry or kite_client is None,
        )

        closed.append({
            "symbol": tradingsymbol,
            "index": sym,
            "quantity": exit_qty,
            "side": exit_side,
            "price": ltp,
            "success": bool(result.get("success")),
            "message": result.get("message"),
            "live": not use_dry and kite_client is not None,
        })

        trade_ledger.record("order.exit", {
            "symbol": tradingsymbol,
            "side": exit_side,
            "quantity": exit_qty,
            "price": ltp,
            "reason": reason,
            "eod_flatten": True,
        })

        if strategies and sym in strategies:
            strat = strategies[sym]
            try:
                if hasattr(strat, "_clear_trade_state"):
                    strat._clear_trade_state()
                elif hasattr(strat, "persist_state"):
                    strat.persist_state()
            except Exception as exc:
                logger.debug("[EOD] Strategy state clear failed for %s: %s", sym, exc)

    try:
        from .risk_state_persistence import save_risk_state

        save_risk_state(mgr)
    except Exception as exc:
        logger.debug("[EOD] Risk state save note: %s", exc)

    _mark_flattened(mark_day)
    audit_logger.record("eod.flatten", {
        "reason": reason,
        "open_before": open_before,
        "closed_positions": closed,
        "mode": "paper" if use_dry else "live",
    })
    trade_ledger.record("eod.flatten", {
        "reason": reason,
        "closed": len(closed),
        "open_before": open_before,
    })

    logger.info(
        "[EOD] MIS flatten complete — %s position(s) closed (was %s open)",
        len(closed),
        open_before,
    )

    return {
        "flattened": True,
        "skipped": False,
        "reason": reason,
        "open_before": open_before,
        "closed_positions": closed,
    }


def maybe_run_eod_flatten(
    multi_risk_manager,
    *,
    kite=None,
    strategies: Optional[Dict[str, Any]] = None,
    at=None,
) -> Dict[str, Any]:
    """
    Run EOD MIS flatten once per IST trading day when inside the flatten window.
    Safe to call every main-loop iteration.
    """
    if not eod_flatten_enabled():
        return {"flattened": False, "skipped": True, "reason": "disabled"}

    current = (at or now_ist()).astimezone(IST)
    if not is_eod_flatten_window(current):
        return {"flattened": False, "skipped": True, "reason": "outside_window"}

    if already_flattened_today(current.date()):
        return {"flattened": False, "skipped": True, "reason": "already_done_today"}

    if multi_risk_manager.count_open_positions() == 0:
        _mark_flattened(current.date())
        return {"flattened": True, "skipped": True, "reason": "no_open_positions"}

    return execute_eod_mis_flatten(
        multi_risk_manager,
        kite=kite,
        strategies=strategies,
        reason="eod_mis_flatten_scheduled",
        for_date=current.date(),
    )