"""
Iron Condor options strategy runner — regime-gated entry/exit for Aegis v1.0.

Called from main loop when OPTIONS_TRADING_ENABLED=true.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .config_loader import get_options_config
from .market_calendar import is_market_open, is_real_market_open, now_ist
from .options_execution_engine import options_execution_engine
from .options_positions import options_position_store
from .strategies.iron_condor import (
    default_strikes_from_spot,
    propose_iron_condor,
)

logger = logging.getLogger(__name__)

_last_cycle_result: Optional[Dict[str, Any]] = None
_last_cycle_at: Optional[str] = None


def _env_options_trading_flag() -> bool:
    from .trading_controls import effective_options_trading_enabled

    return effective_options_trading_enabled()


def options_trading_enabled() -> bool:
    from .trading_controls import effective_options_trading_enabled

    return effective_options_trading_enabled()


def futures_trading_enabled() -> bool:
    from .trading_controls import effective_futures_trading_enabled

    return effective_futures_trading_enabled()


def _time_to_expiry_years(expiry: date) -> float:
    today = now_ist().date()
    days = max((expiry - today).days, 1)
    return days / 365.0


def _resolve_iv(config: Dict[str, Any], market_context: Optional[Dict[str, Any]] = None) -> float:
    """Use India VIX / 100 with configured floor/cap (legacy fallback)."""
    from .options_iv import _vix_proxy_iv, _clamp_iv

    iv, _source = _vix_proxy_iv(config, market_context)
    return _clamp_iv(iv, config)


def check_regime_gates(
    config: Dict[str, Any],
    *,
    underlying: str = "NIFTY",
    market_context: Optional[Dict[str, Any]] = None,
    expiry_eval: Optional[Any] = None,
) -> Tuple[bool, List[str]]:
    """Regime / calendar gates for new short-vol structures."""
    from .expiry_risk import (
        ExpiryGateEvaluation,
        evaluate_expiry_gates,
        expiry_gate_blocks_entries,
    )

    reasons: List[str] = []
    gates = config.get("regime_gates") or {}

    if not is_real_market_open():
        reasons.append("Market closed")
        return False, reasons

    evaluation = expiry_eval
    if evaluation is None:
        evaluation = evaluate_expiry_gates(
            config,
            underlying=underlying,
            market_context=market_context,
        )
    elif not isinstance(evaluation, ExpiryGateEvaluation):
        evaluation = evaluate_expiry_gates(
            config,
            underlying=underlying,
            market_context=market_context,
        )

    if expiry_gate_blocks_entries(evaluation):
        reasons.extend(evaluation.reasons)

    vix_block = gates.get("max_vix")
    vix_min = gates.get("min_vix")
    vix_level = None
    if market_context:
        vix = market_context.get("india_vix") or {}
        if isinstance(vix, dict) and vix.get("available"):
            vix_level = float(vix.get("level") or 0)

    if vix_level is not None:
        if vix_block is not None and vix_level > float(vix_block):
            reasons.append(f"India VIX {vix_level:.1f} above max_vix {vix_block}")
        if vix_min is not None and vix_level < float(vix_min):
            reasons.append(f"India VIX {vix_level:.1f} below min_vix {vix_min}")

    return len(reasons) == 0, reasons


def _structure_status_row(
    struct,
    *,
    kite=None,
    ws_feed=None,
    include_mtm: bool = True,
) -> Dict[str, Any]:
    row = struct.to_dict()
    legs = row.get("legs") or []
    for leg in legs:
        opt = (leg.get("option_type") or "").upper()
        side = (leg.get("transaction_type") or "").upper()
        if opt == "PE" and side == "BUY":
            leg["role"] = "put_long"
        elif opt == "PE" and side == "SELL":
            leg["role"] = "put_short"
        elif opt == "CE" and side == "SELL":
            leg["role"] = "call_short"
        elif opt == "CE" and side == "BUY":
            leg["role"] = "call_long"
        leg["last_ltp"] = leg.get("premium")
    if include_mtm:
        mtm = estimate_structure_mtm(struct, kite=kite, ws_feed=ws_feed)
        if mtm is not None:
            row["mtm"] = round(mtm, 2)
            row["mtm_estimate"] = round(mtm, 2)
    return row


def record_options_cycle_result(result: Dict[str, Any]) -> None:
    """Persist last cycle outcome for dashboard / API status."""
    global _last_cycle_result, _last_cycle_at
    _last_cycle_result = deepcopy(result)
    _last_cycle_at = now_ist().isoformat()


def get_options_algo_status_payload(
    *,
    fast: bool = False,
    kite=None,
    ws_feed=None,
    market_context: Optional[Dict[str, Any]] = None,
    include_index_tickers: bool = False,
) -> Dict[str, Any]:
    """
    Automated options structures status for dashboard status/SSE and REST.

    Returns enabled flags, open structures, structures_today, last cycle,
    and regime gate summary.
    """
    cfg = get_options_config()
    underlying = str(cfg.get("underlying", "NIFTY")).upper()
    gates_cfg = cfg.get("regime_gates") or {}

    mctx = market_context
    if mctx is None and not fast:
        try:
            from .market_context import load_market_context

            mctx = load_market_context()
        except Exception:
            mctx = None

    from .expiry_risk import evaluate_expiry_gates

    now = now_ist()
    expiry_eval = evaluate_expiry_gates(
        cfg,
        underlying=underlying,
        market_context=mctx,
        now=now,
    )
    regime_ok, regime_reasons = check_regime_gates(
        cfg,
        underlying=underlying,
        market_context=mctx,
        expiry_eval=expiry_eval,
    )

    vix_level = None
    if mctx:
        vix = mctx.get("india_vix") or mctx.get("vix") or {}
        if isinstance(vix, dict) and vix.get("available"):
            vix_level = float(vix.get("level") or 0)

    options_position_store.maybe_reset_daily()
    open_structures = [
        _structure_status_row(
            struct,
            kite=kite if not fast else None,
            ws_feed=ws_feed if not fast else None,
            include_mtm=not fast,
        )
        for struct in options_position_store.list_open()
    ]

    mtm_values = [s.get("mtm") for s in open_structures if s.get("mtm") is not None]
    mtm_total = round(sum(mtm_values), 2) if mtm_values else None

    enabled_flags = {
        "options_trading": options_trading_enabled(),
        "config_trading_enabled": bool(cfg.get("trading_enabled")),
        "env_trading_enabled": _env_options_trading_flag(),
        "futures_trading": futures_trading_enabled(),
    }
    config_block = {
        "underlying": underlying,
        "product": cfg.get("product", "NRML"),
        "allowed_structures": list(cfg.get("allowed_structures") or []),
        "max_structures_per_day": int(cfg.get("max_structures_per_day", 1)),
        "evaluation_interval_sec": int(cfg.get("evaluation_interval_sec", 300)),
    }
    last_cycle = deepcopy(_last_cycle_result) if _last_cycle_result else None

    index_tickers = None
    if not fast and include_index_tickers:
        try:
            from .options_desk_tickers import get_index_option_tickers

            index_tickers = get_index_option_tickers(kite=kite, ws_feed=ws_feed)
        except Exception:
            index_tickers = None

    return {
        "available": True,
        "timestamp": now_ist().isoformat(),
        "enabled": enabled_flags,
        "config": config_block,
        # Flat aliases for React panel / legacy clients
        "config_enabled": enabled_flags["config_trading_enabled"],
        "env_enabled": enabled_flags["env_trading_enabled"],
        "futures_trading_enabled": enabled_flags["futures_trading"],
        "underlying": underlying,
        "product": config_block["product"],
        "allowed_structures": config_block["allowed_structures"],
        "max_structures_per_day": config_block["max_structures_per_day"],
        "session_date": options_position_store._session_date or options_position_store._today_key(),
        "structures_today": int(options_position_store.structures_today or 0),
        "open_structures": open_structures,
        "open_count": len(open_structures),
        "mtm_estimate": {
            "total": mtm_total,
            "structures": len(open_structures),
            "available": mtm_total is not None,
        },
        "last_cycle_result": last_cycle,
        "last_cycle": last_cycle,
        "last_cycle_at": _last_cycle_at,
        "gamma_caution_level": expiry_eval.gamma_caution_level,
        "expiry_triggers": list(expiry_eval.triggers),
        "is_expiry_day": expiry_eval.is_expiry_day,
        "expiry_caution": expiry_eval.expiry_caution,
        "regime_gates": {
            "allowed": regime_ok,
            "passed": regime_ok,
            "reasons": regime_reasons,
            "underlying": underlying,
            "vix_level": vix_level,
            "is_expiry_day": expiry_eval.is_expiry_day,
            "expiry_caution": expiry_eval.expiry_caution,
            "expiry_entry_cutoff_hour": expiry_eval.expiry_entry_cutoff_hour,
            "gamma_caution_level": expiry_eval.gamma_caution_level,
            "expiry_triggers": list(expiry_eval.triggers),
            "trigger_type": expiry_eval.trigger_type,
            "gamma_proxy": expiry_eval.gamma_proxy,
            "gates": {
                "max_vix": gates_cfg.get("max_vix"),
                "min_vix": gates_cfg.get("min_vix"),
                "block_expiry_day_entries": bool(
                    gates_cfg.get("block_expiry_day_entries", False)
                ),
                "expiry_day_entry_cutoff_hour": expiry_eval.expiry_entry_cutoff_hour,
                "enable_gamma_proxy": bool(gates_cfg.get("enable_gamma_proxy", False)),
                "gamma_proxy_hard_threshold": gates_cfg.get("gamma_proxy_hard_threshold"),
            },
        },
        "index_tickers": index_tickers,
    }


def build_iron_condor_proposal(
    kite,
    config: Optional[Dict[str, Any]] = None,
    *,
    market_context: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Build a live iron condor proposal with chain expiry + spot."""
    from .instruments_manager import instruments_manager
    from .options_chain import options_chain_manager

    cfg = config or get_options_config()
    underlying = cfg.get("underlying", "NIFTY").upper()
    ic_cfg = cfg.get("iron_condor") or {}

    if not instruments_manager.kite and kite:
        instruments_manager.bind(kite)
    options_chain_manager.bind(kite or instruments_manager.kite)

    spot = instruments_manager.fetch_index_spot_ltp(underlying)
    if not spot or spot <= 0:
        return None, f"No spot price for {underlying}"

    expiry_dt = options_chain_manager.resolve_expiry(underlying)
    if not expiry_dt:
        return None, f"No option expiry for {underlying}"

    wing = ic_cfg.get("wing_width")
    body = ic_cfg.get("body_width")
    strikes = default_strikes_from_spot(
        spot,
        underlying,
        wing_width=float(wing) if wing is not None else None,
        body_width=float(body) if body is not None else None,
    )
    strikes = options_chain_manager.resolve_strikes_from_chain(
        strikes,
        underlying,
        expiry_dt,
    )

    tte = _time_to_expiry_years(expiry_dt)
    active_kite = kite or instruments_manager.kite

    from .options_iv import enrich_proposal_with_live_quotes, resolve_blended_iv

    iv, iv_meta = resolve_blended_iv(
        active_kite,
        underlying=underlying,
        expiry=expiry_dt,
        spot=spot,
        time_to_expiry_years=tte,
        config=cfg,
        market_context=market_context,
    )

    proposal = propose_iron_condor(
        spot=spot,
        iv=iv,
        strikes=strikes,
        underlying=underlying,
        expiry=expiry_dt,
        time_to_expiry_years=tte,
        research_only=False,
    )
    proposal["iv_source"] = iv_meta.get("source")
    proposal["iv_details"] = iv_meta
    proposal["time_to_expiry_years"] = tte

    if active_kite:
        proposal, enrich_err = enrich_proposal_with_live_quotes(
            active_kite,
            proposal,
            instruments_mgr=instruments_manager,
        )
        if enrich_err:
            logger.warning("Live quote enrichment skipped: %s", enrich_err)

    return proposal, None


