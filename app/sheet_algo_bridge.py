"""
Bridge manual Options Sheet inputs into the futures breakout algo.

The sheet is discretionary analysis (CE/PE levels); this module translates
one-sided bias into futures entry gates and builds a daily scoreboard vs algo P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config_loader import get_external_signals_config
from .external_signals import INDICES, external_signals_store, iter_legs
from .fo_market_mood import _brother_bias_for_index
from .market_calendar import now_ist

FUTURES_SIDE_LONG = frozenset({"BUY", "LONG"})
FUTURES_SIDE_SHORT = frozenset({"SELL", "SHORT"})


@dataclass(frozen=True)
class SheetGateResult:
    allowed: bool
    reason: str
    mode: str
    bias: str
    detail: str
    advisory_only: bool = False


def _normalize_futures_side(side: str) -> str:
    s = (side or "").upper()
    if s in FUTURES_SIDE_SHORT:
        return "SHORT"
    return "LONG"


def _trend_mismatch(bias: str, algo_trend: Optional[str], symbol: str) -> Optional[str]:
    """Only flag direct trend conflicts; sheet bias is primary when algo is ranging or unknown."""
    if not algo_trend:
        return None
    algo = str(algo_trend).lower()
    if algo not in ("uptrend", "downtrend"):
        return None
    if bias == "bullish" and algo == "downtrend":
        return f"{symbol}: sheet bullish (CE) vs algo downtrend"
    if bias == "bearish" and algo == "uptrend":
        return f"{symbol}: sheet bearish (PE) vs algo uptrend"
    return None


def check_sheet_allows_futures_entry(
    symbol: str,
    side: str,
    *,
    sheet: Optional[Dict[str, Any]] = None,
    algo_trend: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> SheetGateResult:
    """
    Decide whether the futures algo may enter given today's options sheet.

    Modes (config external_signals.mode):
      off       — sheet ignored
      advisory  — never blocks; bias returned for logging / UI
      filter    — block LONG on bearish sheet, SHORT on bullish sheet
      confirm   — only allow LONG when bullish/balanced, SHORT when bearish/balanced
    """
    cfg = config if config is not None else get_external_signals_config()
    if not cfg.get("enabled", True):
        return SheetGateResult(True, "", "off", "none", "sheet integration disabled", False)

    mode = str(cfg.get("mode", "filter")).lower()
    if mode in ("off", "disabled", "none"):
        return SheetGateResult(True, "", "off", "none", "mode off", False)

    working_sheet = sheet if sheet is not None else external_signals_store.get()
    bias, detail, _score = _brother_bias_for_index(working_sheet, symbol)
    allow_empty = bool(cfg.get("allow_when_empty", True))
    block_mismatch = bool(cfg.get("block_on_mismatch", True))
    side_norm = _normalize_futures_side(side)
    advisory_only = mode == "advisory"

    if bias == "none" and allow_empty:
        return SheetGateResult(True, detail, mode, bias, detail, advisory_only)

    mismatch = _trend_mismatch(bias, algo_trend, symbol) if block_mismatch else None

    if mode == "advisory":
        note = detail if not mismatch else f"{detail}; {mismatch}"
        return SheetGateResult(True, note, mode, bias, detail, True)

    if mode == "filter":
        if bias == "bullish" and side_norm == "SHORT":
            return SheetGateResult(False, f"sheet_bias: CE-only blocks SHORT — {detail}", mode, bias, detail, False)
        if bias == "bearish" and side_norm == "LONG":
            return SheetGateResult(False, f"sheet_bias: PE-only blocks LONG — {detail}", mode, bias, detail, False)
        if mismatch:
            return SheetGateResult(False, f"sheet_mismatch: {mismatch}", mode, bias, detail, False)
        return SheetGateResult(True, detail, mode, bias, detail, False)

    if mode == "confirm":
        if bias == "none" and not allow_empty:
            return SheetGateResult(False, "sheet_confirm: no active leg on sheet", mode, bias, detail, False)
        if side_norm == "LONG" and bias not in ("bullish", "balanced"):
            return SheetGateResult(False, f"sheet_confirm: LONG needs CE bias, got {bias}", mode, bias, detail, False)
        if side_norm == "SHORT" and bias not in ("bearish", "balanced"):
            return SheetGateResult(False, f"sheet_confirm: SHORT needs PE bias, got {bias}", mode, bias, detail, False)
        if mismatch:
            return SheetGateResult(False, f"sheet_mismatch: {mismatch}", mode, bias, detail, False)
        return SheetGateResult(True, detail, mode, bias, detail, False)

    return SheetGateResult(True, detail, mode, bias, detail, False)


def get_sheet_inputs_for_symbol(symbol: str, sheet: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalized sheet view for dashboard / strategy diagnostics."""
    working = sheet if sheet is not None else external_signals_store.get()
    block = (working.get("indices") or {}).get(symbol) or {}
    bias, detail, score = _brother_bias_for_index(working, symbol)
    legs = []
    for leg_id, index, leg, opt, side in iter_legs(working):
        if index != symbol:
            continue
        if not (side.get("strike") or side.get("entry") is not None):
            continue
        legs.append({
            "leg_id": leg_id,
            "option_type": opt,
            "strike": side.get("strike"),
            "entry": side.get("entry"),
            "target": side.get("target"),
            "stop_loss": side.get("stop_loss"),
            "journal_status": side.get("journal_status"),
            "last_ltp": side.get("last_ltp"),
            "mtm_net_1lot": side.get("mtm_net_1lot"),
        })
    return {
        "symbol": symbol,
        "bias": bias,
        "detail": detail,
        "bias_score": score,
        "legs": legs,
        "date": working.get("date"),
    }


