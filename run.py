"""
Aegis - Single Command Runner

This is the recommended way to run the entire system.

Usage:
    python run.py
    python run.py --dev                     # Best for closed-market development & testing
    python run.py --dev --sim-vol 2.0       # More lively simulated price movement
    python run.py --dev --fixed-time "2026-06-02 11:30:00"

Dev Mode (--dev) automatically enables:
  • FORCE_DRY_RUN=true (100% safe paper)
  • DEV_FORCE_MARKET_OPEN=true (bypasses all calendar checks for testing entry logic)
  • Richer diagnostic logging
  • Optional: controllable simulation volatility + fixed synthetic time

What this does:
- Starts the full trading logic (strategy + risk management + reconciliation) in a background thread.
- Starts the professional web dashboard on http://localhost:8050
- Everything runs in ONE Python process → perfect shared state (no more "dead GUI" problem).
- All trading logs continue to appear in this terminal.
- Dashboard shows real-time data from the actual running engine.

This is lightweight, safe, and the current best practice for this project.
"""

import sys
import asyncio
import json
import socket
import threading
import signal
import time
import os
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8050"))
DASHBOARD_PROBE_HOST = "127.0.0.1"

# Windows ProactorEventLoop + uvicorn accept() can raise WinError 64 and kill :8050.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Shared shutdown event for graceful termination
shutdown_event = threading.Event()

# Import the real trading logic (we will monkey-patch the shutdown event)
import app.main as main_module
main_module.shutdown_event = shutdown_event

from app.branding import PROJECT_NAME
from app.main import main as run_trading_loop

# Import the dashboard
from web.dashboard import app as dashboard_app

import uvicorn
from uvicorn import Config, Server


def _frontend_build_stale() -> bool:
    """True when production UI bundle is missing or older than React source."""
    root = Path(__file__).resolve().parent
    dist_index = root / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        return True
    dist_mtime = dist_index.stat().st_mtime
    src_root = root / "frontend" / "src"
    if not src_root.exists():
        return False
    newest_src = max(p.stat().st_mtime for p in src_root.rglob("*") if p.is_file())
    return newest_src > dist_mtime


def _ensure_frontend_build(*, force: bool = False) -> None:
    """Build React UI into frontend/dist when source changed (single-port :8050/ui/)."""
    if not force and not _frontend_build_stale():
        return
    root = Path(__file__).resolve().parent
    frontend_dir = root / "frontend"
    if not (frontend_dir / "package.json").exists():
        return
    print("[RUNNER] Building Aegis UI (frontend/dist) — source newer than bundle…")
    import subprocess

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    try:
        subprocess.run(
            [npm, "run", "build"],
            cwd=str(frontend_dir),
            check=True,
        )
        print("[RUNNER] UI build complete → http://localhost:8050/ui/settings")
    except FileNotFoundError:
        print("[RUNNER] npm not found — skip UI build. Use: cd frontend && npm run dev")
    except subprocess.CalledProcessError as exc:
        print(f"[RUNNER] UI build failed (exit {exc.returncode}). Dev fallback: npm run dev → :5173")


def _load_portal_trading_controls():
    """Apply data/trading_controls.json to os.environ before engine starts."""
    try:
        from app.trading_controls import apply_runtime_to_environment

        apply_runtime_to_environment()
    except Exception as exc:
        print(f"[RUNNER] Trading controls note: {exc}")


def start_trading_in_background():
    """Run the main trading loop in a daemon thread."""
    _load_portal_trading_controls()
    print("[RUNNER] Starting trading engine in background thread...")
    thread = threading.Thread(target=run_trading_loop, daemon=True, name="TradingEngine")
    thread.start()
    return thread


