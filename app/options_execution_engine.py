"""
Multi-leg options execution — routes every leg through RiskGatekeeper.

Iron condor entry order: buy wings first, then sell shorts (defined-risk).
Paper mode uses dry-run guarded orders; live uses real Kite placement.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .audit_logger import audit_logger
from .kite_margins_basket import simulate_basket_margin
from .trade_ledger import trade_ledger
from .options_positions import OptionsPositionStore, options_position_store
from .options_risk import OptionsRiskChecker, options_risk_checker
from .order_tags import DEFAULT_ALGO_TAG

logger = logging.getLogger(__name__)

# Safer leg sequence: long protection before short body
IRON_CONDOR_LEG_ORDER = ("put_long", "call_long", "put_short", "call_short")


class OptionsExecutionEngine:
    def __init__(
        self,
        risk_checker: Optional[OptionsRiskChecker] = None,
        position_store: Optional[OptionsPositionStore] = None,
    ):
        self.risk_checker = risk_checker or options_risk_checker
        self.position_store = position_store or options_position_store

    def resolve_proposal_legs(self, proposal: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
        """Attach tradingsymbol / exchange / lot_size from instruments master."""
        from .instruments_manager import instruments_manager

        legs_in = proposal.get("legs") or []
        if legs_in and all(leg.get("tradingsymbol") for leg in legs_in):
            return deepcopy(proposal), None

        underlying = proposal.get("underlying", "NIFTY").upper()
        expiry = proposal.get("expiry")
        if not expiry:
            return proposal, "Missing expiry on proposal"

        resolved: List[Dict[str, Any]] = []
        for leg in legs_in:
            inst = instruments_manager.get_option_instruments(
                underlying,
                leg.get("option_type"),
                expiry,
                float(leg.get("strike")),
            )
            if not inst or not inst.get("tradingsymbol"):
                return proposal, (
                    f"No instrument for {underlying} {leg.get('option_type')} "
                    f"K{leg.get('strike')} exp {expiry}"
                )
            row = deepcopy(leg)
            row["tradingsymbol"] = inst["tradingsymbol"]
            row["exchange"] = inst.get("exchange") or ("BFO" if underlying == "SENSEX" else "NFO")
            row["instrument_token"] = inst.get("instrument_token")
            row["lot_size"] = int(inst.get("lot_size") or leg.get("quantity") or 1)
            if not row.get("quantity"):
                row["quantity"] = row["lot_size"]
            resolved.append(row)

        out = deepcopy(proposal)
        out["legs"] = resolved
        return out, None

    def validate_proposal(
        self,
        proposal: Dict[str, Any],
        *,
        capital: float,
        max_margin_pct: float,
        max_structure_loss: float,
        kite=None,
        product: str = "NRML",
        dry_run_fallback_margin: bool = False,
    ) -> Dict[str, Any]:
        """Options risk + Kite basket margin gate."""
        econ = proposal.get("economics") or {}
        risk_cfg = self.risk_checker.config
        if max_structure_loss > 0 and float(econ.get("max_loss") or 0) > max_structure_loss:
            return {
                "approved": False,
                "stage": "structure_loss",
                "reasons": [
                    f"Max loss ₹{econ.get('max_loss'):,.0f} exceeds "
                    f"max_structure_loss ₹{max_structure_loss:,.0f}"
                ],
            }

        from .options_risk import OptionsRiskConfig

        risk_cfg = OptionsRiskConfig(
            max_premium_at_risk=float(
                proposal.get("_max_premium_at_risk")
                or self.risk_checker.config.max_premium_at_risk
            ),
            max_legs=int(
                proposal.get("_max_legs") or self.risk_checker.config.max_legs
            ),
            block_naked_short_vol=self.risk_checker.config.block_naked_short_vol,
        )
        risk_result = self.risk_checker.validate_dicts(
            proposal.get("legs") or [],
            config=risk_cfg,
        )
        if not risk_result.approved:
            return {
                "approved": False,
                "stage": "options_risk",
                "reasons": risk_result.reasons,
                "risk": risk_result.to_dict(),
            }

        margin_result = simulate_basket_margin(
            kite,
            proposal.get("legs") or [],
            capital=capital,
            max_margin_pct=max_margin_pct,
            product=product,
            dry_run_fallback=dry_run_fallback_margin,
        )
        if not margin_result.approved:
            return {
                "approved": False,
                "stage": "margin_basket",
                "reasons": margin_result.reasons,
                "margin": margin_result.to_dict(),
            }

        return {
            "approved": True,
            "risk": risk_result.to_dict(),
            "margin": margin_result.to_dict(),
        }

    def _sort_iron_condor_legs(self, legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Order legs: buy wings before sell shorts."""
        role_map: Dict[str, Dict[str, Any]] = {}
        for leg in legs:
            strike = float(leg.get("strike") or 0)
            opt = leg.get("option_type", "").upper()
            side = leg.get("transaction_type", "").upper()
            if opt == "PE" and side == "BUY":
                role_map["put_long"] = leg
            elif opt == "PE" and side == "SELL":
                role_map["put_short"] = leg
            elif opt == "CE" and side == "SELL":
                role_map["call_short"] = leg
            elif opt == "CE" and side == "BUY":
                role_map["call_long"] = leg

        ordered = [role_map[r] for r in IRON_CONDOR_LEG_ORDER if r in role_map]
        if len(ordered) == len(legs):
            return ordered
        return sorted(
            legs,
            key=lambda leg: (0 if leg.get("transaction_type") == "BUY" else 1, leg.get("strike")),
        )

    def execute_structure(
        self,
        kite,
        proposal: Dict[str, Any],
        *,
        force_dry_run: bool = True,
        product: str = "NRML",
        tag: str = DEFAULT_ALGO_TAG,
    ) -> Dict[str, Any]:
        """
        Place all legs atomically (best-effort). Rolls back successful legs on failure.
        """
        from .risk_gatekeeper import risk_gatekeeper

        proposal, resolve_err = self.resolve_proposal_legs(proposal)
        if resolve_err:
            return {"success": False, "message": resolve_err, "stage": "resolve"}

        legs = self._sort_iron_condor_legs(proposal.get("legs") or [])
        leg_results: List[Dict[str, Any]] = []
        placed_for_rollback: List[Dict[str, Any]] = []

        for idx, leg in enumerate(legs):
            symbol = leg["tradingsymbol"]
            qty = int(leg["quantity"])
            side = leg["transaction_type"].upper()
            exchange = leg.get("exchange")
            lot_size = int(leg.get("lot_size") or qty)

            result = risk_gatekeeper.place_guarded_order(
                kite=kite,
                symbol=symbol,
                quantity=qty,
                transaction_type=side,
                order_type="MARKET",
                product=product,
                force_dry_run=force_dry_run,
                is_exit=False,
                tag=tag,
                exchange=exchange,
                lot_size=lot_size,
                multi_symbol_entry=True,
            )
            leg_results.append({
                "leg_index": idx,
                "tradingsymbol": symbol,
                "transaction_type": side,
                "quantity": qty,
                **result,
            })

            if not result.get("success"):
                rollback = self._rollback_legs(kite, placed_for_rollback, force_dry_run=force_dry_run, product=product, tag=tag)
                audit_logger.record("options.structure_failed", {
                    "underlying": proposal.get("underlying"),
                    "failed_leg": symbol,
                    "message": result.get("message"),
                    "rollback": rollback,
                })
                return {
                    "success": False,
                    "message": result.get("message", "Leg order failed"),
                    "stage": "order",
                    "leg_results": leg_results,
                    "rollback": rollback,
                }

            placed_for_rollback.append({
                "tradingsymbol": symbol,
                "quantity": qty,
                "transaction_type": side,
                "exchange": exchange,
                "lot_size": lot_size,
            })

        struct = self.position_store.add_structure(proposal, leg_results)
        audit_logger.record("options.structure_opened", {
            "structure_id": struct.structure_id,
            "underlying": struct.underlying,
            "structure_type": struct.structure_type,
            "entry_credit": struct.entry_credit,
            "max_loss": struct.max_loss,
            "order_ids": struct.order_ids,
            "dry_run": force_dry_run,
        })
        trade_ledger.record("options.structure.open", {
            "structure_id": struct.structure_id,
            "underlying": struct.underlying,
            "credit": struct.entry_credit,
            "legs": len(struct.legs or []),
        })

        return {
            "success": True,
            "structure_id": struct.structure_id,
            "message": f"Opened {struct.structure_type} on {struct.underlying}",
            "leg_results": leg_results,
            "dry_run": force_dry_run,
        }

    def _rollback_legs(
        self,
        kite,
        placed_legs: List[Dict[str, Any]],
        *,
        force_dry_run: bool,
        product: str,
        tag: str,
    ) -> List[Dict[str, Any]]:
        """Reverse any legs that were placed before a failure."""
        from .risk_gatekeeper import risk_gatekeeper

        rollback_results = []
        for leg in reversed(placed_legs):
            reverse_side = "SELL" if leg["transaction_type"].upper() == "BUY" else "BUY"
            result = risk_gatekeeper.place_guarded_order(
                kite=kite,
                symbol=leg["tradingsymbol"],
                quantity=int(leg["quantity"]),
                transaction_type=reverse_side,
                order_type="MARKET",
                product=product,
                force_dry_run=force_dry_run,
                is_exit=True,
                tag=tag,
                exchange=leg.get("exchange"),
                lot_size=int(leg.get("lot_size") or leg["quantity"]),
                multi_symbol_entry=True,
            )
            rollback_results.append({
                "tradingsymbol": leg["tradingsymbol"],
                "reverse_side": reverse_side,
                **result,
            })
            time.sleep(0.05)
        return rollback_results

    def close_structure(
        self,
        kite,
        structure_id: str,
        *,
        reason: str = "manual_close",
        force_dry_run: bool = True,
        product: str = "NRML",
        tag: str = DEFAULT_ALGO_TAG,
    ) -> Dict[str, Any]:
        """Flatten an open structure by reversing each leg."""
        from .risk_gatekeeper import risk_gatekeeper

        struct = self.position_store.get(structure_id)
        if not struct or struct.status != "OPEN":
            return {"success": False, "message": f"No open structure {structure_id}"}

        leg_results = []
        for leg in struct.legs:
            close_side = "SELL" if leg.get("transaction_type", "").upper() == "BUY" else "BUY"
            result = risk_gatekeeper.place_guarded_order(
                kite=kite,
                symbol=leg["tradingsymbol"],
                quantity=int(leg["quantity"]),
                transaction_type=close_side,
                order_type="MARKET",
                product=product,
                force_dry_run=force_dry_run,
                is_exit=True,
                tag=tag,
                exchange=leg.get("exchange"),
                lot_size=int(leg.get("lot_size") or leg["quantity"]),
                multi_symbol_entry=True,
            )
            leg_results.append({
                "tradingsymbol": leg["tradingsymbol"],
                "transaction_type": close_side,
                **result,
            })
            if not result.get("success"):
                return {
                    "success": False,
                    "message": result.get("message", "Close leg failed"),
                    "leg_results": leg_results,
                }

        closed = self.position_store.close_structure(structure_id, reason)
        audit_logger.record("options.structure_closed", {
            "structure_id": structure_id,
            "reason": reason,
            "dry_run": force_dry_run,
        })
        trade_ledger.record("options.structure.close", {
            "structure_id": structure_id,
            "underlying": struct.underlying,
            "credit": struct.entry_credit,
            "legs": len(struct.legs or []),
            "reason": reason,
        })
        return {
            "success": True,
            "structure_id": structure_id,
            "message": f"Closed {structure_id}: {reason}",
            "leg_results": leg_results,
            "closed": closed.to_dict() if closed else None,
        }


options_execution_engine = OptionsExecutionEngine()