def _manual_pnl_by_index(sheet: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-index manual sheet stats from in-sheet MTM fields (no Kite)."""
    out: Dict[str, Dict[str, Any]] = {}
    for idx in INDICES:
        block = (sheet.get("indices") or {}).get(idx) or {}
        net = 0.0
        legs_active = 0
        targets = 0
        stops = 0
        for leg_name in ("call", "put"):
            side = block.get(leg_name) or {}
            if not (side.get("strike") or side.get("entry") is not None):
                continue
            legs_active += 1
            status = str(side.get("journal_status") or "")
            if status == "target_met":
                targets += 1
            elif status == "stop_hit":
                stops += 1
            mtm = side.get("mtm_net_1lot")
            if mtm is not None and status in ("entered", "target_met", "stop_hit"):
                net += float(mtm)
        bias, detail, _ = _brother_bias_for_index(sheet, idx)
        out[idx] = {
            "pnl_net": round(net, 2),
            "legs_active": legs_active,
            "targets_hit": targets,
            "stops_hit": stops,
            "bias": bias,
            "detail": detail,
        }
    return out


def _algo_pnl_by_index() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from .multi_symbol_risk import multi_risk_manager
        status = multi_risk_manager.get_per_symbol_status()
        for idx in INDICES:
            row = status.get(idx) or {}
            out[idx] = {
                "pnl_net": round(float(row.get("daily_pnl") or 0), 2),
                "trades": int(row.get("daily_trades") or 0),
                "position": int(row.get("position") or 0),
            }
    except Exception:
        for idx in INDICES:
            out[idx] = {"pnl_net": 0.0, "trades": 0, "position": 0}
    return out


def build_sheet_vs_algo_scoreboard(
    trade_date: Optional[str] = None,
    *,
    sheet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Daily scoreboard: manual options sheet vs futures algo P&L per index.
    """
    working = sheet if sheet is not None else external_signals_store.get(trade_date)
    day = (trade_date or working.get("date") or now_ist().date().isoformat())[:10]
    manual = _manual_pnl_by_index(working)
    algo = _algo_pnl_by_index()
    cfg = get_external_signals_config()

    per_index: List[Dict[str, Any]] = []
    manual_total = 0.0
    algo_total = 0.0

    for idx in INDICES:
        m = manual.get(idx) or {}
        a = algo.get(idx) or {}
        m_pnl = float(m.get("pnl_net") or 0)
        a_pnl = float(a.get("pnl_net") or 0)
        manual_total += m_pnl
        algo_total += a_pnl
        winner = "tie"
        if m_pnl > a_pnl + 0.5:
            winner = "sheet"
        elif a_pnl > m_pnl + 0.5:
            winner = "algo"
        per_index.append({
            "symbol": idx,
            "sheet_pnl": m_pnl,
            "algo_pnl": a_pnl,
            "winner": winner,
            "sheet_bias": m.get("bias"),
            "sheet_legs": m.get("legs_active", 0),
            "sheet_targets": m.get("targets_hit", 0),
            "sheet_stops": m.get("stops_hit", 0),
            "algo_trades": a.get("trades", 0),
            "algo_position": a.get("position", 0),
        })

    overall = "tie"
    if manual_total > algo_total + 1.0:
        overall = "sheet"
    elif algo_total > manual_total + 1.0:
        overall = "algo"

    return {
        "date": day,
        "available": any((m.get("legs_active") or 0) > 0 for m in manual.values())
            or any(abs(a.get("pnl_net") or 0) > 0.01 for a in algo.values()),
        "integration_mode": cfg.get("mode", "filter"),
        "integration_enabled": bool(cfg.get("enabled", True)),
        "manual_total_pnl": round(manual_total, 2),
        "algo_total_pnl": round(algo_total, 2),
        "overall_winner": overall,
        "per_index": per_index,
        "notes": working.get("notes") or "",
    }