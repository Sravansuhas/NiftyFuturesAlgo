"""
backtesting/walk_forward_runner.py

Professional Walk-Forward + Regime-Aware Backtest Runner.

This is a production-grade tool for validating strategy robustness across
different market regimes and time periods.

Features:
- Walk-forward optimization (multiple folds)
- Regime detection integration (volatility + trend)
- Performance breakdown by regime
- Realistic costs
- Clean reporting

Usage example:
    from backtesting.walk_forward_runner import run_walk_forward

    results = run_walk_forward(
        strategy_class=PreviousCandleBacktestStrategy,
        data=df,
        param_grid={...},
        n_folds=5,
        ...
    )
"""

from typing import Dict, Any, List, Type, Callable, Optional
import pandas as pd
import numpy as np
import statistics
from datetime import datetime

from backtesting.backtester import Backtester
from backtesting.costs import default_cost_model
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy, StrategyParams
from backtesting.backtest_memory import backtest_memory
from backtesting.metrics import monte_carlo_simulation
from backtesting.wfo_splits import (
    build_anchored_purged_folds,
    score_train_result,
    parameter_stability,
)
from backtesting.promotion_gates import evaluate_wfo_summary, write_candidate


def detect_regime_simple(df: pd.DataFrame, window: int = 50) -> pd.Series:
    """
    Vol regime Series with trend sidecar — aligned with live app/strategy.py.
    Delegates to backtesting.regime for parity.
    """
    from backtesting.regime import detect_regime_simple as _detect

    return _detect(df, window=window)


