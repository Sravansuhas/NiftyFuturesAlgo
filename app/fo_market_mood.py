"""
Market F&O mood — tape readability vs tradeability for dashboard / API.

Tape mood (0–100): how clean / directional the session tape looks.
Tradeability (0–100): whether the engine should be taking new risk right now.

Brother's manual options sheet (external_signals) is compared to algo regime for
mismatch summaries — e.g. one-sided CE bias while algo sees ranging chop.
"""

from __future__ import annotations

from time import time
from typing import Any, Dict, List, Optional, Tuple

from .market_calendar import now_ist

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

_TAPE_WEIGHTS = {
    "trend_clarity": 0.30,
    "chop_inverted": 0.30,
    "one_sided_strength": 0.20,
    "htf_alignment": 0.20,
}

_TRADEABILITY_WEIGHTS = {
    "session_window": 0.25,
    "fo_guards_clear": 0.25,
    "posture_tier": 0.20,
    "market_trading_allowed": 0.15,
    "engine_not_halted": 0.15,
}

_POSTURE_SCORES = {
    "aggressive": 100.0,
    "normal": 72.0,
    "defensive": 42.0,
    "contingency": 12.0,
}

_HALTED_STATES = frozenset({
    "EMERGENCY_HALT",
    "CIRCUIT_BREAKER_TRIGGERED",
    "RECONCILIATION_FAILED",
    "BROKER_DISCONNECTED",
    "TRADING_DISABLED",
})

_TREND_CLARITY = {
    "uptrend": 88.0,
    "downtrend": 88.0,
    "ranging": 28.0,
    "flat": 22.0,
}

_FO_MOOD_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None, "key": None}
_FO_MOOD_CACHE_TTL_SEC = 30

_MACRO_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None, "version": 0}
_MACRO_CACHE_TTL_SEC = 300
_MACRO_CACHE_VERSION = 2  # bump when macro parser shape changes (e.g. FII/DII NSE format)


def bust_fo_mood_cache() -> None:
    """Invalidate the 30s mood snapshot cache."""
    global _FO_MOOD_CACHE
    _FO_MOOD_CACHE = {"ts": 0.0, "payload": None, "key": None}


def fetch_macro_cached(*, force: bool = False) -> Dict[str, Any]:
    """Macro snapshot (VIX + FII/DII) with 5-minute TTL; VIX uses Kite fallback when NSE fails."""
    global _MACRO_CACHE
    now = time()
    if (
        not force
        and _MACRO_CACHE.get("payload")
        and int(_MACRO_CACHE.get("version") or 0) == _MACRO_CACHE_VERSION
        and (now - float(_MACRO_CACHE.get("ts") or 0)) < _MACRO_CACHE_TTL_SEC
    ):
        return _MACRO_CACHE["payload"]

    kite = None
    try:
        from .instruments_manager import instruments_manager

        kite = instruments_manager.kite
    except Exception:
        kite = None

    try:
        from .market_context import fetch_india_vix
        from .nse_data import fetch_fii_dii_flow

        payload = {
            "fetched_at": now_ist().isoformat(),
            "vix": fetch_india_vix(kite=kite),
            "fii_dii": fetch_fii_dii_flow(),
        }
    except Exception:
        payload = {"fetched_at": now_ist().isoformat(), "available": False}

    _MACRO_CACHE = {"ts": now, "payload": payload, "version": _MACRO_CACHE_VERSION}
    return payload


def _clamp(score: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, score))


def _tape_zone(score: float) -> str:
    if score < 25:
        return "chop_trap"
    if score < 45:
        return "weak"
    if score < 55:
        return "neutral"
    if score < 75:
        return "trend_ok"
    return "extended"


def _tradeability_zone(score: float) -> str:
    if score < 35:
        return "blocked"
    if score < 60:
        return "cautious"
    return "ready"


def _regime_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    snap = snapshot or {}
    regime = snap.get("regime") or {}
    if not isinstance(regime, dict):
        regime = {}
    return regime


