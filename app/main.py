import sys
import os
import time
import datetime
from pathlib import Path
from typing import Optional

# Allow running this file directly: python app/main.py
# This fixes: "ImportError: attempted relative import with no known parent package"
if __package__ is None or __package__ == "":
    # Add project root to path
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from kiteconnect import KiteConnect

from .broker_reconciliation import BrokerReconciliation
from config import KITE_API_KEY
from .risk_gatekeeper import risk_gatekeeper
from .state_machine import SystemState, state_machine
from .strategy import PreviousCandleBreakoutStrategy, DataFeedError
from .paper_trading_params import DEFAULT_PAPER_PARAMS, AGGRESSIVE_PAPER_PARAMS
from .diagnostics import log_system_status
from .token_manager import TokenManager
from .market_calendar import is_market_open, now_ist
from .state_persistence import load_strategy_state, save_strategy_state, clear_strategy_state


POLL_INTERVAL_SECONDS = 10
RECON_INTERVAL_SECONDS = 15

# === Paper Trading Presets ===
# DEFAULT_PAPER_PARAMS     → Balanced for live testing (recommended starting point)
# AGGRESSIVE_PAPER_PARAMS  → More signals (use only after validating DEFAULT)
#
# You can switch easily:
#   from .paper_trading_params import AGGRESSIVE_PAPER_PARAMS
#   strategy = PreviousCandleBreakoutStrategy(kite, paper_params=AGGRESSIVE_PAPER_PARAMS)


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


def _print_trading_mode_banner():
    """Very prominent banner so user ALWAYS knows if this is paper or live trading."""
    force_dry_run = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
    trading_mode = os.getenv("TRADING_MODE", "").lower()
    current_state = state_machine.get_state()

    is_paper = force_dry_run or trading_mode == "paper" or current_state in (SystemState.PAPER_MODE, SystemState.BOOTING)

    if is_paper:
        banner = "=== PAPER TRADING MODE — All orders simulated (no real money risk) ==="
    else:
        banner = "=== LIVE TRADING MODE — REAL ORDERS, REAL CAPITAL AT RISK ==="
    print(banner)


def main():
    _print_trading_mode_banner()

    state_machine.set_state(SystemState.BOOTING)

    kite = KiteConnect(api_key=KITE_API_KEY)
    token_manager = TokenManager(kite)

    reconciliation_service = BrokerReconciliation(kite)

    state_machine.set_state(SystemState.PAPER_MODE)

    # Use paper trading params for better signal frequency during testing
    strategy = PreviousCandleBreakoutStrategy(kite, paper_params=DEFAULT_PAPER_PARAMS)

    # Robust restart recovery (silent unless needed)
    load_strategy_state()

    # Lightweight startup note (full params available in dashboard)
    print("[MAIN] Paper trading engine initialized (see dashboard for full params & diagnostics)")

    last_recon = 0.0
    last_day = None
    last_status = 0.0
    STATUS_INTERVAL = 600  # seconds (10 minutes) — clean terminal by default; use dashboard for live view

    print("[MAIN] Engine running. Terminal is calm — use dashboard for live details.")

    while True:
        try:
            last_day = _maybe_reset_daily_metrics(last_day)

            # Strategy decision step
            try:
                strategy.run_once()
            except DataFeedError as dfe:
                print(f"DATA FEED ERROR (LIVE safety): {dfe}")
                state_machine.set_state(SystemState.TRADING_DISABLED)
            except Exception as se:
                print(f"Strategy step error (non-fatal): {se}")

            # Risk gates
            risk_gatekeeper.check_all_gates()

            # Periodic broker reconciliation
            now = time.time()
            if now - last_recon >= RECON_INTERVAL_SECONDS:
                reconciliation_service.run_reconciliation()
                last_recon = now

            # Only print position when it actually changes (reduces noise dramatically)
            # Position status is now mostly available via dashboard
            # risk_gatekeeper.print_position_status()  # Commented out for cleaner terminal

            # Periodic system health diagnostic (very useful during paper trading)
            if now - last_status >= STATUS_INTERVAL:
                status = {
                    "State": state_machine.get_state().value,
                    "Position": risk_gatekeeper.get_position_quantity(),
                    "Trades Today": risk_gatekeeper.trades_today,
                    "Daily Loss": round(risk_gatekeeper.daily_loss, 2),
                    "Token Valid": token_manager.is_token_valid(),
                    "Last Recon (s ago)": round(now - last_recon, 1),
                    "Live ATR (pts)": round(getattr(strategy, 'current_atr', 0), 2),
                }
                log_system_status(status)
                last_status = now

            # Gentle token health hint
            if not token_manager.is_token_valid():
                print("⚠️ Token may need refresh (check TokenManager)")

            # Periodic state persistence for restart safety (every ~5 minutes)
            if int(now) % 300 == 0 and not risk_gatekeeper.is_flat():
                save_strategy_state({
                    "entry_price": getattr(strategy, "entry_price", 0),
                    "entry_time": getattr(strategy, "_entry_time", now),
                    "best_price": getattr(strategy, "_best_price_in_trade", getattr(strategy, "entry_price", 0)),
                    "symbol": getattr(strategy, "symbol", None)
                })

        except DataFeedError as dfe:
            print(f"[MAIN] Data feed issue: {dfe}")
            if state_machine.get_state() not in (SystemState.TRADING_DISABLED, SystemState.EMERGENCY_HALT):
                print("[MAIN] Data feed degraded — continuing with caution")
        except Exception as exc:
            print(f"Main loop error: {exc}")
            if state_machine.is_trading_allowed():
                state_machine.set_state(SystemState.TRADING_DISABLED)

        # Graceful shutdown check
        shutdown_event = getattr(sys.modules[__name__], 'shutdown_event', None)
        if shutdown_event and shutdown_event.is_set():
            print("[MAIN] Shutdown signal received. Saving final state...")
            break

        time.sleep(POLL_INTERVAL_SECONDS)

    # Final state save on exit
    if not risk_gatekeeper.is_flat():
        save_strategy_state({
            "entry_price": getattr(strategy, "entry_price", 0),
            "entry_time": getattr(strategy, "_entry_time", time.time()),
            "best_price": getattr(strategy, "_best_price_in_trade", getattr(strategy, "entry_price", 0)),
            "symbol": getattr(strategy, "symbol", None)
        })
    else:
        clear_strategy_state()
    print("[MAIN] Trading loop exited cleanly.")


if __name__ == "__main__":
    main()
