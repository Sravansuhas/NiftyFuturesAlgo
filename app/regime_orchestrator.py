"""
Regime orchestrator — session brain for Indian F&O (green / red / sideways).

Unifies:
- Live posture (aggressive → contingency)
- Trade-budget extension in quality windows
- Exit tightness (profit defense in chop, let winners run in trend)
- Contingency playbooks (what to do before limits breach)

Learning may only reduce risk; green validated windows may extend trade budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .market_calendar import now_ist

PROJECT_ROOT = Path(__file__).resolve().parents[1]

POSTURES = ("aggressive", "normal", "defensive", "contingency")

# Indian session realities — tuned for NIFTY/BANKNIFTY/SENSEX futures MIS
_GREEN_TRENDS = frozenset({"uptrend", "downtrend"})
_CHOP_TRENDS = frozenset({"ranging", "flat"})


def classify_market_color(regime: Optional[Dict[str, Any]]) -> str:
    """Map regime to trader-facing color: green, red, sideways."""
    regime = regime or {}
    trend = regime.get("trend", "ranging")
    vol = regime.get("volatility", "normal")
    htf = regime.get("htf_bias", "neutral")
    chop = float(regime.get("chop_score", 0) or 0)

    if trend in _CHOP_TRENDS or chop >= 0.55:
        return "sideways"
    if vol == "high" and trend in _CHOP_TRENDS:
        return "sideways"
    if trend == "uptrend" and htf in ("bullish", "neutral"):
        return "green"
    if trend == "downtrend" and htf in ("bearish", "neutral"):
        return "green"
    if vol == "high":
        return "red"
    return "green" if trend in _GREEN_TRENDS else "sideways"


def assess_live_posture(
    regime: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Intraday posture from live regime + portfolio context.

    Returns posture, trade caps, risk multiplier hint, exit mode, contingencies.
    """
    regime = regime or {}
    context = context or {}
    reasons: List[str] = []
    color = classify_market_color(regime)

    daily_pnl = float(context.get("daily_pnl", 0) or 0)
    capital = float(context.get("capital", 1_000_000) or 1_000_000)
    consecutive_losses = int(context.get("consecutive_losses", 0) or 0)
    rolling_halt = bool(context.get("rolling_edge_halt", False))
    within_event_block = bool(context.get("within_pre_event_block_window", False))
    is_expiry = bool(context.get("is_expiry_day", False))
    learning_mult = float(context.get("learning_mult", 1.0) or 1.0)
    promoted = bool(context.get("params_promoted", False))
    safe_window = bool(context.get("safe_trading_window", True))

    posture = "normal"
    max_trades = 3
    risk_mult = 1.0
    exit_mode = "standard"
    buffer_bias = "normal"
    contingencies: List[str] = []

    loss_pct = abs(daily_pnl) / capital if daily_pnl < 0 else 0.0
    daily_limit_pct = float(context.get("max_daily_loss_pct", 0.02) or 0.02)

    # --- Contingency tier (limits approaching or hard blocks) ---
    if (
        rolling_halt
        or loss_pct >= daily_limit_pct * 0.85
        or consecutive_losses >= 3
        or within_event_block
    ):
        posture = "contingency"
        max_trades = 1
        risk_mult = 0.45
        exit_mode = "tight"
        buffer_bias = "wider"
        if rolling_halt:
            reasons.append("rolling edge negative — halt new risk")
            contingencies.append("No new entries until rolling expectancy recovers")
        if loss_pct >= daily_limit_pct * 0.85:
            reasons.append(f"session loss {loss_pct:.1%} near daily cap")
            contingencies.append("Flatten open winners on stall; no new size")
        if within_event_block:
            reasons.append("pre-event block window")
            contingencies.append("Stand down until event passes (RBI/budget macro)")
        if consecutive_losses >= 3:
            reasons.append("3+ consecutive losses")
            contingencies.append("Revenge-trading cooldown enforced")

    # --- Defensive (red / chop / unvalidated) ---
    elif (
        color == "sideways"
        or color == "red"
        or not promoted
        or learning_mult < 0.80
        or is_expiry
        or not safe_window
    ):
        posture = "defensive"
        max_trades = 2
        risk_mult = 0.70
        exit_mode = "profit_defense"
        buffer_bias = "wider"
        if color == "sideways":
            reasons.append("chop/sideways — scalp defense, no chase")
            contingencies.append("Book green early; widen breakout buffer")
        elif color == "red":
            reasons.append("adverse vol/trend — capital preservation")
            contingencies.append("Tighter stops; trend-only with HTF alignment")
        if not promoted:
            reasons.append("params not promotion-validated")
        if is_expiry:
            reasons.append("expiry day elevated risk")
            max_trades = min(max_trades, 2)

    # --- Aggressive (green quality window) ---
    elif color == "green" and promoted and learning_mult >= 0.95 and daily_pnl >= 0:
        posture = "aggressive"
        max_trades = 5
        risk_mult = 1.12
        exit_mode = "let_run"
        buffer_bias = "normal"
        reasons.append("green trending + validated params + green session")
        contingencies.append("Scale trade budget; trail winners; cut on regime flip")

    else:
        reasons.append("standard session posture")
        contingencies.append("Base cap; FO rules + adaptive budget gate extensions")

    risk_mult = max(0.35, min(1.15, risk_mult * learning_mult))

    return {
        "posture": posture,
        "market_color": color,
        "recommended_max_trades_per_day": max_trades,
        "risk_multiplier_hint": round(risk_mult, 3),
        "exit_mode": exit_mode,
        "breakout_buffer_bias": buffer_bias,
        "reasons": reasons,
        "contingencies": contingencies,
        "regime": {
            "trend": regime.get("trend"),
            "volatility": regime.get("volatility"),
            "htf_bias": regime.get("htf_bias"),
            "chop_score": regime.get("chop_score"),
        },
    }


