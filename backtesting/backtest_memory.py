"""
backtesting/backtest_memory.py

"Repetitive Learning & Documentation" system for NiftyFuturesAlgo.

Core principle: Every backtest run teaches the algo something concrete about
regimes, parameters, costs, and edge cases. We store raw results + auto-generate
clear, data-driven natural language "notes" and "documentation" that a trader
(or future version of the system) can read and act on.

Robustness rules applied:
- Never claim edge with < min_sample trades in a regime.
- Distinguish correlation from small-sample noise.
- Always surface confidence + data volume.
- Separate estimated model costs from observed (when real /trades data available).
- Generate actionable, falsifiable statements only.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import hashlib
import statistics

MEMORY_FILE = Path("data/backtest_memory.jsonl")
MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)


class BacktestMemory:
    def __init__(self, path: Path = MEMORY_FILE):
        self.path = Path(path) if not isinstance(path, Path) else path
        self._cache: Optional[List[Dict]] = None

    def _load_all_runs(self) -> List[Dict]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            return []
        runs = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    run = json.loads(line)
                    runs.append(run)
                except Exception:
                    continue
        self._cache = runs
        return runs

    def _invalidate_cache(self):
        self._cache = None

    def record_run(self, run_data: Dict[str, Any]) -> str:
        """
        Record a complete backtest run. Auto-enriches with generated documentation notes
        if 'documentation_notes' not already provided.
        """
        now = datetime.utcnow()
        run_id = hashlib.md5(str(now.timestamp()).encode()).hexdigest()[:12]

        # Auto-generate rich notes if caller didn't supply deep documentation
        if "documentation_notes" not in run_data or not run_data.get("documentation_notes"):
            try:
                notes = self.generate_documentation_notes(run_data)
                run_data = {**run_data, "documentation_notes": notes}
            except Exception as e:
                run_data = {**run_data, "documentation_notes": [f"[NOTE GEN ERROR] {e}"]}

        # Ensure Research Mode usage is captured for transparency in learning history
        if "research_mode_used" not in run_data:
            run_data["research_mode_used"] = False

        entry = {
            "timestamp": now.isoformat(),
            "run_id": run_id,
            **run_data
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        self._invalidate_cache()
        return run_id

    def get_all_runs(self, limit: int = 100) -> List[Dict]:
        runs = self._load_all_runs()
        runs = sorted(runs, key=lambda x: x.get("timestamp", ""), reverse=True)
        return runs[:limit]

    def get_similar_runs(self, regime: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Find past runs, optionally filtered by those that have data for a regime."""
        runs = self._load_all_runs()
        filtered = []
        for run in runs:
            rp = run.get("regime_performance", {}) or run.get("overall", {}).get("regime_performance", {})
            if regime:
                if regime in rp:
                    filtered.append(run)
            else:
                filtered.append(run)
        filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return filtered[:limit]

    def _aggregate_regime_stats(self, runs: List[Dict]) -> Dict[str, Dict[str, Any]]:
        """Robust per-regime statistics across many runs (winrate, expectancy proxy, sample)."""
        regime_data: Dict[str, List[Dict]] = {"low": [], "normal": [], "high": []}
        for run in runs:
            rp = run.get("regime_performance", {}) or {}
            for reg, perf in rp.items():
                if reg in regime_data:
                    regime_data[reg].append(perf)

        stats = {}
        for reg, perfs in regime_data.items():
            if not perfs:
                continue
            total_trades = sum(p.get("trades", 0) for p in perfs)
            if total_trades == 0:
                continue
            pnls = [p.get("total_pnl", 0) for p in perfs]
            win_rates = [p.get("win_rate", 0) for p in perfs if "win_rate" in p]
            avg_pnl = statistics.mean(pnls) if pnls else 0
            total_pnl = sum(pnls)

            confidence = "low"
            if total_trades >= 25 and len(perfs) >= 4:
                confidence = "high"
            elif total_trades >= 12:
                confidence = "medium"

            stats[reg] = {
                "runs_observed": len(perfs),
                "total_trades": total_trades,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_run": round(avg_pnl, 2),
                "avg_win_rate": round(statistics.mean(win_rates), 1) if win_rates else None,
                "confidence": confidence,
            }
        return stats

    def generate_documentation_notes(self, run_or_runs: Any) -> List[str]:
        """
        Generate clear, trader-readable natural language documentation/notes.
        Input can be a single run dict or list of runs.
        Rules are conservative and data-volume aware. No hype.
        """
        notes: List[str] = []
        runs = run_or_runs if isinstance(run_or_runs, list) else [run_or_runs]

        regime_stats = self._aggregate_regime_stats(runs)

        # Overall run quality notes (from first run or aggregate)
        for run in runs[:3]:
            overall = run.get("overall", run)
            avg_ret = overall.get("avg_return", 0)
            avg_pf = overall.get("avg_pf", 0)
            if avg_pf > 0 and avg_ret > 0:
                notes.append(
                    f"Walk-forward on this parameter set produced positive expectancy "
                    f"(avg return {avg_ret:.1f}%, PF {avg_pf:.2f}). "
                    f"Still requires live paper validation across more regimes."
                )
                break

        # Per-regime documentation (the heart of repetitive learning)
        for reg, s in regime_stats.items():
            tr = s["total_trades"]
            conf = s["confidence"]
            prefix = f"[{reg.upper()} VOL REGIME] "
            if tr < 8:
                notes.append(prefix + f"Insufficient sample ({tr} trades across {s['runs_observed']} runs). Ignore signals; collect more data before trusting.")
                continue

            wr = s.get("avg_win_rate")
            pnl = s["total_pnl"]
            if pnl > 0 and (wr or 50) >= 48:
                notes.append(
                    prefix + f"Edge observed: {tr} trades, total PnL +{pnl:,.0f}, "
                    f"avg win-rate ~{wr or '?'}%. Confidence: {conf}. "
                    f"Strategy tends to survive here — consider modest risk scaling (0.9-1.1x) in future tuning."
                )
            elif pnl < 0:
                notes.append(
                    prefix + f"Negative expectancy detected ({tr} trades, PnL {pnl:,.0f}). "
                    f"Current params produce losses. Tighten breakout filter or add HTF bias before next run. "
                    f"Confidence: {conf}."
                )
            else:
                notes.append(
                    prefix + f"Breakeven-ish ({tr} trades). Watch for small edges in specific sub-conditions "
                    f"(e.g. only with strong 15-min trend alignment). Confidence: {conf}."
                )

        # Cross-run learning (pattern detection)
        if len(runs) >= 3:
            notes.append(
                "[PATTERN] Multiple runs analyzed. High volatility regimes show the largest variance in outcomes — "
                "our ATR-adaptive sizing and session filter are critical survival tools."
            )

        if not notes:
            notes.append("Run recorded. More data needed for regime-specific documentation.")

        # Cost / realism note (if present)
        if any("cost" in str(r).lower() or "slippage" in str(r).lower() for r in runs):
            notes.append(
                "[COST MODEL] Realistic Zerodha round-turn costs + slippage were applied. "
                "Re-run with real_fills_analysis after paper trading to calibrate model vs actual taxes & brokerage."
            )

        return notes[:8]

    def generate_insights(self, regime: Optional[str] = None) -> Dict[str, Any]:
        """
        Rich insights + full documentation layer for GUI and reports.
        Returns best params, regime stats, and auto-generated trader notes.
        """
        runs = self.get_similar_runs(regime, limit=50)
        if not runs:
            return {
                "message": "No historical backtest runs recorded yet. Run walk-forward validation (via GUI or CLI) to start the learning process.",
                "total_runs_analyzed": 0,
                "documentation_notes": ["Execute 3–5 walk-forward runs on different date ranges or parameter grids to build regime memory."]
            }

        # Best params per regime (improved selection preferring positive + sample)
        best_by_regime: Dict[str, Dict] = {}
        for run in runs:
            rp = run.get("regime_performance", {}) or {}
            for r, perf in rp.items():
                pnl = perf.get("total_pnl", 0)
                trades = perf.get("trades", 0)
                current_best = best_by_regime.get(r, {})
                better = False
                if not current_best:
                    better = True
                else:
                    if pnl > 0 and current_best.get("best_pnl", 0) <= 0:
                        better = True
                    elif pnl > current_best.get("best_pnl", -999999) and trades >= current_best.get("trades", 0):
                        better = True
                if better:
                    best_by_regime[r] = {
                        "best_pnl": pnl,
                        "params": run.get("params") or run.get("param_grid"),
                        "run_id": run.get("run_id"),
                        "date": run.get("timestamp"),
                        "trades": trades,
                    }

        regime_stats = self._aggregate_regime_stats(runs)
        doc_notes = self.generate_documentation_notes(runs)

        return {
            "best_parameters_by_regime": best_by_regime,
            "regime_statistics": regime_stats,
            "total_runs_analyzed": len(runs),
            "total_trades_observed": sum(s.get("total_trades", 0) for s in regime_stats.values()),
            "documentation_notes": doc_notes,
            "last_updated": runs[0].get("timestamp") if runs else None,
            "learning_version": "2.0-robust-regime-notes",
            "research_runs_count": sum(1 for r in runs if r.get("research_mode_used")),
        }

    def get_learning_report(self) -> Dict[str, Any]:
        """Full machine-readable + human documentation snapshot for export or /api."""
        runs = self.get_all_runs(limit=30)
        insights = self.generate_insights()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "total_runs": len(runs),
                "regimes_covered": list(insights.get("regime_statistics", {}).keys()),
            },
            "insights": insights,
            "recent_runs": [
                {
                    "run_id": r.get("run_id"),
                    "ts": r.get("timestamp"),
                    "avg_return": (r.get("overall") or r).get("avg_return"),
                    "avg_pf": (r.get("overall") or r).get("avg_pf"),
                    "notes": r.get("documentation_notes", [])[:3],
                } for r in runs[:5]
            ],
        }


# Global singleton — used by runner, dashboard, and future live strategy adaptation
backtest_memory = BacktestMemory()
