"""
Indian F&O rules engine — loads encodable retail failure-pattern rules and
evaluates them before strategy entries.

Rules live in data/knowledge_base/indian_fo_rules.json.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "knowledge_base" / "indian_fo_rules.json"


class FORulesEngine:
    """Evaluate structured F&O knowledge rules against an entry context."""

    def __init__(self, rules_path: Optional[Path] = None):
        self.rules_path = Path(rules_path or DEFAULT_RULES_PATH)
        self.metadata: Dict[str, Any] = {}
        self.rules: List[Dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        if not self.rules_path.exists():
            raise FileNotFoundError(f"F&O rules file not found: {self.rules_path}")

        with self.rules_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.metadata = payload.get("metadata", {})
        self.rules = sorted(
            payload.get("rules", []),
            key=lambda rule: (rule.get("tier", 99), rule.get("rule_id", "")),
        )
        logger.info(
            "FORulesEngine loaded %d rules from %s (version %s)",
            len(self.rules),
            self.rules_path.name,
            self.metadata.get("version", "unknown"),
        )

    def check_entry(self, symbol: str, context: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, float]:
        """
        Evaluate all rules for a prospective entry.

        Returns:
            allowed: False if any tier-1 rule (or tier-2 block action) fails.
            reason: Human-readable veto/de-risk explanation (empty if all clear).
            risk_multiplier: Product of tier-2 de-risk multipliers (1.0 = full size).
        """
        ctx = self._prepare_context(symbol, context or {})
        risk_multiplier = 1.0
        de_risk_reasons: List[str] = []

        for rule in self.rules:
            passed, detail = self._evaluate_rule(rule, ctx)
            if passed:
                continue

            rule_id = rule.get("rule_id", "UNKNOWN")
            tier = int(rule.get("tier", 2))
            action = rule.get("params", {}).get("action", "block" if tier == 1 else "de_risk")
            description = rule.get("description", rule_id)

            if tier == 1 or action == "block":
                reason = f"{rule_id}: {detail or description}"
                logger.info("Entry blocked for %s — %s", symbol, reason)
                return False, reason, 1.0

            multiplier = float(rule.get("params", {}).get("risk_multiplier", 1.0))
            risk_multiplier *= multiplier
            de_risk_reasons.append(f"{rule_id} ({multiplier:.2f}x): {detail or description}")

        if de_risk_reasons:
            reason = "; ".join(de_risk_reasons)
            logger.debug("Entry de-risked for %s — %s (net multiplier=%.3f)", symbol, reason, risk_multiplier)
            return True, reason, risk_multiplier

        return True, "", 1.0

    def list_rules(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of loaded rules for dashboards/agents."""
        return list(self.rules)

    def _prepare_context(self, symbol: str, context: Dict[str, Any]) -> Dict[str, Any]:
        ctx = dict(context)
        ctx["symbol"] = symbol

        if (
            "is_expiry_day" not in ctx
            or "is_safe_trading_window" not in ctx
            or "hours_to_high_impact_event" not in ctx
        ):
            from .market_calendar import (
                get_hours_to_high_impact_event,
                is_expiry_day,
                is_safe_trading_window,
                now_ist,
            )

            at = ctx.get("at") or now_ist()
            day = at.date() if hasattr(at, "date") else None
            ctx.setdefault("is_expiry_day", is_expiry_day(day, underlying=symbol))
            safe = is_safe_trading_window(at)
            ctx.setdefault("is_safe_trading_window", safe)
            ctx.setdefault("safe_trading_window", safe)
            ctx.setdefault("hours_to_high_impact_event", get_hours_to_high_impact_event(at))

        return ctx

    def _evaluate_rule(self, rule: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[bool, str]:
        condition_type = rule.get("condition_type", "")
        params = rule.get("params", {})

        if condition_type == "boolean_required":
            field = params["field"]
            expected = params.get("expected", True)
            actual = bool(ctx.get(field, False))
            if actual != expected:
                return False, f"{field} must be {expected}, got {actual}"
            return True, ""

        if condition_type == "boolean_forbidden":
            field = params["field"]
            if bool(ctx.get(field, False)):
                return False, f"{field} is forbidden for automated entries"
            return True, ""

        if condition_type == "max_value":
            if not self._only_when_applies(params.get("only_when"), ctx):
                return True, ""
            field = params["field"]
            maximum = float(params["max"])
            raw_value = ctx.get(field, 0.0)
            if raw_value in (None, ""):
                value = float("inf")
            else:
                value = float(raw_value)
            invert = bool(params.get("invert", False))
            if invert:
                if value <= maximum:
                    return False, f"{field}={value:.2f} within pre-event block window ({maximum:.2f}h)"
                return True, ""
            if value > maximum:
                return False, f"{field}={value:.2f} exceeds max {maximum:.2f}"
            return True, ""

        if condition_type == "min_value":
            if not self._only_when_applies(params.get("only_when"), ctx):
                return True, ""
            field = params["field"]
            minimum = float(params["min"])
            value = float(ctx.get(field, 0.0) or 0.0)
            if value >= minimum:
                return False, f"{field}={value:.2f} reached de-risk threshold {minimum:.2f}"
            return True, ""

        if condition_type == "count_limit":
            field = params["field"]
            maximum = int(ctx.get("effective_max_trades", params["max"]))
            value = int(ctx.get(field, 0) or 0)
            if value >= maximum:
                return False, f"{field}={value} reached daily cap {maximum}"
            return True, ""

        if condition_type == "cooldown_after_loss":
            streak = int(ctx.get(params["consecutive_losses_field"], 0) or 0)
            min_streak = int(params.get("min_loss_streak", 1))
            if streak < min_streak:
                return True, ""

            seconds_field = params["seconds_since_loss_field"]
            cooldown = float(params.get("min_cooldown_seconds", 300))
            elapsed = float(ctx.get(seconds_field, float("inf")) or float("inf"))
            if elapsed < cooldown:
                return False, (
                    f"cooldown active: {elapsed:.0f}s since last loss "
                    f"(need {cooldown:.0f}s, streak={streak})"
                )
            return True, ""

        if condition_type == "calendar_flag":
            flag = params["flag"]
            if bool(ctx.get(flag, False)):
                return False, f"{flag} is active"
            return True, ""

        if condition_type == "time_window":
            window = params.get("window", "safe_trading_window")
            in_window = bool(ctx.get(window, False))
            invert = bool(params.get("invert", False))
            violated = (not in_window) if not invert else in_window
            if violated:
                label = f"outside {window}" if not invert else f"inside restricted {window}"
                return False, label
            return True, ""

        if condition_type == "compound_all":
            for clause in params.get("conditions", []):
                if not self._evaluate_compound_clause(clause, ctx):
                    return True, ""
            return False, "compound risk condition met"

        if condition_type == "rolling_edge_halt":
            count_field = params.get("count_field", "rolling_edge_trade_count")
            expectancy_field = params.get("field", "rolling_expectancy")
            min_trades = int(params.get("min_trades", 10))
            floor = float(params.get("floor", 0.0))
            trade_count = int(ctx.get(count_field, 0) or 0)
            if trade_count < min_trades:
                return True, ""
            expectancy = float(ctx.get(expectancy_field, 0.0) or 0.0)
            if expectancy < floor:
                return False, (
                    f"{expectancy_field}={expectancy:.2f} below floor {floor:.2f} "
                    f"(last {trade_count} trades)"
                )
            return True, ""

        logger.warning("Unknown condition_type '%s' on rule %s — treating as pass", condition_type, rule.get("rule_id"))
        return True, ""

    def _evaluate_compound_clause(self, clause: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
        """Return True when a compound_all sub-clause is satisfied (veto trigger)."""
        if "any_of" in clause:
            return any(self._evaluate_compound_clause(sub, ctx) for sub in clause.get("any_of", []))

        field = clause.get("field")
        if not field:
            return False

        if "equals" in clause:
            return ctx.get(field) == clause["equals"]

        if "max" in clause:
            value = float(ctx.get(field, float("inf")) or float("inf"))
            return value <= float(clause["max"])

        if "min" in clause:
            value = float(ctx.get(field, 0.0) or 0.0)
            return value >= float(clause["min"])

        return False

    @staticmethod
    def _only_when_applies(clause: Optional[Dict[str, Any]], ctx: Dict[str, Any]) -> bool:
        if not clause:
            return True
        field = clause["field"]
        expected = clause.get("equals")
        return ctx.get(field) == expected


try:
    fo_rules_engine = FORulesEngine()
except FileNotFoundError as exc:
    logger.warning("F&O rules file missing — failure-pattern gates disabled: %s", exc)
    fo_rules_engine = None