def _trend_clarity_score(regime: Dict[str, Any]) -> Tuple[float, str]:
    trend = str(regime.get("trend") or "ranging").lower()
    base = _TREND_CLARITY.get(trend, 35.0)
    adx = regime.get("adx_proxy")
    detail = f"trend={trend}"
    if adx is not None:
        adx_f = float(adx)
        adx_boost = _clamp((adx_f - 12.0) / 28.0 * 100.0)
        score = _clamp(base * 0.55 + adx_boost * 0.45)
        detail = f"{detail}, adx_proxy={adx_f:.1f}"
        return score, detail
    return _clamp(base), detail


def _chop_inverted_score(regime: Dict[str, Any]) -> Tuple[float, str]:
    chop = float(regime.get("chop_score", 0.5) or 0.5)
    score = _clamp((1.0 - chop) * 100.0)
    return score, f"chop_score={chop:.2f} (inverted)"


def _htf_alignment_score(regime: Dict[str, Any]) -> Tuple[float, str]:
    trend = str(regime.get("trend") or "ranging").lower()
    htf = str(regime.get("htf_bias") or "neutral").lower()
    if trend == "uptrend" and htf == "bullish":
        return 92.0, "uptrend + bullish HTF"
    if trend == "downtrend" and htf == "bearish":
        return 92.0, "downtrend + bearish HTF"
    if trend in ("uptrend", "downtrend") and htf == "neutral":
        return 68.0, f"{trend} with neutral HTF"
    if trend in ("ranging", "flat") and htf == "neutral":
        return 48.0, "ranging + neutral HTF"
    if trend == "uptrend" and htf == "bearish":
        return 22.0, "uptrend vs bearish HTF — conflict"
    if trend == "downtrend" and htf == "bullish":
        return 22.0, "downtrend vs bullish HTF — conflict"
    return 40.0, f"{trend} + {htf} HTF"


def _one_sided_strength_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    snap = snapshot or {}
    regime = _regime_from_snapshot(snap)
    proposed = str(snap.get("proposed") or "FLAT").upper()
    confidence = float(snap.get("confidence", 0) or 0)
    trend = str(regime.get("trend") or "ranging").lower()

    if proposed in ("LONG", "SHORT"):
        score = _clamp(55.0 + confidence * 45.0)
        return score, f"proposed {proposed} conf={confidence:.2f}"

    if trend == "uptrend":
        adx = float(regime.get("adx_proxy", 25) or 25)
        return _clamp(50.0 + adx * 0.8), f"uptrend drift adx={adx:.1f}"
    if trend == "downtrend":
        adx = float(regime.get("adx_proxy", 25) or 25)
        return _clamp(50.0 + adx * 0.8), f"downtrend drift adx={adx:.1f}"

    chop = float(regime.get("chop_score", 0.6) or 0.6)
    return _clamp(18.0 + (1.0 - chop) * 22.0), "no breakout — ranging tape"


def _leg_active(side: Optional[Dict[str, Any]]) -> bool:
    if not side:
        return False
    journal = str(side.get("journal_status") or "").lower()
    status = str(side.get("status") or "").lower()
    if journal in ("skipped", "expired") or status == "skipped":
        return False
    if side.get("strike") or side.get("entry") is not None:
        return True
    return status not in ("", "ready", "skipped") or journal in ("entered", "watching", "target_met")


def _brother_bias_for_index(
    external_sheet: Optional[Dict[str, Any]],
    symbol: str,
) -> Tuple[str, str, float]:
    """
    Return (bias, detail, one_sided_score) from brother's manual sheet.
    bias: bullish | bearish | balanced | none
    """
    if not external_sheet:
        return "none", "no external sheet", 0.0

    block = (external_sheet.get("indices") or {}).get(symbol) or {}
    call = block.get("call") or {}
    put = block.get("put") or {}
    call_on = _leg_active(call)
    put_on = _leg_active(put)

    if call_on and not put_on:
        entered = str(call.get("journal_status") or "").lower() == "entered"
        score = 88.0 if entered else 72.0
        return "bullish", "brother one-sided CE only", score
    if put_on and not call_on:
        entered = str(put.get("journal_status") or "").lower() == "entered"
        score = 88.0 if entered else 72.0
        return "bearish", "brother one-sided PE only", score
    if call_on and put_on:
        return "balanced", "brother straddle / both legs", 45.0
    return "none", "brother sheet empty for index", 0.0


