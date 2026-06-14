"""
Trading journal — session performance, system feedback, trader notes.

Combines session quality, trade ledger, overnight/macro context into one
reviewable record per trading day.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOURNAL_DIR = PROJECT_ROOT / "data" / "trading_journal"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


class TradingJournal:
    def __init__(self, journal_dir: Path = JOURNAL_DIR):
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, date_ist: str) -> Path:
        return self.journal_dir / f"{date_ist}.json"

    def build_journal_entry(self, date_ist: Optional[str] = None) -> Dict[str, Any]:
        """Assemble full journal for a session date."""
        target = date_ist or now_ist().strftime("%Y-%m-%d")

        from .session_tracker import session_tracker

        session_report = session_tracker.load_daily_session_report(target)
        if session_report is None:
            session_report = session_tracker.build_daily_session_report(target)

        trades = self._extract_closed_trades(target)
        overnight = self._load_overnight()
        macro = self._load_macro_from_brief(target)
        feedback = self.generate_system_feedback(session_report, trades, overnight, macro)

        existing = self.load_journal(target) or {}
        trader_notes = existing.get("trader_notes") or []

        entry: Dict[str, Any] = {
            "date_ist": target,
            "generated_at": now_ist().isoformat(),
            "session_summary": {
                "quality_score": session_report.get("quality_score") or session_report.get("session_quality"),
                "quality_grade": session_report.get("quality_grade"),
                "quality_components": session_report.get("quality_components"),
                "event_metrics": session_report.get("event_metrics"),
                "risk_snapshot": session_report.get("risk_snapshot"),
                "indices": session_report.get("indices"),
            },
            "trades": trades,
            "trade_count": len(trades),
            "overnight_context": overnight,
            "macro_context": macro,
            "system_feedback": feedback,
            "feedback_summary": feedback.get("headline", ""),
            "improvement_actions": feedback.get("actions", []),
            "trader_notes": trader_notes,
            "founder_actions": session_report.get("founder_actions") or feedback.get("actions", []),
        }
        return entry

    def generate_system_feedback(
        self,
        session: Dict[str, Any],
        trades: List[Dict[str, Any]],
        overnight: Optional[Dict[str, Any]],
        macro: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Deterministic coach notes — no LLM."""
        notes: List[str] = []
        actions: List[str] = []
        score = float(session.get("quality_score") or session.get("session_quality") or 0)
        risk = session.get("risk_snapshot") or {}
        metrics = session.get("event_metrics") or {}
        pnl = float(risk.get("daily_pnl", 0) or 0)

        if score >= 85:
            headline = "Strong session discipline — process held."
            notes.append("Risk gates and trade frequency were well controlled.")
        elif score >= 70:
            headline = "Acceptable session — minor refinements possible."
        elif score >= 55:
            headline = "Mixed session — review rejections and sizing."
            actions.append("Read top rejection reasons before next open.")
        else:
            headline = "Weak session — pause and review before adding risk."
            actions.append("Run fo-safe-deploy before next session.")

        if pnl > 0:
            notes.append(f"Green day (+₹{pnl:,.0f}) — protect gains; don't revenge-size tomorrow.")
        elif pnl < -5000:
            notes.append(f"Red day (₹{pnl:,.0f}) — defensive posture mandatory next session.")
            actions.append("Cut max trades by 1 until rolling edge recovers.")

        rej_rate = float(metrics.get("rejection_rate", 0) or 0)
        if rej_rate > 0.25:
            notes.append(f"High gate rejection rate ({rej_rate:.0%}) — market may have been choppy or params tight.")
        elif rej_rate < 0.05 and int(metrics.get("orders_placed", 0) or 0) > 5:
            notes.append("Very few rejections with many orders — verify caps weren't bypassed.")

        winners = sum(1 for t in trades if float(t.get("realized_pnl", 0) or 0) > 0)
        losers = sum(1 for t in trades if float(t.get("realized_pnl", 0) or 0) < 0)
        if trades:
            notes.append(f"Closed trades: {len(trades)} ({winners}W / {losers}L).")
            if losers > winners and pnl < 0:
                actions.append("Review exit reasons in ledger — chop profit defense may need tuning.")

        oh = overnight or {}
        hints = oh.get("session_hints") or {}
        if hints.get("posture_floor") in ("defensive", "contingency"):
            notes.append(f"Overnight gap regime: {oh.get('NIFTY', {}).get('gap_regime', 'n/a')} — open with wider buffers.")
        vix = (macro or {}).get("vix") or {}
        if vix.get("zone") in ("elevated", "extreme"):
            notes.append(f"India VIX {vix.get('level')} ({vix.get('zone')}) — reduce size, favor defense.")
            actions.append("No aggressive posture until VIX normalizes.")
        fii = (macro or {}).get("fii_dii") or {}
        if fii.get("flow_bias") == "fii_selling":
            notes.append(f"FII net selling ₹{fii.get('fii_net_crores')} Cr — fade breakout longs cautiously.")

        if not actions:
            actions.append("Continue weekly earn report cadence.")

        return {
            "headline": headline,
            "score_context": f"Session quality {score:.0f}/100",
            "notes": notes[:8],
            "actions": actions[:5],
        }

    def _extract_closed_trades(self, date_ist: str) -> List[Dict[str, Any]]:
        try:
            from .trade_ledger import trade_ledger
            target = datetime.strptime(date_ist, "%Y-%m-%d").date()
            trades: List[Dict[str, Any]] = []
            for event in trade_ledger.tail(500):
                if event.get("event_type") not in ("trade.closed", "order.exit"):
                    continue
                ts = event.get("ts")
                if ts is None:
                    continue
                try:
                    if datetime.fromtimestamp(float(ts)).date() != target:
                        continue
                except Exception:
                    continue
                payload = event.get("payload") or {}
                trades.append({
                    "symbol": payload.get("symbol"),
                    "side": payload.get("transaction_type") or payload.get("side"),
                    "quantity": payload.get("quantity"),
                    "realized_pnl": payload.get("realized_pnl") or payload.get("net_pnl"),
                    "exit_reason": payload.get("exit_reason") or payload.get("reason"),
                    "ts": ts,
                })
            return trades
        except Exception as exc:
            logger.debug("Trade extract failed: %s", exc)
            return []

    def _load_overnight(self) -> Optional[Dict[str, Any]]:
        try:
            from .overnight_context import load_overnight_context
            return load_overnight_context()
        except Exception:
            return None

    def _load_macro_from_brief(self, date_ist: str) -> Optional[Dict[str, Any]]:
        brief_path = PROJECT_ROOT / "data" / "briefs" / f"{date_ist}.json"
        if brief_path.exists():
            try:
                brief = json.loads(brief_path.read_text(encoding="utf-8"))
                return brief.get("macro_context")
            except Exception:
                pass
        try:
            from .nse_data import fetch_macro_context
            return fetch_macro_context()
        except Exception:
            return None

    def save_journal(self, entry: Dict[str, Any]) -> Path:
        date_str = entry.get("date_ist") or now_ist().strftime("%Y-%m-%d")
        path = self._path_for(date_str)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(entry, handle, indent=2, default=str)
        logger.info("Trading journal saved: %s", path)
        return path

    def load_journal(self, date_ist: str) -> Optional[Dict[str, Any]]:
        path = self._path_for(date_ist)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def add_trader_note(self, note: str, date_ist: Optional[str] = None) -> Dict[str, Any]:
        """Append a trader note to today's journal."""
        target = date_ist or now_ist().strftime("%Y-%m-%d")
        entry = self.load_journal(target) or self.build_journal_entry(target)
        notes: List[Dict[str, Any]] = list(entry.get("trader_notes") or [])
        notes.append({
            "text": note.strip(),
            "added_at": now_ist().isoformat(),
        })
        entry["trader_notes"] = notes[-20:]
        self.save_journal(entry)
        return entry

    def list_journals(self, limit: int = 30) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for path in sorted(self.journal_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                summaries.append({
                    "date_ist": data.get("date_ist"),
                    "quality_score": (data.get("session_summary") or {}).get("quality_score"),
                    "daily_pnl": ((data.get("session_summary") or {}).get("risk_snapshot") or {}).get("daily_pnl"),
                    "trade_count": data.get("trade_count", 0),
                    "feedback_summary": data.get("feedback_summary", ""),
                    "note_count": len(data.get("trader_notes") or []),
                })
            except Exception:
                continue
        return summaries

    def build_and_save(self, date_ist: Optional[str] = None) -> Path:
        entry = self.build_journal_entry(date_ist)
        return self.save_journal(entry)


trading_journal = TradingJournal()