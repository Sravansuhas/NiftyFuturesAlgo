"""
Options risk checks — defined-risk structures only.

Phase 0B infrastructure: validate proposed option legs before any order path.
Blocks naked short volatility, enforces max legs and max premium at risk.

No live order placement.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptionsRiskConfig:
    """Hard limits for defined-risk option proposals."""
    max_premium_at_risk: float = 50_000.0
    max_legs: int = 4
    block_naked_short_vol: bool = True


@dataclass
class OptionsLeg:
    """Single proposed option leg (analysis / pre-trade only)."""
    tradingsymbol: str
    option_type: str
    strike: float
    expiry: Any
    transaction_type: str
    quantity: int
    premium: float = 0.0
    underlying: str = ""

    def __post_init__(self):
        self.option_type = self.option_type.upper()
        self.transaction_type = self.transaction_type.upper()
        self.strike = float(self.strike)
        self.quantity = int(self.quantity)
        self.premium = float(self.premium)
        self.underlying = self.underlying.upper() if self.underlying else ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionsLeg":
        return cls(
            tradingsymbol=data.get("tradingsymbol", ""),
            option_type=data.get("option_type", data.get("instrument_type", "")),
            strike=data.get("strike", 0.0),
            expiry=data.get("expiry"),
            transaction_type=data.get("transaction_type", data.get("side", "BUY")),
            quantity=data.get("quantity", 0),
            premium=data.get("premium", data.get("price", 0.0)),
            underlying=data.get("underlying", data.get("name", "")),
        )


@dataclass
class OptionsRiskResult:
    approved: bool
    reasons: List[str] = field(default_factory=list)
    premium_at_risk: float = 0.0
    leg_count: int = 0
    has_naked_short_vol: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "reasons": self.reasons,
            "premium_at_risk": self.premium_at_risk,
            "leg_count": self.leg_count,
            "has_naked_short_vol": self.has_naked_short_vol,
        }


class OptionsRiskChecker:
    """
    Defined-risk-only gate for option structures.

    Allowed examples:
    - Long CE / long PE (premium paid = max loss)
    - Vertical spreads (bull call, bear put, etc.)
    - Iron condor / iron butterfly (within leg cap)

    Blocked:
    - Naked short calls or puts (unlimited loss potential)
    - Structures exceeding max_legs or max_premium_at_risk
    """

    def __init__(self, config: Optional[OptionsRiskConfig] = None):
        self.config = config or OptionsRiskConfig()

    def calculate_premium_at_risk(self, legs: List[OptionsLeg]) -> float:
        """
        Net debit paid for long premium legs.

        For defined-risk spreads, only the net debit (or spread width debit)
        counts — short legs inside a spread do not add unlimited risk.
        """
        if not legs:
            return 0.0

        buy_premium = sum(
            leg.premium * leg.quantity
            for leg in legs
            if leg.transaction_type == "BUY"
        )
        sell_premium = sum(
            leg.premium * leg.quantity
            for leg in legs
            if leg.transaction_type == "SELL"
        )
        net_debit = buy_premium - sell_premium
        return max(0.0, net_debit)

    def _same_expiry(self, left, right) -> bool:
        if left is None or right is None:
            return True
        return str(left)[:10] == str(right)[:10]

    def detect_naked_short_vol(self, legs: List[OptionsLeg]) -> bool:
        """
        True when short option quantity exceeds long coverage for the same
        option type and expiry (blocks naked short calls/puts and ratio shorts).
        """
        buckets: Dict[tuple, Dict[str, int]] = {}
        for leg in legs:
            if leg.option_type not in {"CE", "PE"}:
                return True
            key = (leg.option_type, str(leg.expiry)[:10])
            bucket = buckets.setdefault(key, {"BUY": 0, "SELL": 0})
            bucket[leg.transaction_type] += leg.quantity

        for totals in buckets.values():
            if totals["SELL"] > totals["BUY"]:
                return True

        return False

    def validate(
        self,
        legs: List[OptionsLeg],
        config: Optional[OptionsRiskConfig] = None,
    ) -> OptionsRiskResult:
        """Run all defined-risk checks on a proposed multi-leg structure."""
        cfg = config or self.config
        reasons: List[str] = []

        if not legs:
            return OptionsRiskResult(
                approved=False,
                reasons=["No option legs provided"],
                leg_count=0,
            )

        leg_count = len(legs)
        if leg_count > cfg.max_legs:
            reasons.append(
                f"Leg count {leg_count} exceeds max_legs ({cfg.max_legs})"
            )

        naked_short = cfg.block_naked_short_vol and self.detect_naked_short_vol(legs)
        if naked_short:
            reasons.append(
                "Naked short volatility blocked — short CE/PE quantity exceeds long coverage"
            )

        premium_at_risk = self.calculate_premium_at_risk(legs)
        if premium_at_risk > cfg.max_premium_at_risk:
            reasons.append(
                f"Premium at risk ₹{premium_at_risk:,.0f} exceeds "
                f"max_premium_at_risk ₹{cfg.max_premium_at_risk:,.0f}"
            )

        for leg in legs:
            if leg.quantity <= 0:
                reasons.append(f"Invalid quantity on {leg.tradingsymbol or leg.option_type}")
            if leg.option_type not in {"CE", "PE"}:
                reasons.append(f"Invalid option_type: {leg.option_type}")
            if leg.transaction_type not in {"BUY", "SELL"}:
                reasons.append(f"Invalid transaction_type: {leg.transaction_type}")

        approved = len(reasons) == 0
        if not approved:
            logger.info(f"[OptionsRisk] Rejected: {'; '.join(reasons)}")

        return OptionsRiskResult(
            approved=approved,
            reasons=reasons,
            premium_at_risk=premium_at_risk,
            leg_count=leg_count,
            has_naked_short_vol=naked_short,
        )

    def validate_dicts(
        self,
        leg_dicts: List[Dict[str, Any]],
        config: Optional[OptionsRiskConfig] = None,
    ) -> OptionsRiskResult:
        legs = [OptionsLeg.from_dict(item) for item in leg_dicts]
        return self.validate(legs, config=config)


# Global singleton (mirrors risk_gatekeeper / instruments_manager pattern)
options_risk_checker = OptionsRiskChecker()