def _weighted_score(parts: List[Tuple[str, float, float, str]], weights: Dict[str, float]) -> Tuple[float, List[Dict[str, Any]]]:
    components: List[Dict[str, Any]] = []
    total = 0.0
    weight_sum = 0.0
    for key, score, weight, detail in parts:
        w = weights.get(key, weight)
        total += score * w
        weight_sum += w
        components.append({
            "label": key.replace("_", " ").title(),
            "key": key,
            "score": round(score, 1),
            "weight": round(w, 3),
            "detail": detail,
        })
    if weight_sum <= 0:
        return 0.0, components
    return _clamp(total / weight_sum), components


def _index_tape_mood(snapshot: Optional[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]], Dict[str, Any]]:
    regime = _regime_from_snapshot(snapshot)
    parts = [
        ("trend_clarity", *_trend_clarity_score(regime)),
        ("chop_inverted", *_chop_inverted_score(regime)),
        ("one_sided_strength", *_one_sided_strength_from_snapshot(snapshot)),
        ("htf_alignment", *_htf_alignment_score(regime)),
    ]
    weighted_parts = [
        (parts[0][0], parts[0][1], _TAPE_WEIGHTS["trend_clarity"], parts[0][2]),
        (parts[1][0], parts[1][1], _TAPE_WEIGHTS["chop_inverted"], parts[1][2]),
        (parts[2][0], parts[2][1], _TAPE_WEIGHTS["one_sided_strength"], parts[2][2]),
        (parts[3][0], parts[3][1], _TAPE_WEIGHTS["htf_alignment"], parts[3][2]),
    ]
    score, components = _weighted_score(weighted_parts, _TAPE_WEIGHTS)
    return score, components, regime


def _session_window_score(market_status: Dict[str, Any]) -> Tuple[float, str]:
    if market_status.get("is_trading_holiday"):
        return 0.0, "trading holiday"
    if not market_status.get("is_market_open", False):
        return 8.0, "market closed"
    if market_status.get("is_eod_flatten_window"):
        return 25.0, "EOD flatten window"
    if market_status.get("within_pre_event_block_window"):
        return 20.0, "pre-event block window"
    try:
        from .paper_trading_params import DEFAULT_PAPER_PARAMS

        now_t = now_ist().time()
        if now_t < DEFAULT_PAPER_PARAMS.session_start:
            return 15.0, f"before paper session ({DEFAULT_PAPER_PARAMS.session_start})"
        if now_t > DEFAULT_PAPER_PARAMS.session_end:
            return 12.0, f"after paper session ({DEFAULT_PAPER_PARAMS.session_end})"
    except Exception:
        pass
    if market_status.get("is_safe_trading_window", True):
        return 100.0, "inside strategy session window"
    return 45.0, "open but outside safe window"


def _fo_guards_score(fo_guards: Dict[str, Any], symbol: str) -> Tuple[float, str]:
    sym_snap = (fo_guards.get("symbols") or {}).get(symbol) or {}
    if not sym_snap:
        return 70.0, "no guard snapshot — assume clear"
    if sym_snap.get("allowed", True):
        return 100.0, "tier-1 guards clear"
    rule = sym_snap.get("blocked_rule") or sym_snap.get("block_reason") or "blocked"
    return 5.0, f"blocked: {rule}"


