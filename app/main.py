import sys
import os
import time
import datetime
from pathlib import Path
from typing import Optional
import datetime as dt  # for nicer formatting in logs

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
from .diagnostics import log_system_status, logger
from .token_manager import TokenManager
from .market_calendar import is_market_open, now_ist
from .state_persistence import load_strategy_state, save_strategy_state, clear_strategy_state
from .data_feed import KiteWebSocketFeed
from .instruments_manager import instruments_manager


POLL_INTERVAL_SECONDS = 10

# During the first 2 minutes after start (and market open), poll a bit faster to get real data quicker
FAST_POLL_SECONDS = 5
FAST_POLL_DURATION = 120  # seconds
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

    # Hard safety for Monday paper trading
    force_dry = os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no")
    if not force_dry:
        print("!!! WARNING: FORCE_DRY_RUN is not true. For safety, forcing paper mode.")
        os.environ["FORCE_DRY_RUN"] = "true"

    # === DIAGNOSTIC LOGGING (Kite best practice + easy remote diagnosis) ===
    # Every run gets its own file: logs/run_YYYYMMDD_HHMMSS.log
    # User can simply send the latest file in logs/ for full diagnosis.
    from .diagnostic_logger import diag, get_latest_log_file
    log_path = diag.initialize()
    print(f"[DIAG] Full diagnostic log for this run: {log_path}")
    print(f"[DIAG] To help debug, just zip the latest file in logs/ and share it.")

    state_machine.set_state(SystemState.BOOTING)

    kite = KiteConnect(api_key=KITE_API_KEY)
    token_manager = TokenManager(kite)

    reconciliation_service = BrokerReconciliation(kite)

    state_machine.set_state(SystemState.PAPER_MODE)

    # Clean ledger for a fresh paper demo run (best practice so Recent Trades + Diagnostics show only this session)
    if force_dry:
        try:
            from .trade_ledger import trade_ledger
            if trade_ledger.path.exists():
                trade_ledger.path.write_text("")  # fresh start for the visible table
        except Exception:
            pass

    # === Monday Multi-Index Paper Trading (NIFTY + BANKNIFTY + SENSEX FUT) ===
    from .multi_symbol_risk import multi_risk_manager
    from . import live_snapshots

    # Create one strategy instance per index using the central InstrumentsManager
    from .instruments_manager import instruments_manager
    instruments_manager.kite = kite
    instruments_manager.load()  # Load NFO + BFO instruments once at startup

    symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]
    strategies = {}
    for sym in symbols:
        strat = PreviousCandleBreakoutStrategy(kite, paper_params=DEFAULT_PAPER_PARAMS, risk_manager=multi_risk_manager)
        strat._initialize_index_future(sym)
        strategies[sym] = strat

        # Better startup logging for selected contracts
        contract_info = f"{strat.symbol} (token: {strat.instrument_token})"
        logger.info(f"[MAIN] Initialized strategy for {sym:10} -> {contract_info}")

    # Use the multi-symbol risk manager for paper trading
    # (global daily risk still enforced)

    # Robust restart recovery (silent unless needed)
    load_strategy_state()

    # Lightweight startup note (full params available in dashboard)
    print("[MAIN] Paper trading engine initialized (see dashboard for full params & diagnostics)")
    print("[MAIN] === MONDAY PAPER MODE: NIFTY + BANKNIFTY + SENSEX FUTURES ONLY ===")
    print("[MAIN] All orders are FORCED to DRY-RUN. No real capital at risk.")

    # Contract selection summary
    print("\n[CONTRACTS] Active futures selected:")
    for sym, strat in strategies.items():
        print(f"  {sym:10} → {strat.symbol:20} (token: {strat.instrument_token})")

    # === WebSocket Data Feed (currently disabled by default) ===
    # KiteTicker uses Twisted and has reactor/threading conflicts with uvicorn when started from background threads.
    # For now we use improved polling + strong paper simulation.
    # To experiment: set ENABLE_WEBSOCKET=true (expect possible shutdown issues).
    ws_feed = None
    if os.getenv("ENABLE_WEBSOCKET", "false").lower() == "true":
        access_token = os.getenv("KITE_ACCESS_TOKEN", "")
        if access_token:
            try:
                ws_feed = KiteWebSocketFeed(kite, KITE_API_KEY, access_token)
                ws_feed.start()

                tokens = [s.instrument_token for s in strategies.values() if s.instrument_token]
                if tokens:
                    ws_feed.subscribe(tokens)
                    logger.info(f"[WS] Subscribed to {len(tokens)} instruments")
            except Exception as ws_err:
                logger.warning(f"[WS] Failed to start WebSocket feed: {ws_err}. Falling back to polling + simulation.")
                ws_feed = None
        else:
            logger.warning("[WS] ENABLE_WEBSOCKET=true but no access token.")
    else:
        logger.info("[WS] WebSocket disabled (polling + simulation active). Set ENABLE_WEBSOCKET=true to try WebSocket.")

    # === WebSocket Migration Preparation (Kite Best Practice) ===
    # Kite strongly recommends WebSocket (KiteTicker) with MODE_LTP or MODE_QUOTE over REST polling.
    # Current polling path is used because of Twisted reactor conflicts with uvicorn/FastAPI.
    # Future: Run ticker in a separate process or use asyncio-native client.
    # When ENABLE_WEBSOCKET=true, the system will attempt proper WS for lower latency + better SENSEX freshness.
    # All price sources are already logged via diagnostic_logger for easy bottleneck analysis.

    last_recon = 0.0
    last_day = None
    last_status = 0.0
    STATUS_INTERVAL = 600  # seconds (10 minutes) — clean terminal by default; use dashboard for live view

    print("[MAIN] Engine running. Terminal is calm — use dashboard for live details.")

    while True:
        try:
            last_day = _maybe_reset_daily_metrics(last_day)

            # Get timestamp at the very start of the loop to prevent UnboundLocalError
            now = time.time()

            # === Run all 3 index strategies (Monday paper trading) ===
            for sym, strat in strategies.items():
                try:
                    strat.run_once()
                except DataFeedError as dfe:
                    print(f"DATA FEED ERROR [{sym}] (LIVE safety): {dfe}")
                    state_machine.set_state(SystemState.TRADING_DISABLED)
                except Exception as se:
                    print(f"Strategy step error [{sym}] (non-fatal): {se}")

            # Rich per-symbol logging + live snapshots (very important for UX)
            # Snapshots updated EVERY loop (cheap, makes dashboard cards + T/SL live for all 3)
            # Pretty terminal block only every ~7s or warm-up to keep terminal calm
            for sym, strat in strategies.items():
                snap = strat.get_signal_snapshot()
                live_snapshots.update_snapshot(sym, snap)

            if (now - (last_recon or now) < 60) or (int(now) % 7 == 0):
                print("\n" + "="*70)
                print(f"[3-INDEX PAPER] {dt.datetime.now().strftime('%H:%M:%S')} | FORCED DRY-RUN MODE")
                for sym, strat in strategies.items():
                    snap = live_snapshots.get_snapshot(sym) or strat.get_signal_snapshot()
                    color = "🟢" if snap.get('proposed') == "LONG" else ("🔴" if snap.get('proposed') == "SHORT" else "⚪")
                    source = snap.get("data_source", "UNKNOWN")
                    # Prefer fast responsive ATR in the demo print so it doesn't look hardcoded/locked
                    display_atr = snap.get('fast_atr') or snap.get('atr', 0)
                    conf_val = snap.get('confidence', 0)
                    conf_note = ""
                    if snap.get('position_health_conf') is not None:
                        conf_note = " (pos health)"
                    age = snap.get('data_age_seconds', 0)
                    age_str = f" age:{age}s" if age > 8 else ""
                    print(f"{color} {sym:10} [{source:9}] | {snap.get('proposed','?'):6} | LTP:{snap.get('ltp',0):8.1f} | ATR:{display_atr:5.1f} | "
                          f"T:{snap.get('target',0):8.0f} / SL:{snap.get('stop_loss',0):8.0f} | Conf:{conf_val:.0%}{conf_note}{age_str} | "
                          f"Regime:{(snap.get('regime') or {}).get('volatility','?')}")

                    # Warning only on first few simulated prints during open market
                    if source == "SIMULATED" and is_market_open():
                        if not hasattr(strat, '_sim_warning_shown'):
                            logger.warning(f"[DATA] {sym} using SIMULATED data while market OPEN. Real data not flowing yet.")
                            setattr(strat, '_sim_warning_shown', True)
                print("="*70)

                # Also write the full rich block to the diagnostic log file
                try:
                    from .diagnostic_logger import diag
                    diag.get_logger().info(f"3-INDEX BLOCK @ {dt.datetime.now().strftime('%H:%M:%S')} | {len(strategies)} symbols")
                except Exception:
                    pass

            # Global risk gates (daily loss / drawdown) - still use the original for global limits
            risk_gatekeeper.check_all_gates()

            # Granular diagnostic for multi-symbol risk state (very useful when debugging position tracking)
            try:
                from .diagnostic_logger import diag
                for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
                    pos = multi_risk_manager.positions.get(sym)
                    if pos:
                        diag.get_logger().debug(f"[MULTI_RISK] {sym} qty={pos.quantity} avg={pos.avg_price}")
            except Exception:
                pass

            # Additional per-symbol safety via multi manager (for awareness)
            # (The strategies themselves now route orders through it when risk_manager is passed)

            # Periodic broker reconciliation — more lenient in PAPER mode
            if now - last_recon >= RECON_INTERVAL_SECONDS:
                try:
                    reconciliation_service.run_reconciliation()
                except Exception as recon_err:
                    if os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no"):
                        logger.debug(f"[PAPER] Reconciliation warning (ignored in paper): {recon_err}")
                    else:
                        raise
                last_recon = now

            # Multi-symbol status for dashboard / logging (now uses rich snapshots)
            if now - last_status >= STATUS_INTERVAL:
                status = {
                    "State": state_machine.get_state().value,
                    "Trades Today": risk_gatekeeper.trades_today,
                    "Daily Loss": round(risk_gatekeeper.daily_loss, 2),
                    "Token Valid": token_manager.is_token_valid(),
                    "Last Recon (s ago)": round(now - last_recon, 1),
                }
                for sym, strat in strategies.items():
                    snap = strat.get_signal_snapshot()
                    status[f"{sym}_LTP"] = snap.get("ltp", 0)
                    status[f"{sym}_ATR"] = snap.get("atr", 0)
                    status[f"{sym}_Symbol"] = snap.get("symbol", None)
                    status[f"{sym}_Source"] = snap.get("data_source", "UNKNOWN")

                log_system_status(status)
                last_status = now

            # Gentle token health hint
            if not token_manager.is_token_valid():
                print("⚠️ Token may need refresh (check TokenManager)")

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

        # Use faster polling during initial warm-up when market is open
        poll_interval = FAST_POLL_SECONDS if (now - (last_recon or now) < FAST_POLL_DURATION and is_market_open()) else POLL_INTERVAL_SECONDS
        time.sleep(poll_interval)

    print("[MAIN] Trading loop exited cleanly.")

    # Final diagnostic note + easy zip command for the user
    try:
        from .diagnostic_logger import diag, get_latest_log_file
        diag.shutdown()
        latest = get_latest_log_file()
        if latest:
            print(f"[DIAG] This run's complete diagnostic log: {latest}")
            print(f"[DIAG] Please share this file (or zip of logs/) for fast diagnosis.")
            # Cross-platform friendly zip hint
            print(f"[DIAG] Quick zip command (Windows PowerShell):")
            print(f'         Compress-Archive -Path "{latest}" -DestinationPath "logs\\latest_run.zip"')
            print(f"[DIAG] Quick zip command (Git Bash / WSL / macOS / Linux):")
            print(f'         zip -r logs/latest_run.zip "{latest}"')
    except Exception:
        pass


if __name__ == "__main__":
    main()
