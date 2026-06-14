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
from .promoted_params import merge_paper_params
from .diagnostics import log_system_status, logger
from .token_manager import TokenManager
from .market_calendar import is_market_open, is_real_market_open, now_ist
from .state_persistence import load_strategy_state, save_strategy_state, clear_strategy_state
from .data_feed import KiteWebSocketFeed
from .instruments_manager import instruments_manager


POLL_INTERVAL_SECONDS = 10

# During the first 2 minutes after start (and market open), poll a bit faster to get real data quicker
FAST_POLL_SECONDS = 5
FAST_POLL_DURATION = 120  # seconds
RECON_INTERVAL_SECONDS = 15


def _flush_state_on_shutdown(strategies, multi_risk_manager) -> None:
    """Persist risk, strategy, and pending-order state before process exit."""
    try:
        from .risk_state_persistence import save_risk_state

        save_risk_state(multi_risk_manager)
        logger.info("[MAIN] Risk state saved on shutdown")
    except Exception as exc:
        logger.warning("[MAIN] Shutdown risk save failed: %s", exc)

    for sym, strat in strategies.items():
        try:
            if hasattr(strat, "persist_state"):
                strat.persist_state()
        except Exception as exc:
            logger.warning("[MAIN] Shutdown state save failed for %s: %s", sym, exc)

    try:
        from .order_lifecycle import order_lifecycle

        order_lifecycle.persist_pending_orders()
        logger.info("[MAIN] Pending orders saved on shutdown")
    except Exception as exc:
        logger.warning("[MAIN] Shutdown pending-order save failed: %s", exc)

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
            try:
                from .multi_symbol_risk import multi_risk_manager
                multi_risk_manager.reset_daily()
            except Exception:
                pass
            print("[MAIN] Daily risk metrics reset for new trading day")
            return today
    return last_reset_date or today


def _engine_should_evaluate_signals() -> bool:
    """Skip strategy entry checks when market is closed (unless dev force-open)."""
    idle_when_closed = os.getenv("ENGINE_IDLE_WHEN_CLOSED", "true").lower() not in {
        "0", "false", "no", "off",
    }
    if not idle_when_closed:
        return True
    dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
    if dev_force and force_dry:
        return True
    return is_real_market_open()


def _init_trade_ledger_session(force_dry: bool) -> None:
    """Persist ledger across restarts; optional archive on explicit clear."""
    try:
        from .trade_ledger import trade_ledger

        session_id = now_ist().strftime("%Y%m%d_%H%M%S")
        trade_ledger.set_session_id(session_id)

        clear_on_start = os.getenv("CLEAR_LEDGER_ON_START", "false").lower() in {
            "1", "true", "yes", "on",
        }
        if clear_on_start and force_dry:
            archived = trade_ledger.archive_current(reason="clear_on_start")
            if archived:
                print(f"[MAIN] Previous ledger archived → {archived}")
            print("[MAIN] CLEAR_LEDGER_ON_START=true — fresh ledger for this run")
        else:
            count = trade_ledger.event_count()
            print(f"[MAIN] Trade ledger persisted ({count} events) — data/trade_ledger.jsonl")

        trade_ledger.record("session.start", {
            "mode": "PAPER" if force_dry else "LIVE",
            "session_id": session_id,
        })
    except Exception as exc:
        logger.warning("[MAIN] Trade ledger init note: %s", exc)


