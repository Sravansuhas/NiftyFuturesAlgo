import datetime
import time
from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
from state_machine import state_machine, SystemState
from risk_gatekeeper import risk_gatekeeper
from broker_reconciliation import BrokerReconciliation
from token_manager import TokenManager

from strategy import TestStrategy



print("🚀 Starting Nifty Futures Algo - Phase 1")

state_machine.set_state(SystemState.BOOTING)

kite = KiteConnect(api_key=KITE_API_KEY)

# Initialize Token Manager (handles auto-refresh)
token_manager = TokenManager(kite)
from strategy import TestStrategy


print("✅ All Kite credentials loaded successfully")
print("✅ Kite Connect object created successfully")
print("✅ Access token loaded from .env - Ready for trading!")

print("✅ Risk & Compliance Gatekeeper loaded")

reconciliation_service = BrokerReconciliation(kite)
print("✅ Broker Reconciliation Service loaded")

state_machine.set_state(SystemState.PAPER_MODE)
print("🔧 TEST MODE ACTIVATED: System is now in PAPER_MODE (risk gates enabled)")

print("✅ Phase 1 Infrastructure Ready")

# ============================================================
# MAIN WORKER LOOP
# ============================================================
while True:
    # NOTE: Temporary auto dry-run verification block is intentionally disabled.
    # Re-enable only when you want to validate dry_run behavior manually.
    # if not hasattr(risk_gatekeeper, '_auto_dry_run_test_done'):
    #     from market_calendar import is_market_open
    #
    #     print("\n🧪 Testing Automatic dry_run Detection...")
    #     print(f"   Current Time     : {datetime.datetime.now()}")
    #     print(f"   Market Open?     : {is_market_open()}")
    #     print(f"   Expected dry_run : {not is_market_open()}")
    #
    #     result = risk_gatekeeper.place_guarded_order(
    #         kite=kite,
    #         symbol="NIFTY25MAYFUT",
    #         quantity=75,
    #         transaction_type="BUY"
    #     )
    #
    #     print(f"🧪 Auto dry_run Test Result: {result}")
    #     risk_gatekeeper.print_position_status()
    #
    #     risk_gatekeeper._auto_dry_run_test_done = True
    

    # After all initializations
    strategy = TestStrategy(kite)
    strategy.run()
    
    risk_gatekeeper.check_all_gates()
    reconciliation_service.run_reconciliation()
    risk_gatekeeper.print_position_status()

    time.sleep(10)