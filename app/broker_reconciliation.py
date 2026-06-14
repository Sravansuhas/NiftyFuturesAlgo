import os
import time
import logging

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from .audit_logger import audit_logger
from .order_lifecycle import order_lifecycle
from .risk_gatekeeper import risk_gatekeeper
from .state_machine import SystemState, state_machine

logger = logging.getLogger(__name__)


class BrokerReconciliation:
    def __init__(self, kite: KiteConnect):
        self.kite = kite
        self.last_check = time.time()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        # Silent init — reconciliation runs periodically

    def run_reconciliation(self):
        """Check Zerodha order/position state against internal state."""
        try:
            self._sync_order_statuses()

            positions = self.kite.positions()
            if positions is None:
                raise ValueError("Broker returned null positions")

            broker_net = positions.get("net", [])
            if not isinstance(broker_net, list):
                raise ValueError(f"Broker 'net' is not a list: {type(broker_net)}")

            # Only log at DEBUG during live hours to keep terminal calm.
            # Dashboard shows real-time reconciliation status.
            logger.debug(f"Reconciliation check - Broker net positions: {len(broker_net)}")

            is_paper = os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no")

            try:
                risk_gatekeeper.sync_with_broker(broker_net)
                try:
                    from .multi_symbol_risk import multi_risk_manager
                    multi_risk_manager.broker_connected = True
                    mismatches = multi_risk_manager.detect_broker_mismatches(broker_net)
                    if mismatches and not is_paper:
                        logger.critical(
                            "[RECON] Position mismatch detected — halting entries: %s",
                            "; ".join(mismatches),
                        )
                        state_machine.set_state(SystemState.EMERGENCY_HALT)
                        audit_logger.record("recon.mismatch_halt", {"mismatches": mismatches})
                    multi_risk_manager.sync_with_broker(broker_net)
                except Exception as multi_exc:
                    logger.debug(f"[RECON] Multi-symbol sync note: {multi_exc}")
                self.consecutive_failures = 0
            except Exception as exc:
                if is_paper:
                    logger.debug(f"[PAPER] Reconciliation sync warning (non-fatal): {exc}")
                    self.consecutive_failures = 0  # don't count against paper
                else:
                    logger.warning(f"Risk gatekeeper sync failed: {exc}")
                    state_machine.set_state(SystemState.RECONCILIATION_FAILED)
                    raise

            self.last_check = time.time()

        except TokenException as exc:
            logger.warning("Reconciliation halted — Kite access token expired: %s", exc)
            try:
                from .kite_connect_rules import on_token_exception

                on_token_exception("reconciliation")
            except Exception:
                pass
            try:
                from .multi_symbol_risk import multi_risk_manager
                multi_risk_manager.broker_connected = False
            except Exception:
                pass
            self._handle_reconciliation_failure("TOKEN_EXPIRED")
        except ConnectionError as exc:
            logger.warning(f"Network error during reconciliation: {exc}")
            try:
                from .multi_symbol_risk import multi_risk_manager
                multi_risk_manager.broker_connected = False
            except Exception:
                pass
            self._handle_reconciliation_failure("CONNECTION_ERROR")
        except Exception as exc:
            logger.warning(f"Reconciliation failed: {exc}")
            try:
                from .multi_symbol_risk import multi_risk_manager
                multi_risk_manager.broker_connected = False
            except Exception:
                pass
            self._handle_reconciliation_failure("UNKNOWN_ERROR")

    def _sync_order_statuses(self):
        """Track submitted orders until Zerodha reports a terminal status."""
        if not order_lifecycle.get_pending_orders():
            return

        orders = self.kite.orders()
        if not isinstance(orders, list):
            raise ValueError("Broker orders response is not a list")

        broker_orders = {str(order.get("order_id")): order for order in orders if order.get("order_id")}
        for order_id, pending in list(order_lifecycle.get_pending_orders().items()):
            broker_order = broker_orders.get(str(order_id))
            if not broker_order:
                continue

            status = str(broker_order.get("status", "")).upper()
            if status in {"COMPLETE", "REJECTED", "CANCELLED", "UPDATE"}:
                filled = broker_order.get("filled_quantity", 0) or 0
                audit_logger.record("order.status", {
                    "order_id": order_id,
                    "status": status,
                    "average_price": broker_order.get("average_price"),
                    "filled_quantity": filled,
                    "pending": pending,
                })
                lifecycle_result = order_lifecycle.handle_broker_update(
                    order_id, broker_order, kite=self.kite
                )
                if lifecycle_result.get("processed") and status == "COMPLETE":
                    if filled > 0 and filled < pending.get("quantity", filled):
                        logger.info(
                            f"Partial fill detected for {order_id}: {filled}/{pending.get('quantity')}"
                        )

    def _handle_reconciliation_failure(self, error_type: str):
        self.consecutive_failures += 1

        if self.consecutive_failures >= self.max_consecutive_failures:
            logger.warning(f"Circuit breaker triggered after {self.consecutive_failures} failures ({error_type})")
            state_machine.set_state(SystemState.BROKER_DISCONNECTED)
        else:
            logger.warning(f"Reconciliation failure #{self.consecutive_failures}/{self.max_consecutive_failures} ({error_type})")
