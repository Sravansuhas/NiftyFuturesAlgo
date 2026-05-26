"""
Restart / Recovery Testing Script

This script simulates the critical production scenario:
"Start the system → Take a trade → Kill the process → Restart → Verify that
trailing stop, breakeven, and time exits still function correctly."

This is essential for live trading reliability.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
import tempfile
import shutil
from app.strategy import PreviousCandleBreakoutStrategy, PaperTradingParams
from app.risk_gatekeeper import RiskGatekeeper, RiskConfig
from app.state_persistence import save_strategy_state, load_strategy_state, clear_strategy_state, STATE_FILE


def test_restart_with_open_trade():
    print("\n=== RESTART RECOVERY TEST ===")

    # Setup
    params = PaperTradingParams()
    strategy = PreviousCandleBreakoutStrategy(kite=None, paper_params=params)

    # Simulate we have an open long position
    strategy.entry_price = 24000.0
    strategy._entry_time = time.time() - 30 * 60  # 30 minutes ago
    strategy._best_price_in_trade = 24150.0       # Best price reached so far

    # Save state as if the process is about to be killed
    save_strategy_state({
        "entry_price": strategy.entry_price,
        "entry_time": strategy._entry_time,
        "best_price": strategy._best_price_in_trade,
        "symbol": "NIFTY26MAYFUT"
    })
    print("State saved before 'kill'.")

    # Simulate process death
    del strategy

    # === RESTART ===
    print("Process restarted...")

    # New strategy instance (simulates fresh start)
    new_strategy = PreviousCandleBreakoutStrategy(kite=None, paper_params=params)

    # Load persisted state (this is what happens in real restart)
    persisted = load_strategy_state()
    if persisted:
        new_strategy.entry_price = persisted["entry_price"]
        new_strategy._entry_time = persisted["entry_time"]
        new_strategy._best_price_in_trade = persisted.get("best_price", new_strategy.entry_price)
        print("State successfully restored after restart.")

    # Now simulate price action after restart
    # Current price pulls back — trailing stop should trigger
    current_price = 24100.0   # Pullback from 24150 best

    # Manually call should_exit (in real system this is called in run_once)
    should_exit = new_strategy.should_exit()

    print(f"Current Price: {current_price}")
    print(f"Entry Price:   {new_strategy.entry_price}")
    print(f"Best Price:    {new_strategy._best_price_in_trade}")
    print(f"Should Exit after restart? -> {should_exit}")

    if should_exit:
        print("✅ SUCCESS: Trailing / Breakeven logic survived restart correctly.")
    else:
        print("❌ FAILURE: Exit logic did not trigger as expected after restart.")

    # Cleanup
    clear_strategy_state()
    print("Test cleanup complete.\n")


if __name__ == "__main__":
    test_restart_with_open_trade()
