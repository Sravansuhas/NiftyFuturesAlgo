"""
NiftyFuturesAlgo - Single Command Runner

This is the recommended way to run the entire system.

Usage:
    python run.py

What this does:
- Starts the full trading logic (strategy + risk management + reconciliation) in a background thread.
- Starts the professional web dashboard on http://localhost:8050
- Everything runs in ONE Python process → perfect shared state (no more "dead GUI" problem).
- All trading logs continue to appear in this terminal.
- Dashboard shows real-time data from the actual running engine.

This is lightweight, safe, and the current best practice for this project.
"""

import sys
import threading
import signal
import time
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).resolve()
sys.path.insert(0, str(project_root))

# Shared shutdown event for graceful termination
shutdown_event = threading.Event()

# Import the real trading logic (we will monkey-patch the shutdown event)
import app.main as main_module
main_module.shutdown_event = shutdown_event

from app.main import main as run_trading_loop

# Import the dashboard
from web.dashboard import app as dashboard_app

import uvicorn
from uvicorn import Config, Server


def start_trading_in_background():
    """Run the main trading loop in a daemon thread."""
    print("[RUNNER] Starting trading engine in background thread...")
    thread = threading.Thread(target=run_trading_loop, daemon=True, name="TradingEngine")
    thread.start()
    return thread


def start_dashboard():
    """Start the FastAPI dashboard using Server for better shutdown control."""
    print("[RUNNER] Web dashboard live at http://localhost:8050")

    config = Config(
        app=dashboard_app,
        host="0.0.0.0",
        port=8050,
        log_level="warning",
        access_log=False,
    )
    server = Server(config=config)

    # This will block until shutdown is triggered
    server.run()


if __name__ == "__main__":
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"[RUNNER] NiftyFuturesAlgo starting at {current_time}")

    # Start trading logic in background
    trading_thread = start_trading_in_background()

    # Give it a moment to initialize
    time.sleep(2.5)

    print("[RUNNER] Ready. Dashboard: http://localhost:8050  (Ctrl+C to stop)\n")

    def handle_shutdown(signum, frame):
        print("\n[RUNNER] Shutdown signal received. Initiating graceful shutdown...")
        shutdown_event.set()
        # Give uvicorn a moment to stop accepting requests
        time.sleep(0.5)

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        start_dashboard()
    except Exception as e:
        print(f"[RUNNER] Dashboard error: {e}")
    finally:
        print("[RUNNER] Dashboard stopped. Waiting for trading thread to finish...")
        shutdown_event.set()
        # Wait for trading thread to react to shutdown_event (it should poll it)
        trading_thread.join(timeout=5)
        print("[RUNNER] Shutdown complete.")
