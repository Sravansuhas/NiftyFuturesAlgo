"""
Run walk-forward optimization + promotion gates for NIFTY, BANKNIFTY, and SENSEX.

Usage:
    python scripts/run_promotion_wfo.py
    python scripts/run_promotion_wfo.py --apply-overlays   # write overlays for passed indices
    python scripts/run_promotion_wfo.py --cache-only       # offline: use historical_cache only

Requires valid Kite token for indices without local cache (BANKNIFTY/SENSEX typically).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from app.kite_auth import validate_access_token
from app.promoted_params import apply_promoted_overlay, preview_promoted_overlay
from backtesting.costs import CostConfig, TransactionCostModel
from backtesting.data_loader import fetch_real_index_futures_data
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.promotion_gates import (
    PromotionGateConfig,
    PromotionResult,
    evaluate_wfo_summary,
    load_candidates,
    write_candidate,
)
from backtesting.walk_forward_runner import run_walk_forward

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}

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


def _build_cost_model(underlying: str) -> TransactionCostModel:
    lot = LOT_SIZES[underlying]
    return TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0 if underlying == "NIFTY" else 6.0,
        lot_size=lot,
    ))


def _best_cache_date_window(underlying: str) -> tuple[datetime, datetime] | None:
    """Pick the widest available cache window for an index (offline promotion)."""
    from backtesting.data_loader import list_available_cached_datasets

    best = None
    best_rows = 0
    prefix = underlying.upper()
    for ds in list_available_cached_datasets():
        sym = (ds.get("symbol") or "").upper()
        if not sym.startswith(prefix):
            continue
        rows = int(ds.get("rows") or 0)
        if rows > best_rows:
            best_rows = rows
            best = ds
    if not best:
        return None
    from_dt = datetime.strptime(best["actual_from"], "%Y-%m-%d")
    to_dt = datetime.strptime(best["actual_to"], "%Y-%m-%d")
    return from_dt, to_dt


def _load_data(
    kite: KiteConnect,
    underlying: str,
    months_back: int,
    cache_only: bool,
) -> tuple:
    to_date = datetime.now()
    from_date = to_date - timedelta(days=30 * months_back)
    if cache_only:
        window = _best_cache_date_window(underlying)
        if window:
            from_date, to_date = window
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
    }
    return df, meta


def _fold_count_for_bars(n_bars: int) -> int:
    if n_bars >= 3500:
        return 4
    if n_bars >= 2000:
        return 3
    return 2


def _run_index(
    kite: KiteConnect,
    underlying: str,
    months_back: int,
    cache_only: bool,
    cost_multiplier: float,
) -> dict:
    lot = LOT_SIZES[underlying]
    grid = {**PARAM_GRID, "lot_size": [lot], "research_mode": [False]}

    try:
        data, data_meta = _load_data(kite, underlying, months_back, cache_only)
    except Exception as exc:
        result = PromotionResult(
            passed=False,
            underlying=underlying,
            best_params={},
            reasons=[f"data_unavailable: {exc}"],
            summary={"data_meta": {"error": str(exc)}},
        )
        write_candidate(result)
        return {"underlying": underlying, "error": str(exc), "promotion": result.to_dict()}

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


def _print_report(results: dict) -> None:
    print("\n" + "=" * 72)
    print("PROMOTION GATE REPORT — ALL INDICES")
    print("=" * 72)
    for underlying in INDICES:
        res = results.get(underlying, {})
        promo = res.get("promotion") or {}
        if res.get("error"):
            print(f"\n{underlying}: DATA ERROR — {res['error']}")
            continue
        passed = promo.get("passed", False)
        status = "PASSED" if passed else "REJECTED"
        print(f"\n{underlying}: {status}")
        meta = res.get("data_meta") or {}
        print(f"  Data: {meta.get('rows', '?')} bars | {meta.get('from', '?')} → {meta.get('to', '?')}")
        print(f"  Folds run: {res.get('total_folds_run', 0)} | Avg OOS return: {res.get('avg_return', 0):.2f}% | Avg PF: {res.get('avg_pf', 0):.2f}")
        print(f"  Folds passing gates: {promo.get('fold_pass_count', 0)}")
        for fr in (promo.get("summary") or {}).get("fold_reports") or []:
            reasons = ", ".join(fr.get("reasons") or []) or "ok"
            print(f"    Fold {fr.get('fold')}: {'PASS' if fr.get('passed') else 'FAIL'} — {reasons}")
        if promo.get("reasons"):
            print(f"  Gate reasons: {'; '.join(promo['reasons'])}")
        if passed and promo.get("best_params"):
            safe = {k: v for k, v in promo["best_params"].items() if k in {
                "breakout_atr_mult", "profit_target_atr_mult", "stop_loss_atr_mult",
                "risk_per_trade_pct", "max_trades_per_day", "cooldown_minutes_after_trade",
            }}
            if safe:
                print(f"  Consensus params: {json.dumps(safe, indent=2)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WFO promotion gates for all indices")
    parser.add_argument("--months", type=int, default=5, help="Months of history to request")
    parser.add_argument("--cache-only", action="store_true", help="Never call Kite API for data")
    parser.add_argument("--apply-overlays", action="store_true", help="Apply human-confirmed overlays for passed indices")
    parser.add_argument("--cost-multiplier", type=float, default=1.0, help="Cost stress multiplier (>=1.0 for promotion)")
    args = parser.parse_args()

    load_dotenv()
    token_ok, _, token_msg = validate_access_token()
    if not token_ok and not args.cache_only:
        print(f"[WARN] Kite token invalid ({token_msg}). Using --cache-only for available indices.")
        args.cache_only = True

    kite = KiteConnect(api_key=os.getenv("KITE_API_KEY", ""))
    kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN", ""))

    print("=" * 72)
    print("NiftyFuturesAlgo — Multi-Index Promotion WFO")
    print(f"Mode: {'cache-only' if args.cache_only else 'cache-first + Kite fallback'}")
    print(f"Cost multiplier: {args.cost_multiplier}")
    print("=" * 72)

    all_results: dict = {}
    for underlying in INDICES:
        print(f"\n>>> Running WFO for {underlying}...")
        started = time.time()
        all_results[underlying] = _run_index(
            kite=kite,
            underlying=underlying,
            months_back=args.months,
            cache_only=args.cache_only,
            cost_multiplier=args.cost_multiplier,
        )
        elapsed = time.time() - started
        promo = all_results[underlying].get("promotion") or {}
        print(
            f"<<< {underlying} done in {elapsed:.0f}s — "
            f"{'PASSED' if promo.get('passed') else 'REJECTED'}"
        )

    _print_report(all_results)

    if args.apply_overlays:
        print("\n--- Applying promoted overlays (human-confirmed) ---")
        for underlying in INDICES:
            preview = preview_promoted_overlay(underlying)
            if not preview.get("eligible"):
                print(f"  {underlying}: skip — {preview.get('reason')}")
                continue
            applied = apply_promoted_overlay(underlying, human_confirmed=True)
            if applied.get("success"):
                print(f"  {underlying}: overlay written → {applied['path']}")
            else:
                print(f"  {underlying}: failed — {applied.get('error')}")

    candidates = load_candidates()
    passed = [c["underlying"] for c in candidates if c.get("passed")]
    print("\n" + "=" * 72)
    print(f"Candidates on disk: {len(candidates)} | Passed: {passed or 'none'}")
    print("Re-run: python scripts/fo_safe_deploy.py")
    print("=" * 72)
    return 0 if len(passed) == len(INDICES) else 1


if __name__ == "__main__":
    raise SystemExit(main())