def evaluate_structure_exit(
    structure,
    config: Dict[str, Any],
    *,
    current_mtm: Optional[float] = None,
) -> Optional[str]:
    """
    Return close reason if profit target or loss stop hit.
    MTM positive = profit for credit structures.
    """
    ic_cfg = config.get("iron_condor") or {}
    profit_pct = float(ic_cfg.get("exit_profit_pct", 0.50))
    loss_pct = float(ic_cfg.get("exit_loss_pct", 1.0))

    entry_credit = float(structure.entry_credit or 0.0)
    max_loss = float(structure.max_loss or 0.0)
    if entry_credit <= 0:
        return None

    if current_mtm is None:
        return None

    if current_mtm >= entry_credit * profit_pct:
        return f"profit_target ({profit_pct:.0%} of credit)"
    if current_mtm <= -max_loss * loss_pct:
        return f"loss_stop ({loss_pct:.0%} of max_loss)"
    return None


def estimate_structure_mtm(structure, kite=None, ws_feed=None) -> Optional[float]:
    """Rough MTM: entry credit minus current cost to close."""
    legs = structure.legs or []
    if not legs:
        return None

    close_cost = 0.0
    for leg in legs:
        symbol = leg.get("tradingsymbol")
        qty = int(leg.get("quantity") or 0)
        if not symbol or qty <= 0:
            continue
        ltp = _fetch_leg_ltp(leg, kite=kite, ws_feed=ws_feed)
        if ltp is None:
            return None
        open_side = leg.get("transaction_type", "").upper()
        # Cost to close: reverse the open side
        if open_side == "SELL":
            close_cost += ltp * qty
        else:
            close_cost -= ltp * qty

    # For credit structure: MTM ≈ entry_credit - cost_to_close
    return float(structure.entry_credit) - close_cost