def start_dashboard(server: Server):
    """Start the FastAPI dashboard using Server for better shutdown control."""
    print(f"[RUNNER] Web dashboard live at http://127.0.0.1:{DASHBOARD_PORT}")
    server.run()


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _probe_engine_health() -> dict | None:
    try:
        url = f"http://{DASHBOARD_PROBE_HOST}:{DASHBOARD_PORT}/health"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _wait_for_api_ready(timeout: float = 20.0) -> bool:
    """Block until /health responds so the UI does not race a slow engine boot."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe_engine_health():
            print(f"[RUNNER] API ready at http://{DASHBOARD_PROBE_HOST}:{DASHBOARD_PORT}/health")
            return True
        time.sleep(0.25)
    print(f"[RUNNER] WARNING: /health not ready after {timeout:.0f}s — refresh the UI shortly")
    return False


def _guard_single_instance() -> None:
    """Prevent a second run.py from failing mid-start with WinError 10048."""
    if not _port_in_use(DASHBOARD_PROBE_HOST, DASHBOARD_PORT):
        return

    health = _probe_engine_health()
    if health and health.get("status") == "ok":
        ready = health.get("engine_ready")
        print("\n" + "=" * 72)
        print(f"[RUNNER] Aegis is already running on http://localhost:{DASHBOARD_PORT}")
        print(f"[RUNNER] /health → ok, engine_ready={ready}")
        print("[RUNNER] Do not start a second run.py in another terminal.")
        print("[RUNNER] React dev UI: cd frontend && npm run dev  →  http://localhost:5173/ui/dashboard")
        print("[RUNNER] Single-port UI: http://localhost:8050/ui/dashboard")
        print("=" * 72 + "\n")
        sys.exit(0)

    print("\n" + "=" * 72)
    print(f"[RUNNER] ERROR: Port {DASHBOARD_PORT} is already in use on {DASHBOARD_HOST}")
    print("[RUNNER] /health did not respond — another process may own :8050.")
    print("[RUNNER] Free the port (close the other app) or stop the stale python process:")
    print("         Get-Process python | Stop-Process   # PowerShell (use with care)")
    print("=" * 72 + "\n")
    sys.exit(1)


if __name__ == "__main__":
    # ========================================================================
    # CLOSED-MARKET DEVELOPMENT & TESTING SUPPORT (documented 2026-06)
    # ========================================================================
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME} Unified Runner (paper + dashboard + backtest lab)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (especially useful when market is closed):
  python run.py
  python run.py --dev
  python run.py --dev --sim-vol 1.8
  python run.py --dev --fixed-time "2026-06-02 12:45:00"
  python run.py --dev --sim-vol 2.5 --fixed-time "2026-05-15 10:30:00"

The --dev flag (or DEV_MODE=true env var) is the recommended way to develop
and test the full strategy logic, ATR, regime detection, risk gates, and
dashboard behavior without waiting for market hours.
"""
    )
    parser.add_argument("--dev", "--dev-mode", "-d", action="store_true",
                        help="Enable full closed-market development mode: auto-sets FORCE_DRY_RUN + DEV_FORCE_MARKET_OPEN + rich diagnostics")
    parser.add_argument("--sim-vol", type=float, default=None, metavar="MULT",
                        help="Simulation volatility multiplier (e.g. 1.5 = 50%% more movement). Sets DEV_SIM_VOL_MULTIPLIER")
    parser.add_argument("--fixed-time", type=str, default=None, metavar="YYYY-MM-DD HH:MM:SS",
                        help="Force a synthetic market time for reproducible testing of time-based logic (session windows, expiry, etc.)")
    parser.add_argument("--help-dev", action="store_true",
                        help="Show detailed help for closed-market development flags and environment variables")
    parser.add_argument("--login", action="store_true",
                        help="Run Kite auto-login before starting (opens browser, saves token to .env)")
    parser.add_argument("--ensure-token", action="store_true",
                        help="Validate Kite token; auto-login if expired (recommended before market open)")

    args = parser.parse_args()

    if args.help_dev:
        print(__doc__)
        print("\n" + "="*70)
        print("CLOSED-MARKET DEVELOPMENT FLAGS & ENV VARS (detailed)")
        print("="*70)
        print("""
--dev / -d
    The single best command for development when the market is closed.
    Automatically activates:
        FORCE_DRY_RUN=true
        DEV_FORCE_MARKET_OPEN=true   (bypasses calendar in paper mode only)
    Also enables richer [SIGNAL] / [ATR] logging.

--sim-vol <number>
    Controls how lively the simulated prices are in the live engine.
    Example: --sim-vol 2.0  → prices move twice as much (great for testing
    ATR rolling, regime detection, and breakout buffers quickly).
    Equivalent env var: DEV_SIM_VOL_MULTIPLIER=2.0

--fixed-time "2026-06-02 11:30:00"
    Makes the entire system believe it is this specific IST time.
    Perfect for testing:
        - Entry window logic (9:45-15:10)
        - Expiry day caution
        - Time-based exits
        - Daily reset behavior
    The calendar and strategy will behave as if the market is open at that time.
    Equivalent env var: DEV_FIXED_SIM_TIME="2026-06-02 11:30:00"

Safety guarantees (hard-coded):
    • These flags ONLY work when FORCE_DRY_RUN is active (paper).
    • They are completely ignored if the system ever enters LIVE_MODE.
    • All activations are printed loudly at startup and written to the run log.

See docs/DEV_TESTING_GUIDE.md and PHASE0_DIAGNOSTICS_AND_LOGGING.md for full details.
""")
        sys.exit(0)

    # Apply --dev and related flags to environment BEFORE importing main logic
    dev_mode_activated = False
    if args.dev or os.getenv("DEV_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        os.environ["FORCE_DRY_RUN"] = "true"
        os.environ["DEV_FORCE_MARKET_OPEN"] = "true"
        dev_mode_activated = True

        if args.sim_vol is not None:
            os.environ["DEV_SIM_VOL_MULTIPLIER"] = str(args.sim_vol)
        elif not os.getenv("DEV_SIM_VOL_MULTIPLIER"):
            os.environ["DEV_SIM_VOL_MULTIPLIER"] = "1.0"   # explicit default for clarity

        if args.fixed_time:
            os.environ["DEV_FIXED_SIM_TIME"] = args.fixed_time

        print("\n" + "="*72)
        print("🧪  DEV MODE ACTIVATED via --dev / DEV_MODE")
        print("="*72)
        print("   FORCE_DRY_RUN            = true   (all orders forced dry-run)")
        print("   DEV_FORCE_MARKET_OPEN    = true   (calendar bypassed for testing)")
        print("   Simulation + full decision chain now exercisable while market closed")
        print("="*72 + "\n")

    # Handle explicit --sim-vol even without --dev (user may set other flags manually)
    if args.sim_vol is not None:
        os.environ["DEV_SIM_VOL_MULTIPLIER"] = str(args.sim_vol)
        print(f"[DEV] DEV_SIM_VOL_MULTIPLIER set to {args.sim_vol} from --sim-vol")

    if args.fixed_time:
        os.environ["DEV_FIXED_SIM_TIME"] = args.fixed_time
        print(f"[DEV] DEV_FIXED_SIM_TIME set to {args.fixed_time} from --fixed-time")

    # Record that we are in a documented dev session (visible in every run log)
    if dev_mode_activated or os.getenv("DEV_FORCE_MARKET_OPEN"):
        os.environ["DEV_SESSION_ACTIVE"] = "true"

    # ========================================================================
    # Kite token validation / auto-login
    # ========================================================================
    if args.login or args.ensure_token or os.getenv("KITE_ENSURE_TOKEN", "").lower() in {"1", "true", "yes"}:
        from app.kite_auth import start_auto_login, validate_access_token

        ok, profile, err = validate_access_token()
        if args.login or not ok:
            if not ok:
                print(f"[RUNNER] Kite token invalid: {err}")
            print("[RUNNER] Starting Kite auto-login — complete login in your browser...")
            try:
                session = start_auto_login(open_browser=True)
                print(f"[RUNNER] Kite login OK — {session.get('user_name', session.get('user_id'))}")
            except Exception as login_err:
                print(f"[RUNNER] Kite auto-login failed: {login_err}")
                print("[RUNNER] Fix: set Redirect URL to http://127.0.0.1:8765/callback in Kite developer console")
                print("[RUNNER] Or run: python generate_token.py --manual")
                sys.exit(1)
        else:
            print(f"[RUNNER] Kite token valid — {profile.get('user_name')} ({profile.get('user_id')})")

    # ========================================================================
    # Normal startup continues
    # ========================================================================
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"[RUNNER] {PROJECT_NAME} starting at {current_time}")

    from app.startup_checks import run_startup_checks
    startup_ctx = run_startup_checks()
    print(
        f"[RUNNER] Compliance: algo_id={startup_ctx['algo_id']} "
        f"dry_run={startup_ctx['force_dry_run']} "
        f"ip={startup_ctx.get('outbound_ip') or 'unknown'}"
    )

    _guard_single_instance()

    # Build UI before binding :8050 so npm does not delay the API port coming up.
    _ensure_frontend_build()

    frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
    if frontend_dist.exists():
        print("[RUNNER] Aegis UI (built): http://localhost:8050/ui/dashboard")
    else:
        print("[RUNNER] Aegis dev UI: cd frontend && npm run dev  →  http://localhost:5173/ui/dashboard")
    print("[RUNNER] API + legacy dashboard: http://localhost:8050  (Ctrl+C to stop)\n")

    dashboard_config = Config(
        app=dashboard_app,
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    dashboard_server = Server(config=dashboard_config)

    def handle_shutdown(signum, frame):
        print("\n[RUNNER] Shutdown signal received. Initiating graceful shutdown...")
        shutdown_event.set()
        dashboard_server.should_exit = True

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    dashboard_thread = threading.Thread(
        target=start_dashboard,
        args=(dashboard_server,),
        daemon=True,
        name="Dashboard",
    )
    dashboard_thread.start()
    _wait_for_api_ready()

    # Start trading after API is live — avoids "Cannot reach API" while engine warms up.
    trading_thread = start_trading_in_background()

    if dev_mode_activated:
        print("[RUNNER] Dev testing tips:")
        print("   • Watch logs/run_*.log for [SIGNAL] PROPOSED_BUT_REJECTED_BY_GATES")
        print("   • Open http://localhost:8050/ui/backtest for Aegis validation runs")
        print("   • Use Ctrl+C for clean shutdown\n")

    try:
        while dashboard_thread.is_alive() and not shutdown_event.is_set():
            dashboard_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        handle_shutdown(signal.SIGINT, None)
    except Exception as e:
        if "CancelledError" not in type(e).__name__:
            print(f"[RUNNER] Dashboard error: {e}")
    finally:
        print("[RUNNER] Dashboard stopped. Waiting for trading thread to finish...")
        shutdown_event.set()
        dashboard_server.should_exit = True
        dashboard_thread.join(timeout=5)
        trading_thread.join(timeout=5)
        print("[RUNNER] Shutdown complete.")
