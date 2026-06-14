"""
Promotion gates — deployment oracle for walk-forward results.

A parameter set is eligible for paper deployment only when it passes
all configured statistical gates on out-of-sample folds.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

CANDIDATES_FILE = Path("data/strategy_candidates.json")


@dataclass(frozen=True)
class PromotionGateConfig:
    min_oos_profit_factor: float = 1.2
    max_oos_drawdown_pct: float = 8.0
    min_trades_per_fold: int = 5
    min_folds_passing: int = 2
    min_avg_return_pct: float = 0.0
    require_mc_p5_positive: bool = True
    max_param_cv: float = 0.35
    min_cost_multiplier_tested: float = 2.0
    min_regime_trades: int = 5
    min_regime_pnl: float = 0.0
    require_high_vol_non_negative: bool = True
    require_ranging_non_negative: bool = True


@dataclass
class PromotionResult:
    passed: bool
    underlying: str
    best_params: Dict[str, Any]
    reasons: List[str] = field(default_factory=list)
    fold_pass_count: int = 0
    summary: Dict[str, Any] = field(default_factory=dict)
    stability: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fold_passes(
    fold: Dict[str, Any],
    gates: PromotionGateConfig,
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    pf = float(fold.get("test_pf", 0) or 0)
    dd = float(fold.get("test_dd", 0) or 0)
    trades = int(fold.get("trades", 0) or 0)
    ret = float(fold.get("test_return", 0) or 0)

    if trades < gates.min_trades_per_fold:
        reasons.append(f"trades={trades} < {gates.min_trades_per_fold}")
    if pf < gates.min_oos_profit_factor:
        reasons.append(f"PF={pf:.2f} < {gates.min_oos_profit_factor}")
    if dd > gates.max_oos_drawdown_pct:
        reasons.append(f"DD={dd:.2f}% > {gates.max_oos_drawdown_pct}%")
    if ret < gates.min_avg_return_pct:
        reasons.append(f"return={ret:.2f}% < {gates.min_avg_return_pct}%")

    if gates.require_mc_p5_positive:
        mc = fold.get("monte_carlo") or {}
        p5 = mc.get("final_return_5th_percentile")
        if p5 is not None and float(p5) <= 0:
            reasons.append(f"MC p5 return={p5} <= 0")

    return len(reasons) == 0, reasons


def _regime_gate_reasons(
    summary: Dict[str, Any],
    gates: PromotionGateConfig,
) -> List[str]:
    """Reject strategies that only work in favorable regimes."""
    reasons: List[str] = []
    agg = summary.get("regime_aggregate") or {}
    high = agg.get("high", {})
    if gates.require_high_vol_non_negative:
        trades = int(high.get("trades", 0) or 0)
        pnl = float(high.get("total_pnl", 0) or 0)
        if trades >= gates.min_regime_trades and pnl < gates.min_regime_pnl:
            reasons.append(
                f"high-vol regime PnL={pnl:.0f} < {gates.min_regime_pnl} ({trades} trades)"
            )

    trend_agg = summary.get("trend_aggregate") or {}
    ranging = trend_agg.get("ranging", {})
    if gates.require_ranging_non_negative:
        trades = int(ranging.get("trades", 0) or 0)
        pnl = float(ranging.get("total_pnl", 0) or 0)
        if trades >= gates.min_regime_trades and pnl < gates.min_regime_pnl:
            reasons.append(
                f"ranging/sideways PnL={pnl:.0f} < {gates.min_regime_pnl} ({trades} trades)"
            )
    return reasons


def evaluate_wfo_summary(
    summary: Dict[str, Any],
    underlying: str = "NIFTY",
    gates: Optional[PromotionGateConfig] = None,
    cost_multiplier: float = 1.0,
) -> PromotionResult:
    """
    Evaluate aggregated walk-forward output against promotion gates.

    ``summary`` is the dict returned by ``run_walk_forward``.
    """
    gates = gates or PromotionGateConfig()
    folds = summary.get("folds") or []
    reasons: List[str] = []

    if cost_multiplier < gates.min_cost_multiplier_tested:
        reasons.append(
            f"cost_multiplier={cost_multiplier} < required {gates.min_cost_multiplier_tested}"
        )

    if not folds:
        reasons.append("no OOS folds produced")
        return PromotionResult(
            passed=False,
            underlying=underlying.upper(),
            best_params={},
            reasons=reasons,
            summary=summary,
        )

    pass_count = 0
    fold_reports: List[Dict[str, Any]] = []
    for fold in folds:
        ok, fold_reasons = _fold_passes(fold, gates)
        fold_reports.append({"fold": fold.get("fold"), "passed": ok, "reasons": fold_reasons})
        if ok:
            pass_count += 1

    if pass_count < gates.min_folds_passing:
        reasons.append(
            f"only {pass_count}/{len(folds)} folds passed (need {gates.min_folds_passing})"
        )

    reasons.extend(_regime_gate_reasons(summary, gates))

    stability = summary.get("parameter_stability") or {}
    if stability and not stability.get("stable", True):
        unstable = [
            name
            for name, stats in (stability.get("parameters") or {}).items()
            if stats.get("coefficient_of_variation", 0) > gates.max_param_cv
        ]
        if unstable:
            reasons.append(f"unstable params (CV>{gates.max_param_cv}): {', '.join(unstable[:5])}")

    # Consensus params: mode across passing folds, else last fold
    passing_params = [
        f.get("best_params") or {}
        for f, r in zip(folds, fold_reports)
        if r["passed"] and f.get("best_params")
    ]
    if passing_params:
        best_params = _consensus_params(passing_params)
    elif folds:
        best_params = folds[-1].get("best_params") or {}
    else:
        best_params = {}

    passed = len(reasons) == 0 and pass_count >= gates.min_folds_passing

    return PromotionResult(
        passed=passed,
        underlying=underlying.upper(),
        best_params=best_params,
        reasons=reasons,
        fold_pass_count=pass_count,
        summary={
            "avg_return": summary.get("avg_return"),
            "avg_pf": summary.get("avg_pf"),
            "total_folds_run": summary.get("total_folds_run"),
            "fold_reports": fold_reports,
        },
        stability=stability,
    )


def _consensus_params(param_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick most frequent value per key across passing folds."""
    if not param_list:
        return {}
    keys = set()
    for p in param_list:
        keys.update(p.keys())
    consensus: Dict[str, Any] = {}
    for key in keys:
        values = [p[key] for p in param_list if key in p]
        if not values:
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            consensus[key] = sum(values) / len(values)
        else:
            consensus[key] = max(set(values), key=values.count)
    return consensus


def write_candidate(result: PromotionResult, path: Path = CANDIDATES_FILE) -> None:
    """Append or update promoted candidate in JSON store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            existing = payload.get("candidates", [])
        except Exception:
            existing = []

    entry = result.to_dict()
    entry["status"] = "promoted" if result.passed else "rejected"

    replaced = False
    for i, cand in enumerate(existing):
        if cand.get("underlying") == result.underlying:
            existing[i] = entry
            replaced = True
            break
    if not replaced:
        existing.append(entry)

    with path.open("w", encoding="utf-8") as handle:
        json.dump({"updated_at": time.time(), "candidates": existing}, handle, indent=2)


def load_candidates(path: Path = CANDIDATES_FILE) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle).get("candidates", [])
    except Exception:
        return []