def _fetch_leg_ltp(leg: Dict[str, Any], kite=None, ws_feed=None) -> Optional[float]:
    token = leg.get("instrument_token")
    if ws_feed and token:
        price, _age = ws_feed.get_last_price_with_age(int(token))
        if price and price > 0:
            return float(price)

    symbol = leg.get("tradingsymbol")
    exchange = leg.get("exchange") or "NFO"
    if kite and symbol:
        try:
            from .instruments_manager import instruments_manager, ltp_key

            key = ltp_key(symbol, exchange)
            prices = instruments_manager.fetch_ltp_batch([key])
            last = float(prices.get(key) or 0)
            return last if last > 0 else None
        except Exception:
            pass
    return float(leg.get("premium") or 0) or None


def _finish_options_cycle(result: Dict[str, Any]) -> Dict[str, Any]:
    record_options_cycle_result(result)
    from .trade_ledger import trade_ledger

    cfg = get_options_config()
    underlying = str(cfg.get("underlying", "NIFTY")).upper()

    if result.get("skipped"):
        payload: Dict[str, Any] = {
            "reason": result.get("reason"),
            "details": result.get("details"),
            "underlying": underlying,
        }
        for key in (
            "trigger_type",
            "gamma_caution_level",
            "expiry_triggers",
            "is_expiry_day",
            "expiry_caution",
            "gamma_proxy",
        ):
            if result.get(key) is not None:
                payload[key] = result.get(key)
        if result.get("expiry_audit"):
            payload.update(result["expiry_audit"])
        trade_ledger.record("options.cycle.skip", payload)
    elif result.get("action") in ("open", "close") and not result.get("success", True):
        fail_payload: Dict[str, Any] = {
            "action": result.get("action"),
            "message": result.get("message"),
            "underlying": underlying,
            "stage": result.get("stage"),
            "exit_reason": result.get("exit_reason"),
        }
        leg_results = result.get("leg_results") or []
        failed_legs = [
            lr.get("tradingsymbol")
            for lr in leg_results
            if isinstance(lr, dict) and not lr.get("success")
        ]
        if failed_legs:
            fail_payload["failed_legs"] = failed_legs
        trade_ledger.record("options.cycle.fail", fail_payload)
    return result