def _portfolio_fo_guards_score(fo_guards: Dict[str, Any]) -> Tuple[float, str]:
    if not fo_guards:
        return 75.0, "no portfolio guard snapshot"
    if not fo_guards.get("any_blocked"):
        return 100.0, "all indices clear"
    blocked = [
        sym for sym in INDICES
        if not ((fo_guards.get("symbols") or {}).get(sym) or {}).get("allowed", True)
    ]
    if len(blocked) >= 2:
        reason = fo_guards.get("portfolio_block_reason") or "multiple symbols blocked"
        return 8.0, f"portfolio blocked ({', '.join(blocked)}): {reason}"
    return 35.0, f"{blocked[0]} blocked"


def _posture_score(posture_snapshot: Dict[str, Any]) -> Tuple[float, str]:
    portfolio = posture_snapshot.get("portfolio") or {}
    tier = str(portfolio.get("posture") or "normal").lower()
    score = _POSTURE_SCORES.get(tier, 50.0)
    color = portfolio.get("market_color") or "unknown"
    return score, f"portfolio posture={tier}, color={color}"


def _engine_halted(market_status: Dict[str, Any]) -> Tuple[bool, str]:
    state = str(market_status.get("engine_state") or "").upper()
    if state in _HALTED_STATES:
        return True, f"engine state={state}"
    if market_status.get("system_halted"):
        return True, "system_halted flag set"
    return False, state or "active"


def _tradeability_score(
    market_status: Dict[str, Any],
    fo_guards: Dict[str, Any],
    posture_snapshot: Dict[str, Any],
) -> Tuple[float, List[Dict[str, Any]]]:
    session_s, session_d = _session_window_score(market_status)
    guards_s, guards_d = _portfolio_fo_guards_score(fo_guards)
    posture_s, posture_d = _posture_score(posture_snapshot)
    market_ok = bool(market_status.get("trading_allowed", True))
    market_s = 100.0 if market_ok else 0.0
    market_d = "calendar trading_allowed" if market_ok else "calendar blocks new entries"

    halted, halt_d = _engine_halted(market_status)
    engine_allowed = market_status.get("engine_trading_allowed")
    if engine_allowed is None:
        engine_allowed = not halted
    engine_s = 100.0 if engine_allowed and not halted else 0.0

    parts = [
        ("session_window", session_s, _TRADEABILITY_WEIGHTS["session_window"], session_d),
        ("fo_guards_clear", guards_s, _TRADEABILITY_WEIGHTS["fo_guards_clear"], guards_d),
        ("posture_tier", posture_s, _TRADEABILITY_WEIGHTS["posture_tier"], posture_d),
        ("market_trading_allowed", market_s, _TRADEABILITY_WEIGHTS["market_trading_allowed"], market_d),
        ("engine_not_halted", engine_s, _TRADEABILITY_WEIGHTS["engine_not_halted"], halt_d),
    ]
    return _weighted_score(parts, _TRADEABILITY_WEIGHTS)


def _algo_trend_label(regime: Dict[str, Any]) -> str:
    trend = str(regime.get("trend") or "ranging").lower()
    chop = float(regime.get("chop_score", 0) or 0)
    if trend in ("ranging", "flat") or chop >= 0.55:
        return "ranging"
    return trend


def _detect_mismatch(
    per_index: Dict[str, Any],
) -> Tuple[bool, str]:
    mismatches: List[str] = []
    for sym, row in per_index.items():
        brother = row.get("brother_bias") or "none"
        algo = row.get("algo_trend") or "ranging"
        if brother == "bullish" and algo == "ranging":
            mismatches.append(f"{sym}: brother one-sided CE vs algo ranging")
        elif brother == "bearish" and algo == "ranging":
            mismatches.append(f"{sym}: brother one-sided PE vs algo ranging")
        elif brother == "bullish" and algo == "downtrend":
            mismatches.append(f"{sym}: brother bullish vs algo downtrend")
        elif brother == "bearish" and algo == "uptrend":
            mismatches.append(f"{sym}: brother bearish vs algo uptrend")
    if not mismatches:
        return False, ""
    return True, "; ".join(mismatches)


