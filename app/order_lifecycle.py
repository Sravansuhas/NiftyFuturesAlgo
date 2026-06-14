"""
Order lifecycle — live fill confirmation drives position updates.

Paper/dry-run keeps instant fill accounting in risk_gatekeeper / multi_symbol_risk.
After COMPLETE entry fills in LIVE mode, optional exchange SL-M hooks run via on_order_terminal.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kiteconnect import KiteConnect

from .audit_logger import audit_logger
from .risk_gatekeeper import RiskGatekeeper, risk_gatekeeper
from .state_machine import SystemState, state_machine

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"COMPLETE", "REJECTED", "CANCELLED"})
_PENDING_ORDERS_FILE = Path("data/pending_orders.json")


def _normalize_index_key(symbol: str) -> str:
    s = (symbol or "").upper()
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "SENSEX" in s:
        return "SENSEX"
    return "NIFTY"


def _exchange_slm_enabled() -> bool:
    return os.getenv("ENABLE_EXCHANGE_SLM", "true").strip().lower() not in {"0", "false", "no"}


class OrderLifecycleManager:
    """Tracks submitted live orders until broker reports a terminal status."""

    def __init__(self):
        self.pending_orders: Dict[str, Dict] = {}
        self._kite: Optional[KiteConnect] = None
        self._fill_handlers: Dict[str, Callable[..., None]] = {}
        self._terminal_handlers: Dict[str, Callable[..., None]] = {}
        self._processed_terminal: set = set()

    def bind_kite(self, kite: KiteConnect) -> None:
        """Attach live Kite client for postback SL-M hooks and deferred protection."""
        self._kite = kite

    def register_fill_handler(self, index_key: str, handler: Callable[..., None]) -> None:
        """Strategy callback invoked after broker-confirmed fills (live mode)."""
        self._fill_handlers[index_key.upper()] = handler

    def register_terminal_handler(self, index_key: str, handler: Callable[..., None]) -> None:
        """Strategy callback for terminal no-fill (reject/cancel) — clears pending state."""
        self._terminal_handlers[index_key.upper()] = handler

    def register_submitted_order(self, order_id: str, meta: dict) -> None:
        """Register a live order after Kite accepts submission (not filled yet)."""
        order_id = str(order_id)
        enriched = dict(meta)
        enriched.setdefault("placed_at", time.time())
        if "index_key" not in enriched and enriched.get("symbol"):
            enriched["index_key"] = _normalize_index_key(enriched["symbol"])
        if enriched.get("protective_stop") is not None and enriched.get("stop_price") is None:
            enriched["stop_price"] = enriched["protective_stop"]

        self.pending_orders[order_id] = enriched

        if order_id in risk_gatekeeper.pending_orders:
            risk_gatekeeper.pending_orders[order_id].update(enriched)
        else:
            risk_gatekeeper.pending_orders[order_id] = dict(enriched)

        audit_logger.record("order.registered", {"order_id": order_id, **enriched})
        logger.debug("[ORDER_LIFECYCLE] Registered pending order %s (%s)", order_id, enriched.get("symbol"))

    def handle_broker_update(
        self,
        order_id: str,
        broker_order: dict,
        kite: Optional[KiteConnect] = None,
    ) -> dict:
        """
        Process a broker status update. Position changes only on COMPLETE with fills.
        Returns {processed, status, fill_price, filled_qty}.
        """
        order_id = str(order_id)
        status = str(broker_order.get("status", "")).upper()
        filled_qty = int(broker_order.get("filled_quantity", 0) or 0)
        fill_price = float(broker_order.get("average_price", 0) or 0)

        result = {
            "processed": False,
            "status": status,
            "fill_price": fill_price,
            "filled_qty": filled_qty,
        }

        pending = self.pending_orders.get(order_id) or risk_gatekeeper.pending_orders.get(order_id)
        if not pending:
            return result

        if status == "UPDATE":
            if self._sync_fill_progress(order_id, pending, filled_qty, fill_price):
                result["processed"] = True
            return result

        if status not in _TERMINAL_STATUSES:
            return result

        if order_id in self._processed_terminal:
            return result

        if status == "COMPLETE" and filled_qty > 0:
            already = int(pending.get("applied_fill_qty", 0) or 0)
            if already < filled_qty:
                self._sync_fill_progress(order_id, pending, filled_qty, fill_price)
            elif already == 0:
                self._apply_fill(order_id, pending, filled_qty, fill_price)
            if kite is not None:
                try:
                    on_order_terminal(kite, order_id, pending, broker_order)
                except Exception as exc:
                    logger.warning("SL-M hook failed for %s: %s", order_id, exc)
            result["processed"] = True
        elif status in {"REJECTED", "CANCELLED"}:
            if filled_qty > 0:
                self._apply_fill(order_id, pending, filled_qty, fill_price)
            else:
                self._notify_terminal_no_fill(order_id, pending, status)
            audit_logger.record("order.terminal_no_fill", {
                "order_id": order_id,
                "status": status,
                "symbol": pending.get("symbol"),
                "quantity": pending.get("quantity"),
                "filled_qty": filled_qty,
                "is_exit": pending.get("is_exit", False),
                "reason": broker_order.get("status_message") or broker_order.get("status_message_raw"),
            })
            logger.info("[ORDER_LIFECYCLE] Order %s %s (filled_qty=%s)", order_id, status, filled_qty)
            result["processed"] = True
        elif status == "COMPLETE":
            audit_logger.record("order.complete_zero_fill", {
                "order_id": order_id,
                "symbol": pending.get("symbol"),
            })
            result["processed"] = True

        self._processed_terminal.add(order_id)
        self._remove_pending(order_id)
        return result

    def _notify_terminal_no_fill(self, order_id: str, pending: dict, status: str) -> None:
        index_key = (pending.get("index_key") or _normalize_index_key(pending.get("symbol", ""))).upper()
        handler = self._terminal_handlers.get(index_key)
        if handler:
            try:
                handler(order_id=order_id, status=status, pending=pending)
            except Exception as exc:
                logger.warning("[ORDER_LIFECYCLE] terminal handler failed for %s: %s", index_key, exc)

    def handle_postback(self, payload: dict) -> dict:
        """Map Kite postback payload fields to handle_broker_update."""
        order_id = payload.get("order_id")
        if not order_id:
            return {"processed": False, "status": "", "fill_price": 0.0, "filled_qty": 0}

        broker_order = {
            "status": payload.get("status"),
            "filled_quantity": payload.get("filled_quantity", payload.get("quantity", 0)),
            "average_price": payload.get("average_price", payload.get("price", 0)),
            "status_message": payload.get("status_message"),
        }
        return self.handle_broker_update(str(order_id), broker_order, kite=self._kite)

    def get_pending_orders(self) -> dict:
        """Return pending orders for dashboard/debug (shallow copy)."""
        merged = dict(risk_gatekeeper.pending_orders)
        merged.update(self.pending_orders)
        return merged

    def _sync_fill_progress(
        self,
        order_id: str,
        pending: dict,
        filled_qty: int,
        fill_price: float,
    ) -> bool:
        """Apply incremental fills from UPDATE / partial-fill postbacks."""
        if filled_qty <= 0:
            return False
        already = int(pending.get("applied_fill_qty", 0) or 0)
        delta = filled_qty - already
        if delta <= 0:
            return False
        self._apply_fill(order_id, pending, delta, fill_price)
        pending["applied_fill_qty"] = filled_qty
        self.pending_orders[order_id] = pending
        if order_id in risk_gatekeeper.pending_orders:
            risk_gatekeeper.pending_orders[order_id]["applied_fill_qty"] = filled_qty
        audit_logger.record("order.partial_fill", {
            "order_id": order_id,
            "filled_qty": filled_qty,
            "delta": delta,
            "fill_price": fill_price,
            "symbol": pending.get("symbol"),
        })
        logger.info(
            "[ORDER_LIFECYCLE] Partial fill progress %s/%s for order %s",
            filled_qty,
            pending.get("quantity"),
            order_id,
        )
        return True

    def persist_pending_orders(self, path: Path = _PENDING_ORDERS_FILE) -> None:
        if not self.pending_orders:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "orders": self.pending_orders,
        }
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def restore_pending_orders(self, path: Path = _PENDING_ORDERS_FILE) -> int:
        if not path.exists():
            return 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("restore_pending_orders failed: %s", exc)
            return 0
        restored = 0
        for order_id, meta in (payload.get("orders") or {}).items():
            if order_id in self.pending_orders:
                continue
            self.register_submitted_order(str(order_id), meta)
            restored += 1
        if restored:
            logger.info("[ORDER_LIFECYCLE] Restored %s pending live orders from disk", restored)
        return restored

    def _apply_fill(self, order_id: str, pending: dict, filled_qty: int, fill_price: float) -> None:
        symbol = pending["symbol"]
        transaction_type = str(pending["transaction_type"]).upper()
        is_exit = bool(pending.get("is_exit", False))
        expected_qty = int(pending.get("quantity", filled_qty))

        if filled_qty > expected_qty:
            logger.warning(
                "[ORDER_LIFECYCLE] Fill qty %s exceeds submitted %s for %s — capping",
                filled_qty,
                expected_qty,
                order_id,
            )
            filled_qty = expected_qty

        risk_gatekeeper.on_order_placed(
            symbol,
            filled_qty,
            transaction_type,
            fill_price,
            is_exit=is_exit,
        )

        if not is_exit and not risk_gatekeeper.config.force_dry_run:
            risk_gatekeeper.trades_today += 1

        try:
            from .multi_symbol_risk import multi_risk_manager

            if not multi_risk_manager.config.force_dry_run:
                multi_risk_manager.on_broker_fill(
                    symbol,
                    filled_qty,
                    transaction_type,
                    fill_price,
                    is_exit=is_exit,
                )
        except Exception as exc:
            logger.warning("[ORDER_LIFECYCLE] multi_symbol fill update failed: %s", exc)

        audit_logger.record("order.filled", {
            "order_id": order_id,
            "symbol": symbol,
            "index_key": pending.get("index_key"),
            "filled_qty": filled_qty,
            "fill_price": fill_price,
            "transaction_type": transaction_type,
            "is_exit": is_exit,
        })

        if filled_qty < expected_qty:
            logger.info(
                "[ORDER_LIFECYCLE] Partial fill %s/%s for order %s",
                filled_qty,
                expected_qty,
                order_id,
            )

        index_key = (pending.get("index_key") or _normalize_index_key(symbol)).upper()
        handler = self._fill_handlers.get(index_key)
        if handler:
            try:
                handler(
                    fill_price=fill_price,
                    filled_qty=filled_qty,
                    transaction_type=transaction_type,
                    is_exit=is_exit,
                    order_id=order_id,
                )
            except Exception as exc:
                logger.warning("[ORDER_LIFECYCLE] fill handler failed for %s: %s", index_key, exc)

    def _remove_pending(self, order_id: str) -> None:
        self.pending_orders.pop(order_id, None)
        risk_gatekeeper.pending_orders.pop(order_id, None)


order_lifecycle = OrderLifecycleManager()


def on_order_terminal(
    kite: KiteConnect,
    order_id: str,
    pending: Dict[str, Any],
    broker_order: Dict[str, Any],
) -> None:
    """
    Handle broker terminal COMPLETE fill hooks for exchange SL-M protection.

    Position updates are handled by OrderLifecycleManager.handle_broker_update.
    """
    status = str(broker_order.get("status", "")).upper()
    if status != "COMPLETE":
        return

    filled = int(broker_order.get("filled_quantity", 0) or 0)
    if filled <= 0:
        return

    is_exit = bool(pending.get("is_exit"))
    symbol = pending.get("symbol", "")

    if is_exit:
        _on_exit_fill(kite, symbol)
        return

    _on_entry_fill(kite, order_id, pending, broker_order)


def _on_entry_fill(
    kite: KiteConnect,
    order_id: str,
    pending: Dict[str, Any],
    broker_order: Dict[str, Any],
) -> None:
    if state_machine.get_state() != SystemState.LIVE_MODE:
        return

    if not _exchange_slm_enabled():
        logger.debug("ENABLE_EXCHANGE_SLM=false — skipping exchange protection on entry fill")
        return

    stop_price = pending.get("protective_stop") or pending.get("stop_price")
    if stop_price is None:
        logger.debug("No protective_stop on pending order %s — SL-M not placed", order_id)
        return

    symbol = pending["symbol"]
    exchange = pending.get("exchange") or RiskGatekeeper.resolve_exchange(symbol)
    index_key = pending.get("index_key") or _normalize_index_key(symbol)
    filled = int(broker_order.get("filled_quantity", 0) or pending.get("quantity", 0))
    avg_price = float(broker_order.get("average_price", 0) or 0)

    fill_meta = {
        "symbol": symbol,
        "quantity": filled,
        "transaction_type": pending.get("transaction_type", "BUY"),
        "avg_price": avg_price,
        "stop_price": float(stop_price),
        "exchange": exchange,
        "index_key": index_key,
    }

    try:
        from .exchange_protection import exchange_protection

        result = exchange_protection.on_entry_fill(kite, fill_meta)
        audit_logger.record("order_lifecycle.entry_fill", {
            "order_id": order_id,
            "fill_meta": fill_meta,
            "protection_result": result,
        })
        if not result.get("success"):
            logger.warning(
                "Exchange protection failed after entry fill %s: %s",
                order_id,
                result.get("message"),
            )
    except Exception as exc:
        logger.error("order_lifecycle entry fill hook failed for %s: %s", order_id, exc)
        audit_logger.record("order_lifecycle.entry_fill_error", {
            "order_id": order_id,
            "error": str(exc),
        })


def _on_exit_fill(kite: KiteConnect, symbol: str) -> None:
    if state_machine.get_state() != SystemState.LIVE_MODE:
        return

    index_key = _normalize_index_key(symbol)

    try:
        from .exchange_protection import exchange_protection

        result = exchange_protection.on_exit_fill(index_key, kite=kite)
        audit_logger.record("order_lifecycle.exit_fill", {
            "symbol": symbol,
            "index_key": index_key,
            "protection_result": result,
        })
    except Exception as exc:
        logger.error("order_lifecycle exit fill hook failed for %s: %s", symbol, exc)
        audit_logger.record("order_lifecycle.exit_fill_error", {
            "symbol": symbol,
            "error": str(exc),
        })