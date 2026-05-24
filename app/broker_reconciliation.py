from kiteconnect import KiteConnect
from state_machine import state_machine, SystemState
from risk_gatekeeper import risk_gatekeeper
import time


class BrokerReconciliation:
    def __init__(self, kite: KiteConnect):
        self.kite = kite
        self.last_check = time.time()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3  # Circuit breaker threshold
        print("✅ Broker Reconciliation Service initialized")

    def run_reconciliation(self):
        """Runs every 5-10 seconds to check broker vs internal state"""
        try:
            # === GET POSITIONS FROM BROKER ===
            positions = self.kite.positions()
            
            # Edge case: null or malformed response
            if positions is None:
                raise ValueError("Broker returned null positions")
            
            broker_net = positions.get("net", [])
            if not isinstance(broker_net, list):
                raise ValueError(f"Broker 'net' is not a list: {type(broker_net)}")

            print(f"🔍 Reconciliation check — Broker net positions: {len(broker_net)}")

            # === SYNC WITH RISK GATEKEEPER ===
            try:
                risk_gatekeeper.sync_with_broker(broker_net)
                self.consecutive_failures = 0  # Reset on success
            except Exception as e:
                print(f"❌ Risk gatekeeper sync failed: {e}")
                state_machine.set_state(SystemState.RECONCILIATION_FAILED)
                raise

            self.last_check = time.time()

        except ConnectionError as e:
            print(f"❌ Network error during reconciliation: {e}")
            self._handle_reconciliation_failure("CONNECTION_ERROR")
        except Exception as e:
            print(f"❌ Reconciliation failed: {e}")
            self._handle_reconciliation_failure("UNKNOWN_ERROR")

    def _handle_reconciliation_failure(self, error_type: str):
        """Centralized failure handling with circuit breaker logic"""
        self.consecutive_failures += 1
        
        if self.consecutive_failures >= self.max_consecutive_failures:
            print(f"🚨 Circuit breaker triggered after {self.consecutive_failures} failures ({error_type})")
            state_machine.set_state(SystemState.BROKER_DISCONNECTED)
        else:
            print(f"⚠️  Reconciliation failure #{self.consecutive_failures}/{self.max_consecutive_failures} ({error_type})")
