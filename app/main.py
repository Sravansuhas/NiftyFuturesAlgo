import time
import datetime
from typing import Optional

from kiteconnect import KiteConnect

from .broker_reconciliation import BrokerReconciliation
from config import KITE_API_KEY
from .risk_gatekeeper import risk_gatekeeper
from .state_machine import SystemState, state_machine
from .strategy import PreviousCandleBreakoutStrategy, DataFeedError
from .token_manager import TokenManager
from .market_calendar import is_market_open, now_ist


POLL_INTERVAL_SECONDS = 10
RECON_INTERVAL_SECONDS = 15


def _maybe_reset_daily_metrics(last_reset_date: Optional[datetime.date]) -> datetime.date:
    """Call at the start of each trading day (09:15 IST) to reset risk daily counters."""
    today = now_ist().date()
    if last_reset_date != today:
        # Only reset if we are inside or after market open on a new trading day
        if is_market_open():
            risk_gatekeeper.reset_daily()
            print("[MAIN] Daily risk metrics reset for new trading day")
            return today
    return last_reset_date or today


def main():
    print("Starting NiftyFuturesAlgo — Complete Infrastructure (Phase 1+2)")
    state_machine.set_state(SystemState.BOOTING)

    kite = KiteConnect(api_key=KITE_API_KEY)
    token_manager = TokenManager(kite)

    print("Kite Connect object created")
    print("Access token loaded from .env" if token_manager.access_token else "No access token loaded")
    print("Risk & Compliance Gatekeeper loaded")

    reconciliation_service = BrokerReconciliation(kite)
    print("Broker Reconciliation Service loaded")

    state_machine.set_state(SystemState.PAPER_MODE)
    print("PAPER_MODE activated — all orders guarded by risk gates + force_dry_run if set")

    strategy = PreviousCandleBreakoutStrategy(kite)
    print("Previous Candle Breakout strategy initialized (previous candle seeded from history where possible)")

    last_recon = 0.0
    last_day = None
    print("Main driver loop ready. Polling every 10s with recon every ~15s.")

    while True:
        try:
            last_day = _maybe_reset_daily_metrics(last_day)

            # Strategy decision step (respects all gates internally via risk_gatekeeper)
            try:
                strategy.run_once()
            except DataFeedError as dfe:
                print(f"DATA FEED ERROR (LIVE safety): {dfe}")
                state_machine.set_state(SystemState.TRADING_DISABLED)
            except Exception as se:
                print(f"Strategy step error (non-fatal): {se}")

            # Risk gates (can transition to CIRCUIT etc.)
            risk_gatekeeper.check_all_gates()

            # Periodic broker reconciliation (authoritative state)
            now = time.time()
            if now - last_recon >= RECON_INTERVAL_SECONDS:
                reconciliation_service.run_reconciliation()
                last_recon = now

            risk_gatekeeper.print_position_status()

            # Gentle token health hint
            if not token_manager.is_token_valid():
                print("⚠️ Token may need refresh (check TokenManager)")

        except DataFeedError as dfe:
            print(f"FATAL DATA FEED in main: {dfe} — halting new entries")
            state_machine.set_state(SystemState.TRADING_DISABLED)
        except Exception as exc:
            print(f"Main loop error: {exc}")
            # Fail closed
            if state_machine.is_trading_allowed():
                state_machine.set_state(SystemState.TRADING_DISABLED)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
