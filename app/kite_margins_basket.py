"""
Kite basket margin simulation for multi-leg options structures.

Uses ``kite.basket_order_margins()`` (POST /margins/basket) before any
options structure reaches RiskGatekeeper order placement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .audit_logger import audit_logger

logger = logging.getLogger(__name__)


@dataclass
class BasketMarginResult:
    approved: bool
    final_margin: float = 0.0
    initial_margin: float = 0.0
    max_allowed_margin: float = 0.0
    reasons: List[str] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "final_margin": round(self.final_margin, 2),
            "initial_margin": round(self.initial_margin, 2),
            "max_allowed_margin": round(self.max_allowed_margin, 2),
            "reasons": self.reasons,
        }


def build_margin_order_params(
    leg: Dict[str, Any],
    *,
    product: str = "NRML",
    order_type: str = "MARKET",
    price: float = 0.0,
) -> Dict[str, Any]:
    """Map an Aegis options leg dict to Kite basket margin order payload."""
    exchange = (leg.get("exchange") or "NFO").upper()
    return {
        "exchange": exchange,
        "tradingsymbol": leg["tradingsymbol"],
        "transaction_type": leg["transaction_type"].upper(),
        "variety": "regular",
        "product": product.upper(),
        "order_type": order_type.upper(),
        "quantity": int(leg["quantity"]),
        "price": float(price) if order_type.upper() == "LIMIT" else 0,
        "trigger_price": 0,
    }


def simulate_basket_margin(
    kite,
    legs: List[Dict[str, Any]],
    *,
    capital: float,
    max_margin_pct: float = 0.15,
    product: str = "NRML",
    consider_positions: bool = True,
    dry_run_fallback: bool = False,
) -> BasketMarginResult:
    """
    Simulate SPAN margin for a multi-leg basket and enforce capital cap.

    When ``dry_run_fallback=True`` and Kite is unavailable, approves with
    economics-based estimate (wing width × lot) for paper-only paths.
    """
    info_notes: List[str] = []
    block_reasons: List[str] = []
    max_allowed = float(capital) * float(max_margin_pct)

    if not legs:
        return BasketMarginResult(
            approved=False,
            max_allowed_margin=max_allowed,
            reasons=["No legs for margin simulation"],
        )

    missing = [i for i, leg in enumerate(legs) if not leg.get("tradingsymbol")]
    if missing:
        return BasketMarginResult(
            approved=False,
            max_allowed_margin=max_allowed,
            reasons=[f"Legs missing tradingsymbol at indices {missing}"],
        )

    order_params = [
        build_margin_order_params(leg, product=product) for leg in legs
    ]

    final_margin = 0.0
    initial_margin = 0.0
    raw: Optional[Dict[str, Any]] = None

    if kite is not None:
        try:
            raw = kite.basket_order_margins(
                order_params,
                consider_positions=consider_positions,
            )
            final_block = (raw or {}).get("final") or {}
            initial_block = (raw or {}).get("initial") or {}
            final_margin = float(final_block.get("total") or 0.0)
            initial_margin = float(initial_block.get("total") or 0.0)
        except Exception as exc:
            logger.warning("[MarginsBasket] Kite basket_order_margins failed: %s", exc)
            if not dry_run_fallback:
                return BasketMarginResult(
                    approved=False,
                    max_allowed_margin=max_allowed,
                    reasons=[f"Margin API error: {exc}"],
                )
            final_margin = _estimate_margin_fallback(legs)
            initial_margin = final_margin
            info_notes.append("Used paper fallback margin estimate (Kite API unavailable)")
    else:
        if not dry_run_fallback:
            return BasketMarginResult(
                approved=False,
                max_allowed_margin=max_allowed,
                reasons=["Kite client required for margin simulation"],
            )
        final_margin = _estimate_margin_fallback(legs)
        initial_margin = final_margin
        info_notes.append("Used paper fallback margin estimate (no Kite client)")

    if final_margin > max_allowed:
        block_reasons.append(
            f"Required margin ₹{final_margin:,.0f} exceeds cap "
            f"₹{max_allowed:,.0f} ({max_margin_pct:.0%} of capital)"
        )

    approved = len(block_reasons) == 0
    display_reasons = block_reasons if block_reasons else info_notes

    result = BasketMarginResult(
        approved=approved,
        final_margin=final_margin,
        initial_margin=initial_margin,
        max_allowed_margin=max_allowed,
        reasons=display_reasons,
        raw=raw,
    )

    audit_logger.record("options.margin_basket", {
        "approved": result.approved,
        "final_margin": result.final_margin,
        "initial_margin": result.initial_margin,
        "max_allowed_margin": result.max_allowed_margin,
        "leg_count": len(legs),
        "reasons": result.reasons,
        "symbols": [leg.get("tradingsymbol") for leg in legs],
    })

    return result


def _estimate_margin_fallback(legs: List[Dict[str, Any]]) -> float:
    """Conservative paper estimate: max single-leg notional × 2."""
    notionals = []
    for leg in legs:
        prem = float(leg.get("premium") or leg.get("ltp") or 0.0)
        qty = int(leg.get("quantity") or 0)
        strike = float(leg.get("strike") or 0.0)
        notional = max(prem * qty, strike * qty * 0.02)
        notionals.append(notional)
    return max(notionals) * 2.0 if notionals else 50_000.0