def run_walk_forward(
    strategy_class: Type,
    data: pd.DataFrame,
    param_grid: Dict[str, List[Any]],
    n_folds: int = 5,
    train_size: float = 0.6,
    cost_model=None,
    regime_detector: Callable = detect_regime_simple,
    progress_callback: Callable[[int, str], None] = None,
    cost_multiplier: float = 1.0,
    min_trades_for_validity: int = 5,
    wfo_mode: str = "rolling_purged",
    test_fraction: float = 0.4,
    embargo_bars: int = 78,
    objective: str = "calmar",
    underlying: str = "NIFTY",
    random_seed: Optional[int] = 42,
    run_promotion_gates: bool = True,
) -> Dict[str, Any]:
    """
    Run a walk-forward backtest with regime analysis.

    progress_callback: Optional function(progress: int, stage: str) -> None
                       Called periodically to update UI progress.
    cost_multiplier: Scale all transaction costs (1.0 = base case, 2.0 = pessimistic, etc.)
    """
    if cost_model is None:
        cost_model = default_cost_model

    if random_seed is not None:
        np.random.seed(random_seed)

    if progress_callback:
        progress_callback(10, "starting_walk_forward")

    results = []
    n_bars = len(data)

    if wfo_mode == "rolling_purged":
        fold_slices = build_anchored_purged_folds(
            n_bars=n_bars,
            n_folds=n_folds,
            test_fraction=test_fraction,
            embargo_bars=embargo_bars,
            min_train_bars=max(500, int(n_bars * 0.15)),
            min_test_bars=100,
        )
    else:
        fold_slices = []
        fold_size = n_bars // n_folds
        for fold in range(n_folds):
            test_start = fold * fold_size
            test_end = (fold + 1) * fold_size if fold < n_folds - 1 else n_bars
            train_end = int(test_start + (test_end - test_start) * train_size)
            fold_slices.append((slice(test_start, train_end), slice(train_end, test_end)))

    if not fold_slices:
        print("[WFO] No valid folds — insufficient data for rolling purged WFO")
        return {
            "folds": [],
            "avg_return": 0.0,
            "avg_pf": 0.0,
            "total_folds_run": 0,
            "total_trades": 0,
            "wfo_mode": wfo_mode,
            "error": "insufficient_data_for_folds",
        }

    for fold_idx, (train_sl, test_sl) in enumerate(fold_slices):
        fold = fold_idx
        fold_progress = 10 + int((fold / max(1, len(fold_slices))) * 70)
        if progress_callback:
            progress_callback(fold_progress, f"fold_{fold+1}_training")

        print(f"\n=== Fold {fold + 1}/{len(fold_slices)} ({wfo_mode}) ===")

        train_data = data.iloc[train_sl]
        test_data = data.iloc[test_sl]

        if len(train_data) < 100 or len(test_data) < 50:
            print("Skipping fold — insufficient data")
            continue

        # Simple parameter search on train (can be expanded to full optimization)
        best_params = None
        best_score = -np.inf

        param_combos = list(_generate_param_combinations(param_grid))
        total_combos = len(param_combos) or 1

        for i, flat_params in enumerate(param_combos):
            strategy = _instantiate_strategy(strategy_class, flat_params)
            bt = Backtester(strategy, cost_model=cost_model, verbose=False, cost_multiplier=cost_multiplier)
            train_result = bt.run(train_data)
            raw_return = train_result.get('total_return_pct', 0)
            n_trades = len(train_result.get('trades', []))

            score = score_train_result(
                train_result,
                objective=objective,
                min_trades=min_trades_for_validity,
            )

            if score > best_score:
                best_score = score
                best_params = flat_params

            # Granular progress during grid search (prevents "stuck" feeling)
            if progress_callback:
                combo_progress = fold_progress + int((i / total_combos) * 8)
                progress_callback(combo_progress, f"fold_{fold+1}_training")

        if best_params is None:
            continue

        if progress_callback:
            progress_callback(fold_progress + 9, f"fold_{fold+1}_testing")

        # Final small bump before the actual test run starts
        if progress_callback:
            progress_callback(fold_progress + 10, f"fold_{fold+1}_testing")

        # Test on out-of-sample using the professional helper (verbose so user sees the chosen params result)
        test_strategy = _instantiate_strategy(strategy_class, best_params or {})
        bt_test = Backtester(test_strategy, cost_model=cost_model, verbose=True, cost_multiplier=cost_multiplier)
        test_result = bt_test.run(test_data)

        # Monte Carlo on the final selected parameters (professional robustness check)
        mc_results = monte_carlo_simulation(
            test_result.get('trades', []),
            n_sims=1000,
            initial_capital=1_000_000
        )

        # Regime analysis on test period
        regimes = regime_detector(test_data)
        regime_perf = _analyze_by_regime(test_result, regimes)

        test_trades = test_result.get('trades', []) or []
        test_trades_count = len(test_trades)
        results.append({
            "fold": fold + 1,
            "best_params": best_params,
            "test_return": test_result.get('total_return_pct', 0),
            "test_pf": test_result.get('profit_factor', 0),
            "test_dd": test_result.get('max_drawdown_pct', 0),
            "regime_performance": regime_perf,
            "trades": test_trades_count,
            "trades_list": test_trades,
            "min_trades_met": test_trades_count >= min_trades_for_validity,
            "monte_carlo": mc_results
        })

        print(f"Fold {fold+1} | Test Return: {test_result.get('total_return_pct', 0):.2f}% | PF: {test_result.get('profit_factor', 0):.2f}")

        if progress_callback:
            progress_callback(10 + int(((fold + 1) / max(1, n_folds)) * 80), f"fold_{fold+1}_completed")

    if progress_callback:
        progress_callback(95, "finalizing_results")

    stability = parameter_stability(results)

    summary = {
        "folds": results,
        "avg_return": float(np.mean([r["test_return"] for r in results])) if results else 0.0,
        "avg_pf": float(np.mean([r["test_pf"] for r in results])) if results else 0.0,
        "total_folds_run": len(results),
        "total_trades": sum(r.get("trades", 0) for r in results),
        "wfo_mode": wfo_mode,
        "objective": objective,
        "underlying": underlying.upper(),
        "embargo_bars": embargo_bars,
        "test_fraction": test_fraction,
        "random_seed": random_seed,
        "parameter_stability": stability,
    }

    if run_promotion_gates and results:
        promotion = evaluate_wfo_summary(
            summary,
            underlying=underlying,
            cost_multiplier=cost_multiplier,
        )
        summary["promotion"] = promotion.to_dict()
        try:
            write_candidate(promotion)
        except Exception as promo_exc:
            print(f"[PROMOTION] Failed to write candidate: {promo_exc}")

    # Record to memory for long-term repetitive learning + auto documentation
    learning_notes: List[str] = []
    regime_aggregate: Dict = {}
    trend_aggregate: Dict = {}
    run_id: Optional[str] = None
    try:
        mem_payload: Dict[str, Any] = {
            "params": param_grid,
            "overall": summary,
            "folds": results,
            "regime_performance": {},
        }
        agg_rp: Dict[str, Any] = {}
        agg_tp: Dict[str, Any] = {}
        for r in results:
            for reg, p in (r.get("regime_performance", {}) or {}).items():
                if reg not in agg_rp:
                    agg_rp[reg] = {"trades": 0, "total_pnl": 0.0, "win_samples": []}
                agg_rp[reg]["trades"] += p.get("trades", 0)
                agg_rp[reg]["total_pnl"] += p.get("total_pnl", 0)
                if "win_rate" in p:
                    agg_rp[reg]["win_samples"].append(p["win_rate"])
            for trd, p in (r.get("trend_performance", {}) or {}).items():
                if trd not in agg_tp:
                    agg_tp[trd] = {"trades": 0, "total_pnl": 0.0, "win_samples": []}
                agg_tp[trd]["trades"] += p.get("trades", 0)
                agg_tp[trd]["total_pnl"] += p.get("total_pnl", 0)
                if "win_rate" in p:
                    agg_tp[trd]["win_samples"].append(p["win_rate"])
        for agg in (agg_rp, agg_tp):
            for label, bucket in agg.items():
                ws = bucket.get("win_samples", [])
                bucket["avg_win_rate"] = round(statistics.mean(ws), 1) if ws else None
                bucket["total_pnl"] = round(bucket["total_pnl"], 2)
                if "win_samples" in bucket:
                    del bucket["win_samples"]
        mem_payload["regime_performance"] = agg_rp
        regime_aggregate = agg_rp
        trend_aggregate = agg_tp

        run_id = backtest_memory.record_run(mem_payload)
        # Pull the freshly generated documentation for this run
        latest = backtest_memory.get_all_runs(limit=1)
        if latest:
            learning_notes = latest[0].get("documentation_notes", [])
    except Exception as e:
        print(f"[MEMORY] Failed to record run: {e}")
        learning_notes = [f"Recording failed: {e}"]

    # Enrich return for GUI / caller (richer than before)
    summary["learning_notes"] = learning_notes
    summary["regime_aggregate"] = regime_aggregate
    summary["trend_aggregate"] = trend_aggregate
    summary["run_id"] = run_id

    return summary


