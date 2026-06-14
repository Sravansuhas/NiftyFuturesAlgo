"""
backtesting/wfo_splits.py

Institutional walk-forward split utilities: anchored purged folds,
train-fold scoring objectives, and cross-fold parameter stability.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Tuple


def build_anchored_purged_folds(
    n_bars: int,
    n_folds: int,
    test_fraction: float = 0.4,
    embargo_bars: int = 78,
    min_train_bars: int = 500,
    min_test_bars: int = 100,
) -> List[Tuple[slice, slice]]:
    """
    Build anchored, purged walk-forward folds.

    - Train is anchored at index 0 and grows through later folds.
    - OOS test windows are sequential, non-overlapping slices in the
      last ``test_fraction`` of the series.
    - An embargo (purge) gap of at least ``embargo_bars`` sits between
      train_end and test_start to avoid label leakage.

    Returns an empty list when constraints cannot be satisfied.
    """
    if n_bars <= 0 or n_folds <= 0:
        return []

    if not 0.0 < test_fraction < 1.0:
        return []

    oos_start = int(n_bars * (1.0 - test_fraction))
    oos_length = n_bars - oos_start

    if oos_length < n_folds * min_test_bars:
        return []

    if oos_start - embargo_bars < min_train_bars:
        return []

    base_window = oos_length // n_folds
    if base_window < min_test_bars:
        return []

    remainder = oos_length % n_folds
    folds: List[Tuple[slice, slice]] = []
    offset = oos_start

    for fold_idx in range(n_folds):
        window = base_window + (1 if fold_idx < remainder else 0)
        test_start = offset
        test_end = offset + window
        offset = test_end

        train_end = test_start - embargo_bars
        if train_end < min_train_bars or (test_end - test_start) < min_test_bars:
            return []

        folds.append((slice(0, train_end), slice(test_start, test_end)))

    return folds


def _trade_count(result: dict) -> int:
    if "total_trades" in result and result["total_trades"] is not None:
        return int(result["total_trades"])
    return len(result.get("trades") or [])


def score_train_result(
    result: dict,
    objective: str = "calmar",
    min_trades: int = 5,
) -> float:
    """
    Score an in-sample backtest result for parameter selection.

    Objectives:
    - calmar: total_return_pct / max(max_drawdown_pct, 0.1)
    - sharpe_penalized: (return * sqrt(trades)) / (1 + max_dd)

    Returns -inf when trade count is below ``min_trades``.
    """
    trades = _trade_count(result)
    if trades < min_trades:
        return float("-inf")

    total_return = float(result.get("total_return_pct", 0.0) or 0.0)
    max_dd = float(result.get("max_drawdown_pct", 0.0) or 0.0)

    if objective == "calmar":
        return total_return / max(max_dd, 0.1)

    if objective == "sharpe_penalized":
        return (total_return * math.sqrt(trades)) / (1.0 + max_dd)

    raise ValueError(f"Unknown objective: {objective}")


def _extract_params(fold_result: dict) -> Dict[str, Any]:
    for key in ("best_params", "params", "parameters"):
        params = fold_result.get(key)
        if isinstance(params, dict):
            return params
    return {}


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parameter_stability(fold_results: List[dict]) -> dict:
    """
    Summarize parameter variance across walk-forward folds.

    Each fold result may expose parameters under ``best_params``, ``params``,
    or ``parameters``. Numeric parameters receive mean/std/min/max/range/cv.
    """
    if not fold_results:
        return {
            "n_folds": 0,
            "parameters": {},
            "stable": True,
        }

    param_names = set()
    for fold in fold_results:
        param_names.update(_extract_params(fold).keys())

    parameters: Dict[str, dict] = {}
    for name in sorted(param_names):
        values: List[float] = []
        for fold in fold_results:
            raw = _extract_params(fold).get(name)
            if _is_numeric(raw):
                values.append(float(raw))

        if not values:
            continue

        mean = statistics.fmean(values)
        std = statistics.pstdev(values) if len(values) > 1 else 0.0
        min_val = min(values)
        max_val = max(values)
        param_range = max_val - min_val
        cv = abs(std / mean) if mean != 0 else (0.0 if std == 0 else float("inf"))

        parameters[name] = {
            "values": values,
            "mean": mean,
            "std": std,
            "min": min_val,
            "max": max_val,
            "range": param_range,
            "coefficient_of_variation": cv,
        }

    return {
        "n_folds": len(fold_results),
        "parameters": parameters,
        "stable": all(
            stats.get("coefficient_of_variation", float("inf")) < 0.25
            for stats in parameters.values()
        ),
    }