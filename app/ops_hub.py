"""
Unified ops hub — testable functions backing scripts/algo_lab_ops.py.

Aggregates system health, compliance, data quality, and promotion status
without requiring a running trading engine or live Kite session.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.branding import DEFAULT_ALGO_ID
from app.db.connection import get_database_url, is_db_enabled, ping_database
from app.order_tags import DEFAULT_ALGO_TAG, resolve_order_tag
from app.startup_checks import run_startup_checks

ROOT = Path(__file__).resolve().parents[1]
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
OPS_TAG = "[OPS]"


def _bool_env(name: str, *, default_true: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default_true
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _check_item(
    item_id: str,
    passed: bool,
    detail: str,
    *,
    category: str = "general",
    severity: str = "info",
    manual: bool = False,
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "category": category,
        "passed": bool(passed),
        "detail": detail,
        "severity": severity,
        "manual": manual,
    }


def _index_prefix(symbol: str) -> Optional[str]:
    sym = (symbol or "").upper()
    if sym.startswith("BANKNIFTY") or sym.startswith("BNF"):
        return "BANKNIFTY"
    if sym.startswith("SENSEX"):
        return "SENSEX"
    if sym.startswith("NIFTY"):
        return "NIFTY"
    return None


def _module_attr_exists(module_path: str, name: str) -> bool:
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, name, None) is not None
    except Exception:
        return False


def _module_callable_exists(module_path: str, name: str) -> bool:
    try:
        mod = importlib.import_module(module_path)
        obj = getattr(mod, name, None)
        return obj is not None and (callable(obj) or hasattr(obj, "__class__"))
    except Exception:
        return False


def _class_method_exists(module_path: str, class_name: str, method_name: str) -> bool:
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name, None)
        return cls is not None and callable(getattr(cls, method_name, None))
    except Exception:
        return False


def _source_uses(module_path: str, needle: str) -> bool:
    try:
        mod = importlib.import_module(module_path)
        src_path = getattr(mod, "__file__", None)
        if not src_path:
            return False
        text = Path(src_path).read_text(encoding="utf-8")
        return needle in text
    except Exception:
        return False


def _check_token(*, validate: bool = True) -> Dict[str, Any]:
    force_dry = _bool_env("FORCE_DRY_RUN", default_true=True)
    if not validate:
        return {
            "valid": None,
            "message": "skipped",
            "severity": "info",
            "force_dry_run": force_dry,
        }
    try:
        from app.kite_auth import validate_access_token

        valid, profile, msg = validate_access_token()
        return {
            "valid": bool(valid),
            "message": msg or "ok",
            "profile": (profile or {}).get("user_name") if valid else None,
            "severity": "warning" if force_dry and not valid else ("blocker" if not valid else "info"),
            "force_dry_run": force_dry,
        }
    except Exception as exc:
        return {
            "valid": False,
            "message": str(exc),
            "severity": "warning" if force_dry else "blocker",
            "force_dry_run": force_dry,
        }


def _check_websocket() -> Dict[str, Any]:
    enabled = _bool_env("ENABLE_WEBSOCKET", default_true=True)
    engine_running = False
    connected = False
    detail = "engine not running"

    try:
        from app.state_machine import state_machine, SystemState

        state = state_machine.get_state()
        engine_running = state != SystemState.BOOTING
    except Exception:
        pass

    if engine_running:
        try:
            from app import live_snapshots

            snaps = live_snapshots.get_all_snapshots() or {}
            engine_running = bool(snaps) or engine_running
        except Exception:
            pass

    try:
        from app.multi_symbol_risk import multi_risk_manager

        if multi_risk_manager is not None:
            for strat_attr in ("ws_feed",):
                feed = getattr(multi_risk_manager, strat_attr, None)
                if feed is not None and hasattr(feed, "is_connected"):
                    connected = bool(feed.is_connected())
                    detail = "connected" if connected else "disconnected"
                    break
    except Exception:
        pass

    if not engine_running:
        if not enabled:
            detail = "disabled via ENABLE_WEBSOCKET=false"
        else:
            detail = "engine not running (start run.py for live WS)"

    return {
        "enabled": enabled,
        "engine_running": engine_running,
        "connected": connected if engine_running else None,
        "detail": detail,
        "severity": "info" if not engine_running else ("warning" if enabled and not connected else "info"),
    }


def _check_database() -> Dict[str, Any]:
    url = get_database_url()
    backend = os.getenv("PERSISTENCE_BACKEND", "jsonl").strip().lower() or "jsonl"
    if not url:
        return {
            "configured": False,
            "backend": backend,
            "reachable": None,
            "enabled_for_trading": False,
            "detail": "DATABASE_URL not set (JSONL default)",
            "passed": True,
        }
    reachable = ping_database()
    required = is_db_enabled()
    return {
        "configured": True,
        "backend": backend,
        "reachable": reachable,
        "enabled_for_trading": required,
        "detail": "postgres reachable" if reachable else "postgres unreachable",
        "passed": reachable if required else True,
    }


def _promotion_snapshot() -> Dict[str, Any]:
    from app.promoted_params import preview_promoted_overlay
    from backtesting.promotion_gates import load_candidates

    candidates = {c.get("underlying"): c for c in load_candidates()}
    per_index: Dict[str, Any] = {}
    any_passed = False
    for idx in INDICES:
        cand = candidates.get(idx)
        preview = preview_promoted_overlay(idx)
        passed = bool(cand and cand.get("passed"))
        any_passed = any_passed or passed
        per_index[idx] = {
            "has_record": cand is not None,
            "passed": passed,
            "status": (cand or {}).get("status", "no_record"),
            "fold_pass_count": (cand or {}).get("fold_pass_count", 0),
            "overlay_eligible": bool(preview.get("eligible")),
            "overlay_reason": preview.get("reason") or preview.get("proposed"),
        }
    return {
        "per_index": per_index,
        "any_passed": any_passed,
        "all_passed": all(per_index[i]["passed"] for i in INDICES),
    }


def build_status_report(*, validate_token: bool = True) -> Dict[str, Any]:
    """System health: token, market, WS, algo_id, db, promotion gates."""
    from app.market_calendar import get_market_status
    from app.intelligence_loop import intelligence_loop
    from app.state_machine import state_machine

    token = _check_token(validate=validate_token)
    market = get_market_status()
    websocket = _check_websocket()
    database = _check_database()
    promotion = _promotion_snapshot()
    startup = run_startup_checks()
    deploy = intelligence_loop.run_safe_deploy_checklist()

    algo_id = resolve_order_tag()
    force_dry = token.get("force_dry_run", True)

    checks: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = []

    token_valid = token.get("valid")
    checks.append(_check_item(
        "token",
        True if token_valid is None else bool(token_valid),
        token.get("message", "?"),
        category="connectivity",
        severity=token.get("severity", "info"),
    ))
    if token_valid is False:
        msg = f"Kite token invalid ({token.get('message')})"
        if force_dry:
            warnings.append(msg)
        else:
            blockers.append(msg)

    checks.append(_check_item(
        "market_session",
        bool(market.get("is_market_open")),
        market.get("session_status", "?"),
        category="market",
        severity="info",
    ))
    if market.get("is_expiry_day"):
        warnings.append("Expiry day — elevated risk")

    ws_ok = (not websocket.get("enabled")) or websocket.get("connected") or not websocket.get("engine_running")
    checks.append(_check_item(
        "websocket",
        ws_ok,
        websocket.get("detail", "?"),
        category="connectivity",
        severity=websocket.get("severity", "info"),
    ))

    algo_ok = bool(algo_id) and (
        algo_id.replace("-", "").replace("_", "").isalnum()
    )
    checks.append(_check_item(
        "algo_id",
        algo_ok,
        f"{algo_id} (default={DEFAULT_ALGO_TAG})",
        category="compliance",
        severity="warning" if algo_id == DEFAULT_ALGO_TAG else "info",
    ))
    if algo_id == DEFAULT_ALGO_TAG:
        warnings.append(f"ALGO_ID unset — using default {DEFAULT_ALGO_ID}")

    checks.append(_check_item(
        "database",
        bool(database.get("passed")),
        database.get("detail", "?"),
        category="infrastructure",
        severity="blocker" if database.get("enabled_for_trading") and not database.get("reachable") else "info",
    ))
    if database.get("enabled_for_trading") and not database.get("reachable"):
        blockers.append("Postgres configured but unreachable")

    for idx, info in promotion["per_index"].items():
        checks.append(_check_item(
            f"promotion_{idx}",
            info["passed"],
            info["status"],
            category="promotion",
            severity="warning",
        ))
        if not info["passed"]:
            warnings.append(f"{idx}: promotion not passed")

    state = state_machine.get_state().value
    checks.append(_check_item(
        "state_machine",
        deploy.get("ready", False) or state == "BOOTING",
        f"state={state} deploy_ready={deploy.get('ready')}",
        category="runtime",
        severity="blocker" if state in {
            "EMERGENCY_HALT", "BROKER_DISCONNECTED", "RECONCILIATION_FAILED",
            "TRADING_DISABLED", "CIRCUIT_BREAKER_TRIGGERED",
        } else "info",
    ))

    blockers.extend(deploy.get("blockers") or [])
    warnings.extend(deploy.get("warnings") or [])

    healthy = len(blockers) == 0
    return {
        "healthy": healthy,
        "mode": deploy.get("mode", "paper" if force_dry else "live"),
        "algo_id": algo_id,
        "token": token,
        "market": market,
        "websocket": websocket,
        "database": database,
        "promotion": promotion,
        "startup": startup,
        "deploy": deploy,
        "state": state,
        "checks": checks,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def run_compliance_checks() -> Dict[str, Any]:
    """Run code-checkable items from COMPLIANCE.md."""
    checks: List[Dict[str, Any]] = []

    algo_id = resolve_order_tag()
    checks.append(_check_item(
        "algo_id_resolved",
        bool(algo_id) and len(algo_id) <= 20,
        f"tag={algo_id}",
        category="1_algo_tagging",
    ))
    checks.append(_check_item(
        "order_tag_on_place_order",
        _source_uses("app.risk_gatekeeper", "resolve_order_tag"),
        "risk_gatekeeper.place_guarded_order uses resolve_order_tag",
        category="1_algo_tagging",
    ))
    checks.append(_check_item(
        "protection_tag_on_slm",
        _source_uses("app.exchange_protection", "resolve_protection_tag"),
        "exchange_protection uses resolve_protection_tag",
        category="1_algo_tagging",
    ))
    checks.append(_check_item(
        "audit_tag_on_submitted",
        _source_uses("app.risk_gatekeeper", '"tag": resolved_tag'),
        "order.submitted audit events include tag",
        category="1_algo_tagging",
    ))

    startup = run_startup_checks()
    checks.append(_check_item(
        "startup_ip_logging",
        startup.get("outbound_ip") is not None or bool(os.getenv("STATIC_OUTBOUND_IP", "").strip()),
        f"ip={startup.get('outbound_ip') or 'not resolved'}",
        category="2_static_ip",
        severity="warning" if not startup.get("outbound_ip") else "info",
    ))

    checks.append(_check_item(
        "order_rate_limiter",
        _module_attr_exists("app.kite_rate_limit", "order_limiter"),
        "order_limiter @ kite_rate_limit.py",
        category="3_rate_limiting",
    ))
    checks.append(_check_item(
        "order_burst_tracker",
        _module_attr_exists("app.kite_rate_limit", "order_burst_tracker"),
        "rolling 10s burst guard",
        category="3_rate_limiting",
    ))

    checks.append(_check_item(
        "audit_logger_jsonl",
        Path("data/audit_events.json").parent.exists() or _module_attr_exists("app.audit_logger", "audit_logger"),
        "audit_logger → data/audit_events.json",
        category="4_audit_logs",
    ))
    checks.append(_check_item(
        "trade_ledger_jsonl",
        _module_attr_exists("app.trade_ledger", "trade_ledger"),
        "trade_ledger → data/trade_ledger.jsonl",
        category="4_audit_logs",
    ))
    checks.append(_check_item(
        "postgres_migrations_scaffold",
        (ROOT / "migrations").exists() and any((ROOT / "migrations").glob("*.sql")),
        "migrations/*.sql present",
        category="4_audit_logs",
    ))
    checks.append(_check_item(
        "eod_audit_script",
        (ROOT / "scripts" / "eod_data_audit.py").exists(),
        "scripts/eod_data_audit.py",
        category="4_audit_logs",
    ))

    checks.append(_check_item(
        "risk_gatekeeper",
        _module_callable_exists("app.risk_gatekeeper", "RiskGatekeeper"),
        "central RiskGatekeeper",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "state_machine_veto",
        _module_attr_exists("app.state_machine", "state_machine"),
        "TradingStateMachine singleton",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "broker_reconciliation",
        _class_method_exists("app.broker_reconciliation", "BrokerReconciliation", "run_reconciliation"),
        "BrokerReconciliation.run_reconciliation",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "promotion_gates",
        _module_callable_exists("backtesting.promotion_gates", "evaluate_wfo_summary"),
        "walk-forward promotion gates",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "multi_symbol_guarded_orders",
        _source_uses("app.multi_symbol_risk", "place_guarded_order"),
        "multi_symbol_risk.place_guarded_order",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "options_execution_engine",
        _module_attr_exists("app.options_execution_engine", "options_execution_engine"),
        "Multi-leg options routing through RiskGatekeeper",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "options_margins_basket",
        _module_callable_exists("app.kite_margins_basket", "simulate_basket_margin"),
        "Kite basket margin pre-check before options orders",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "options_risk_checker",
        _module_attr_exists("app.options_risk", "options_risk_checker"),
        "Defined-risk options validation (blocks naked short vol)",
        category="5_risk_gates",
    ))
    checks.append(_check_item(
        "options_trading_double_gate",
        _source_uses("app.options_strategy_runner", "OPTIONS_TRADING_ENABLED")
        and _source_uses("app.config_loader", "get_options_config"),
        "OPTIONS_TRADING_ENABLED env + YAML trading_enabled",
        category="5_risk_gates",
    ))

    checks.append(_check_item(
        "force_dry_run_safe_default",
        _bool_env("FORCE_DRY_RUN", default_true=True),
        f"FORCE_DRY_RUN={os.getenv('FORCE_DRY_RUN', 'true')}",
        category="7_kill_switches",
    ))
    live_confirmed = os.getenv("LIVE_TRADING_CONFIRMED", "").lower() in {"1", "true", "yes", "confirmed"}
    live_gate_ok = _bool_env("FORCE_DRY_RUN", default_true=True) or live_confirmed
    checks.append(_check_item(
        "live_trading_confirmed_gate",
        live_gate_ok,
        f"LIVE_TRADING_CONFIRMED={live_confirmed}",
        category="7_kill_switches",
        severity="blocker" if not live_gate_ok else "info",
    ))
    checks.append(_check_item(
        "eod_flatten_module",
        _module_callable_exists("app.eod_flatten", "maybe_run_eod_flatten"),
        "app.eod_flatten.maybe_run_eod_flatten",
        category="7_kill_switches",
    ))
    checks.append(_check_item(
        "emergency_halt_module",
        _module_callable_exists("app.emergency", "execute_emergency_halt"),
        "app.emergency.execute_emergency_halt",
        category="7_kill_switches",
    ))

    checks.append(_check_item(
        "strategy_config_versioned",
        (ROOT / "config" / "strategy_config.yaml").exists(),
        "config/strategy_config.yaml",
        category="8_documentation",
    ))
    checks.append(_check_item(
        "candidates_file_path",
        _source_uses("backtesting.promotion_gates", "strategy_candidates.json"),
        "data/strategy_candidates.json",
        category="8_documentation",
    ))

    manual_items = [
        _check_item("extended_paper_trading", False, "≥4 weeks paper — operator attestation", category="6_testing", manual=True),
        _check_item("reconciliation_audit_clean", False, "zero silent drift — operator attestation", category="6_testing", manual=True),
        _check_item("founder_sign_off", False, "document reviewer + date before live", category="6_testing", manual=True),
    ]
    checks.extend(manual_items)

    automated = [c for c in checks if not c.get("manual")]
    passed = sum(1 for c in automated if c["passed"])
    failed = [c for c in automated if not c["passed"]]
    blockers = [
        c["id"] for c in automated
        if not c["passed"] and c.get("severity") == "blocker"
    ]

    return {
        "passed": len(failed) == 0 and len(blockers) == 0,
        "automated_passed": passed,
        "automated_total": len(automated),
        "failed_ids": [c["id"] for c in failed],
        "blockers": blockers,
        "checks": checks,
        "manual_checks": [c["id"] for c in manual_items],
    }


def _summarize_cache() -> Dict[str, Any]:
    from backtesting.data_loader import list_available_cached_datasets

    datasets = list_available_cached_datasets()
    by_index: Dict[str, Dict[str, Any]] = {
        idx: {"datasets": 0, "total_rows": 0, "latest_to": None, "symbols": []}
        for idx in INDICES
    }
    other = 0

    for ds in datasets:
        if ds.get("error"):
            continue
        prefix = _index_prefix(ds.get("symbol", ""))
        if prefix not in by_index:
            other += 1
            continue
        bucket = by_index[prefix]
        bucket["datasets"] += 1
        rows = ds.get("rows")
        if isinstance(rows, int):
            bucket["total_rows"] += rows
        sym = ds.get("symbol")
        if sym and sym not in bucket["symbols"]:
            bucket["symbols"].append(sym)
        actual_to = ds.get("actual_to")
        if actual_to and (bucket["latest_to"] is None or actual_to > bucket["latest_to"]):
            bucket["latest_to"] = actual_to

    min_rows = 400
    sparse = [idx for idx, info in by_index.items() if info["total_rows"] < min_rows]
    return {
        "dataset_count": len(datasets),
        "by_index": by_index,
        "other_symbols": other,
        "sparse_indices": sparse,
        "healthy": len(sparse) == 0 and len(datasets) > 0,
    }


def run_data_health(
    *,
    audit_days: int = 1,
    audit_runner: Optional[Callable[..., Dict[str, Any]]] = None,
    trade_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Cache coverage plus EOD audit summary."""
    from backtesting.eod_audit import previous_trading_day, run_eod_audit

    cache = _summarize_cache()
    runner = audit_runner or run_eod_audit
    start = trade_date or previous_trading_day()

    audits: List[Dict[str, Any]] = []
    unhealthy_days = 0
    for offset in range(max(1, audit_days)):
        d = start - timedelta(days=offset)
        try:
            report = runner(trade_date=d)
        except Exception as exc:
            report = {"overall": "error", "error": str(exc), "trade_date": d.isoformat()}
        overall = report.get("overall", "unknown")
        if overall not in ("healthy", "skipped"):
            unhealthy_days += 1
        audits.append({
            "trade_date": d.isoformat(),
            "overall": overall,
            "indices": report.get("indices") or {},
            "error": report.get("error"),
        })

    healthy = cache.get("healthy", False) and unhealthy_days == 0
    return {
        "healthy": healthy,
        "cache": cache,
        "eod_audits": audits,
        "unhealthy_audit_days": unhealthy_days,
    }


