"""
EOD options flatten — close all OPEN multi-leg structures before session end.

Uses the same proactive MIS flatten window as futures when active
(``market_calendar.is_eod_flatten_window``). Otherwise falls back to the
options ``session_end`` from YAML (or paper-trading session bounds).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audit_logger import audit_logger
from .config_loader import get_options_config, resolve_paper_session_bounds
from .market_calendar import IST, is_eod_flatten_window, is_real_market_open, now_ist

logger = logging.getLogger(__name__)

_STATE_FILE = Path("data/options_eod_flatten_state.json")
_SESSION_END_WINDOW_MINUTES = 5


def options_eod_flatten_enabled() -> bool:
    from .trading_controls import effective_options_eod_flatten_enabled

    return effective_options_eod_flatten_enabled()


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


def _parse_hhmm(raw: str, *, default_h: int = 15, default_m: int = 15) -> dt_time:
    if not raw:
        return dt_time(default_h, default_m)
    try:
        parts = str(raw).replace(".", ":").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return dt_time(hour, minute)
    except Exception:
        return dt_time(default_h, default_m)


def _options_session_end(for_date: Optional[date] = None) -> dt_time:
    """Resolve options session end from YAML or paper-trading bounds."""
    cfg = get_options_config()
    raw = cfg.get("session_end")
    if raw:
        return _parse_hhmm(str(raw))
    bounds = resolve_paper_session_bounds(for_date)
    return _parse_hhmm(str(bounds.get("session_end") or "15:15"))


def is_options_eod_flatten_window(at: Optional[datetime] = None) -> bool:
    """
    True inside the proactive EOD flatten window for options structures.

    Primary: ``is_eod_flatten_window`` (shared with futures MIS flatten).
    Fallback: five-minute window ending at options ``session_end``.
    """
    if not options_eod_flatten_enabled():
        return False

    current = (at or now_ist()).astimezone(IST)
    if not is_real_market_open(current):
        return False

    if is_eod_flatten_window(current):
        return True

    session_end = _options_session_end(current.date())
    end_dt = datetime.combine(current.date(), session_end, tzinfo=IST)
    start_dt = end_dt - timedelta(minutes=_SESSION_END_WINDOW_MINUTES)
    return start_dt.time() <= current.time() <= session_end


def execute_options_eod_flatten(
    *,
    kite=None,
    force_dry_run: bool = True,
    reason: str = "options_eod_flatten",
    for_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Close every OPEN options structure (paper or live)."""
    from .options_execution_engine import options_execution_engine
    from .options_positions import options_position_store
    from .trade_ledger import trade_ledger

    cfg = get_options_config()
    product = str(cfg.get("product") or "NRML")
    mark_day = for_date or now_ist().date()

    open_structures = options_position_store.list_open()
    open_before = len(open_structures)
    if open_before == 0:
        _mark_flattened(mark_day)
        return {
            "flattened": True,
            "skipped": True,
            "reason": "no_open_structures",
            "closed_structures": [],
        }

    if not force_dry_run:
        from .token_manager import live_trading_token_ok

        if not live_trading_token_ok():
            return {
                "flattened": False,
                "skipped": True,
                "reason": "kite_token_invalid",
                "closed_structures": [],
            }

    closed: List[Dict[str, Any]] = []
    for struct in open_structures:
        result = options_execution_engine.close_structure(
            kite,
            struct.structure_id,
            reason=reason,
            force_dry_run=force_dry_run,
            product=product,
        )
        closed.append({
            "structure_id": struct.structure_id,
            "underlying": struct.underlying,
            "entry_credit": struct.entry_credit,
            "legs": len(struct.legs or []),
            "success": bool(result.get("success")),
            "message": result.get("message"),
            "live": not force_dry_run,
        })
        if not result.get("success"):
            logger.warning(
                "[OPTIONS EOD] Failed to close %s: %s",
                struct.structure_id,
                result.get("message"),
            )

    all_ok = all(row.get("success") for row in closed)
    if all_ok:
        _mark_flattened(mark_day)

    audit_logger.record("options.eod_flatten", {
        "reason": reason,
        "open_before": open_before,
        "closed_structures": closed,
        "mode": "paper" if force_dry_run else "live",
    })
    trade_ledger.record("options.eod.flatten", {
        "reason": reason,
        "closed": len(closed),
        "open_before": open_before,
        "all_success": all_ok,
    })

    logger.info(
        "[OPTIONS EOD] Flatten complete — %s/%s structure(s) closed (was %s open)",
        sum(1 for row in closed if row.get("success")),
        len(closed),
        open_before,
    )

    return {
        "flattened": all_ok,
        "skipped": False,
        "reason": reason,
        "open_before": open_before,
        "closed_structures": closed,
    }


def maybe_run_options_eod_flatten(
    *,
    kite=None,
    force_dry_run: bool = True,
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Run options EOD flatten once per IST trading day inside the flatten window.
    Safe to call every main-loop iteration.
    """
    if not options_eod_flatten_enabled():
        return {"flattened": False, "skipped": True, "reason": "disabled"}

    current = (at or now_ist()).astimezone(IST)
    if not is_options_eod_flatten_window(current):
        return {"flattened": False, "skipped": True, "reason": "outside_window"}

    if already_flattened_today(current.date()):
        return {"flattened": False, "skipped": True, "reason": "already_done_today"}

    from .options_positions import options_position_store

    if not options_position_store.list_open():
        _mark_flattened(current.date())
        return {"flattened": True, "skipped": True, "reason": "no_open_structures"}

    return execute_options_eod_flatten(
        kite=kite,
        force_dry_run=force_dry_run,
        reason="options_eod_flatten_scheduled",
        for_date=current.date(),
    )