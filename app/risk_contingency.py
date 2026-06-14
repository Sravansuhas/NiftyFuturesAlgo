"""
Risk contingency — auto-flatten and portfolio sync when limits breach.

FRIDAY rule: when the house is on fire, exit first; explain later.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_AUTO_FLATTEN_ENV = "AUTO_FLATTEN_ON_RISK_BREACH"
_WARN_THRESHOLD = 0.80  # 80% of daily loss limit → warn + tighten


def sync_portfolio_risk_state(multi_manager, gatekeeper) -> None:
    """Mirror multi-symbol P&L into global gatekeeper for circuit breakers."""
    try:
        gatekeeper.daily_pnl = float(multi_manager.daily_pnl)
        gatekeeper.daily_loss = float(multi_manager.daily_loss)
        gatekeeper.current_equity = float(multi_manager.current_equity)
        if multi_manager.peak_equity > gatekeeper.peak_equity:
            gatekeeper.peak_equity = float(multi_manager.peak_equity)
        gatekeeper.trades_today = int(multi_manager.trades_today)
        gatekeeper.consecutive_losses = int(multi_manager.consecutive_losses)
    except Exception as exc:
        logger.debug("Portfolio risk sync skipped: %s", exc)


def _auto_flatten_enabled() -> bool:
    return os.getenv(_AUTO_FLATTEN_ENV, "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def evaluate_contingencies(
    multi_manager,
    gatekeeper,
    *,
    kite=None,
    already_halted: bool = False,
) -> Dict[str, Any]:
    """
    Check portfolio limits and return recommended action.

    Actions: none | warn | block_entries | flatten_all
    """
    sync_portfolio_risk_state(multi_manager, gatekeeper)

    capital = float(gatekeeper.capital or multi_manager.capital)
    daily_loss = float(gatekeeper.daily_loss)
    max_daily = capital * gatekeeper.config.max_daily_loss_pct
    drawdown = gatekeeper._current_drawdown_pct()
    max_dd = gatekeeper.config.max_drawdown_pct

    result: Dict[str, Any] = {
        "action": "none",
        "daily_loss": round(daily_loss, 2),
        "daily_loss_limit": round(max_daily, 2),
        "drawdown_pct": round(drawdown * 100, 2),
        "drawdown_limit_pct": round(max_dd * 100, 2),
        "messages": [],
    }

    if already_halted:
        result["action"] = "block_entries"
        result["messages"].append("System already in halt state")
        return result

    if daily_loss >= max_daily or drawdown >= max_dd:
        result["action"] = "flatten_all"
        result["messages"].append(
            "Hard risk limit breached — flatten all positions"
        )
        if _auto_flatten_enabled() and kite is not None:
            try:
                from .emergency import execute_emergency_halt

                halt_result = execute_emergency_halt(
                    reason="auto_risk_limit_breach"
                )
                result["flatten_result"] = halt_result
                result["messages"].append("Emergency flatten executed")
            except Exception as exc:
                result["messages"].append(f"Flatten failed: {exc}")
                logger.error("Auto-flatten failed: %s", exc)
        elif not _auto_flatten_enabled():
            result["messages"].append(
                f"Set {_AUTO_FLATTEN_ENV}=true to auto-flatten (entries already blocked)"
            )
        return result

    if daily_loss >= max_daily * _WARN_THRESHOLD:
        result["action"] = "warn"
        result["messages"].append(
            f"Daily loss at {daily_loss:,.0f} ({daily_loss/max_daily:.0%} of limit) — defensive mode"
        )

    if drawdown >= max_dd * _WARN_THRESHOLD:
        result["action"] = "warn"
        result["messages"].append(
            f"Drawdown {drawdown:.1%} approaching {max_dd:.0%} cap"
        )

    return result