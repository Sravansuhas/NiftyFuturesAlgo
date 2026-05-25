import time

from kiteconnect import KiteConnect

from broker_reconciliation import BrokerReconciliation
from config import KITE_API_KEY
from risk_gatekeeper import risk_gatekeeper
from state_machine import SystemState, state_machine
from strategy import PreviousCandleBreakoutStrategy
from token_manager import TokenManager


POLL_INTERVAL_SECONDS = 10


def main():
    print("Starting Nifty Futures Algo - Phase 1")
    state_machine.set_state(SystemState.BOOTING)

    kite = KiteConnect(api_key=KITE_API_KEY)
    token_manager = TokenManager(kite)

    print("Kite Connect object created successfully")
    print("Access token loaded from .env" if token_manager.access_token else "No access token loaded")
    print("Risk & Compliance Gatekeeper loaded")

    reconciliation_service = BrokerReconciliation(kite)
    print("Broker Reconciliation Service loaded")

    state_machine.set_state(SystemState.PAPER_MODE)
    print("TEST MODE ACTIVATED: System is now in PAPER_MODE (risk gates enabled)")

    strategy = PreviousCandleBreakoutStrategy(kite)
    print("Phase 1 Infrastructure Ready")

    while True:
        try:
            strategy.run_once()
            risk_gatekeeper.check_all_gates()
            reconciliation_service.run_reconciliation()
            risk_gatekeeper.print_position_status()
        except Exception as exc:
            print(f"Main loop error: {exc}")
            state_machine.set_state(SystemState.TRADING_DISABLED)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