def _verify_price_feeds(strategies, ws_feed, kite) -> None:
    """Cross-check REST vs WebSocket futures LTP and show spot index for comparison."""
    from .instruments_manager import instruments_manager, ltp_key

    print("\n[PRICE CHECK] Futures LTP vs Kite index spot (compare same contract on Kite watchlist):")
    for sym, strat in strategies.items():
        if not strat.symbol:
            continue
        exchange = getattr(strat, "exchange", "BFO" if sym == "SENSEX" else "NFO")
        rest_fut = instruments_manager.fetch_ltp(strat.symbol, exchange)
        ws_fut = None
        if ws_feed and strat.instrument_token:
            ws_fut, _age = ws_feed.get_last_price_with_age(int(strat.instrument_token))
        spot = instruments_manager.fetch_index_spot_ltp(sym)
        basis = round(rest_fut - spot, 2) if rest_fut and spot else None
        print(
            f"  {sym:10} {strat.symbol:18} | REST={rest_fut or '—':>10} "
            f"WS={ws_fut or '—':>10} | SPOT={spot or '—':>10} basis={basis if basis is not None else '—'}"
        )
        if rest_fut and ws_fut and abs(rest_fut - ws_fut) > 2.0:
            logger.warning(
                "[PRICE] %s REST/WS mismatch: REST=%.2f WS=%.2f (token=%s key=%s)",
                sym, rest_fut, ws_fut, strat.instrument_token, ltp_key(strat.symbol, exchange),
            )
        if rest_fut is None and ws_fut is None:
            logger.warning(
                "[PRICE] %s no live price — check token %s and Kite quote key %s",
                sym, strat.instrument_token, ltp_key(strat.symbol, exchange),
            )
    print("  Tip: Kite chart default is often INDEX spot; our engine uses the front-month FUT contract above.\n")


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

    # Live trading requires explicit double confirmation (safety gate)
    force_dry = os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no")
    live_confirmed = os.getenv("LIVE_TRADING_CONFIRMED", "").strip().lower() in {"1", "true", "yes", "confirmed"}

    if not force_dry and not live_confirmed:
        print("!!! LIVE TRADING BLOCKED: set LIVE_TRADING_CONFIRMED=true to enable real orders.")
        print("!!! Forcing FORCE_DRY_RUN=true for safety.")
        os.environ["FORCE_DRY_RUN"] = "true"
        force_dry = True
    elif not force_dry and live_confirmed:
        print("\n" + "!" * 72)
        print("!!! LIVE TRADING MODE — REAL ORDERS ENABLED (FORCE_DRY_RUN=false + LIVE_TRADING_CONFIRMED)")
        print("!!! REAL CAPITAL AT RISK — monitor positions continuously")
        print("!" * 72 + "\n")

    from .micro_live import load_micro_live_config

    micro_live_config = load_micro_live_config()
    micro_live_env = os.getenv("MICRO_LIVE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if micro_live_config.enabled:
        print("\n" + "!" * 72)
        print("=== MICRO-LIVE MODE — Controlled real capital (strict caps) ===")
        print(f"    Max lots: {micro_live_config.max_lots} | Max open positions: {micro_live_config.max_open_positions}")
        print(f"    Allowed symbols: {', '.join(micro_live_config.allowed_symbols)}")
        print("!" * 72 + "\n")
    elif micro_live_env:
        print("!!! MICRO-LIVE BLOCKED: set MICRO_LIVE_CONFIRMED=true to enable micro-live caps.")

    # Developer closed-market testing mode (see market_calendar.py for safety guards)
    dev_force_open = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
    if dev_force_open and force_dry:
        print("\n" + "="*70)
        print("🧪  DEV_FORCE_MARKET_OPEN=true  —  Calendar checks bypassed for testing")
        print("    • is_market_open(), entry windows, and safe windows will return True")
        print("    • Simulation will be used for prices (no real data required)")
        print("    • FULL entry/exit logic (ATR, regime, risk gates, etc.) can now be exercised")
        print("    • This has ZERO effect if LIVE_MODE is ever active (hard safety)")
        print("="*70 + "\n")

    # Fixed synthetic time for reproducible testing of time-based behavior
    fixed_sim_time = os.getenv("DEV_FIXED_SIM_TIME")
    if fixed_sim_time and force_dry:
        print(f"🕒  DEV_FIXED_SIM_TIME active → system believes it is {fixed_sim_time} IST")
        print("    All calendar functions (is_market_open, is_entry_window_open, etc.) and")
        print("    time-based strategy filters will use this fixed moment for the entire run.")
        print("    Perfect for testing session windows, expiry logic, daily resets, etc.\n")

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

    # Bind Kite to instruments manager immediately (before dashboard SSE polls lot sizes)
    instruments_manager.bind(kite, force=True)

    reconciliation_service = BrokerReconciliation(kite)

    if force_dry:
        state_machine.set_state(SystemState.PAPER_MODE)
    else:
        state_machine.set_state(SystemState.LIVE_MODE)

    _init_trade_ledger_session(force_dry)

    # === Monday Multi-Index Paper Trading (NIFTY + BANKNIFTY + SENSEX FUT) ===
    from .multi_symbol_risk import multi_risk_manager
    multi_risk_manager.set_force_dry_run(force_dry)
    multi_risk_manager.set_micro_live_config(micro_live_config)
    risk_gatekeeper.set_force_dry_run(force_dry)

    if force_dry:
        multi_risk_manager.restore_paper_state()
    from . import live_snapshots

    # FAQ: fresh instrument dump each trading morning (NFO + BFO)
    if token_manager.token_valid:
        instruments_manager.bind(kite, force=True)
        if instruments_manager.is_ready():
            print("[MAIN] Kite instruments refreshed (NFO + BFO)")
    else:
        instruments_manager.bind(kite, force=not instruments_manager.is_ready())
    multi_risk_manager.warm_lot_size_cache()

    if not token_manager.token_valid:
        try:
            from .kite_connect_rules import session_guidance
            g = session_guidance()
            print(f"[MAIN] Kite token invalid — {g.get('token_note')}")
            print("[MAIN] Run: python generate_token.py  (after 07:35 IST)")
        except Exception:
            print("[MAIN] Kite token invalid — run: python generate_token.py")

    use_promoted = os.getenv("USE_PROMOTED_PARAMS", "false").lower() in {"1", "true", "yes"}
    if use_promoted:
        print("[MAIN] USE_PROMOTED_PARAMS=true — per-index WFA overlays will be merged at startup")

    symbols = ["NIFTY", "BANKNIFTY", "SENSEX"]
    strategies = {}
    for sym in symbols:
        paper_params, overlay_meta = merge_paper_params(
            DEFAULT_PAPER_PARAMS, sym, use_overlay=use_promoted
        )
        if overlay_meta.get("overlay_applied"):
            logger.info(
                "[MAIN] %s promoted overlay applied: %s",
                sym,
                overlay_meta.get("applied_params"),
            )
        elif use_promoted and overlay_meta.get("skip_reason"):
            logger.info("[MAIN] %s promoted overlay skipped: %s", sym, overlay_meta["skip_reason"])

        strat = PreviousCandleBreakoutStrategy(kite, paper_params=paper_params, risk_manager=multi_risk_manager)
        strat._initialize_index_future(sym)   # This now also does full per-symbol seeding of prev_high/prev_low/ATR
        strategies[sym] = strat

    from .order_lifecycle import order_lifecycle
    order_lifecycle.bind_kite(kite)
    order_lifecycle.restore_pending_orders()
    for sym, strat in strategies.items():
        order_lifecycle.register_fill_handler(sym, strat.on_live_fill_confirmed)
        order_lifecycle.register_terminal_handler(sym, strat.on_live_order_terminal_no_fill)

        # Strong per-symbol initialization confirmation (very useful in dev logs)
        contract_info = f"{strat.symbol} (token: {strat.instrument_token})"
        logger.info(f"[MAIN] Initialized + SEEDED strategy for {sym:10} -> {contract_info} | "
                    f"prevH={getattr(strat, 'prev_high', 0):.2f} prevL={getattr(strat, 'prev_low', 0):.2f} "
                    f"ATR={getattr(strat, 'current_atr', 0):.2f}")

    # Use the multi-symbol risk manager for paper trading
    # (global daily risk still enforced)

    # Broker-first recovery: sync positions before restoring per-symbol strategy context
    try:
        reconciliation_service.run_reconciliation()
        logger.info("[MAIN] Startup broker reconciliation complete")
    except Exception as recon_exc:
        logger.warning(f"[MAIN] Startup reconciliation note (non-fatal in paper): {recon_exc}")

    for sym, strat in strategies.items():
        strat.restore_from_persistence(sym)

    # Lightweight startup note (full params available in dashboard)
    print("[MAIN] Paper trading engine initialized (see dashboard for full params & diagnostics)")
    print("[MAIN] === MONDAY PAPER MODE: NIFTY + BANKNIFTY + SENSEX FUTURES ONLY ===")
    print("[MAIN] All orders are FORCED to DRY-RUN. No real capital at risk.")

    # Phase 4C: optional non-blocking daily session report at startup
    if os.getenv("AUTO_DAILY_REVIEW", "").strip().lower() in {"1", "true", "yes", "on"}:
        import threading

        def _auto_daily_review():
            try:
                from .session_tracker import session_tracker

                report = session_tracker.build_daily_session_report()
                path = session_tracker.save_daily_session_report(report)
                logger.info(
                    "[MAIN] AUTO_DAILY_REVIEW saved daily report score=%s path=%s",
                    report.get("quality_score"),
                    path,
                )
            except Exception as exc:
                logger.warning("[MAIN] AUTO_DAILY_REVIEW failed (non-fatal): %s", exc)

        threading.Thread(target=_auto_daily_review, daemon=True, name="auto-daily-review").start()
        print("[MAIN] AUTO_DAILY_REVIEW=true — daily session report queued (background)")

    # Contract selection summary (futures LTP — not index spot)
    print("\n[CONTRACTS] Active futures selected (app prices = FUT LTP, not NIFTY 50 spot):")
    for sym, strat in strategies.items():
        print(f"  {sym:10} → {strat.symbol:20} (token: {strat.instrument_token})")

    # === WebSocket Data Feed (threaded KiteTicker — official Zerodha pattern) ===
    ws_feed = None
    ws_token_generation = token_manager.token_reload_generation
    access_token = token_manager.access_token or ""
    enable_ws = os.getenv("ENABLE_WEBSOCKET", "true").lower() not in {"0", "false", "no"}
    if enable_ws and access_token and token_manager.token_valid:
        try:
            ws_feed = KiteWebSocketFeed(KITE_API_KEY, access_token)
            ws_feed.start()
            tokens = [s.instrument_token for s in strategies.values() if s.instrument_token]
            if tokens:
                ws_feed.subscribe(tokens)
                for strat in strategies.values():
                    strat.ws_feed = ws_feed
                logger.info(f"[WS] Subscribed to {len(tokens)} instruments (threaded mode)")
                time.sleep(2.0)
                _verify_price_feeds(strategies, ws_feed, kite)
        except Exception as ws_err:
            logger.warning(f"[WS] Failed to start WebSocket feed: {ws_err}. Falling back to REST polling.")
            ws_feed = None
    elif enable_ws and not access_token:
        logger.warning("[WS] WebSocket enabled but no valid KITE access token — using REST polling.")
    elif enable_ws and access_token and not token_manager.token_valid:
        logger.warning("[WS] WebSocket enabled but Kite token invalid — using REST polling.")
    else:
        logger.info("[WS] WebSocket disabled via ENABLE_WEBSOCKET=false — REST polling active.")

    if ws_feed is None and kite:
        _verify_price_feeds(strategies, None, kite)

    # === WebSocket Migration Preparation (Kite Best Practice) ===
    # Kite strongly recommends WebSocket (KiteTicker) with MODE_LTP or MODE_QUOTE over REST polling.
    # Current polling path is used because of Twisted reactor conflicts with uvicorn/FastAPI.
    # Future: Run ticker in a separate process or use asyncio-native client.
    # When ENABLE_WEBSOCKET=true, the system will attempt proper WS for lower latency + better SENSEX freshness.
    # All price sources are already logged via diagnostic_logger for easy bottleneck analysis.

    last_recon = 0.0
    last_token_check = 0.0
    TOKEN_CHECK_INTERVAL = 600  # FAQ: detect session expiry without waiting for order failure
    last_day = None
    last_status = 0.0
    last_terminal_summary = 0.0
    STATUS_INTERVAL = 600  # seconds (10 minutes) — clean terminal by default; use dashboard for live view
    TERMINAL_SUMMARY_SEC = max(15, int(os.getenv("TERMINAL_SUMMARY_SEC", "60")))

    print(f"[MAIN] Engine running. Terminal summary every {TERMINAL_SUMMARY_SEC}s — dashboard for live details.")

    while True:
        try:
            last_day = _maybe_reset_daily_metrics(last_day)

            # Get timestamp at the very start of the loop to prevent UnboundLocalError
            now = time.time()

            # EOD MIS flatten before broker auto square-off (~15:15 IST)
            try:
                from .eod_flatten import maybe_run_eod_flatten

                eod_result = maybe_run_eod_flatten(
                    multi_risk_manager,
                    kite=kite,
                    strategies=strategies,
                )
                if eod_result.get("flattened") and not eod_result.get("skipped"):
                    print(
                        f"[EOD] MIS flatten: closed {len(eod_result.get('closed_positions', []))} position(s)"
                    )
            except Exception as eod_exc:
                logger.warning("[EOD] Flatten check failed (non-fatal): %s", eod_exc)

            # === Run all 3 index strategies (Monday paper trading) ===
            if _engine_should_evaluate_signals():
                for sym, strat in strategies.items():
                    try:
                        strat.run_once()
                    except DataFeedError as dfe:
                        print(f"DATA FEED ERROR [{sym}] (LIVE safety): {dfe}")
                        state_machine.set_state(SystemState.TRADING_DISABLED)
                    except Exception as se:
                        print(f"Strategy step error [{sym}] (non-fatal): {se}")
            elif last_terminal_summary == 0.0 or (now - last_terminal_summary >= TERMINAL_SUMMARY_SEC):
                print("[MAIN] Market closed — engine idle (set ENGINE_IDLE_WHEN_CLOSED=false to disable)")

            # Rich per-symbol logging + live snapshots (very important for UX)
            # Snapshots updated EVERY loop (cheap, makes dashboard cards + T/SL live for all 3)
            # Pretty terminal block only every ~7s or warm-up to keep terminal calm
            for sym, strat in strategies.items():
                snap = strat.get_signal_snapshot()
                live_snapshots.update_snapshot(sym, snap)

            if last_terminal_summary == 0.0 or (now - last_terminal_summary >= TERMINAL_SUMMARY_SEC):
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
                    gate = snap.get('gate_summary') or ""
                    gate_str = f" | {gate}" if gate else ""
                    contract = snap.get('contract') or snap.get('symbol') or sym
                    spot = snap.get('spot_ltp')
                    basis = snap.get('spot_basis')
                    spot_str = ""
                    if spot:
                        spot_str = f" | Spot:{spot:8.1f} basis:{basis:+.1f}" if basis is not None else f" | Spot:{spot:8.1f}"
                    t_val = snap.get('target', 0) or 0
                    sl_val = snap.get('stop_loss', 0) or 0
                    t_str = f"{t_val:8.0f}" if t_val else "       —"
                    sl_str = f"{sl_val:8.0f}" if sl_val else "       —"
                    conf_str = f"{conf_val:.0%}" if snap.get('proposed') not in ('FLAT', 'HOLD', None) and conf_val else "  —"
                    print(f"{color} {sym:10} [{source:9}] {contract:16} | {snap.get('proposed','?'):6} | "
                          f"FUT:{snap.get('ltp',0):8.1f}{spot_str} | ATR:{display_atr:5.1f} | "
                          f"T:{t_str} / SL:{sl_str} | Conf:{conf_str}{conf_note}{age_str} | "
                          f"Regime:{(snap.get('regime') or {}).get('volatility','?')}{gate_str}")

                    # Warning only on first few simulated prints during open market
                    if source == "SIMULATED" and is_market_open():
                        if not hasattr(strat, '_sim_warning_shown'):
                            logger.warning(f"[DATA] {sym} using SIMULATED data while market OPEN. Real data not flowing yet.")
                            setattr(strat, '_sim_warning_shown', True)
                print("="*70)
                last_terminal_summary = now

                try:
                    from .diagnostic_logger import diag
                    diag.get_logger().debug(
                        f"3-INDEX BLOCK @ {dt.datetime.now().strftime('%H:%M:%S')} | {len(strategies)} symbols"
                    )
                except Exception:
                    pass

            # Global risk gates + contingency (sync multi-symbol P&L first)
            try:
                from .risk_contingency import (
                    evaluate_contingencies,
                    sync_portfolio_risk_state,
                )

                sync_portfolio_risk_state(multi_risk_manager, risk_gatekeeper)
                contingency = evaluate_contingencies(
                    multi_risk_manager,
                    risk_gatekeeper,
                    kite=kite,
                    already_halted=not state_machine.is_trading_allowed(),
                )
                if contingency.get("action") == "warn" and contingency.get("messages"):
                    for msg in contingency["messages"][:2]:
                        logger.warning("[CONTINGENCY] %s", msg)
            except Exception as cont_exc:
                logger.debug("Contingency check skipped: %s", cont_exc)

            risk_gatekeeper.check_all_gates()

            if now - last_token_check >= TOKEN_CHECK_INTERVAL:
                last_token_check = now
                prev_valid = token_manager.is_token_valid()
                if not token_manager.validate_token():
                    logger.warning(
                        "[MAIN] Kite token invalid — trading disabled until generate_token.py"
                    )
                elif (
                    token_manager.token_reload_generation != ws_token_generation
                    and token_manager.access_token
                ):
                    ws_token_generation = token_manager.token_reload_generation
                    kite.set_access_token(token_manager.access_token)
                    if ws_feed is not None:
                        try:
                            ws_feed.update_access_token(token_manager.access_token)
                            logger.info("[WS] Access token refreshed — WebSocket restarted")
                        except Exception as ws_tok_err:
                            logger.warning("[WS] Token refresh restart failed: %s", ws_tok_err)
                elif not prev_valid and token_manager.is_token_valid() and ws_feed is None:
                    access_token = token_manager.access_token or ""
                    if enable_ws and access_token:
                        try:
                            ws_feed = KiteWebSocketFeed(KITE_API_KEY, access_token)
                            ws_feed.start()
                            tokens = [s.instrument_token for s in strategies.values() if s.instrument_token]
                            if tokens:
                                ws_feed.subscribe(tokens)
                                for strat in strategies.values():
                                    strat.ws_feed = ws_feed
                            ws_token_generation = token_manager.token_reload_generation
                            logger.info("[WS] Started WebSocket after token became valid")
                        except Exception as ws_start_err:
                            logger.warning("[WS] Late WebSocket start failed: %s", ws_start_err)

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
            _flush_state_on_shutdown(strategies, multi_risk_manager)
            break

        # Use faster polling during initial warm-up when market is open
        poll_interval = FAST_POLL_SECONDS if (now - (last_recon or now) < FAST_POLL_DURATION and is_market_open()) else POLL_INTERVAL_SECONDS
        time.sleep(poll_interval)

    print("[MAIN] Trading loop exited cleanly.")

    if ws_feed is not None:
        try:
            ws_feed.stop()
            logger.info("[WS] WebSocket feed stopped cleanly")
        except Exception as ws_stop_err:
            logger.warning("[WS] Shutdown error (non-fatal): %s", ws_stop_err)

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
