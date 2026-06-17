"""
Improvement loop — Phase 4B weekly earn reports + human-gated improvement proposals.

Principal-trader rules:
- Proposals are auto-generated but NEVER auto-applied.
- apply_improvement_proposal() only records founder approval manifests on disk.
- Weekly reports aggregate WFA memory, sessions, fill calibration, and pending patterns.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EARN_REPORTS_DIR = PROJECT_ROOT / "data" / "earn_reports"
IMPROVEMENT_PROPOSALS_DIR = PROJECT_ROOT / "data" / "improvement_proposals"
APPLIED_MANIFESTS_DIR = IMPROVEMENT_PROPOSALS_DIR / "applied"
FAILURE_PROPOSALS_DIR = PROJECT_ROOT / "data" / "knowledge_base" / "proposals"
SESSIONS_STUB_DIR = PROJECT_ROOT / "data" / "sessions"
AUDIT_EVENTS_PATH = PROJECT_ROOT / "data" / "audit_events.json"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

# Backward-compatible aliases used by dashboard / earlier Phase 4 drafts
WEEKLY_REPORTS_DIR = EARN_REPORTS_DIR

_SMALL_SAMPLE_SESSION_DAYS = 3
_LOW_CONFIDENCE_FILL_COUNT = 8

_PROPOSAL_PREVIEWS: Dict[str, Dict[str, Any]] = {
    "reduce_max_trades_per_day": {
        "target": "risk_gatekeeper.config.max_trades_per_day",
        "current_hint": "check risk_gatekeeper / multi_symbol_risk caps",
        "would_change": "Decrease max_trades_per_day by 1 (floor 1) after founder review.",
        "auto_apply": False,
    },
    "increase_default_slippage_points": {
        "target": "backtesting.costs.CostConfig.default_slippage_points",
        "current_hint": "4.0 points (fill_learning model baseline)",
        "would_change": "Increase default_slippage_points by +0.5 (cap +2.0) in cost model.",
        "auto_apply": False,
    },
    "rerun_wfa_rolling_purged": {
        "target": "backtesting.walk_forward_runner",
        "current_hint": "wfo_mode=rolling_purged",
        "would_change": "Re-run WFA per index with rolling_purged splits before promotion.",
        "auto_apply": False,
    },
    "audit_broker_sync_before_live": {
        "target": "app.broker_reconciliation",
        "current_hint": "recon.mismatch_halt events in audit log",
        "would_change": "Run full broker position sync audit; resolve mismatches before live.",
        "auto_apply": False,
    },
}


class ImprovementLoop:
    """Weekly earn aggregation + deterministic improvement proposals (human-gated)."""

    def __init__(
        self,
        earn_dir: Path = EARN_REPORTS_DIR,
        proposals_dir: Path = IMPROVEMENT_PROPOSALS_DIR,
        applied_dir: Path = APPLIED_MANIFESTS_DIR,
        failure_proposals_dir: Path = FAILURE_PROPOSALS_DIR,
        sessions_stub_dir: Path = SESSIONS_STUB_DIR,
        audit_path: Path = AUDIT_EVENTS_PATH,
    ):
        self.earn_dir = Path(earn_dir)
        self.proposals_dir = Path(proposals_dir)
        self.applied_dir = Path(applied_dir)
        self.failure_proposals_dir = Path(failure_proposals_dir)
        self.sessions_stub_dir = Path(sessions_stub_dir)
        self.audit_path = Path(audit_path)
        self.weekly_dir = self.earn_dir  # dashboard backward compat

        self.earn_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.applied_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Weekly earn report
    # ------------------------------------------------------------------

    def build_weekly_earn_report(self, weeks_back: int = 1) -> Dict[str, Any]:
        """Aggregate WFA, sessions, fill calibration, and pending human reviews."""
        weeks_back = max(1, int(weeks_back))
        days = weeks_back * 7
        now = now_ist()
        week_end = now.date()
        week_start = week_end - timedelta(days=days - 1)

        session_reports = self._load_session_reports(days=days)
        wfa_summary = self._build_wfa_summary()
        fill_calibration = self._build_fill_calibration()
        failure_patterns = self._build_failure_patterns_pending()
        session_summary = self._build_session_summary(session_reports, days=days)
        documentation_notes = self._build_documentation_notes(
            session_reports,
            wfa_summary,
            fill_calibration,
            failure_patterns,
        )

        report: Dict[str, Any] = {
            "generated_at": now.isoformat(),
            "week_label": self._iso_week_label(week_end),
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "weeks_back": weeks_back,
            "days_covered": days,
            "wfa_summary": wfa_summary,
            "session_summary": session_summary,
            "fill_calibration": fill_calibration,
            "failure_patterns_pending": failure_patterns,
            "improvement_proposals": [],
            "founder_actions": [],
            "documentation_notes": documentation_notes,
        }

        proposals = self.generate_improvement_proposals(report)
        report["improvement_proposals"] = proposals
        report["founder_actions"] = self._build_founder_actions(report, proposals)
        return report

    def build_weekly_report(self, days: int = 7) -> Dict[str, Any]:
        """Backward-compatible alias for dashboard / legacy callers."""
        weeks_back = max(1, (int(days) + 6) // 7)
        report = self.build_weekly_earn_report(weeks_back=weeks_back)
        report["days_covered"] = int(days)
        return report

    def save_weekly_report(self, report: Dict[str, Any]) -> Path:
        """Persist to data/earn_reports/{YYYY-Www}.json."""
        week_label = report.get("week_label") or self._iso_week_label(
            datetime.strptime(report.get("week_end", now_ist().strftime("%Y-%m-%d")), "%Y-%m-%d").date()
            if report.get("week_end")
            else now_ist().date()
        )
        path = self.earn_dir / f"{week_label}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)

        latest = self.earn_dir / "latest.json"
        with latest.open("w", encoding="utf-8") as handle:
            json.dump({**report, "saved_path": str(path)}, handle, indent=2, default=str)

        logger.info("[IMPROVEMENT] Weekly earn report saved: %s", path)
        return path

    def load_latest_weekly_report(self) -> Optional[Dict[str, Any]]:
        latest = self.earn_dir / "latest.json"
        if latest.exists():
            try:
                with latest.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass
        files = sorted(self.earn_dir.glob("*.json"), key=lambda p: p.name, reverse=True)
        for path in files:
            if path.name == "latest.json":
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Improvement proposals
    # ------------------------------------------------------------------

    def generate_improvement_proposals(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Deterministic proposals from report metrics — no LLM, no auto-apply."""
        proposals: List[Dict[str, Any]] = []
        session = report.get("session_summary") or {}
        fill = report.get("fill_calibration") or {}
        wfa = report.get("wfa_summary") or {}

        session_quality = session.get("avg_quality_score")
        if session_quality is not None and float(session_quality) < 70:
            proposals.append(self._make_proposal(
                proposal_id="reduce_max_trades_per_day",
                severity="high",
                target="risk_gatekeeper",
                description=(
                    f"Session quality avg {session_quality:.1f} < 70 — "
                    "tighten daily trade cap to reduce overtrading."
                ),
            ))

        cost_premium = fill.get("cost_premium_pct")
        if cost_premium is not None and float(cost_premium) >= 15.0:
            proposals.append(self._make_proposal(
                proposal_id="increase_default_slippage_points",
                severity="medium",
                target="backtesting.costs",
                description=(
                    f"Observed fill costs ~{cost_premium:.1f}% above model — "
                    "raise default_slippage_points for conservative backtests."
                ),
            ))

        promotion_failed = any(
            not (info or {}).get("passed", False)
            for info in (wfa.get("indices") or {}).values()
        )
        if promotion_failed and (wfa.get("memory_runs") or 0) > 0:
            proposals.append(self._make_proposal(
                proposal_id="rerun_wfa_rolling_purged",
                severity="medium",
                target="backtesting.walk_forward_runner",
                description=(
                    "At least one index failed promotion gates — "
                    "re-run WFA with rolling_purged splits before live sizing."
                ),
            ))

        recon_halts = int(session.get("recon_halts", 0) or 0)
        if recon_halts > 0:
            proposals.append(self._make_proposal(
                proposal_id="audit_broker_sync_before_live",
                severity="high",
                target="app.broker_reconciliation",
                description=(
                    f"{recon_halts} reconciliation halt(s) this period — "
                    "audit broker sync and position mismatches before enabling live."
                ),
            ))

        return proposals

    def submit_wfo_candidate(
        self,
        params: Dict[str, Any],
        underlying: str,
    ) -> Path:
        """
        Bridge WFO candidate params to human-gated improvement proposals.

        Writes to data/improvement_proposals/ for founder review — NEVER auto-applies
        overlays, risk config, or strategy parameters.
        """
        key = str(underlying or "NIFTY").upper()
        if key not in INDICES:
            raise ValueError(f"unsupported underlying: {underlying}")

        proposal_id = f"wfo_candidate_{key.lower()}_{int(time.time())}"
        description = (
            f"WFO candidate for {key} — review params before promotion overlay. "
            f"avg_pf={params.get('avg_pf')} avg_return={params.get('avg_return')} "
            f"wfo_mode={params.get('wfo_mode', 'rolling_purged')}."
        )
        proposal = {
            "id": proposal_id,
            "proposal_type": "wfo_candidate",
            "underlying": key,
            "params": dict(params),
            "severity": "medium",
            "target": "backtesting.promotion_gates",
            "description": description,
            "human_gate_required": True,
            "auto_apply": False,
            "status": "pending_review",
        }
        return self.record_improvement_proposal(proposal)

    def record_improvement_proposal(self, proposal: Dict[str, Any]) -> Path:
        """Save proposal to data/improvement_proposals/{timestamp}.json."""
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        proposal_id = proposal.get("id") or proposal.get("proposal_id") or f"proposal_{int(time.time())}"
        path = self.proposals_dir / f"{ts}.json"
        payload = {
            "id": proposal_id,
            "status": proposal.get("status", "pending_review"),
            "human_gate_required": True,
            "created_at": now_ist().isoformat(),
            **proposal,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        return path

    def list_pending_proposals(self) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []
        for path in sorted(self.proposals_dir.glob("*.json")):
            if path.parent.name == "applied":
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                status = data.get("status", "pending_review")
                if status in {"pending", "pending_review", "proposed"}:
                    data["proposal_id"] = data.get("id") or data.get("proposal_id") or path.stem
                    data["_path"] = str(path)
                    proposals.append(data)
            except Exception as exc:
                logger.debug("Skip proposal %s: %s", path, exc)
        return proposals

    def apply_improvement_proposal(
        self,
        proposal_id: str,
        human_confirmed: bool,
    ) -> Dict[str, Any]:
        """
        Human-gated manifest only — NEVER modifies code or risk config.
        Returns read-only preview of what WOULD change.
        """
        preview = self._preview_for_proposal(proposal_id)
        result: Dict[str, Any] = {
            "proposal_id": proposal_id,
            "human_confirmed": human_confirmed,
            "human_gate_required": True,
            "preview": preview,
            "applied": False,
            "manifest_path": None,
        }

        if not human_confirmed:
            result["reason"] = "human_confirmation_required"
            result["message"] = "Founder must confirm before recording an applied manifest."
            return result

        proposal_path = self._resolve_proposal_path(proposal_id)
        proposal_snapshot: Dict[str, Any] = {"id": proposal_id, "status": "synthetic"}
        if proposal_path and proposal_path.exists():
            try:
                with proposal_path.open("r", encoding="utf-8") as handle:
                    proposal_snapshot = json.load(handle)
            except Exception as exc:
                result["reason"] = f"read_failed: {exc}"
                return result

        if proposal_snapshot.get("status") == "applied":
            result["reason"] = "already_applied"
            result["applied_at"] = proposal_snapshot.get("applied_at")
            return result

        manifest = {
            "proposal_id": proposal_id,
            "applied_at": now_ist().isoformat(),
            "applied_by": "founder_human_gate",
            "proposal_snapshot": proposal_snapshot,
            "preview": preview,
            "manifest_type": "improvement_proposal_approval",
            "note": (
                "Manifest only — no automatic code/risk changes. "
                "Implement via /implement or manual merge after review."
            ),
        }
        manifest_path = self.applied_dir / f"{proposal_id}_{int(time.time())}.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, default=str)

        if proposal_path and proposal_path.exists():
            proposal_snapshot["status"] = "applied"
            proposal_snapshot["applied_at"] = manifest["applied_at"]
            proposal_snapshot["manifest_path"] = str(manifest_path)
            with proposal_path.open("w", encoding="utf-8") as handle:
                json.dump(proposal_snapshot, handle, indent=2, default=str)

        result.update({
            "applied": True,
            "manifest_path": str(manifest_path),
            "message": "Manifest recorded. Implement changes manually after review.",
        })
        return result

    def apply_proposal_manifest(
        self,
        proposal_id: str,
        *,
        human_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Backward-compatible wrapper for dashboard POST handler."""
        outcome = self.apply_improvement_proposal(proposal_id, human_confirmed)
        if outcome.get("applied"):
            return {
                "success": True,
                "proposal_id": proposal_id,
                "manifest_path": outcome.get("manifest_path"),
                "message": outcome.get("message"),
                "preview": outcome.get("preview"),
            }
        return {
            "success": False,
            "error": outcome.get("reason", "human_confirmation_required"),
            "message": outcome.get("message"),
            "preview": outcome.get("preview"),
        }

    def get_latest_fill_learning(self) -> Dict[str, Any]:
        try:
            from app.fill_learning import fill_learning_store

            latest = fill_learning_store.load_latest()
            if latest:
                return latest
        except Exception as exc:
            logger.debug("Fill learning load failed: %s", exc)

        return {
            "fills": [],
            "summary": {"fills_analyzed": 0},
            "documentation_notes": ["No fill learning snapshot yet. Run paper/live session or refresh."],
            "source": "none",
        }

    def get_improvement_snapshot(self) -> Dict[str, Any]:
        """Compact dashboard view — latest earn report + pending proposals."""
        report = self.load_latest_weekly_report() or self.build_weekly_earn_report()
        pending = self.list_pending_proposals()
        failure = self._build_failure_patterns_pending()
        return {
            "week_label": report.get("week_label"),
            "session_summary": report.get("session_summary"),
            "wfa_summary": report.get("wfa_summary"),
            "fill_calibration": report.get("fill_calibration"),
            "failure_patterns_pending": failure,
            "pending_proposal_count": len(pending),
            "improvement_proposals": report.get("improvement_proposals", [])[:6],
            "founder_actions": report.get("founder_actions", [])[:5],
            "documentation_notes": report.get("documentation_notes", [])[:6],
        }

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _load_session_reports(self, days: int) -> List[Dict[str, Any]]:
        reports: List[Dict[str, Any]] = []
        try:
            from app.session_tracker import session_tracker

            reports = session_tracker.list_reports_since(days=days)
        except Exception as exc:
            logger.debug("session_tracker unavailable: %s", exc)

        if reports:
            return reports

        # Stub reads from data/sessions/ when tracker store is empty
        self.sessions_stub_dir.mkdir(parents=True, exist_ok=True)
        cutoff = now_ist().date() - timedelta(days=days)
        for path in sorted(self.sessions_stub_dir.glob("*.json")):
            try:
                report_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
                if report_date < cutoff:
                    continue
                with path.open("r", encoding="utf-8") as handle:
                    reports.append(json.load(handle))
            except Exception:
                continue
        return reports

    def _build_wfa_summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "indices": {},
            "memory_runs": 0,
            "memory_trades": 0,
            "recent_runs": [],
        }

        try:
            from backtesting.backtest_memory import backtest_memory

            insights = backtest_memory.generate_insights()
            summary["memory_runs"] = insights.get("total_runs_analyzed", 0)
            summary["memory_trades"] = insights.get("total_trades_observed", 0)
            summary["memory_notes"] = (insights.get("documentation_notes") or [])[:3]

            runs = backtest_memory.get_all_runs(limit=30)
            for run in runs:
                overall = run.get("overall") or run
                underlying = str(overall.get("underlying", "")).upper()
                avg_pf = overall.get("avg_pf")
                summary["recent_runs"].append({
                    "run_id": run.get("run_id"),
                    "underlying": underlying,
                    "avg_pf": avg_pf,
                    "avg_return": overall.get("avg_return"),
                    "wfo_mode": overall.get("wfo_mode"),
                    "timestamp": run.get("timestamp"),
                })
                if len(summary["recent_runs"]) >= 12:
                    break
        except Exception as exc:
            summary["memory_error"] = str(exc)

        try:
            from app.intelligence_loop import intelligence_loop

            for idx in INDICES:
                promo = intelligence_loop._get_promotion_for(idx)
                runs_for_idx = [
                    r for r in summary.get("recent_runs", [])
                    if r.get("underlying") == idx and r.get("avg_pf") is not None
                ]
                pf_values = [float(r["avg_pf"]) for r in runs_for_idx[:5]]
                avg_pf = round(sum(pf_values) / len(pf_values), 3) if pf_values else None
                summary["indices"][idx] = {
                    "runs": len(runs_for_idx),
                    "avg_pf": avg_pf,
                    "promotion_status": (promo or {}).get("status", "no_record"),
                    "passed": bool(promo and promo.get("passed")),
                    "fold_pass_count": (promo or {}).get("fold_pass_count"),
                }
        except Exception as exc:
            summary["promotion_error"] = str(exc)

        for idx in INDICES:
            summary["indices"].setdefault(idx, {
                "runs": 0,
                "avg_pf": None,
                "promotion_status": "no_record",
                "passed": False,
            })

        return summary

    def _build_fill_calibration(self) -> Dict[str, Any]:
        latest = self.get_latest_fill_learning()
        summary = latest.get("summary") or {}
        fills_analyzed = int(summary.get("fills_analyzed", 0) or 0)
        model_slippage = 4.0
        notes = list(latest.get("documentation_notes") or [])

        cost_premium_pct: Optional[float] = None
        observed_premium = summary.get("cost_premium_pct") or latest.get("cost_premium_pct")
        if observed_premium is not None:
            cost_premium_pct = float(observed_premium)
        elif fills_analyzed >= _LOW_CONFIDENCE_FILL_COUNT:
            baseline = float(summary.get("avg_est_cost_per_fill") or 0)
            model_baseline = float(summary.get("model_baseline_cost_per_fill") or baseline * 0.87 or 0)
            if model_baseline > 0 and baseline > 0:
                cost_premium_pct = round((baseline - model_baseline) / model_baseline * 100, 1)

        if fills_analyzed < _LOW_CONFIDENCE_FILL_COUNT:
            notes.append(
                f"[CALIBRATION] Small fill sample ({fills_analyzed} < {_LOW_CONFIDENCE_FILL_COUNT}) — "
                "cost premium estimate low confidence."
            )

        return {
            "fills_analyzed": fills_analyzed,
            "model_default_slippage_points": model_slippage,
            "est_total_cost_rs": summary.get("est_total_cost_rs"),
            "cost_premium_pct": cost_premium_pct,
            "has_snapshot": fills_analyzed > 0,
            "notes": notes[:5],
            "source": latest.get("source", "none"),
        }

    def _build_failure_patterns_pending(self) -> Dict[str, Any]:
        pending: List[Dict[str, Any]] = []
        if self.failure_proposals_dir.exists():
            for path in sorted(self.failure_proposals_dir.glob("proposal_*.json")):
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if data.get("status", "pending_review") in {"pending", "pending_review", "proposed"}:
                        pending.append(data)
                except Exception:
                    continue

        rule_ids = [
            str(p.get("rule_id") or p.get("id") or "unknown")
            for p in pending
        ]
        return {
            "count": len(pending),
            "top_rule_ids": rule_ids[:3],
            "directory": str(self.failure_proposals_dir),
        }

    def _build_session_summary(
        self,
        session_reports: List[Dict[str, Any]],
        *,
        days: int,
    ) -> Dict[str, Any]:
        scores = [
            float(r.get("quality_score", 0))
            for r in session_reports
            if r.get("quality_score") is not None
        ]
        avg_quality = round(sum(scores) / len(scores), 1) if scores else None

        total_pnl = sum(
            float((r.get("risk_snapshot") or {}).get("daily_pnl", 0) or 0)
            for r in session_reports
        )
        total_trades = sum(
            int((r.get("risk_snapshot") or {}).get("trades_today", 0) or 0)
            for r in session_reports
        )

        quality_trend = "stable"
        if len(scores) >= 2:
            first_half = scores[: len(scores) // 2] or scores[:1]
            second_half = scores[len(scores) // 2 :] or scores[-1:]
            if sum(second_half) / len(second_half) > sum(first_half) / len(first_half) + 3:
                quality_trend = "improving"
            elif sum(second_half) / len(second_half) < sum(first_half) / len(first_half) - 3:
                quality_trend = "declining"

        recon_halts = self._count_recon_halts(days=days)

        return {
            "reports_found": len(session_reports),
            "avg_quality_score": avg_quality,
            "quality_trend": quality_trend,
            "total_pnl_rs": round(total_pnl, 2),
            "total_trades": total_trades,
            "recon_halts": recon_halts,
            "daily_scores": [
                {
                    "date": r.get("date_ist"),
                    "score": r.get("quality_score"),
                    "grade": r.get("quality_grade"),
                    "pnl": (r.get("risk_snapshot") or {}).get("daily_pnl"),
                }
                for r in session_reports
            ],
        }

    def _build_documentation_notes(
        self,
        sessions: List[Dict[str, Any]],
        wfa: Dict[str, Any],
        fill: Dict[str, Any],
        failure: Dict[str, Any],
    ) -> List[str]:
        notes: List[str] = []

        if not sessions:
            notes.append(
                "Falsifiable: zero session reports this period — cannot infer live discipline; "
                "run fo-daily-review after each session."
            )
        elif len(sessions) < _SMALL_SAMPLE_SESSION_DAYS:
            notes.append(
                f"Small session sample ({len(sessions)} days < {_SMALL_SAMPLE_SESSION_DAYS}) — "
                "quality trend and PnL aggregates are low confidence."
            )
        else:
            avg_q = (sessions and sum(float(s.get("quality_score", 0)) for s in sessions) / len(sessions))
            notes.append(
                f"Session discipline sample: {len(sessions)} days, avg quality {avg_q:.1f}/100 — "
                "review before sizing up."
            )

        memory_runs = int(wfa.get("memory_runs") or 0)
        if memory_runs < 3:
            notes.append(
                f"WFA memory thin ({memory_runs} runs) — promotion status not statistically robust."
            )
        else:
            promoted = [k for k, v in (wfa.get("indices") or {}).items() if v.get("passed")]
            if promoted:
                notes.append(f"Promotion passed for {', '.join(promoted)} — still requires paper reconciliation.")
            else:
                notes.append("No index passed promotion — falsifiable: edge unproven for deployment.")

        if fill.get("has_snapshot"):
            premium = fill.get("cost_premium_pct")
            if premium is None:
                notes.append(
                    f"Fill calibration: {fill.get('fills_analyzed')} fills — "
                    "insufficient data to claim model vs broker cost match."
                )
            else:
                notes.append(
                    f"Fill cost premium vs model: {premium}% "
                    f"(n={fill.get('fills_analyzed')}) — verify on broker statement."
                )

        if failure.get("count", 0) > 0:
            notes.append(
                f"{failure['count']} failure-pattern proposal(s) pending human review "
                f"(top: {', '.join(failure.get('top_rule_ids') or [])})."
            )

        return notes[:8]

    def _build_founder_actions(
        self,
        report: Dict[str, Any],
        proposals: List[Dict[str, Any]],
    ) -> List[str]:
        actions = [
            "Review auto-generated improvement proposals (human gate required).",
            "Run fo-safe-deploy checklist before any live or micro-live session.",
            "Check micro-live status: MICRO_LIVE_ENABLED + MICRO_LIVE_CONFIRMED + promotion.",
        ]

        pending_failure = (report.get("failure_patterns_pending") or {}).get("count", 0)
        if pending_failure:
            actions.append(
                f"Review {pending_failure} failure-pattern proposal(s) in knowledge_base/proposals/."
            )

        if proposals:
            actions.insert(0, f"Found {len(proposals)} new improvement proposal(s) — approve or reject each.")

        session = report.get("session_summary") or {}
        if session.get("recon_halts", 0) > 0:
            actions.append("Audit broker sync — reconciliation halt(s) detected this week.")

        low_scores = [
            d for d in session.get("daily_scores", [])
            if d.get("score") is not None and float(d["score"]) < 60
        ]
        if low_scores:
            actions.append(f"{len(low_scores)} session(s) scored below 60 — read daily reports.")

        return actions[:6]

    def _count_recon_halts(self, days: int) -> int:
        if not self.audit_path.exists():
            return 0
        cutoff = time.time() - days * 86400
        count = 0
        try:
            with self.audit_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if event.get("event_type") != "recon.mismatch_halt":
                        continue
                    if float(event.get("ts", 0)) >= cutoff:
                        count += 1
        except Exception:
            return 0
        return count

    def _make_proposal(
        self,
        *,
        proposal_id: str,
        severity: str,
        target: str,
        description: str,
    ) -> Dict[str, Any]:
        return {
            "id": proposal_id,
            "severity": severity,
            "target": target,
            "description": description,
            "human_gate_required": True,
            "status": "pending_review",
        }

    def _preview_for_proposal(self, proposal_id: str) -> Dict[str, Any]:
        base = _PROPOSAL_PREVIEWS.get(proposal_id, {
            "target": "unknown",
            "would_change": "Manual founder review required.",
            "auto_apply": False,
        })
        return {**base, "proposal_id": proposal_id}

    def _resolve_proposal_path(self, proposal_id: str) -> Optional[Path]:
        for path in self.proposals_dir.glob("*.json"):
            if path.parent.name == "applied":
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if data.get("id") == proposal_id or data.get("proposal_id") == proposal_id:
                    return path
            except Exception:
                continue
        return None

    @staticmethod
    def _iso_week_label(day) -> str:
        iso = day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"


improvement_loop = ImprovementLoop()