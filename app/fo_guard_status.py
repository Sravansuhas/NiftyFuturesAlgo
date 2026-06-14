"""
Human-readable snapshot of tier-1 F&O entry guards for dashboard / API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


TIER1_RULES = (
    "FO_HARD_SL_REQUIRED",
    "FO_NO_MENTAL_STOPS",
    "FO_BROKER_DISCONNECT_OPEN_POS",
    "FO_REVENGE_TRADING_COOLDOWN",
    "FO_EVENT_CALENDAR",
    "FO_OVERTRADING_DAILY_CAP",
    "FO_ROLLING_EDGE_HALT",
    "FO_CHOP_VETO",
)


def build_symbol_guard_snapshot(
    symbol: str,
    context: Dict[str, Any],
    *,
    engine=None,
) -> Dict[str, Any]:
    """Evaluate FO rules for one symbol and summarize blocking state."""
    if engine is None:
        from .fo_rules_engine import fo_rules_engine
        engine = fo_rules_engine

    if engine is None:
        return {"symbol": symbol, "allowed": True, "block_reason": "", "active_guards": []}

    allowed, reason, risk_mult = engine.check_entry(symbol, context)
    blocked_rule = ""
    if not allowed and reason:
        blocked_rule = reason.split(":", 1)[0].strip()

    highlights = {
        "trend": context.get("trend"),
        "volatility": context.get("volatility"),
        "chop_score": context.get("chop_score"),
        "adx_proxy": context.get("adx_proxy"),
        "trades_used": context.get("trades_today"),
        "effective_cap": context.get("effective_max_trades"),
        "rolling_expectancy": context.get("rolling_expectancy"),
        "rolling_edge_trade_count": context.get("rolling_edge_trade_count"),
        "hours_to_event": context.get("hours_to_high_impact_event"),
        "within_pre_event_block": context.get("within_pre_event_block_window"),
    }

    active: List[Dict[str, str]] = []
    if context.get("trend") == "ranging":
        active.append({"id": "FO_CHOP_VETO", "label": "Chop veto armed (ranging tape)"})
    if context.get("rolling_edge_sufficient") and context.get("rolling_edge_halt"):
        active.append({"id": "FO_ROLLING_EDGE_HALT", "label": "Rolling edge halt (negative expectancy)"})
    elif context.get("rolling_edge_trade_count", 0) < 10:
        active.append({
            "id": "FO_ROLLING_EDGE_HALT",
            "label": f"Rolling edge warming ({context.get('rolling_edge_trade_count', 0)}/10 trades)",
        })
    hours = context.get("hours_to_high_impact_event")
    if hours is not None and hours != float("inf") and float(hours) <= 4.0:
        active.append({
            "id": "FO_EVENT_CALENDAR",
            "label": f"Pre-event block ({float(hours):.1f}h to macro event)",
        })
    if context.get("consecutive_losses", 0) >= 1:
        active.append({"id": "FO_REVENGE_TRADING_COOLDOWN", "label": "Revenge cooldown may apply"})

    return {
        "symbol": symbol,
        "allowed": allowed,
        "block_reason": reason,
        "blocked_rule": blocked_rule,
        "risk_multiplier": round(risk_mult, 3),
        "highlights": highlights,
        "active_guards": active,
    }


def build_portfolio_guard_snapshot(risk_manager) -> Dict[str, Any]:
    """Snapshot for NIFTY / BANKNIFTY / SENSEX."""
    out: Dict[str, Any] = {"symbols": {}, "any_blocked": False}
    if risk_manager is None:
        return out

    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        ctx = risk_manager.build_fo_rules_context(sym)
        snap = build_symbol_guard_snapshot(sym, ctx)
        out["symbols"][sym] = snap
        if not snap.get("allowed"):
            out["any_blocked"] = True
            out.setdefault("portfolio_block_reason", snap.get("block_reason"))

    return out