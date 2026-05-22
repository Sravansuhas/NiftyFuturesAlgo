from kiteconnect import KiteConnect
from state_machine import state_machine, SystemState
import time

# Import the risk_gatekeeper instance so we can safely reference its state
from risk_gatekeeper import risk_gatekeeper


class BrokerReconciliation:
    def __init__(self, kite: KiteConnect):
        self.kite = kite
        self.last_check = time.time()
        print("✅ Broker Reconciliation Service initialized")

    def run_reconciliation(self):
        """Periodic broker-vs-internal state reconciliation.
        This is a foundational implementation. We will expand the mismatch
        detection logic once we begin placing real orders and maintaining
        an authoritative internal position state.
        """
        try:
            positions = self.kite.positions()
            holdings = positions.get('net', [])

            print(f"🔍 Reconciliation check — Broker net positions: {len(holdings)}")

            # Safe placeholder mismatch detection
            if len(holdings) > 0 and getattr(risk_gatekeeper, 'current_position', None) is None:
                print("🚨 RECONCILIATION MISMATCH DETECTED — external position with no internal record!")
                state_machine.set_state(SystemState.RECONCILIATION_FAILED)

            self.last_check = time.time()

        except Exception as e:
            print(f"❌ Reconciliation failed: {e}")
            state_machine.set_state(SystemState.BROKER_DISCONNECTED)