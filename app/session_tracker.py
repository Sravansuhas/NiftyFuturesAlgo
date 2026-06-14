"""
Daily session quality tracking — Phase 4 continuous self-improvement.

Principal-trader rules:
- Reports are deterministic and read-only on trading logic.
- Quality score reflects discipline + risk adherence, not P&L chasing.
- All outputs persisted under data/session_reports/ for weekly aggregation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_REPORTS_DIR = PROJECT_ROOT / "data" / "session_reports"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

_TRADE_EVENT_TYPES = frozenset({
    "order.placed",
    "order.exit",
    "order.pending_fill",
    "order.live_fill_multi",
    "kite.postback",
})


def _compute_session_quality(blockers: int, halts: int) -> int:
    """Principal-trader heuristic: fewer blocks/halts → higher score."""
    score = 100
    score -= min(45, blockers * 12)
    score -= min(40, halts * 25)
    return max(0, score)


class SessionTracker:
    """Builds and persists daily session quality reports from ledger + risk state."""

    def __init__(
        self,
        reports_dir: Path = SESSION_REPORTS_DIR,
        sessions_dir: Optional[Path] = None,
        ledger_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ):
        self.reports_dir = Path(sessions_dir or reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.audit_path = Path(audit_path) if audit_path else None

    def build_daily_session_report(self, date_ist: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate today's (or given date) session events into a quality report."""
        if date_ist:
            target_date = datetime.strptime(date_ist, "%Y-%m-%d").date()
        else:
            target_date = now_ist().date()

        if self.ledger_path is not None:
            return self._build_ledger_audit_report(target_date)

        events = self._load_events_for_date(target_date)
        metrics = self._compute_event_metrics(events)
        risk = self._get_risk_snapshot()
        quality = self._compute_quality_score(metrics, risk)

        report: Dict[str, Any] = {
            "date_ist": target_date.isoformat(),
            "generated_at": now_ist().isoformat(),
            "quality_score": quality["score"],
            "session_quality": quality["score"],
            "quality_grade": quality["grade"],
            "quality_components": quality["components"],
            "quality_notes": quality["notes"],
            "event_metrics": metrics,
            "risk_snapshot": risk,
            "indices": self._per_index_summary(events),
            "founder_actions": quality.get("founder_actions", []),
        }
        return report

    def _build_ledger_audit_report(self, target_date) -> Dict[str, Any]:
        """Rich report from injectable ledger + audit paths (Phase 4A tests / custom stores)."""
        ledger_events = self._read_jsonl_for_date(self.ledger_path, target_date)
        audit_events = self._read_jsonl_for_date(self.audit_path, target_date) if self.audit_path else []

        trades = {"placed": 0, "exited": 0, "pending_fill": 0, "live_fills": 0}
        halts: List[Dict[str, Any]] = []
        blockers = 0
        per_symbol_pnl: Dict[str, float] = {sym: 0.0 for sym in INDICES}

        for event in ledger_events:
            et = event.get("event_type", "")
            payload = event.get("payload") or {}
            if et == "order.placed":
                trades["placed"] += 1
            elif et == "order.exit":
                trades["exited"] += 1
            elif et == "order.pending_fill":
                trades["pending_fill"] += 1
            elif et in {"order.live_fill_multi", "kite.postback"}:
                trades["live_fills"] += 1
            elif et == "emergency.halt":
                halts.append({"type": "emergency", "payload": payload})

        for event in audit_events:
            et = event.get("event_type", "")
            payload = event.get("payload") or {}
            if et == "order.blocked":
                blockers += 1
            elif et == "recon.mismatch_halt":
                halts.append({"type": "recon_mismatch", "payload": payload})
            elif et == "order.paper_multi":
                sym = self._normalize_symbol(str(payload.get("symbol", "")))
                if sym and payload.get("is_exit") and payload.get("realized_pnl") is not None:
                    per_symbol_pnl[sym] = per_symbol_pnl.get(sym, 0.0) + float(payload["realized_pnl"])

        session_quality = _compute_session_quality(blockers, len(halts))
        import os

        return {
            "date_ist": target_date.isoformat(),
            "generated_at": now_ist().isoformat(),
            "session_quality": session_quality,
            "quality_score": session_quality,
            "trades": trades,
            "blockers": {"count": blockers},
            "halts": halts,
            "per_symbol_pnl": per_symbol_pnl,
            "mode": "paper" if os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no") else "live",
        }

    def _read_jsonl_for_date(self, path: Optional[Path], target_date) -> List[Dict[str, Any]]:
        if path is None or not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            ts = event.get("ts")
            if ts is None:
                continue
            try:
                event_date = datetime.fromtimestamp(float(ts)).date()
            except Exception:
                continue
            if event_date == target_date:
                events.append(event)
        return events

    def save_daily_session_report(self, report: Dict[str, Any]) -> Path:
        date_str = report.get("date_ist") or now_ist().strftime("%Y-%m-%d")
        path = self.reports_dir / f"{date_str}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        logger.info("[SESSION] Daily report saved: %s (score=%s)", path, report.get("quality_score"))
        return path

    def load_daily_session_report(self, date_ist: str) -> Optional[Dict[str, Any]]:
        path = self.reports_dir / f"{date_ist}.json"
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.debug("Failed to load session report %s: %s", date_ist, exc)
            return None

    def save_daily_report(self, report: Dict[str, Any]) -> Path:
        return self.save_daily_session_report(report)

    def load_daily_report(self, date_ist: str) -> Optional[Dict[str, Any]]:
        return self.load_daily_session_report(date_ist)

    def get_session_streak(self) -> Dict[str, Any]:
        """Consecutive days with saved reports + last 7 days summary."""
        today = now_ist().date()
        last_7: List[Dict[str, Any]] = []
        consecutive = 0
        counting = True

        for offset in range(7):
            day = today - timedelta(days=offset)
            day_str = day.isoformat()
            report = self.load_daily_session_report(day_str)
            entry = {
                "date_ist": day_str,
                "has_report": report is not None,
                "session_quality": (report or {}).get("session_quality") or (report or {}).get("quality_score"),
            }
            last_7.append(entry)
            if counting:
                if report is not None:
                    consecutive += 1
                else:
                    counting = False

        return {
            "consecutive_days_with_reports": consecutive,
            "last_7_days": last_7,
        }

    def load_latest_daily_report(self) -> Optional[Dict[str, Any]]:
        files = sorted(self.reports_dir.glob("*.json"), reverse=True)
        for path in files:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                continue
        return None

    def list_reports_since(self, days: int = 7) -> List[Dict[str, Any]]:
        cutoff = now_ist().date() - timedelta(days=days)
        reports: List[Dict[str, Any]] = []
        for path in sorted(self.reports_dir.glob("*.json")):
            try:
                date_part = path.stem
                report_date = datetime.strptime(date_part, "%Y-%m-%d").date()
                if report_date >= cutoff:
                    with path.open("r", encoding="utf-8") as handle:
                        reports.append(json.load(handle))
            except Exception:
                continue
        return reports

    def _load_events_for_date(self, target_date) -> List[Dict[str, Any]]:
        ledger_path = self.ledger_path
        if ledger_path is None:
            try:
                from app.trade_ledger import trade_ledger

                ledger_path = trade_ledger.path
            except Exception:
                return []

        return self._read_jsonl_for_date(ledger_path, target_date)

    def _compute_event_metrics(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        accepted = sum(1 for e in events if e.get("event_type") == "signal.accepted")
        rejected = sum(1 for e in events if e.get("event_type") == "signal.rejected")
        placed = sum(1 for e in events if e.get("event_type") == "order.placed")
        exits = sum(1 for e in events if e.get("event_type") == "order.exit")
        total_signals = accepted + rejected
        rejection_rate = round(rejected / max(1, total_signals), 3)

        rejection_reasons: Dict[str, int] = {}
        for event in events:
            if event.get("event_type") != "signal.rejected":
                continue
            reason = (event.get("payload") or {}).get("reason") or "unknown"
            key = str(reason)[:80]
            rejection_reasons[key] = rejection_reasons.get(key, 0) + 1

        return {
            "total_events": len(events),
            "signals_accepted": accepted,
            "signals_rejected": rejected,
            "orders_placed": placed,
            "orders_exited": exits,
            "rejection_rate": rejection_rate,
            "top_rejection_reasons": sorted(
                rejection_reasons.items(), key=lambda x: x[1], reverse=True
            )[:5],
        }

    def _get_risk_snapshot(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "daily_pnl": 0.0,
            "daily_loss": 0.0,
            "trades_today": 0,
            "max_drawdown_pct": 0.0,
            "within_daily_loss_limit": True,
            "per_symbol": {},
        }
        try:
            from app.risk_gatekeeper import risk_gatekeeper

            cfg = risk_gatekeeper.config
            snapshot["daily_pnl"] = round(risk_gatekeeper.daily_pnl, 2)
            snapshot["daily_loss"] = round(risk_gatekeeper.daily_loss, 2)
            snapshot["trades_today"] = risk_gatekeeper.trades_today
            snapshot["max_drawdown_pct"] = round(risk_gatekeeper._current_drawdown_pct() * 100, 2)
            max_loss_rs = risk_gatekeeper.capital * cfg.max_daily_loss_pct
            snapshot["within_daily_loss_limit"] = risk_gatekeeper.daily_loss <= max_loss_rs
            snapshot["capital"] = risk_gatekeeper.capital
            snapshot["max_daily_loss_rs"] = round(max_loss_rs, 2)
        except Exception as exc:
            logger.debug("Risk snapshot partial: %s", exc)

        try:
            from app.multi_symbol_risk import multi_risk_manager

            snapshot["daily_pnl"] = round(multi_risk_manager.daily_pnl, 2)
            snapshot["daily_loss"] = round(multi_risk_manager.daily_loss, 2)
            snapshot["trades_today"] = multi_risk_manager.trades_today
            snapshot["max_drawdown_pct"] = round(multi_risk_manager._current_drawdown_pct() * 100, 2)
            for sym in INDICES:
                pos = multi_risk_manager.get_position(sym)
                snapshot["per_symbol"][sym] = {
                    "quantity": pos.quantity,
                    "daily_pnl": round(getattr(pos, "daily_pnl", 0) or 0, 2),
                    "daily_trades": getattr(pos, "daily_trades", 0) or 0,
                }
        except Exception:
            pass

        return snapshot

    def _per_index_summary(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {sym: {"accepted": 0, "rejected": 0, "placed": 0} for sym in INDICES}
        for event in events:
            payload = event.get("payload") or {}
            raw_sym = str(payload.get("symbol") or "").upper()
            sym = self._normalize_symbol(raw_sym)
            if sym not in summary:
                continue
            et = event.get("event_type", "")
            if et == "signal.accepted":
                summary[sym]["accepted"] += 1
            elif et == "signal.rejected":
                summary[sym]["rejected"] += 1
            elif et == "order.placed":
                summary[sym]["placed"] += 1
        return summary

    def _normalize_symbol(self, raw: str) -> Optional[str]:
        if not raw:
            return None
        if "BANKNIFTY" in raw:
            return "BANKNIFTY"
        if "SENSEX" in raw:
            return "SENSEX"
        if "NIFTY" in raw:
            return "NIFTY"
        return None

    def _compute_quality_score(
        self,
        metrics: Dict[str, Any],
        risk: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Score 0–100: discipline + risk adherence weighted over raw P&L."""
        notes: List[str] = []
        founder_actions: List[str] = []

        # Discipline: healthy rejection rate means gates are working
        rejection_rate = float(metrics.get("rejection_rate", 0))
        if rejection_rate >= 0.15:
            discipline = 85
            notes.append("Gates actively filtering signals (healthy rejection rate).")
        elif rejection_rate >= 0.05:
            discipline = 70
        else:
            discipline = 55
            if metrics.get("signals_accepted", 0) > 6:
                notes.append("Low rejection rate with many accepts — review overtrading.")
                founder_actions.append("Review trade frequency vs max_trades_per_day caps.")

        # Risk adherence
        risk_score = 90 if risk.get("within_daily_loss_limit", True) else 35
        if not risk.get("within_daily_loss_limit", True):
            notes.append("Daily loss limit breached or near breach.")
            founder_actions.append("Pause new entries until loss limit review complete.")

        dd = float(risk.get("max_drawdown_pct", 0))
        if dd > 2.0:
            risk_score = min(risk_score, 50)
            notes.append(f"Drawdown elevated ({dd:.1f}%).")
            founder_actions.append("Run fo-safe-deploy checklist before next session.")

        # Trade count discipline
        trades = int(risk.get("trades_today", 0) or metrics.get("orders_placed", 0))
        frequency_score = 80
        if trades > 9:
            frequency_score = 45
            notes.append(f"High trade count ({trades}) — check for overtrading.")
            founder_actions.append("Compare session trades against weekly earn report.")
        elif trades == 0:
            frequency_score = 65
            notes.append("No trades recorded — session may be idle or data sparse.")

        # P&L component (small weight — discipline matters more)
        pnl = float(risk.get("daily_pnl", 0))
        if pnl > 0:
            pnl_score = min(85, 60 + pnl / 500)
        elif pnl < -2000:
            pnl_score = 40
            founder_actions.append("Review losing session in daily review notes.")
        else:
            pnl_score = 55

        components = {
            "discipline": discipline,
            "risk_adherence": risk_score,
            "frequency": frequency_score,
            "pnl_context": round(pnl_score, 1),
        }
        score = round(
            discipline * 0.35 + risk_score * 0.35 + frequency_score * 0.20 + pnl_score * 0.10,
            1,
        )
        grade = (
            "A" if score >= 85 else
            "B" if score >= 70 else
            "C" if score >= 55 else
            "D"
        )

        if not founder_actions:
            founder_actions.append("No urgent actions — continue weekly earn report cadence.")

        return {
            "score": score,
            "grade": grade,
            "components": components,
            "notes": notes[:6],
            "founder_actions": founder_actions[:4],
        }


session_tracker = SessionTracker()