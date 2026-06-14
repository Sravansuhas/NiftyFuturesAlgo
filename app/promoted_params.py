"""
Human-gated promoted parameter overlays.

Principal-trader rules:
- Only whitelisted tuning keys may be applied from WFA promotion.
- Risk and frequency may only tighten vs base (never leverage up silently).
- Requires explicit human confirmation before writing overlay to disk.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .paper_trading_params import DEFAULT_PAPER_PARAMS, PaperTradingParams

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_DIR = PROJECT_ROOT / "data" / "promoted_overlays"

# Keys safe to tune from WFA — session boundaries and hard stops excluded
ALLOWED_KEYS = frozenset({
    "breakout_atr_mult",
    "profit_target_atr_mult",
    "stop_loss_atr_mult",
    "risk_per_trade_pct",
    "max_trades_per_day",
    "cooldown_minutes_after_trade",
})

_SANITIZERS: Dict[str, Callable[[Any, Any], Any]] = {
    "breakout_atr_mult": lambda base, v: max(0.50, min(1.20, float(v))),
    "profit_target_atr_mult": lambda base, v: max(1.00, min(4.00, float(v))),
    "stop_loss_atr_mult": lambda base, v: max(0.80, min(2.00, float(v))),
    "risk_per_trade_pct": lambda base, v: min(float(base), float(v)),
    "max_trades_per_day": lambda base, v: min(int(base), int(v)),
    "cooldown_minutes_after_trade": lambda base, v: max(int(base), int(v)),
}


def _base_params_dict() -> Dict[str, Any]:
    return {f.name: getattr(DEFAULT_PAPER_PARAMS, f.name) for f in fields(PaperTradingParams)}


def _get_promoted_candidate(underlying: str) -> Optional[Dict[str, Any]]:
    try:
        from backtesting.promotion_gates import load_candidates

        for cand in load_candidates():
            if cand.get("underlying") == underlying.upper():
                return cand
    except Exception:
        pass
    return None


def _sanitize_overlay(base: Dict[str, Any], proposed: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in proposed.items():
        if key not in ALLOWED_KEYS:
            continue
        sanitizer = _SANITIZERS.get(key)
        if sanitizer is None:
            continue
        sanitized[key] = sanitizer(base.get(key), value)
    return sanitized


def preview_promoted_overlay(underlying: str) -> Dict[str, Any]:
    """Read-only diff: base params vs promotion-approved overlay."""
    key = underlying.upper()
    base = _base_params_dict()
    cand = _get_promoted_candidate(key)
    if not cand:
        return {"eligible": False, "underlying": key, "reason": "no_promotion_record"}
    if not cand.get("passed") or cand.get("status") != "promoted":
        return {
            "eligible": False,
            "underlying": key,
            "reason": cand.get("status", "not_promoted"),
            "status": cand.get("status"),
        }

    raw = cand.get("best_params") or {}
    proposed = _sanitize_overlay(base, {k: v for k, v in raw.items() if k in ALLOWED_KEYS})
    if not proposed:
        return {"eligible": False, "underlying": key, "reason": "no_safe_params_in_candidate"}

    overlay_path = OVERLAY_DIR / f"{key}.json"
    active_overlay = load_overlay(key)
    return {
        "eligible": True,
        "underlying": key,
        "base": {k: base[k] for k in proposed},
        "proposed": proposed,
        "active_overlay": active_overlay.get("params") if active_overlay else None,
        "overlay_path": str(overlay_path),
        "fold_pass_count": cand.get("fold_pass_count"),
        "evaluated_at": cand.get("evaluated_at"),
    }


def load_overlay(underlying: str) -> Optional[Dict[str, Any]]:
    path = OVERLAY_DIR / f"{underlying.upper()}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def apply_promoted_overlay(underlying: str, human_confirmed: bool = False) -> Dict[str, Any]:
    """Write overlay after human confirmation. Never mutates strategy_config.yaml."""
    preview = preview_promoted_overlay(underlying)
    if not preview.get("eligible"):
        return {"success": False, "error": preview.get("reason", "not_eligible"), "preview": preview}
    if not human_confirmed:
        return {"success": False, "error": "human_confirmation_required", "preview": preview}

    key = underlying.upper()
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "underlying": key,
        "applied_at": time.time(),
        "source": "promotion_gates",
        "params": preview["proposed"],
        "base_snapshot": preview["base"],
        "fold_pass_count": preview.get("fold_pass_count"),
    }
    path = OVERLAY_DIR / f"{key}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return {"success": True, "path": str(path), "params": preview["proposed"]}


def merge_paper_params(
    base: PaperTradingParams,
    underlying: str,
    *,
    use_overlay: bool = False,
) -> Tuple[PaperTradingParams, Dict[str, Any]]:
    """
    Return paper params with optional promoted overlay merged in.

    Overlay is ignored unless use_overlay=True (USE_PROMOTED_PARAMS env in main).
    Re-validates promotion status on every load — stale overlays are not applied.
    """
    meta: Dict[str, Any] = {"overlay_applied": False, "underlying": underlying.upper()}
    if not use_overlay:
        return base, meta

    preview = preview_promoted_overlay(underlying)
    if not preview.get("eligible"):
        meta["skip_reason"] = preview.get("reason", "not_eligible")
        return base, meta

    overlay = load_overlay(underlying)
    if not overlay or not overlay.get("params"):
        meta["skip_reason"] = "no_overlay_file"
        return base, meta

    merged = deepcopy(base)
    for key, value in overlay["params"].items():
        if key in ALLOWED_KEYS and hasattr(merged, key):
            setattr(merged, key, value)
    meta["overlay_applied"] = True
    meta["applied_params"] = overlay["params"]
    return merged, meta