def run_options_cycle(
    kite,
    *,
    force_dry_run: bool = True,
    ws_feed=None,
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    One options strategy cycle: evaluate exits, maybe open new iron condor.
    """
    if not options_trading_enabled():
        return _finish_options_cycle({"skipped": True, "reason": "options_trading_disabled"})

    cfg = get_options_config()
    options_position_store.maybe_reset_daily()

    # --- Exits first ---
    for struct in options_position_store.list_open():
        mtm = estimate_structure_mtm(struct, kite=kite, ws_feed=ws_feed)
        exit_reason = evaluate_structure_exit(struct, cfg, current_mtm=mtm)
        if exit_reason:
            close_result = options_execution_engine.close_structure(
                kite,
                struct.structure_id,
                reason=exit_reason,
                force_dry_run=force_dry_run,
                product=cfg.get("product", "NRML"),
            )
            return _finish_options_cycle(
                {"action": "close", "exit_reason": exit_reason, **close_result}
            )

    # --- Entry gates ---
    max_per_day = int(cfg.get("max_structures_per_day", 1))
    if options_position_store.structures_today >= max_per_day:
        return _finish_options_cycle(
            {"skipped": True, "reason": "max_structures_per_day_reached"}
        )

    if options_position_store.list_open():
        return _finish_options_cycle({"skipped": True, "reason": "open_structure_exists"})

    underlying = cfg.get("underlying", "NIFTY").upper()
    allowed = cfg.get("allowed_structures") or ["iron_condor"]
    if "iron_condor" not in allowed:
        return _finish_options_cycle({"skipped": True, "reason": "iron_condor_not_allowed"})

    from .expiry_risk import evaluate_expiry_gates

    expiry_eval = evaluate_expiry_gates(
        cfg,
        underlying=underlying,
        market_context=market_context,
    )
    regime_ok, regime_reasons = check_regime_gates(
        cfg,
        underlying=underlying,
        market_context=market_context,
        expiry_eval=expiry_eval,
    )
    if not regime_ok:
        skip_payload: Dict[str, Any] = {
            "skipped": True,
            "reason": "regime_gate",
            "details": regime_reasons,
            **expiry_eval.to_audit_dict(),
            "expiry_audit": expiry_eval.to_audit_dict(),
        }
        if expiry_eval.blocks_new_entries:
            skip_payload["trigger_type"] = expiry_eval.trigger_type
        elif any("VIX" in r for r in regime_reasons):
            skip_payload["trigger_type"] = "vix_block"
        return _finish_options_cycle(skip_payload)

    proposal, build_err = build_iron_condor_proposal(kite, cfg, market_context=market_context)
    if build_err or not proposal:
        return _finish_options_cycle(
            {"skipped": True, "reason": build_err or "proposal_build_failed"}
        )

    from .risk_gatekeeper import risk_gatekeeper

    proposal["_max_premium_at_risk"] = float(cfg.get("max_premium_at_risk", 50_000))
    proposal["_max_legs"] = int(cfg.get("max_legs", 4))

    validation = options_execution_engine.validate_proposal(
        proposal,
        capital=risk_gatekeeper.capital,
        max_margin_pct=float(cfg.get("max_margin_pct_of_capital", 0.15)),
        max_structure_loss=float(cfg.get("max_structure_loss", 100_000)),
        kite=kite,
        product=cfg.get("product", "NRML"),
        dry_run_fallback_margin=force_dry_run,
    )
    if not validation.get("approved"):
        return _finish_options_cycle(
            {"skipped": True, "reason": "validation_failed", "validation": validation}
        )

    if expiry_eval.gamma_caution_level > 0:
        proposal["gamma_caution_level"] = expiry_eval.gamma_caution_level
        proposal["expiry_triggers"] = list(expiry_eval.triggers)
        proposal["expiry_caution"] = expiry_eval.expiry_caution
        if expiry_eval.gamma_proxy:
            proposal["gamma_proxy"] = expiry_eval.gamma_proxy

    exec_result = options_execution_engine.execute_structure(
        kite,
        proposal,
        force_dry_run=force_dry_run,
        product=cfg.get("product", "NRML"),
    )
    open_result: Dict[str, Any] = {
        "action": "open",
        "proposal_summary": proposal.get("message"),
        **exec_result,
    }
    if expiry_eval.gamma_caution_level > 0:
        open_result.update(expiry_eval.to_audit_dict())
    return _finish_options_cycle(open_result)