def _human_summary(
    tape_mood: float,
    tape_zone: str,
    tradeability: float,
    trade_zone: str,
    mismatch: bool,
    mismatch_detail: str,
    per_index: Dict[str, Any],
) -> str:
    zone_words = {
        "chop_trap": "chop-trap tape",
        "weak": "weak / noisy tape",
        "neutral": "neutral tape",
        "trend_ok": "trend-friendly tape",
        "extended": "extended / stretched tape",
    }
    trade_words = {
        "blocked": "entries blocked",
        "cautious": "trade cautiously",
        "ready": "conditions ready for entries",
    }
    lead = (
        f"Tape mood {tape_mood:.0f}/100 ({zone_words.get(tape_zone, tape_zone)}); "
        f"tradeability {tradeability:.0f}/100 ({trade_words.get(trade_zone, trade_zone)})."
    )
    if mismatch:
        return f"{lead} Mismatch: {mismatch_detail}."
    aligned = [
        sym for sym, row in per_index.items()
        if row.get("brother_bias") not in ("none", "balanced", "")
        and row.get("algo_trend") in ("uptrend", "downtrend")
    ]
    if aligned:
        return f"{lead} Brother bias aligns with algo trend on {', '.join(aligned)}."
    return lead


def _algo_summary(
    tape_mood: float,
    tape_zone: str,
    tradeability: float,
    trade_zone: str,
    components: List[Dict[str, Any]],
    mismatch_detail: str,
) -> str:
    top = sorted(components, key=lambda c: c.get("weight", 0) * abs(c.get("score", 50) - 50), reverse=True)
    drivers = ", ".join(
        f"{c['label']}={c['score']:.0f}" for c in top[:2]
    ) if top else "insufficient data"
    base = (
        f"tape_mood={tape_mood:.1f} zone={tape_zone}; "
        f"tradeability={tradeability:.1f} zone={trade_zone}; drivers: {drivers}"
    )
    if mismatch_detail:
        return f"{base}; mismatch={mismatch_detail}"
    return base


def _load_brother_sheet() -> Optional[Dict[str, Any]]:
    try:
        from .external_signals import external_signals_store

        return external_signals_store.get()
    except Exception:
        return None


def _cache_key(
    live_snapshots: Dict[str, Any],
    market_status: Dict[str, Any],
    fo_guards: Dict[str, Any],
    posture_snapshot: Dict[str, Any],
) -> str:
    parts = []
    for sym in INDICES:
        snap = live_snapshots.get(sym) or {}
        regime = snap.get("regime") or {}
        parts.append(
            f"{sym}:{regime.get('trend')}:{regime.get('chop_score')}:{snap.get('proposed')}"
        )
    parts.append(str(market_status.get("trading_allowed")))
    parts.append(str(market_status.get("engine_state")))
    parts.append(str(fo_guards.get("any_blocked")))
    portfolio = posture_snapshot.get("portfolio") or {}
    parts.append(str(portfolio.get("posture")))
    return "|".join(parts)


