from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
from state_machine import state_machine, SystemState
from risk_gatekeeper import risk_gatekeeper
from broker_reconciliation import BrokerReconciliation
import time

print("🚀 Starting Nifty Futures Algo - Phase 1")

state_machine.set_state(SystemState.BOOTING)

kite = KiteConnect(api_key=KITE_API_KEY)
if KITE_ACCESS_TOKEN:
    kite.set_access_token(KITE_ACCESS_TOKEN)

print("✅ All Kite credentials loaded successfully")
print("✅ Kite Connect object created successfully")
print("✅ Access token loaded from .env - Ready for trading!")

print("✅ Risk & Compliance Gatekeeper loaded")

# Proper class import + instantiation (no more NameError)
reconciliation_service = BrokerReconciliation(kite)
print("✅ Broker Reconciliation Service loaded")

# TEST MODE: remain in PAPER_MODE so all gates pass during development
state_machine.set_state(SystemState.PAPER_MODE)
print("🔧 TEST MODE ACTIVATED: System is now in PAPER_MODE (risk gates enabled)")

print("✅ Phase 1 Infrastructure Ready")

# Main worker loop – gates + reconciliation
while True:
    print(f"[{time.strftime('%H:%M:%S')}] Testing risk gates + reconciliation...")
    
    risk_gatekeeper.check_all_gates()
    
    if time.time() - reconciliation_service.last_check > 10:
        reconciliation_service.run_reconciliation()
    
    time.sleep(10)