"""
Intelligence loop — Phase 2 closed learning layer.

Principal-trader rules:
- Learning outputs may only REDUCE risk or tighten posture (never bypass gates).
- Promotion candidates gate parameter trust; memory gates regime trust.
- All outputs are deterministic, auditable, and stored on disk.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIEFS_DIR = PROJECT_ROOT / "data" / "briefs"
PROPOSALS_DIR = PROJECT_ROOT / "data" / "knowledge_base" / "proposals"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

# Conservative de-risk factors (product capped at 1.0 — never leverage up from learning)
_UNVALIDATED_PARAMS_MULT = 0.85
_NEGATIVE_REGIME_MULT = 0.70
_LOW_CONFIDENCE_MEMORY_MULT = 0.90
_MIN_LEARNING_MULT = 0.35


class IntelligenceLoop:
    """Reads memory + promotion + calendar; produces briefs and risk adjustments."""

    def __init__(self, briefs_dir: Path = BRIEFS_DIR):
        self.briefs_dir = Path(briefs_dir)
        self.briefs_dir.mkdir(parents=True, exist_ok=True)

    def get_learning_risk_multiplier(
        self,
        underlying: str,
        volatility_regime: str,
    ) -> Tuple[float, List[str]]:
        """
        Session-level de-risk from validated memory + promotion state.

        Returns:
            multiplier in [_MIN_LEARNING_MULT, 1.0] — never > 1.0
            human-readable reasons for diagnostics
        """
        key = underlying.upper()
        mult = 1.0
        reasons: List[str] = []

        promo = self._get_promotion_for(key)
        if promo is None:
            mult *= _UNVALIDATED_PARAMS_MULT
            reasons.append(f"{key}: no promotion record — params unvalidated ({_UNVALIDATED_PARAMS_MULT}x)")
        elif promo.get("status") != "promoted" or not promo.get("passed", False):
            mult *= _UNVALIDATED_PARAMS_MULT
            reasons.append(f"{key}: promotion gate failed ({_UNVALIDATED_PARAMS_MULT}x)")

        regime_mult, regime_reason = self._regime_memory_adjustment(volatility_regime)
        if regime_mult < 1.0:
            mult *= regime_mult
            if regime_reason:
                reasons.append(regime_reason)

        mult = max(_MIN_LEARNING_MULT, min(1.0, mult))
        return round(mult, 3), reasons

    def _regime_memory_adjustment(self, volatility_regime: str) -> Tuple[float, str]:
        """De-risk when historical WFA memory shows negative expectancy in this regime."""
        try:
            from backtesting.backtest_memory import backtest_memory

            insights = backtest_memory.generate_insights(regime=volatility_regime)
            stats = insights.get("regime_statistics", {}).get(volatility_regime)
            if not stats:
                return 1.0, ""

            confidence = stats.get("confidence", "low")
            total_pnl = float(stats.get("total_pnl", 0) or 0)
            total_trades = int(stats.get("total_trades", 0) or 0)

            if total_trades < 8:
                return _LOW_CONFIDENCE_MEMORY_MULT, (
                    f"memory: thin sample in {volatility_regime} vol ({total_trades} trades) "
                    f"({_LOW_CONFIDENCE_MEMORY_MULT}x)"
                )

            if total_pnl < 0 and confidence in {"medium", "high"}:
                return _NEGATIVE_REGIME_MULT, (
                    f"memory: negative expectancy in {volatility_regime} vol "
                    f"(PnL {total_pnl:,.0f}, conf={confidence}) ({_NEGATIVE_REGIME_MULT}x)"
                )
        except Exception as exc:
            logger.debug(f"Regime memory adjustment skipped: {exc}")
        return 1.0, ""

    def _get_promotion_for(self, underlying: str) -> Optional[Dict[str, Any]]:
        try:
            from backtesting.promotion_gates import load_candidates

            for cand in load_candidates():
                if cand.get("underlying") == underlying.upper():
                    return cand
        except Exception as exc:
            logger.debug(f"Promotion lookup failed: {exc}")
        return None

    def build_market_brief(self) -> Dict[str, Any]:
        """Deterministic pre-market brief (no LLM)."""
        from app.market_calendar import (
            get_event_calendar_status,
            holiday_name,
            is_expiry_day,
            is_market_open,
            is_safe_trading_window,
            is_trading_holiday,
            next_trading_day,
            now_ist,
        )

        now = now_ist()
        today = now.date()
        expiry_map = {idx: is_expiry_day(today, underlying=idx) for idx in INDICES}
        brief: Dict[str, Any] = {
            "generated_at": now.isoformat(),
            "date_ist": now.strftime("%Y-%m-%d"),
            "session": {
                "market_open": is_market_open(now),
                "safe_trading_window": is_safe_trading_window(now),
                "is_expiry_day": any(expiry_map.values()),
                "expiry_by_index": expiry_map,
                "is_trading_holiday": is_trading_holiday(today),
                "holiday_name": holiday_name(today),
                "next_trading_day": next_trading_day(today).isoformat(),
            },
            "indices": {},
            "failure_patterns_active": [],
            "promotion_status": {},
            "memory_snapshot": {},
            "macro_context": {},
            "market_context": {},
            "overnight_context": {},
            "posture": {},
            "risk_notes": [],
        }

        try:
            from app.nse_data import fetch_macro_context
            brief["macro_context"] = fetch_macro_context()
        except Exception as exc:
            brief["risk_notes"].append(f"Macro context unavailable: {exc}")

        try:
            from app.market_context import build_market_context

            brief["market_context"] = build_market_context()
        except Exception as exc:
            brief["risk_notes"].append(f"Market context unavailable: {exc}")

        try:
            from app.overnight_context import build_overnight_context
            brief["overnight_context"] = build_overnight_context()
        except Exception as exc:
            brief["risk_notes"].append(f"Overnight context unavailable: {exc}")

        try:
            brief["event_calendar"] = get_event_calendar_status(now)
        except Exception:
            pass

        try:
            from backtesting.backtest_memory import backtest_memory

            insights = backtest_memory.generate_insights()
            brief["memory_snapshot"] = {
                "total_runs": insights.get("total_runs_analyzed", 0),
                "total_trades": insights.get("total_trades_observed", 0),
                "regime_statistics": insights.get("regime_statistics", {}),
                "top_notes": (insights.get("documentation_notes") or [])[:4],
            }
        except Exception as exc:
            brief["risk_notes"].append(f"Memory unavailable: {exc}")

        try:
            from app.fo_rules_engine import fo_rules_engine

            if fo_rules_engine is not None:
                tier1 = [r for r in fo_rules_engine.list_rules() if int(r.get("tier", 2)) == 1]
                brief["failure_patterns_active"] = [
                    {"rule_id": r["rule_id"], "description": r.get("description", "")[:120]}
                    for r in tier1[:6]
                ]
        except Exception as exc:
            brief["risk_notes"].append(f"FO rules unavailable: {exc}")

        for idx in INDICES:
            promo = self._get_promotion_for(idx)
            brief["promotion_status"][idx] = {
                "passed": bool(promo and promo.get("passed")),
                "status": (promo or {}).get("status", "unknown"),
                "fold_pass_count": (promo or {}).get("fold_pass_count"),
            }
            runs = self._latest_runs_for_index(idx, limit=3)
            brief["indices"][idx] = {
                "recent_wfa_runs": len(runs),
                "last_avg_return": runs[0].get("avg_return") if runs else None,
                "last_avg_pf": runs[0].get("avg_pf") if runs else None,
                "last_wfo_mode": runs[0].get("wfo_mode") if runs else None,
            }

        brief["posture"] = self._derive_session_posture(brief)
        return brief

    def _latest_runs_for_index(self, underlying: str, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            from backtesting.backtest_memory import backtest_memory

            runs = backtest_memory.get_all_runs(limit=50)
            matched = []
            for run in runs:
                overall = run.get("overall") or run
                if overall.get("underlying", "").upper() == underlying.upper():
                    matched.append({
                        "run_id": run.get("run_id"),
                        "timestamp": run.get("timestamp"),
                        "avg_return": overall.get("avg_return"),
                        "avg_pf": overall.get("avg_pf"),
                        "wfo_mode": overall.get("wfo_mode"),
                    })
                if len(matched) >= limit:
                    break
            return matched
        except Exception:
            return []

    def _derive_session_posture(self, brief: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from .regime_orchestrator import assess_session_posture

            return assess_session_posture(brief)
        except Exception as exc:
            logger.debug("Regime orchestrator posture fallback: %s", exc)
            return {
                "recommended_max_trades_per_day": 3,
                "breakout_buffer_bias": "normal",
                "caution_level": "standard",
                "watch_for": ["Regime orchestrator unavailable — using defaults"],
            }

    def format_brief_text(self, brief: Dict[str, Any]) -> str:
        """Human-readable brief for terminal / logs."""
        lines = [
            f"=== FO MARKET BRIEF — {brief.get('date_ist')} IST ===",
            "",
            "Session:",
            f"  Market open: {brief['session'].get('market_open')}",
            f"  Safe window: {brief['session'].get('safe_trading_window')}",
            f"  Expiry day: {brief['session'].get('is_expiry_day')}",
            "",
            "Regime / WFA snapshot:",
        ]
        for idx, info in brief.get("indices", {}).items():
            lines.append(
                f"  {idx}: runs={info.get('recent_wfa_runs')} "
                f"last_PF={info.get('last_avg_pf')} promo={brief['promotion_status'].get(idx, {}).get('status')}"
            )
        lines.extend(["", "Active failure-pattern blocks (tier 1):"])
        for fp in brief.get("failure_patterns_active", [])[:5]:
            lines.append(f"  - {fp['rule_id']}")
        posture = brief.get("posture", {})
        session = brief.get("session", {})
        if session.get("is_trading_holiday"):
            lines.extend([
                "",
                f"Holiday: {session.get('holiday_name')} — market closed.",
                f"  Next session: {session.get('next_trading_day')}",
            ])
        macro = brief.get("macro_context") or {}
        vix = macro.get("vix") or {}
        fii = macro.get("fii_dii") or {}
        if vix.get("available"):
            lines.extend([
                "",
                "India VIX:",
                f"  level={vix.get('level')} zone={vix.get('zone')} chg={vix.get('change_pct')}%",
            ])
        if fii.get("available"):
            lines.append(
                f"FII/DII: FII ₹{fii.get('fii_net_crores')} Cr | DII ₹{fii.get('dii_net_crores')} Cr "
                f"| bias={fii.get('flow_bias')}"
            )
        mc = brief.get("market_context") or {}
        mc_hints = mc.get("session_hints") or {}
        if mc.get("available") and mc_hints.get("open_bias"):
            lines.append(f"Open bias: {mc_hints.get('open_bias')} (floor={mc_hints.get('posture_floor')})")
        oh = brief.get("overnight_context") or {}
        nifty_oh = oh.get("NIFTY") or {}
        if oh.get("available"):
            lines.extend([
                "",
                "GIFT overnight:",
                f"  implied gap {nifty_oh.get('implied_gap_pct')}% ({nifty_oh.get('gap_regime')})",
                f"  hints: {oh.get('session_hints', {}).get('posture_floor')}",
            ])
        lines.extend([
            "",
            "Session posture:",
            f"  max_trades/day: {posture.get('recommended_max_trades_per_day')}",
            f"  breakout buffer: {posture.get('breakout_buffer_bias')}",
            f"  caution: {posture.get('caution_level')}",
        ])
        for note in brief.get("memory_snapshot", {}).get("top_notes", [])[:3]:
            lines.append(f"  Note: {note}")
        return "\n".join(lines)

    def save_market_brief(self, brief: Optional[Dict[str, Any]] = None) -> Path:
        brief = brief or self.build_market_brief()
        date_str = brief.get("date_ist") or datetime.now().strftime("%Y-%m-%d")
        path = self.briefs_dir / f"{date_str}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(brief, handle, indent=2, default=str)
        text_path = self.briefs_dir / f"{date_str}.txt"
        text_path.write_text(self.format_brief_text(brief), encoding="utf-8")
        logger.info("Market brief saved: %s", path)
        return path

    def get_improvement_snapshot(self) -> Dict[str, Any]:
        """Phase 4B weekly earn + proposals view for dashboard — read-only."""
        try:
            from app.improvement_loop import improvement_loop

            return improvement_loop.get_improvement_snapshot()
        except Exception as exc:
            logger.debug("Improvement snapshot unavailable: %s", exc)
            return {
                "error": str(exc),
                "pending_proposal_count": 0,
                "improvement_proposals": [],
                "founder_actions": ["Run scripts/fo_weekly_earn_report.py to build first earn report."],
                "documentation_notes": [],
            }

    def get_agent_insights(self) -> Dict[str, Any]:
        """Aggregated Phase 2 view for dashboard — read-only, auditable."""
        deploy = self.run_safe_deploy_checklist()
        brief = self.load_latest_brief() or self.build_market_brief()

        learning: Dict[str, Any] = {}
        for idx in INDICES:
            mult, reasons = self.get_learning_risk_multiplier(idx, "normal")
            learning[idx] = {"multiplier": mult, "reasons": reasons[:3]}

        promoted: Dict[str, Any] = {}
        try:
            from app.promoted_params import preview_promoted_overlay

            for idx in INDICES:
                promoted[idx] = preview_promoted_overlay(idx)
        except Exception as exc:
            promoted = {"error": str(exc)}

        return {
            "deploy_ready": deploy.get("ready"),
            "deploy_mode": deploy.get("mode"),
            "blockers": deploy.get("blockers", []),
            "warnings": deploy.get("warnings", [])[:8],
            "brief_date": brief.get("date_ist"),
            "session": brief.get("session", {}),
            "posture": brief.get("posture", {}),
            "promotion_status": brief.get("promotion_status", {}),
            "learning_multipliers": learning,
            "promoted_params": promoted,
            "failure_patterns": brief.get("failure_patterns_active", [])[:5],
            "memory_runs": brief.get("memory_snapshot", {}).get("total_runs"),
        }

    def load_latest_brief(self) -> Optional[Dict[str, Any]]:
        files = sorted(self.briefs_dir.glob("*.json"), reverse=True)
        if not files:
            return None
        try:
            with files[0].open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def run_safe_deploy_checklist(self, *, include_micro_live_gate: bool = True) -> Dict[str, Any]:
        """
        Pre-deployment checklist — fail-closed for live; informative for paper.
        """
        from app.state_machine import state_machine, SystemState

        checks: List[Dict[str, Any]] = []
        blockers: List[str] = []
        warnings: List[str] = []

        force_dry = os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no")

        state = state_machine.get_state()
        halt_states = {
            SystemState.EMERGENCY_HALT,
            SystemState.BROKER_DISCONNECTED,
            SystemState.RECONCILIATION_FAILED,
            SystemState.TRADING_DISABLED,
            SystemState.CIRCUIT_BREAKER_TRIGGERED,
        }
        if state in halt_states:
            checks.append({
                "id": "state_machine",
                "passed": False,
                "detail": f"state={state.value}",
                "severity": "blocker",
            })
            blockers.append(f"System state {state.value} blocks trading")
        elif state == SystemState.BOOTING:
            checks.append({
                "id": "state_machine",
                "passed": True,
                "detail": "state=BOOTING (engine not started — start run.py before session)",
                "severity": "warning",
            })
            warnings.append("Engine not started — run `python run.py` before trading session")
        else:
            checks.append({
                "id": "state_machine",
                "passed": state_machine.is_trading_allowed(),
                "detail": f"state={state.value}",
                "severity": "info",
            })

        live_confirmed = os.getenv("LIVE_TRADING_CONFIRMED", "").lower() in {"1", "true", "yes", "confirmed"}
        live_ok = force_dry or live_confirmed
        checks.append({
            "id": "live_gate",
            "passed": live_ok if not force_dry else True,
            "detail": f"FORCE_DRY_RUN={force_dry} LIVE_CONFIRMED={live_confirmed}",
            "severity": "blocker" if not force_dry else "info",
        })
        if not force_dry and not live_confirmed:
            blockers.append("Live trading requires LIVE_TRADING_CONFIRMED=true")

        micro_live_env = os.getenv("MICRO_LIVE_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        if micro_live_env and include_micro_live_gate:
            from app.micro_live import load_micro_live_config

            ml_config = load_micro_live_config()
            micro_confirmed = os.getenv("MICRO_LIVE_CONFIRMED", "").lower() in {
                "1", "true", "yes", "confirmed",
            }
            promo_passed = False
            for sym in ml_config.allowed_symbols:
                promo = self._get_promotion_for(sym)
                if promo and promo.get("passed"):
                    promo_passed = True
                    break
            gate_ok = micro_confirmed and promo_passed and ml_config.enabled
            checks.append({
                "id": "micro_live_gate",
                "passed": gate_ok,
                "detail": (
                    f"MICRO_LIVE_ENABLED={micro_live_env} "
                    f"MICRO_LIVE_CONFIRMED={micro_confirmed} "
                    f"promotion_passed={promo_passed} "
                    f"symbols={','.join(ml_config.allowed_symbols)}"
                ),
                "severity": "blocker",
            })
            if not micro_confirmed:
                blockers.append("Micro-live requires MICRO_LIVE_CONFIRMED=true")
            if ml_config.require_promotion and not promo_passed:
                blockers.append(
                    "Micro-live requires at least one allowed symbol with promotion passed"
                )

        token_valid = self._check_token_valid()
        token_severity = "warning" if force_dry else "blocker"
        checks.append({
            "id": "kite_token",
            "passed": token_valid,
            "detail": "access token profile check",
            "severity": token_severity,
        })
        if not token_valid:
            msg = "Kite access token invalid or missing (run: python generate_token.py --auto)"
            if force_dry:
                warnings.append(msg)
            else:
                blockers.append(msg)

        for idx in INDICES:
            promo = self._get_promotion_for(idx)
            passed = bool(promo and promo.get("passed"))
            checks.append({
                "id": f"promotion_{idx}",
                "passed": passed,
                "detail": (promo or {}).get("status", "no_record"),
                "severity": "warning",
            })
            if not passed:
                warnings.append(f"{idx}: strategy params not promotion-approved")

        try:
            from app.multi_symbol_risk import multi_risk_manager

            broker_ok = multi_risk_manager.broker_connected
            checks.append({
                "id": "broker_connected",
                "passed": broker_ok,
                "detail": "multi_risk_manager.broker_connected",
                "severity": "warning",
            })
            if not broker_ok:
                warnings.append("Broker API not connected")
        except Exception:
            pass

        from app.market_calendar import is_expiry_day, is_safe_trading_window, now_ist

        now = now_ist()
        if is_expiry_day(now.date()):
            warnings.append("Expiry day — elevated gamma/pinning risk")
        if not is_safe_trading_window(now):
            warnings.append("Outside safe trading window (09:45–15:15 IST)")

        ready = len(blockers) == 0
        return {
            "ready": ready,
            "mode": "paper" if force_dry else "live",
            "checked_at": now.isoformat(),
            "blockers": blockers,
            "warnings": warnings,
            "checks": checks,
        }

    def _check_token_valid(self) -> bool:
        try:
            from app.kite_auth import validate_access_token
            from config import KITE_API_KEY

            valid, _, _ = validate_access_token(KITE_API_KEY)
            return bool(valid)
        except Exception:
            try:
                from app.token_manager import TokenManager
                from kiteconnect import KiteConnect
                from config import KITE_API_KEY

                tm = TokenManager(KiteConnect(api_key=KITE_API_KEY))
                return tm.is_token_valid()
            except Exception:
                return False

    def record_failure_pattern_proposal(
        self,
        proposal: Dict[str, Any],
    ) -> Path:
        """
        Store miner output for human review — never auto-applies to rules.
        """
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = PROPOSALS_DIR / f"proposal_{date_str}_{int(time.time())}.json"
        payload = {
            "status": "pending_review",
            "created_at": datetime.utcnow().isoformat(),
            **proposal,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return path


intelligence_loop = IntelligenceLoop()