def compute_fo_market_mood(
    live_snapshots: Dict[str, Any],
    market_status: Dict[str, Any],
    fo_guards: Dict[str, Any],
    posture_snapshot: Dict[str, Any],
    macro_context: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Compute portfolio F&O mood from live snapshots, guards, posture, and session.

    market_status may include engine_state / engine_trading_allowed for halt awareness.
    macro_context is attached to the payload when provided (or left empty).
    """
    global _FO_MOOD_CACHE
    now = time()
    key = _cache_key(live_snapshots, market_status, fo_guards, posture_snapshot)
    if (
        not force
        and _FO_MOOD_CACHE.get("payload")
        and _FO_MOOD_CACHE.get("key") == key
        and (now - float(_FO_MOOD_CACHE.get("ts") or 0)) < _FO_MOOD_CACHE_TTL_SEC
    ):
        return _FO_MOOD_CACHE["payload"]

    live_snapshots = live_snapshots or {}
    market_status = market_status or {}
    fo_guards = fo_guards or {}
    posture_snapshot = posture_snapshot or {}

    brother_sheet = _load_brother_sheet()
    per_index: Dict[str, Any] = {}
    tape_scores: List[float] = []
    all_tape_components: List[Dict[str, Any]] = []

    for sym in INDICES:
        snap = live_snapshots.get(sym) or {}
        tape_score, tape_components, regime = _index_tape_mood(snap)
        brother_bias, brother_detail, _ = _brother_bias_for_index(brother_sheet, sym)
        guard_score, guard_detail = _fo_guards_score(fo_guards, sym)
        sym_posture = (posture_snapshot.get("per_symbol") or {}).get(sym) or {}
        posture_tier = sym_posture.get("posture") or (posture_snapshot.get("portfolio") or {}).get("posture")

        per_index[sym] = {
            "tape_mood": round(tape_score, 1),
            "tape_zone": _tape_zone(tape_score),
            "regime": {
                "trend": regime.get("trend"),
                "volatility": regime.get("volatility"),
                "htf_bias": regime.get("htf_bias"),
                "chop_score": regime.get("chop_score"),
                "adx_proxy": regime.get("adx_proxy"),
            },
            "algo_trend": _algo_trend_label(regime),
            "proposed": snap.get("proposed"),
            "brother_bias": brother_bias,
            "brother_detail": brother_detail,
            "guard_allowed": ((fo_guards.get("symbols") or {}).get(sym) or {}).get("allowed", True),
            "guard_detail": guard_detail,
            "posture": posture_tier,
            "components": tape_components,
        }
        tape_scores.append(tape_score)
        for comp in tape_components:
            tagged = dict(comp)
            tagged["index"] = sym
            all_tape_components.append(tagged)

    tape_mood = round(sum(tape_scores) / max(len(tape_scores), 1), 1)
    tape_zone = _tape_zone(tape_mood)

    tradeability, trade_components = _tradeability_score(
        market_status, fo_guards, posture_snapshot,
    )
    blocked_count = sum(
        1 for sym in INDICES
        if not ((fo_guards.get("symbols") or {}).get(sym) or {}).get("allowed", True)
    )
    if blocked_count >= 2:
        tradeability = min(tradeability, 32.0)
    elif blocked_count == 1:
        tradeability = min(tradeability, 48.0)
    tradeability = round(tradeability, 1)
    trade_zone = _tradeability_zone(tradeability)

    portfolio_components = [
        {
            "label": c["label"],
            "key": c.get("key"),
            "score": c["score"],
            "weight": c["weight"],
            "detail": c["detail"],
            "scope": "portfolio",
        }
        for c in trade_components
    ]

    mismatch, mismatch_detail = _detect_mismatch(per_index)
    human = _human_summary(
        tape_mood, tape_zone, tradeability, trade_zone,
        mismatch, mismatch_detail, per_index,
    )
    algo = _algo_summary(
        tape_mood, tape_zone, tradeability, trade_zone,
        all_tape_components, mismatch_detail,
    )

    payload: Dict[str, Any] = {
        "tape_mood": tape_mood,
        "tape_zone": tape_zone,
        "tradeability": tradeability,
        "tradeability_zone": trade_zone,
        "human_summary": human,
        "algo_summary": algo,
        "mismatch": mismatch,
        "mismatch_detail": mismatch_detail,
        "per_index": per_index,
        "components": portfolio_components + [
            {**c, "scope": "tape"} for c in sorted(
                all_tape_components,
                key=lambda x: (x.get("index", ""), x.get("key", "")),
            )
        ],
        "computed_at": now_ist().isoformat(),
        "cached": False,
    }
    if macro_context is not None:
        payload["macro_context"] = macro_context

    payload["cached"] = False
    _FO_MOOD_CACHE = {"ts": now, "payload": {**payload, "cached": True}, "key": key}
    return payload