def apply_overnight_macro_hints(
    posture: Dict[str, Any],
    overnight: Optional[Dict[str, Any]] = None,
    macro: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply pre-open GIFT gap + macro de-risk on top of live posture."""
    result = dict(posture)
    reasons = list(result.get("reasons") or [])
    macro = macro or {}
    overnight = overnight or {}

    vix = macro.get("vix") or {}
    if vix.get("zone") in ("elevated", "extreme"):
        floor = 0.85 if vix.get("zone") == "elevated" else 0.75
        rm = float(result.get("risk_multiplier_hint", 1.0) or 1.0)
        result["risk_multiplier_hint"] = round(max(0.35, min(rm, rm * floor)), 3)
        reasons.append(f"India VIX {vix.get('zone')} — macro de-risk")
    fii = macro.get("fii_dii") or {}
    if fii.get("flow_bias") in ("fii_selling", "risk_off"):
        rm = float(result.get("risk_multiplier_hint", 1.0) or 1.0)
        result["risk_multiplier_hint"] = round(max(0.35, min(rm, rm * 0.90)), 3)
        reasons.append(f"FII/DII bias={fii.get('flow_bias')}")

    hints = overnight.get("session_hints") or {}
    buffer_mult = float(hints.get("breakout_buffer_mult", 1.0) or 1.0)
    result["breakout_buffer_mult"] = buffer_mult
    if overnight.get("available"):
        floor = hints.get("posture_floor")
        if floor in ("defensive", "contingency"):
            rank = {"aggressive": 3, "normal": 2, "defensive": 1, "contingency": 0}
            if rank.get(floor, 1) < rank.get(result.get("posture", "normal"), 2):
                result["posture"] = floor
                reasons.append(f"overnight gap floor={floor}")
            result["recommended_max_trades_per_day"] = max(
                1,
                int(result.get("recommended_max_trades_per_day", 3))
                + int(hints.get("max_trades_delta", 0) or 0),
            )
            if buffer_mult > 1.0:
                result["breakout_buffer_bias"] = "wider"
        for hint_reason in hints.get("reasons") or []:
            if hint_reason not in reasons:
                reasons.append(str(hint_reason))

    result["reasons"] = reasons
    return result


def _load_intraday_session_context() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Today's overnight cache + macro from morning brief (no live NSE during session)."""
    today = now_ist().strftime("%Y-%m-%d")
    overnight: Optional[Dict[str, Any]] = None
    macro: Optional[Dict[str, Any]] = None
    try:
        from .overnight_context import load_overnight_context

        cached = load_overnight_context()
        if cached and cached.get("date_ist") == today:
            overnight = cached
    except Exception:
        pass
    try:
        brief_path = PROJECT_ROOT / "data" / "briefs" / f"{today}.json"
        if brief_path.exists():
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            macro = brief.get("macro_context")
            if overnight is None:
                overnight = brief.get("overnight_context")
    except Exception:
        pass
    return overnight, macro


def assess_session_posture(brief: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-market posture from brief (no live regime yet)."""
    session = brief.get("session", {})
    macro = brief.get("macro_context") or {}
    overnight = brief.get("overnight_context") or {}
    promoted_count = sum(
        1 for s in brief.get("promotion_status", {}).values() if s.get("passed")
    )
    context = {
        "is_expiry_day": session.get("is_expiry_day", False),
        "safe_trading_window": session.get("safe_trading_window", True),
        "params_promoted": promoted_count >= 2,
        "learning_mult": 1.0,
        "daily_pnl": 0.0,
        "within_pre_event_block_window": bool(
            (brief.get("event_calendar") or {}).get("within_pre_event_block_window", False)
        ),
    }
    regime = {"trend": "ranging", "volatility": "normal", "htf_bias": "neutral"}
    vix = macro.get("vix") or {}
    if vix.get("zone") in ("elevated", "extreme"):
        regime["volatility"] = "high"
        context["learning_mult"] = 0.85
    fii = macro.get("fii_dii") or {}
    if fii.get("flow_bias") in ("fii_selling", "risk_off"):
        context["learning_mult"] = min(context["learning_mult"], 0.90)

    posture = apply_overnight_macro_hints(
        assess_live_posture(regime, context),
        overnight=overnight,
        macro=macro,
    )
    if session.get("is_trading_holiday"):
        posture["posture"] = "contingency"
        posture["recommended_max_trades_per_day"] = 0
        posture["reasons"].append(f"holiday: {session.get('holiday_name')}")
    if promoted_count == 0:
        posture["posture"] = "defensive"
        posture["recommended_max_trades_per_day"] = min(
            posture.get("recommended_max_trades_per_day", 2), 2
        )
        posture["reasons"].append("no index passed promotion gates")
    posture["caution_level"] = posture["posture"]
    posture["watch_for"] = [
        "Slippage at 09:15–09:45 open (NSE auction aftermath)",
        "Expiry Tuesday (NIFTY/BNF) / Thursday (SENSEX) — roll basis risk",
        "RBI/budget event calendar — FO_EVENT_CALENDAR blocks entries",
        "Regime flip intraday: green → sideways triggers profit defense",
    ]
    return posture


def exit_overrides_for_posture(
    posture: str,
    regime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tune exit stack by posture — profit defense in chop, patience in trend."""
    regime = regime or {}
    color = classify_market_color(regime)
    mode = {
        "aggressive": {
            "breakeven_activation_mult": 0.75,
            "chop_profit_defense": False,
            "profit_lock_retrace_pct": 0.45,
            "time_exit_profit_fraction": 0.35,
        },
        "normal": {
            "breakeven_activation_mult": 0.80,
            "chop_profit_defense": color == "sideways",
            "profit_lock_retrace_pct": 0.40,
            "time_exit_profit_fraction": 0.40,
        },
        "defensive": {
            "breakeven_activation_mult": 0.65,
            "chop_profit_defense": True,
            "profit_lock_retrace_pct": 0.30,
            "time_exit_profit_fraction": 0.50,
        },
        "contingency": {
            "breakeven_activation_mult": 0.55,
            "chop_profit_defense": True,
            "profit_lock_retrace_pct": 0.25,
            "time_exit_profit_fraction": 0.55,
        },
    }
    return mode.get(posture, mode["normal"])


def posture_for_symbol(
    symbol: str,
    regime: Optional[Dict[str, Any]],
    risk_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convenience wrapper used by strategy and multi_symbol_risk."""
    ctx = dict(risk_context or {})
    try:
        from .rolling_edge import assess_rolling_edge

        edge = assess_rolling_edge(symbol=symbol)
        ctx["rolling_edge_halt"] = edge.get("rolling_edge_halt", False)
    except Exception:
        pass
    posture = assess_live_posture(regime, ctx)
    overnight, macro = _load_intraday_session_context()
    return apply_overnight_macro_hints(posture, overnight=overnight, macro=macro)