"""
Run walk-forward optimization across NIFTY, BANKNIFTY, and SENSEX with shared reporting.

Uses cached parquet from data/historical_cache/ when available (NFO for NIFTY/BANKNIFTY,
BFO for SENSEX). Writes a consolidated JSON report to data/wfo_runs/.

Usage:
    python scripts/run_multi_index_wfo.py
    python scripts/run_multi_index_wfo.py --days 180 --indices NIFTY,BANKNIFTY
    python scripts/run_multi_index_wfo.py --cache-only
    python scripts/algo_lab_ops.py wfo-run --days 180
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from app.branding import PROJECT_NAME
from app.instruments_manager import instruments_manager
from app.kite_auth import validate_access_token
from backtesting.costs import CostConfig, TransactionCostModel
from backtesting.data_loader import (
    best_cache_date_window_for_underlying,
    fetch_real_index_futures_data,
    list_available_cached_datasets,
)
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.promotion_gates import PromotionResult, write_candidate
from backtesting.walk_forward_runner import run_walk_forward

DEFAULT_INDICES: Tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")
WFO_RUNS_DIR = ROOT / "data" / "wfo_runs"

# Production-safe grid — research_mode explicitly excluded
PARAM_GRID = {
    "risk_per_trade_pct": [0.003, 0.0035, 0.004],
    "breakout_atr_mult": [0.50, 0.60, 0.70],
    "profit_target_atr_mult": [1.9, 2.1],
    "stop_loss_atr_mult": [1.0, 1.15],
    "min_prev_candle_range_atr": [0.22, 0.32, 0.42],
    "max_trades_per_day": [2, 3],
    "use_trend_filter": [False, True],
}


def parse_indices(raw: Optional[str]) -> List[str]:
    if not raw:
        return list(DEFAULT_INDICES)
    indices = [part.strip().upper() for part in raw.split(",") if part.strip()]
    unknown = [idx for idx in indices if idx not in DEFAULT_INDICES]
    if unknown:
        raise ValueError(f"Unsupported indices: {', '.join(unknown)}")
    return indices


def index_exchange(underlying: str) -> str:
    return instruments_manager.get_exchange(underlying)


def index_lot_size(underlying: str) -> int:
    return instruments_manager.get_lot_size(underlying)


def _build_cost_model(underlying: str) -> TransactionCostModel:
    lot = index_lot_size(underlying)
    return TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0 if underlying == "NIFTY" else 6.0,
        lot_size=lot,
    ))


def _load_data(
    kite: KiteConnect,
    underlying: str,
    days: int,
    cache_only: bool,
) -> tuple[Any, Dict[str, Any]]:
    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)
    cache_window = None
    if cache_only:
        cache_window = best_cache_date_window_for_underlying(underlying)
        if cache_window:
            from_date, to_date = cache_window
    df = fetch_real_index_futures_data(
        kite=kite,
        from_date=from_date,
        to_date=to_date,
        underlying=underlying,
        interval="5minute",
        use_cache=True,
        cache_only=cache_only,
        min_cache_rows=400,
    )
    meta = {
        "rows": len(df),
        "from": str(df.index.min()),
        "to": str(df.index.max()),
        "cache_only": cache_only,
        "exchange": index_exchange(underlying),
        "lot_size": index_lot_size(underlying),
        "requested_days": days,
        "cache_window_used": cache_window is not None,
    }
    return df, meta


def _fold_count_for_bars(n_bars: int) -> int:
    if n_bars >= 3500:
        return 4
    if n_bars >= 2000:
        return 3
    return 2


def _index_entry_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    promo = summary.get("promotion") or {}
    passed = bool(promo.get("passed"))
    return {
        "underlying": summary.get("underlying"),
        "exchange": (summary.get("data_meta") or {}).get("exchange") or index_exchange(
            str(summary.get("underlying") or "NIFTY")
        ),
        "lot_size": (summary.get("data_meta") or {}).get("lot_size"),
        "passed": passed,
        "status": "PASSED" if passed else "REJECTED",
        "error": summary.get("error"),
        "data_meta": summary.get("data_meta") or {},
        "avg_return": summary.get("avg_return"),
        "avg_pf": summary.get("avg_pf"),
        "total_folds_run": summary.get("total_folds_run", 0),
        "total_trades": summary.get("total_trades", 0),
        "fold_pass_count": promo.get("fold_pass_count", 0),
        "promotion_reasons": promo.get("reasons") or [],
        "promotion": promo,
    }


def build_multi_index_report(
    *,
    indices: Sequence[str],
    results: Dict[str, Dict[str, Any]],
    config: Dict[str, Any],
    started_at: float,
    finished_at: Optional[float] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    finished = finished_at or time.time()
    per_index = {idx: _index_entry_from_summary(results.get(idx) or {"underlying": idx}) for idx in indices}
    passed_indices = [idx for idx in indices if per_index[idx].get("passed")]
    failed_indices = [idx for idx in indices if idx not in passed_indices and not per_index[idx].get("error")]
    errored_indices = [idx for idx in indices if per_index[idx].get("error")]

    return {
        "run_id": run_id or f"multi_index_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "started_at": started_at,
        "finished_at": finished,
        "elapsed_seconds": round(finished - started_at, 1),
        "config": config,
        "indices": per_index,
        "summary": {
            "any_passed": bool(passed_indices),
            "all_passed": len(passed_indices) == len(indices),
            "passed_indices": passed_indices,
            "failed_indices": failed_indices,
            "errored_indices": errored_indices,
        },
    }


def write_multi_index_report(
    report: Dict[str, Any],
    runs_dir: Optional[Path] = None,
) -> Path:
    runs_dir = runs_dir or WFO_RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report['run_id']}.json"
    path = runs_dir / filename
    try:
        report["report_path"] = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        report["report_path"] = str(path).replace("\\", "/")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    return path


def find_latest_multi_index_report(runs_dir: Optional[Path] = None) -> Optional[Path]:
    runs_dir = runs_dir or WFO_RUNS_DIR
    if not runs_dir.exists():
        return None
    candidates = sorted(
        runs_dir.glob("multi_index_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_multi_index_report(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    target = path or find_latest_multi_index_report()
    if not target or not target.exists():
        return None
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["report_file"] = str(target)
        return payload
    except Exception:
        return None


def _run_index(
    kite: KiteConnect,
    underlying: str,
    days: int,
    cache_only: bool,
    cost_multiplier: float,
) -> Dict[str, Any]:
    lot = index_lot_size(underlying)
    exchange = index_exchange(underlying)
    grid = {**PARAM_GRID, "lot_size": [lot], "research_mode": [False]}

    try:
        data, data_meta = _load_data(kite, underlying, days, cache_only)
    except Exception as exc:
        result = PromotionResult(
            passed=False,
            underlying=underlying,
            best_params={},
            reasons=[f"data_unavailable: {exc}"],
            summary={"data_meta": {"error": str(exc), "exchange": exchange, "lot_size": lot}},
        )
        write_candidate(result)
        return {
            "underlying": underlying,
            "error": str(exc),
            "promotion": result.to_dict(),
            "data_meta": {"error": str(exc), "exchange": exchange, "lot_size": lot},
        }

    n_folds = _fold_count_for_bars(len(data))
    cost_model = _build_cost_model(underlying)

    summary = run_walk_forward(
        strategy_class=PreviousCandleBacktestStrategy,
        data=data,
        param_grid=grid,
        n_folds=n_folds,
        test_fraction=0.42,
        embargo_bars=78,
        cost_model=cost_model,
        cost_multiplier=cost_multiplier,
        min_trades_for_validity=3,
        wfo_mode="rolling_purged",
        objective="calmar",
        underlying=underlying,
        run_promotion_gates=True,
    )
    summary["data_meta"] = data_meta
    return summary


def print_gate_report(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print("MULTI-INDEX WFO — PROMOTION GATE REPORT")
    print("=" * 72)
    for idx, entry in (report.get("indices") or {}).items():
        if entry.get("error"):
            print(f"\n{idx}: DATA ERROR — {entry['error']}")
            continue
        status = entry.get("status", "REJECTED")
        print(f"\n{idx}: {status}")
        meta = entry.get("data_meta") or {}
        print(
            f"  Exchange: {entry.get('exchange') or meta.get('exchange')} | "
            f"Lot: {entry.get('lot_size') or meta.get('lot_size')}"
        )
        print(f"  Data: {meta.get('rows', '?')} bars | {meta.get('from', '?')} → {meta.get('to', '?')}")
        print(
            f"  Folds run: {entry.get('total_folds_run', 0)} | "
            f"Avg OOS return: {float(entry.get('avg_return') or 0):.2f}% | "
            f"Avg PF: {float(entry.get('avg_pf') or 0):.2f}"
        )
        print(f"  Folds passing gates: {entry.get('fold_pass_count', 0)}")
        promo = entry.get("promotion") or {}
        for fr in (promo.get("summary") or {}).get("fold_reports") or []:
            reasons = ", ".join(fr.get("reasons") or []) or "ok"
            print(f"    Fold {fr.get('fold')}: {'PASS' if fr.get('passed') else 'FAIL'} — {reasons}")
        reasons = entry.get("promotion_reasons") or promo.get("reasons") or []
        if reasons:
            print(f"  Gate reasons: {'; '.join(reasons)}")


def run_multi_index_wfo(
    *,
    indices: Sequence[str],
    days: int = 180,
    cache_only: bool = False,
    cost_multiplier: float = 1.0,
    kite: Optional[KiteConnect] = None,
    write_report: bool = True,
) -> Dict[str, Any]:
    started = time.time()
    config = {
        "days": days,
        "indices": list(indices),
        "cache_only": cache_only,
        "cost_multiplier": cost_multiplier,
    }

    if kite is None:
        kite = KiteConnect(api_key=os.getenv("KITE_API_KEY", ""))
        kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN", ""))
        instruments_manager.bind(kite)

    all_results: Dict[str, Dict[str, Any]] = {}
    for underlying in indices:
        print(f"\n>>> Running WFO for {underlying} ({index_exchange(underlying)}, lot={index_lot_size(underlying)})...")
        index_started = time.time()
        all_results[underlying] = _run_index(
            kite=kite,
            underlying=underlying,
            days=days,
            cache_only=cache_only,
            cost_multiplier=cost_multiplier,
        )
        promo = all_results[underlying].get("promotion") or {}
        print(
            f"<<< {underlying} done in {time.time() - index_started:.0f}s — "
            f"{'PASSED' if promo.get('passed') else 'REJECTED'}"
        )

    report = build_multi_index_report(
        indices=indices,
        results=all_results,
        config=config,
        started_at=started,
    )
    if write_report:
        path = write_multi_index_report(report)
        print(f"\n[REPORT] Wrote {path}")
    print_gate_report(report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run multi-index walk-forward optimization")
    parser.add_argument("--days", type=int, default=180, help="Days of history to request")
    parser.add_argument(
        "--indices",
        type=str,
        default=",".join(DEFAULT_INDICES),
        help="Comma-separated indices (default: NIFTY,BANKNIFTY,SENSEX)",
    )
    parser.add_argument("--cache-only", action="store_true", help="Never call Kite API for data")
    parser.add_argument(
        "--cost-multiplier",
        type=float,
        default=1.0,
        help="Cost stress multiplier (>=2.0 required for promotion pass)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    load_dotenv()
    try:
        indices = parse_indices(args.indices)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2

    token_ok, _, token_msg = validate_access_token()
    cache_only = args.cache_only
    if not token_ok and not cache_only:
        print(f"[WARN] Kite token invalid ({token_msg}). Using --cache-only for available indices.")
        cache_only = True

    kite = KiteConnect(api_key=os.getenv("KITE_API_KEY", ""))
    kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN", ""))
    instruments_manager.bind(kite)

    print("=" * 72)
    print(f"{PROJECT_NAME} — Multi-Index WFO")
    print(f"Indices: {', '.join(indices)}")
    print(f"Days: {args.days} | Mode: {'cache-only' if cache_only else 'cache-first + Kite fallback'}")
    print(f"Cost multiplier: {args.cost_multiplier}")
    print("=" * 72)

    report = run_multi_index_wfo(
        indices=indices,
        days=args.days,
        cache_only=cache_only,
        cost_multiplier=args.cost_multiplier,
        kite=kite,
    )

    summary = report.get("summary") or {}
    print("\n" + "=" * 72)
    print(
        f"Passed: {summary.get('passed_indices') or 'none'} | "
        f"Failed: {summary.get('failed_indices') or 'none'} | "
        f"Errors: {summary.get('errored_indices') or 'none'}"
    )
    print("Status: python scripts/algo_lab_ops.py wfo-status")
    print("=" * 72)
    return 0 if summary.get("all_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())