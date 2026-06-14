"""
Exchange-side SL-M (stop-loss market) protective orders for LIVE mode.

SL-M is a backup layer; strategy software exits remain primary.
Never places protection orders in paper/dry-run mode.
"""

import logging
import os
from typing import Any, Dict, Optional

from kiteconnect import KiteConnect

from .audit_logger import audit_logger
from .risk_gatekeeper import RiskGatekeeper

logger = logging.getLogger(__name__)

MIN_TRIGGER_POINTS = 5.0
MIN_TRIGGER_PCT = 0.0005  # 0.05%


def _is_force_dry_run() -> bool:
    return os.getenv("FORCE_DRY_RUN", "true").strip().lower() in {"1", "true", "yes", "on"}


def _exchange_slm_enabled() -> bool:
    return os.getenv("ENABLE_EXCHANGE_SLM", "true").strip().lower() not in {"0", "false", "no"}


def _normalize_side(side: str) -> str:
    s = (side or "").upper()
    if s in {"BUY", "LONG"}:
        return "LONG"
    if s in {"SELL", "SHORT"}:
        return "SHORT"
    return s


class ExchangeProtectionManager:
    """Singleton manager for per-index exchange SL-M backup stops."""

    def __init__(self) -> None:
        self.active_protections: Dict[str, Dict[str, Any]] = {}
        self._kite: Optional[KiteConnect] = None

    def place_protective_sl(
        self,
        kite: KiteConnect,
        symbol: str,
        quantity: int,
        side: str,
        trigger_price: float,
        exchange: str,
        force_dry_run: Optional[bool] = None,
    ) -> dict:
        """
        Place exchange SL-M protective order.

        LONG position -> SELL SL-M with trigger below LTP.
        SHORT position -> BUY SL-M with trigger above LTP.
        """
        dry = _is_force_dry_run() if force_dry_run is None else force_dry_run
        if dry:
            audit_logger.record("protection.skipped", {
                "reason": "dry_run",
                "symbol": symbol,
                "trigger_price": trigger_price,
            })
            return {
                "success": False,
                "order_id": None,
                "message": "SL-M skipped in dry-run/paper mode",
            }

        if not _exchange_slm_enabled():
            return {
                "success": False,
                "order_id": None,
                "message": "Exchange SL-M disabled via ENABLE_EXCHANGE_SLM",
            }

        position_side = _normalize_side(side)
        order_exchange = exchange or RiskGatekeeper.resolve_exchange(symbol)

        try:
            from .kite_rate_limit import order_limiter, quote_limiter

            quote_limiter.wait()
            ltp_key = f"{order_exchange}:{symbol}"
            ltp_data = kite.ltp([ltp_key])
            ltp = float(ltp_data[ltp_key]["last_price"])
        except Exception as exc:
            logger.warning("Could not fetch LTP for SL-M validation on %s: %s", symbol, exc)
            return {
                "success": False,
                "order_id": None,
                "message": f"LTP unavailable for trigger validation: {exc}",
            }

        valid, reason = self._validate_trigger(ltp, trigger_price, position_side)
        if not valid:
            audit_logger.record("protection.rejected", {
                "symbol": symbol,
                "trigger_price": trigger_price,
                "ltp": ltp,
                "side": position_side,
                "reason": reason,
            })
            return {"success": False, "order_id": None, "message": reason}

        exit_side = "SELL" if position_side == "LONG" else "BUY"

        try:
            order_limiter.wait()
            order_id = kite.place_order(
                variety="regular",
                exchange=order_exchange,
                tradingsymbol=symbol,
                transaction_type=exit_side,
                quantity=int(quantity),
                product="MIS",
                order_type="SL-M",
                trigger_price=float(trigger_price),
                validity="DAY",
                tag="NFALGO-SLM",
            )
            audit_logger.record("protection.placed", {
                "order_id": order_id,
                "symbol": symbol,
                "exchange": order_exchange,
                "quantity": quantity,
                "position_side": position_side,
                "exit_side": exit_side,
                "trigger_price": trigger_price,
                "ltp": ltp,
            })
            logger.info(
                "Exchange SL-M placed: %s %s qty=%s trigger=%.2f (order_id=%s)",
                exit_side,
                symbol,
                quantity,
                trigger_price,
                order_id,
            )
            return {
                "success": True,
                "order_id": order_id,
                "message": "SL-M protective order placed",
            }
        except Exception as exc:
            audit_logger.record("protection.failed", {
                "symbol": symbol,
                "trigger_price": trigger_price,
                "error": str(exc),
            })
            logger.error("Failed to place SL-M for %s: %s", symbol, exc)
            return {"success": False, "order_id": None, "message": str(exc)}

    def cancel_protection(
        self,
        kite: KiteConnect,
        order_id: str,
        exchange: str,
        symbol: str,
    ) -> dict:
        """Cancel an existing SL-M protection order."""
        if not order_id:
            return {"success": False, "message": "No order_id to cancel"}

        order_exchange = exchange or RiskGatekeeper.resolve_exchange(symbol)
        try:
            from .kite_rate_limit import order_limiter

            order_limiter.wait()
            kite.cancel_order(variety="regular", order_id=str(order_id))
            audit_logger.record("protection.cancelled", {
                "order_id": order_id,
                "symbol": symbol,
                "exchange": order_exchange,
            })
            return {"success": True, "message": "Protection order cancelled"}
        except Exception as exc:
            audit_logger.record("protection.cancel_failed", {
                "order_id": order_id,
                "symbol": symbol,
                "error": str(exc),
            })
            return {"success": False, "message": str(exc)}

    def on_entry_fill(self, kite: KiteConnect, fill_meta: dict) -> dict:
        """
        Called when entry fill is confirmed in live mode.

        fill_meta keys: symbol, quantity, transaction_type, avg_price,
        stop_price, exchange, index_key
        """
        self._kite = kite

        if _is_force_dry_run() or not _exchange_slm_enabled():
            return {"success": False, "message": "Protection not active in dry-run or disabled"}

        stop_price = fill_meta.get("stop_price")
        if stop_price is None or float(stop_price) <= 0:
            audit_logger.record("protection.skipped", {
                "reason": "no_stop_price",
                "fill_meta": fill_meta,
            })
            return {"success": False, "message": "No stop_price in fill metadata"}

        symbol = fill_meta["symbol"]
        index_key = (fill_meta.get("index_key") or symbol).upper()
        quantity = int(fill_meta.get("quantity") or 0)
        transaction_type = fill_meta.get("transaction_type", "BUY")
        exchange = fill_meta.get("exchange") or RiskGatekeeper.resolve_exchange(symbol)
        position_side = _normalize_side(transaction_type)

        result = self.place_protective_sl(
            kite=kite,
            symbol=symbol,
            quantity=quantity,
            side=position_side,
            trigger_price=float(stop_price),
            exchange=exchange,
            force_dry_run=False,
        )

        if result.get("success"):
            self.active_protections[index_key] = {
                "order_id": result.get("order_id"),
                "symbol": symbol,
                "trigger": float(stop_price),
                "qty": quantity,
                "exchange": exchange,
                "side": position_side,
            }
            audit_logger.record("protection.active", {
                "index_key": index_key,
                **self.active_protections[index_key],
            })

        return result

    def on_exit_fill(self, index_key: str, kite: Optional[KiteConnect] = None) -> dict:
        """Cancel active protection for the given index after exit fill."""
        key = (index_key or "").upper()
        protection = self.active_protections.get(key)
        if not protection:
            return {"success": True, "message": "No active protection to cancel"}

        kite_client = kite or self._kite
        if not kite_client:
            audit_logger.record("protection.cancel_skipped", {
                "index_key": key,
                "order_id": protection.get("order_id"),
                "reason": "no_kite_client",
            })
            return {"success": False, "message": "No Kite client available to cancel protection"}

        result = self.cancel_protection(
            kite=kite_client,
            order_id=protection["order_id"],
            exchange=protection.get("exchange"),
            symbol=protection["symbol"],
        )
        if result.get("success"):
            self.active_protections.pop(key, None)
        return result

    def modify_protection(
        self,
        kite: KiteConnect,
        index_key: str,
        new_trigger: float,
    ) -> dict:
        """Cancel existing SL-M and place a new one at updated trigger (trailing stop)."""
        key = (index_key or "").upper()
        protection = self.active_protections.get(key)
        if not protection:
            return {"success": False, "message": f"No active protection for {key}"}

        cancel_result = self.cancel_protection(
            kite=kite,
            order_id=protection["order_id"],
            exchange=protection.get("exchange"),
            symbol=protection["symbol"],
        )
        if not cancel_result.get("success"):
            return cancel_result

        place_result = self.place_protective_sl(
            kite=kite,
            symbol=protection["symbol"],
            quantity=protection["qty"],
            side=protection["side"],
            trigger_price=float(new_trigger),
            exchange=protection.get("exchange"),
            force_dry_run=False,
        )

        if place_result.get("success"):
            self.active_protections[key] = {
                **protection,
                "order_id": place_result.get("order_id"),
                "trigger": float(new_trigger),
            }
            audit_logger.record("protection.modified", {
                "index_key": key,
                "new_trigger": new_trigger,
                "order_id": place_result.get("order_id"),
            })

        return place_result

    @staticmethod
    def _validate_trigger(ltp: float, trigger: float, position_side: str) -> tuple:
        if ltp <= 0:
            return False, "Invalid LTP for trigger validation"

        min_dist = max(MIN_TRIGGER_POINTS, ltp * MIN_TRIGGER_PCT)

        if position_side == "LONG":
            if trigger >= ltp:
                return False, "LONG SL-M trigger must be below current LTP"
            if (ltp - trigger) < min_dist:
                return False, f"Trigger too close to LTP (minimum distance {min_dist:.2f})"
        elif position_side == "SHORT":
            if trigger <= ltp:
                return False, "SHORT SL-M trigger must be above current LTP"
            if (trigger - ltp) < min_dist:
                return False, f"Trigger too close to LTP (minimum distance {min_dist:.2f})"
        else:
            return False, f"Unknown position side: {position_side}"

        return True, ""


exchange_protection = ExchangeProtectionManager()