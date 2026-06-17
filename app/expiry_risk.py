"""
Expiry / gamma caution gates for options structures.

gamma_caution_level:
  0 = clear
  1 = soft (expiry morning — caution, allow entries with metadata)
  2 = hard (block new entries)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .greeks import DEFAULT_RISK_FREE_RATE, greeks_for_underlying
from .market_calendar import get_nse_fo_market_close, is_expiry_day, now_ist

logger = logging.getLogger(__name__)

# Gamma caution levels
GAMMA_CAUTION_CLEAR = 0
GAMMA_CAUTION_SOFT = 1
GAMMA_CAUTION_HARD = 2

# Trigger type strings (audit / dashboard)
TRIGGER_NONE = "none"
TRIGGER_CALENDAR_SOFT = "calendar_soft"
TRIGGER_CALENDAR_HARD = "calendar_hard"
TRIGGER_GAMMA_PROXY_SOFT = "gamma_proxy_soft"
TRIGGER_GAMMA_PROXY_HARD = "gamma_proxy_hard"

DEFAULT_GAMMA_PROXY_HARD_THRESHOLD = 0.00035
DEFAULT_GAMMA_PROXY_OI_THRESHOLD = 8_000_000

_STRIKE_STEPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}


@dataclass
class ExpiryGateEvaluation:
    gamma_caution_level: int
    reasons: List[str]
    trigger_type: str
    is_expiry_day: bool = False
    expiry_caution: bool = False
    expiry_entry_cutoff_hour: int = 12
    gamma_proxy: Optional[Dict[str, Any]] = None
    triggers: List[str] = field(default_factory=list)

    @property
    def blocks_new_entries(self) -> bool:
        return self.gamma_caution_level >= GAMMA_CAUTION_HARD

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "gamma_caution_level": self.gamma_caution_level,
            "trigger_type": self.trigger_type,
            "expiry_triggers": list(self.triggers),
            "reasons": list(self.reasons),
            "is_expiry_day": self.is_expiry_day,
            "expiry_caution": self.expiry_caution,
            "expiry_entry_cutoff_hour": self.expiry_entry_cutoff_hour,
            "gamma_proxy": self.gamma_proxy,
        }


def _strike_step(underlying: str) -> int:
    return _STRIKE_STEPS.get((underlying or "NIFTY").upper(), 50)


def _round_atm_strike(spot: float, underlying: str) -> float:
    step = _strike_step(underlying)
    return round(spot / step) * step


def _resolve_spot_iv(
    underlying: str,
    market_context: Optional[Dict[str, Any]],
    config: Dict[str, Any],
) -> Tuple[Optional[float], float]:
    spot: Optional[float] = None
    if market_context:
        for key in ("spot", "index_spot", "ltp"):
            raw = market_context.get(key)
            if raw is not None:
                try:
                    spot = float(raw)
                    if spot > 0:
                        break
                except (TypeError, ValueError):
                    pass

        tickers = market_context.get("index_tickers") or market_context.get("options_tickers")
        if spot is None and isinstance(tickers, dict):
            indices = tickers.get("indices") or tickers
            row = indices.get(underlying.upper()) if isinstance(indices, dict) else None
            if isinstance(row, dict):
                try:
                    spot = float(row.get("spot") or 0) or None
                except (TypeError, ValueError):
                    spot = None

    iv = float(config.get("default_iv") or 0.16)
    if market_context:
        vix = market_context.get("india_vix") or market_context.get("vix") or {}
        if isinstance(vix, dict) and vix.get("available"):
            try:
                level = float(vix.get("level") or 0)
                if level > 0:
                    iv = level / 100.0
            except (TypeError, ValueError):
                pass

    floor = float(config.get("iv_floor") or 0.12)
    cap = float(config.get("iv_cap") or 0.35)
    return spot, max(floor, min(cap, iv))


def _time_to_expiry_years(now: datetime, *, for_date: date) -> float:
    """Years to session close on the evaluation date (0DTE uses remaining session time)."""
    close_t = get_nse_fo_market_close(for_date)
    close_dt = datetime.combine(for_date, close_t, tzinfo=now.tzinfo)
    if now >= close_dt:
        return max(1.0 / (365.0 * 24.0 * 60.0), 1e-6)
    seconds = (close_dt - now).total_seconds()
    return max(seconds / (365.25 * 24.0 * 3600.0), 1.0 / (365.0 * 24.0 * 60.0))


def _atm_oi_from_context(
    market_context: Optional[Dict[str, Any]],
    underlying: str,
) -> Optional[Dict[str, Any]]:
    if not market_context:
        return None

    tickers = market_context.get("index_tickers") or market_context.get("options_tickers")
    if not isinstance(tickers, dict):
        return None

    indices = tickers.get("indices") or tickers
    if not isinstance(indices, dict):
        return None

    row = indices.get(underlying.upper())
    if not isinstance(row, dict):
        return None

    ce = row.get("ce") or {}
    pe = row.get("pe") or {}
    ce_oi = int(ce.get("oi") or 0) if isinstance(ce, dict) else 0
    pe_oi = int(pe.get("oi") or 0) if isinstance(pe, dict) else 0
    atm_strike = row.get("atm_strike")
    if ce_oi <= 0 and pe_oi <= 0:
        return None

    return {
        "atm_strike": atm_strike,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "atm_oi_sum": ce_oi + pe_oi,
    }


def _evaluate_gamma_proxy(
    config: Dict[str, Any],
    *,
    underlying: str,
    market_context: Optional[Dict[str, Any]],
    now: datetime,
    on_expiry: bool,
) -> Tuple[int, List[str], str, Optional[Dict[str, Any]]]:
    gates = config.get("regime_gates") or {}
    if not gates.get("enable_gamma_proxy", False):
        return GAMMA_CAUTION_CLEAR, [], TRIGGER_NONE, None

    if not on_expiry:
        return GAMMA_CAUTION_CLEAR, [], TRIGGER_NONE, None

    spot, iv = _resolve_spot_iv(underlying, market_context, config)
    if spot is None or spot <= 0:
        try:
            from .options_chain import options_chain_manager

            spot = options_chain_manager.get_spot_price(underlying)
            if spot is None or spot <= 0:
                expiry = options_chain_manager.resolve_expiry(underlying)
                atm = options_chain_manager.get_atm_strike(underlying, expiry=expiry)
                if atm:
                    spot = float(atm)
        except Exception as exc:
            logger.debug("gamma proxy spot resolve failed: %s", exc)

    if spot is None or spot <= 0:
        return GAMMA_CAUTION_CLEAR, [], TRIGGER_NONE, None

    atm = _round_atm_strike(spot, underlying)
    tte = _time_to_expiry_years(now, for_date=now.date())

    try:
        g_ce = greeks_for_underlying(
            underlying, spot, atm, tte, iv, "CE", risk_free_rate=DEFAULT_RISK_FREE_RATE
        )
        g_pe = greeks_for_underlying(
            underlying, spot, atm, tte, iv, "PE", risk_free_rate=DEFAULT_RISK_FREE_RATE
        )
    except Exception as exc:
        logger.debug("gamma proxy greeks failed: %s", exc)
        return GAMMA_CAUTION_CLEAR, [], TRIGGER_NONE, None

    combined_gamma = float(g_ce.gamma + g_pe.gamma)
    hard_threshold = float(
        gates.get("gamma_proxy_hard_threshold") or DEFAULT_GAMMA_PROXY_HARD_THRESHOLD
    )
    soft_threshold = gates.get("gamma_proxy_soft_threshold")

    meta: Dict[str, Any] = {
        "spot": round(spot, 2),
        "atm_strike": atm,
        "iv": round(iv, 4),
        "tte_years": round(tte, 8),
        "combined_atm_gamma": round(combined_gamma, 8),
        "ce_gamma": round(g_ce.gamma, 8),
        "pe_gamma": round(g_pe.gamma, 8),
        "hard_threshold": hard_threshold,
    }

    oi_meta = _atm_oi_from_context(market_context, underlying)
    if oi_meta:
        meta.update(oi_meta)
        oi_threshold = gates.get("gamma_proxy_oi_threshold")
        if oi_threshold is None:
            oi_threshold = DEFAULT_GAMMA_PROXY_OI_THRESHOLD
        oi_threshold = float(oi_threshold)
        meta["oi_threshold"] = oi_threshold
        if oi_meta.get("atm_oi_sum", 0) >= oi_threshold:
            return (
                GAMMA_CAUTION_HARD,
                [
                    f"High OI at ATM ({oi_meta['atm_oi_sum']:,} >= {int(oi_threshold):,})"
                ],
                TRIGGER_GAMMA_PROXY_HARD,
                meta,
            )

    if combined_gamma >= hard_threshold:
        return (
            GAMMA_CAUTION_HARD,
            [
                f"ATM gamma proxy {combined_gamma:.6f} >= hard threshold {hard_threshold:.6f}"
            ],
            TRIGGER_GAMMA_PROXY_HARD,
            meta,
        )

    if soft_threshold is not None and combined_gamma >= float(soft_threshold):
        return (
            GAMMA_CAUTION_SOFT,
            [
                f"ATM gamma proxy {combined_gamma:.6f} >= soft threshold {float(soft_threshold):.6f}"
            ],
            TRIGGER_GAMMA_PROXY_SOFT,
            meta,
        )

    return GAMMA_CAUTION_CLEAR, [], TRIGGER_NONE, meta


def _merge_evaluations(
    calendar: ExpiryGateEvaluation,
    proxy_level: int,
    proxy_reasons: List[str],
    proxy_trigger: str,
    proxy_meta: Optional[Dict[str, Any]],
) -> ExpiryGateEvaluation:
    level = max(calendar.gamma_caution_level, proxy_level)
    reasons = list(calendar.reasons)
    trigger = calendar.trigger_type
    triggers = list(calendar.triggers)
    expiry_caution = calendar.expiry_caution
    gamma_proxy = calendar.gamma_proxy

    if proxy_level > 0:
        if proxy_meta is not None:
            gamma_proxy = proxy_meta
        if proxy_level > calendar.gamma_caution_level:
            reasons = list(proxy_reasons)
            trigger = proxy_trigger
        elif proxy_level == calendar.gamma_caution_level and proxy_reasons:
            for r in proxy_reasons:
                if r not in reasons:
                    reasons.append(r)
            if proxy_trigger != TRIGGER_NONE and proxy_trigger not in triggers:
                triggers.append(proxy_trigger)
        elif proxy_reasons:
            for r in proxy_reasons:
                if r not in reasons:
                    reasons.append(r)

    if proxy_trigger != TRIGGER_NONE and proxy_trigger not in triggers:
        triggers.append(proxy_trigger)
    if calendar.trigger_type != TRIGGER_NONE and calendar.trigger_type not in triggers:
        triggers.insert(0, calendar.trigger_type)

    if level == GAMMA_CAUTION_SOFT and not expiry_caution:
        expiry_caution = True

    return ExpiryGateEvaluation(
        gamma_caution_level=level,
        reasons=reasons,
        trigger_type=trigger if trigger != TRIGGER_NONE else (
            proxy_trigger if proxy_level > 0 else TRIGGER_NONE
        ),
        is_expiry_day=calendar.is_expiry_day,
        expiry_caution=expiry_caution,
        expiry_entry_cutoff_hour=calendar.expiry_entry_cutoff_hour,
        gamma_proxy=gamma_proxy,
        triggers=triggers,
    )


def evaluate_expiry_gates(
    config: Dict[str, Any],
    underlying: str = "NIFTY",
    market_context: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> ExpiryGateEvaluation:
    """
    Evaluate calendar + optional gamma-proxy gates for new short-vol entries.

    Returns ExpiryGateEvaluation; unpack as (level, reasons, trigger_type) via fields.
    """
    gates = config.get("regime_gates") or {}
    ts = now or now_ist()
    day = ts.date()
    underlying_u = (underlying or "NIFTY").upper()
    on_expiry = is_expiry_day(day, underlying=underlying_u, include_weekly=True)
    cutoff_hour = int(gates.get("expiry_day_entry_cutoff_hour", 12))

    cal_level = GAMMA_CAUTION_CLEAR
    cal_reasons: List[str] = []
    cal_trigger = TRIGGER_NONE
    expiry_caution = False

    if gates.get("block_expiry_day_entries", False) and on_expiry:
        cal_level = GAMMA_CAUTION_HARD
        cal_reasons.append(f"Expiry day entry blocked for {underlying_u}")
        cal_trigger = TRIGGER_CALENDAR_HARD
    elif on_expiry:
        if ts.hour >= cutoff_hour:
            cal_level = GAMMA_CAUTION_HARD
            cal_reasons.append(
                f"Expiry day — no new entries after {cutoff_hour:02d}:00 IST (gamma caution)"
            )
            cal_trigger = TRIGGER_CALENDAR_HARD
        else:
            cal_level = GAMMA_CAUTION_SOFT
            cal_reasons.append(
                f"Expiry day morning — elevated gamma risk before {cutoff_hour:02d}:00 IST"
            )
            cal_trigger = TRIGGER_CALENDAR_SOFT
            expiry_caution = True

    calendar_eval = ExpiryGateEvaluation(
        gamma_caution_level=cal_level,
        reasons=cal_reasons,
        trigger_type=cal_trigger,
        is_expiry_day=on_expiry,
        expiry_caution=expiry_caution,
        expiry_entry_cutoff_hour=cutoff_hour,
        triggers=[cal_trigger] if cal_trigger != TRIGGER_NONE else [],
    )

    proxy_level, proxy_reasons, proxy_trigger, proxy_meta = _evaluate_gamma_proxy(
        config,
        underlying=underlying_u,
        market_context=market_context,
        now=ts,
        on_expiry=on_expiry,
    )

    return _merge_evaluations(
        calendar_eval,
        proxy_level,
        proxy_reasons,
        proxy_trigger,
        proxy_meta,
    )


def expiry_gate_blocks_entries(evaluation: ExpiryGateEvaluation) -> bool:
    """True when evaluation hard-blocks new entries."""
    return evaluation.blocks_new_entries