def _generate_param_combinations(grid: Dict[str, List]) -> List[Dict]:
    """Simple cartesian product for small grids."""
    from itertools import product
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*values)]


def _analyze_by_regime(result: Dict, regimes: pd.Series) -> Dict:
    """
    Break down performance by (vol) regime.
    Also mutates/enriches the trades list in result with 'regime' label for learning.
    Trend info (if attached) is noted per trade too.
    """
    trades = result.get('trades', []) or []
    if not trades:
        return {}

    # Robust lookup: regimes is indexed by timestamp
    def _lookup_regime(ts):
        try:
            if ts is None:
                return "normal"
            # pandas Series .loc or .get for Timestamp/datetime
            val = regimes.loc[ts] if hasattr(regimes, "loc") else regimes.get(ts, "normal")
            return str(val) if val else "normal"
        except Exception:
            return "normal"

    trend_series = getattr(regimes, "_trend", None)

    _TREND_MAP = {"up": "uptrend", "down": "downtrend", "flat": "ranging"}

    def _lookup_trend(ts):
        if trend_series is None:
            return "ranging"
        try:
            val = trend_series.loc[ts] if hasattr(trend_series, "loc") else "flat"
            raw = str(val) if val else "flat"
            return _TREND_MAP.get(raw, "ranging")
        except Exception:
            return "ranging"

    enriched_trades = []
    perf: Dict[str, Dict] = {}
    trend_perf: Dict[str, Dict] = {}
    for regime_label in ['low', 'normal', 'high']:
        perf[regime_label] = {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0, "pnls": []}
    for trend_label in ['uptrend', 'downtrend', 'ranging']:
        trend_perf[trend_label] = {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0, "pnls": []}

    for t in trades:
        ts = t.get("entry_time")
        reg = _lookup_regime(ts)
        trd = _lookup_trend(ts)
        t_enriched = {**t, "regime": reg, "trend_bias": trd}
        enriched_trades.append(t_enriched)

        if reg in perf:
            bucket = perf[reg]
            bucket["trades"] += 1
            pnl = t.get("pnl", 0)
            bucket["total_pnl"] += pnl
            bucket["pnls"].append(pnl)

        if trd in trend_perf:
            tb = trend_perf[trd]
            tb["trades"] += 1
            pnl = t.get("pnl", 0)
            tb["total_pnl"] += pnl
            tb["pnls"].append(pnl)

    for buckets in (perf, trend_perf):
        for label, bucket in buckets.items():
            if bucket["trades"] > 0:
                wins = sum(1 for p in bucket["pnls"] if p > 0)
                bucket["win_rate"] = round(wins / bucket["trades"] * 100, 1)
                bucket["total_pnl"] = round(bucket["total_pnl"], 2)
            del bucket["pnls"]

    result["trades"] = enriched_trades
    result["trend_performance"] = {
        k: v for k, v in trend_perf.items() if v["trades"] > 0
    }

    return {k: v for k, v in perf.items() if v["trades"] > 0}


def _instantiate_strategy(strategy_class: Type, flat_params: Dict[str, Any]):
    """
    Professional parameter application helper for walk-forward optimization and
    hyperparameter search.

    Handles the two common patterns in modern quant codebases:
    1. Strategy accepts a rich parameter object (StrategyParams dataclass here)
    2. Strategy accepts flat kwargs (common in many backtesting frameworks)

    This makes our research tooling resilient and follows current best practices.
    """
    # Best path: use our StrategyParams dataclass when the names match
    if StrategyParams is not None:
        try:
            valid = {k: v for k, v in flat_params.items()
                     if k in getattr(StrategyParams, '__dataclass_fields__', {})}
            if valid:
                sp = StrategyParams(**valid)
                return strategy_class(params=sp)
        except Exception:
            pass

    # Fallback: the strategy supports flat kwargs directly
    # (our PreviousCandleBacktestStrategy was updated to support this for research ergonomics)
    try:
        return strategy_class(**flat_params)
    except TypeError:
        # Absolute last resort
        return strategy_class()