def _latest_multi_index_report_path() -> Optional[Path]:
    runs_dir = ROOT / "data" / "wfo_runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(
        runs_dir.glob("multi_index_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_multi_index_wfo_status() -> Dict[str, Any]:
    """Read the latest multi_index WFO JSON report when present."""
    path = _latest_multi_index_report_path()
    if not path:
        return {
            "has_report": False,
            "report_path": None,
            "run_id": None,
            "finished_at": None,
            "config": {},
            "source": None,
            "per_index": {},
            "summary": {},
        }

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return {
            "has_report": False,
            "report_path": str(path),
            "error": str(exc),
            "per_index": {},
            "summary": {},
        }

    per_index: Dict[str, Any] = {}
    for idx in INDICES:
        entry = (payload.get("indices") or {}).get(idx) or {}
        per_index[idx] = {
            "has_record": bool(entry),
            "passed": bool(entry.get("passed")),
            "status": entry.get("status", "no_record"),
            "fold_pass_count": entry.get("fold_pass_count", 0),
            "exchange": entry.get("exchange"),
            "lot_size": entry.get("lot_size"),
            "avg_return": entry.get("avg_return"),
            "avg_pf": entry.get("avg_pf"),
            "error": entry.get("error"),
            "data_rows": (entry.get("data_meta") or {}).get("rows"),
            "data_from": (entry.get("data_meta") or {}).get("from"),
            "data_to": (entry.get("data_meta") or {}).get("to"),
            "promotion_reasons": entry.get("promotion_reasons") or [],
        }

    finished_at = payload.get("finished_at")
    finished_iso = None
    if finished_at:
        try:
            finished_iso = datetime.fromtimestamp(float(finished_at)).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            finished_iso = str(finished_at)

    config = payload.get("config") or {}
    return {
        "has_report": True,
        "report_path": str(path),
        "run_id": payload.get("run_id"),
        "finished_at": finished_iso,
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "config": config,
        "source": config.get("source"),
        "per_index": per_index,
        "summary": payload.get("summary") or {},
    }


def build_wfo_status_report() -> Dict[str, Any]:
    """Promotion candidate status per index (read-only)."""
    promotion = _promotion_snapshot()
    multi_index = run_multi_index_wfo_status()
    token = _check_token(validate=True)
    token_invalid = token.get("valid") is False
    cache_suffix = " --cache-only" if token_invalid else ""
    promotion_run_cmd = f"python scripts/run_promotion_wfo.py --cost-multiplier 2.0{cache_suffix}"

    actions: List[str] = []
    if not promotion["any_passed"]:
        actions = [
            "python generate_token.py --auto",
            "python scripts/fetch_promotion_data.py",
            promotion_run_cmd,
        ]
    elif not promotion["all_passed"]:
        actions = [
            promotion_run_cmd,
            "python scripts/run_promotion_wfo.py --apply-overlays  # passed indices only",
        ]
    else:
        actions = ["python scripts/run_promotion_wfo.py --apply-overlays"]

    if not multi_index.get("has_report"):
        actions = [a for a in actions if a != promotion_run_cmd]
        actions.insert(0, promotion_run_cmd)

    indices_view = dict(promotion["per_index"])
    if multi_index.get("has_report"):
        for idx, info in (multi_index.get("per_index") or {}).items():
            merged = dict(indices_view.get(idx) or {})
            merged["latest_wfo_run"] = info
            if info.get("has_record"):
                merged["last_run_status"] = info.get("status")
                merged["last_run_passed"] = info.get("passed")
            indices_view[idx] = merged

    return {
        "indices": indices_view,
        "any_passed": promotion["any_passed"],
        "all_passed": promotion["all_passed"],
        "multi_index_run": multi_index,
        "actions": actions,
    }


def run_preflight(
    *,
    validate_token: bool = True,
    audit_days: int = 1,
    audit_runner: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Morning routine: safe deploy + compliance + data-health combined."""
    status = build_status_report(validate_token=validate_token)
    compliance = run_compliance_checks()
    data_health = run_data_health(audit_days=audit_days, audit_runner=audit_runner)
    wfo = build_wfo_status_report()

    blockers: List[str] = []
    warnings: List[str] = []

    if not status.get("healthy"):
        blockers.extend(status.get("blockers") or [])
    warnings.extend(status.get("warnings") or [])

    if not compliance.get("passed"):
        blockers.append(
            f"compliance failed ({compliance.get('automated_passed')}/"
            f"{compliance.get('automated_total')} automated checks)"
        )
        blockers.extend([f"compliance:{cid}" for cid in compliance.get("failed_ids") or []])

    if not data_health.get("healthy"):
        cache = data_health.get("cache") or {}
        if cache.get("sparse_indices"):
            warnings.append(f"sparse cache: {', '.join(cache['sparse_indices'])}")
        if data_health.get("unhealthy_audit_days"):
            blockers.append(
                f"eod audit unhealthy on {data_health['unhealthy_audit_days']} day(s)"
            )

    ready = len(blockers) == 0
    return {
        "ready": ready,
        "mode": status.get("mode"),
        "status": status,
        "compliance": compliance,
        "data_health": data_health,
        "wfo": wfo,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def run_db_migrate() -> Dict[str, Any]:
    """Apply SQL migrations when DATABASE_URL is configured."""
    url = get_database_url()
    if not url:
        return {"applied": 0, "skipped": True, "detail": "DATABASE_URL not set", "ok": True}

    if not ping_database():
        return {"applied": 0, "skipped": False, "detail": "Cannot connect to Postgres", "ok": False}

    migrations_dir = ROOT / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        return {"applied": 0, "skipped": False, "detail": "No migration files found", "ok": True}

    import psycopg

    applied: List[str] = []
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for path in sql_files:
                cur.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
        conn.commit()

    return {
        "applied": len(applied),
        "files": applied,
        "skipped": False,
        "detail": f"Applied {len(applied)} migration(s)",
        "ok": True,
    }


def build_lunar_today(*, refresh: bool = False) -> Dict[str, Any]:
    """Delegate to lunar calendar build for today."""
    from app.lunar_calendar import build_lunar_context, format_lunar_summary, save_lunar_context

    payload = build_lunar_context(refresh=refresh)
    path = save_lunar_context(payload)
    return {
        "path": str(path),
        "summary": format_lunar_summary(payload),
        "payload": payload,
    }


def _print_check_lines(checks: List[Dict[str, Any]], *, tag: str = OPS_TAG) -> None:
    for check in checks:
        if check.get("manual"):
            status = "MANUAL"
        else:
            status = "PASS" if check.get("passed") else "FAIL"
        cid = check.get("id", "?")
        detail = check.get("detail", "")
        print(f"{tag} [{status}] {cid}: {detail}")


def format_status_report(report: Dict[str, Any]) -> str:
    """Return multi-line status text (also suitable for print)."""
    lines = [
        f"{OPS_TAG} === SYSTEM STATUS ===",
        f"{OPS_TAG} Mode: {report.get('mode')} | Healthy: {report.get('healthy')}",
        f"{OPS_TAG} Algo ID: {report.get('algo_id')} | State: {report.get('state')}",
        f"{OPS_TAG} Token: {('skipped' if (report.get('token') or {}).get('valid') is None else ('valid' if (report.get('token') or {}).get('valid') else 'invalid'))}",
        f"{OPS_TAG} Market: {(report.get('market') or {}).get('session_status')} "
        f"({(report.get('market') or {}).get('ist_time')})",
        f"{OPS_TAG} WebSocket: {(report.get('websocket') or {}).get('detail')}",
        f"{OPS_TAG} Database: {(report.get('database') or {}).get('detail')}",
    ]
    promo = (report.get("promotion") or {}).get("per_index") or {}
    for idx in INDICES:
        info = promo.get(idx, {})
        gate = "PASS" if info.get("passed") else "FAIL"
        lines.append(f"{OPS_TAG} Promotion {idx}: [{gate}] {info.get('status', 'no_record')}")
    if report.get("blockers"):
        lines.append(f"{OPS_TAG} Blockers:")
        for b in report["blockers"]:
            lines.append(f"{OPS_TAG}   ! {b}")
    if report.get("warnings"):
        lines.append(f"{OPS_TAG} Warnings:")
        for w in report["warnings"][:8]:
            lines.append(f"{OPS_TAG}   * {w}")
    return "\n".join(lines)


def format_compliance_report(report: Dict[str, Any]) -> str:
    lines = [
        f"{OPS_TAG} === COMPLIANCE CHECKLIST ===",
        f"{OPS_TAG} Automated: {report.get('automated_passed')}/{report.get('automated_total')} passed",
    ]
    for check in report.get("checks") or []:
        if check.get("manual"):
            status = "MANUAL"
        else:
            status = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"{OPS_TAG} [{status}] {check.get('id')}: {check.get('detail')}")
    if report.get("manual_checks"):
        lines.append(f"{OPS_TAG} Manual attestation required: {', '.join(report['manual_checks'])}")
    return "\n".join(lines)


def format_data_health_report(report: Dict[str, Any]) -> str:
    lines = [
        f"{OPS_TAG} === DATA HEALTH ===",
        f"{OPS_TAG} Healthy: {report.get('healthy')}",
    ]
    cache = report.get("cache") or {}
    lines.append(f"{OPS_TAG} Cache datasets: {cache.get('dataset_count', 0)}")
    for idx in INDICES:
        info = (cache.get("by_index") or {}).get(idx, {})
        lines.append(
            f"{OPS_TAG}   {idx}: {info.get('datasets', 0)} files, "
            f"{info.get('total_rows', 0)} rows, latest={info.get('latest_to')}"
        )
    for audit in report.get("eod_audits") or []:
        lines.append(
            f"{OPS_TAG} EOD {audit.get('trade_date')}: {str(audit.get('overall')).upper()}"
        )
    return "\n".join(lines)


def format_wfo_status_report(report: Dict[str, Any]) -> str:
    lines = [f"{OPS_TAG} === WFO / PROMOTION STATUS ==="]
    multi = report.get("multi_index_run") or {}
    if multi.get("has_report"):
        source = multi.get("source") or (multi.get("config") or {}).get("source")
        run_label = (
            "promotion WFO run"
            if source == "run_promotion_wfo"
            else "multi-index run"
        )
        lines.append(
            f"{OPS_TAG} Latest {run_label}: {multi.get('run_id')} "
            f"({multi.get('finished_at') or 'unknown time'})"
        )
        cfg = multi.get("config") or {}
        if cfg:
            cfg_bits = [
                f"source={source}" if source else None,
                f"days={cfg.get('days')}" if cfg.get("days") is not None else None,
                f"months={cfg.get('months')}" if cfg.get("months") is not None else None,
                f"indices={cfg.get('indices')}" if cfg.get("indices") is not None else None,
                f"cache_only={cfg.get('cache_only')}" if cfg.get("cache_only") is not None else None,
                f"cost_multiplier={cfg.get('cost_multiplier')}" if cfg.get("cost_multiplier") is not None else None,
            ]
            cfg_line = " ".join(bit for bit in cfg_bits if bit)
            if cfg_line:
                lines.append(f"{OPS_TAG}   {cfg_line}")
    for idx in INDICES:
        info = (report.get("indices") or {}).get(idx, {})
        gate = "PASSED" if info.get("passed") else "REJECTED"
        lines.append(
            f"{OPS_TAG} {idx}: {gate} ({info.get('status', 'no_record')}) "
            f"folds={info.get('fold_pass_count', 0)} "
            f"overlay={'eligible' if info.get('overlay_eligible') else 'no'}"
        )
        last_run = info.get("latest_wfo_run") or {}
        if last_run.get("has_record"):
            run_gate = "PASSED" if last_run.get("passed") else "REJECTED"
            exch = last_run.get("exchange") or "?"
            lot = last_run.get("lot_size") or "?"
            lines.append(
                f"{OPS_TAG}   last WFO: {run_gate} | {exch} lot={lot} "
                f"rows={last_run.get('data_rows', '?')}"
            )
    if report.get("actions"):
        lines.append(f"{OPS_TAG} Actions:")
        for act in report["actions"]:
            lines.append(f"{OPS_TAG}   → {act}")
    return "\n".join(lines)


def format_preflight_report(report: Dict[str, Any]) -> str:
    sections = [
        format_status_report(report.get("status") or {}),
        format_compliance_report(report.get("compliance") or {}),
        format_data_health_report(report.get("data_health") or {}),
        format_wfo_status_report(report.get("wfo") or {}),
        f"{OPS_TAG} === PREFLIGHT ===",
        f"{OPS_TAG} Ready: {report.get('ready')} | Mode: {report.get('mode')}",
    ]
    if report.get("blockers"):
        sections.append(f"{OPS_TAG} Blockers:")
        for b in report["blockers"]:
            sections.append(f"{OPS_TAG}   ! {b}")
    return "\n".join(sections)