"""
Agent insights — aggregated Aegis bridge view.

Read-only aggregation of promotion status, multi-index WFO, improvement proposals,
lunar context, and optional market_context on disk. Never auto-applies changes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_INSIGHTS_PATH = PROJECT_ROOT / "data" / "agent_insights.json"
MARKET_CONTEXT_PATH = PROJECT_ROOT / "data" / "market_context.json"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


def _load_market_context() -> Dict[str, Any]:
    """Load data/market_context.json when present."""
    if not MARKET_CONTEXT_PATH.exists():
        return {"available": False, "source": "none", "path": str(MARKET_CONTEXT_PATH)}

    try:
        with MARKET_CONTEXT_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return {
            "available": True,
            "source": "disk",
            "path": str(MARKET_CONTEXT_PATH),
            "payload": payload,
        }
    except Exception as exc:
        logger.debug("market_context load failed: %s", exc)
        return {
            "available": False,
            "source": "disk",
            "path": str(MARKET_CONTEXT_PATH),
            "error": str(exc),
        }


def _compact_lunar(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {"available": False}

    panchang = payload.get("panchang") or {}
    astronomical = payload.get("astronomical") or {}
    hints = payload.get("session_hints") or {}
    trading = payload.get("trading_context") or {}

    return {
        "available": True,
        "date_ist": payload.get("date_ist") or payload.get("trade_date"),
        "paksha": panchang.get("paksha"),
        "tithi_name": panchang.get("tithi_name"),
        "phase_name": astronomical.get("phase_name"),
        "illumination_pct": astronomical.get("illumination_pct"),
        "folklore_tag": hints.get("folklore_tag"),
        "is_trading_session": trading.get("is_trading_session"),
        "is_expiry_day": trading.get("is_expiry_day"),
        "research_only": True,
    }


def _build_documentation_notes(insights: Dict[str, Any]) -> List[str]:
    notes: List[str] = []

    promo = insights.get("promotion_status") or {}
    passed = [idx for idx in INDICES if (promo.get(idx) or {}).get("passed")]
    if passed:
        notes.append(f"Promotion passed for {', '.join(passed)} — overlays still require human gate.")
    else:
        notes.append("No index passed promotion — falsifiable: params unproven for sizing up.")

    wfo = insights.get("multi_index_wfo") or {}
    if wfo.get("has_report"):
        summary = wfo.get("summary") or {}
        notes.append(
            f"Latest multi-index WFO {wfo.get('run_id')}: "
            f"{summary.get('passed_count', 0)}/{summary.get('index_count', 3)} indices passed."
        )
    else:
        notes.append("No multi-index WFO report on disk — run algo_lab_ops wfo-run.")

    pending = insights.get("pending_proposals") or {}
    count = int(pending.get("count") or 0)
    if count:
        notes.append(f"{count} improvement proposal(s) awaiting founder review (human gate required).")
    else:
        notes.append("No pending improvement proposals in data/improvement_proposals/.")

    lunar = insights.get("lunar_context") or {}
    if lunar.get("available"):
        notes.append(
            f"Lunar metadata: {lunar.get('phase_name')} / {lunar.get('tithi_name')} "
            "(research tagging only — not a primary signal)."
        )

    market = insights.get("market_context") or {}
    if market.get("available"):
        notes.append("market_context.json loaded — supplemental context for review.")
    else:
        notes.append("market_context.json absent — optional enrichment only.")

    return notes[:8]


def _build_founder_actions(insights: Dict[str, Any]) -> List[str]:
    actions: List[str] = [
        "Review pending improvement proposals before any config overlay.",
        "Run fo-safe-deploy checklist before paper or micro-live sessions.",
    ]

    pending = int((insights.get("pending_proposals") or {}).get("count") or 0)
    if pending:
        actions.insert(0, f"Approve or reject {pending} pending proposal(s) in Aegis.")

    wfo = insights.get("multi_index_wfo") or {}
    if not wfo.get("has_report"):
        actions.append("python scripts/algo_lab_ops.py wfo-run --days 180 --cache-only")

    promo = insights.get("promotion_status") or {}
    if not any((promo.get(idx) or {}).get("passed") for idx in INDICES):
        actions.append("python scripts/fetch_promotion_data.py then re-run WFO.")

    return actions[:6]


def build_agent_insights(*, refresh_lunar: bool = False) -> Dict[str, Any]:
    """
    Aggregate promotion, WFO, proposals, lunar, and optional market context.
    Read-only — does not modify strategy config or risk limits.
    """
    now = now_ist()

    try:
        from app.ops_hub import _promotion_snapshot, run_multi_index_wfo_status

        promotion = _promotion_snapshot()
        multi_index_wfo = run_multi_index_wfo_status()
    except Exception as exc:
        logger.debug("ops_hub aggregation failed: %s", exc)
        promotion = {"per_index": {}, "any_passed": False, "all_passed": False}
        multi_index_wfo = {"has_report": False, "error": str(exc)}

    pending_proposals: List[Dict[str, Any]] = []
    try:
        from app.improvement_loop import improvement_loop

        pending_proposals = improvement_loop.list_pending_proposals()
    except Exception as exc:
        logger.debug("pending proposals load failed: %s", exc)

    lunar_payload: Optional[Dict[str, Any]] = None
    try:
        from app.lunar_calendar import build_lunar_context, load_lunar_context

        lunar_payload = load_lunar_context()
        if refresh_lunar or lunar_payload is None:
            lunar_payload = build_lunar_context(refresh=refresh_lunar)
    except Exception as exc:
        logger.debug("lunar context load failed: %s", exc)

    insights: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "date_ist": now.strftime("%Y-%m-%d"),
        "promotion_status": promotion.get("per_index") or {},
        "promotion_summary": {
            "any_passed": bool(promotion.get("any_passed")),
            "all_passed": bool(promotion.get("all_passed")),
        },
        "multi_index_wfo": multi_index_wfo,
        "pending_proposals": {
            "count": len(pending_proposals),
            "proposals": pending_proposals[:12],
            "directory": str(PROJECT_ROOT / "data" / "improvement_proposals"),
        },
        "lunar_context": _compact_lunar(lunar_payload),
        "market_context": _load_market_context(),
        "human_gate_required": True,
    }

    insights["documentation_notes"] = _build_documentation_notes(insights)
    insights["founder_actions"] = _build_founder_actions(insights)
    return insights


def save_agent_insights(
    insights: Optional[Dict[str, Any]] = None,
    path: Path = AGENT_INSIGHTS_PATH,
) -> Path:
    """Persist snapshot to data/agent_insights.json."""
    payload = insights or build_agent_insights()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({**payload, "saved_path": str(path)}, handle, indent=2, default=str)
    logger.info("[AGENT_INSIGHTS] Saved: %s", path)
    return path


def load_agent_insights(path: Path = AGENT_INSIGHTS_PATH) -> Optional[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.debug("agent_insights load failed: %s", exc)
        return None


def format_agent_insights_summary(insights: Dict[str, Any], *, tag: str = "[OPS]") -> str:
    """Multi-line CLI summary."""
    lines = [
        f"{tag} === AGENT INSIGHTS ===",
        f"{tag} Generated: {insights.get('generated_at')} | Date: {insights.get('date_ist')}",
    ]

    promo = insights.get("promotion_status") or {}
    for idx in INDICES:
        info = promo.get(idx) or {}
        gate = "PASS" if info.get("passed") else "FAIL"
        lines.append(
            f"{tag} Promotion {idx}: [{gate}] {info.get('status', 'no_record')} "
            f"overlay={'eligible' if info.get('overlay_eligible') else 'no'}"
        )

    wfo = insights.get("multi_index_wfo") or {}
    if wfo.get("has_report"):
        summary = wfo.get("summary") or {}
        lines.append(
            f"{tag} Multi-index WFO: {wfo.get('run_id')} "
            f"({wfo.get('finished_at') or 'unknown'}) "
            f"passed={summary.get('passed_count', '?')}/{summary.get('index_count', '?')}"
        )
    else:
        lines.append(f"{tag} Multi-index WFO: no report on disk")

    pending = insights.get("pending_proposals") or {}
    lines.append(f"{tag} Pending proposals: {pending.get('count', 0)}")

    lunar = insights.get("lunar_context") or {}
    if lunar.get("available"):
        lines.append(
            f"{tag} Lunar: {lunar.get('phase_name')} | {lunar.get('tithi_name')} "
            f"(research only)"
        )

    market = insights.get("market_context") or {}
    if market.get("available"):
        lines.append(f"{tag} Market context: loaded from {market.get('path')}")
    else:
        lines.append(f"{tag} Market context: not available")

    for note in insights.get("documentation_notes") or []:
        lines.append(f"{tag} Note: {note}")

    if insights.get("founder_actions"):
        lines.append(f"{tag} Actions:")
        for act in insights["founder_actions"]:
            lines.append(f"{tag}   → {act}")

    return "\n".join(lines)