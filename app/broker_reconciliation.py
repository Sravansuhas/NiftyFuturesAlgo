import time

from kiteconnect import KiteConnect

from .audit_logger import audit_logger
from .risk_gatekeeper import risk_gatekeeper
from .state_machine import SystemState, state_machine


class BrokerReconciliation:
    def __init__(self, kite: KiteConnect):
        self.kite = kite
        self.last_check = time.time()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        print("Broker Reconciliation Service initialized")

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

            print(f"Reconciliation check - Broker net positions: {len(broker_net)}")

            try:
                risk_gatekeeper.sync_with_broker(broker_net)
                self.consecutive_failures = 0
            except Exception as exc:
                print(f"Risk gatekeeper sync failed: {exc}")
                state_machine.set_state(SystemState.RECONCILIATION_FAILED)
                raise

            self.last_check = time.time()

        except ConnectionError as exc:
            print(f"Network error during reconciliation: {exc}")
            self._handle_reconciliation_failure("CONNECTION_ERROR")
        except Exception as exc:
            print(f"Reconciliation failed: {exc}")
            self._handle_reconciliation_failure("UNKNOWN_ERROR")

    def _sync_order_statuses(self):
        """Track submitted orders until Zerodha reports a terminal status."""
        if not risk_gatekeeper.pending_orders:
            return

        orders = self.kite.orders()
        if not isinstance(orders, list):
            raise ValueError("Broker orders response is not a list")

        broker_orders = {str(order.get("order_id")): order for order in orders if order.get("order_id")}
        for order_id, pending in list(risk_gatekeeper.pending_orders.items()):
            broker_order = broker_orders.get(str(order_id))
            if not broker_order:
                continue

            status = str(broker_order.get("status", "")).upper()
            if status in {"COMPLETE", "REJECTED", "CANCELLED"}:
                filled = broker_order.get("filled_quantity", 0) or 0
                audit_logger.record("order.status", {
                    "order_id": order_id,
                    "status": status,
                    "average_price": broker_order.get("average_price"),
                    "filled_quantity": filled,
                    "pending": pending,
                })
                # Terminal status — remove from pending (COMPLETE will be reflected in next position sync)
                risk_gatekeeper.pending_orders.pop(order_id, None)
                # Partial fill edge: if partial COMPLETE, risk_gatekeeper on_order_placed can be called by recon if needed
                if status == "COMPLETE" and filled > 0 and filled < pending.get("quantity", filled):
                    print(f"Partial fill detected for {order_id}: {filled}/{pending.get('quantity')}")

    def _handle_reconciliation_failure(self, error_type: str):
        self.consecutive_failures += 1

        if self.consecutive_failures >= self.max_consecutive_failures:
            print(f"Circuit breaker triggered after {self.consecutive_failures} failures ({error_type})")
            state_machine.set_state(SystemState.BROKER_DISCONNECTED)
        else:
            print(f"Reconciliation failure #{self.consecutive_failures}/{self.max_consecutive_failures} ({error_type})")
