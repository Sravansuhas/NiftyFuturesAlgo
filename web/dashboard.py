"""
Bloomberg Terminal Style Dashboard for Aegis — risk-first Indian F&O platform

Run alongside your trading process:
    uvicorn web.dashboard:app --host 0.0.0.0 --port 8050 --reload

Features:
- Real-time updating metrics via SSE
- Clean, dark, professional terminal aesthetic
- Trades, Risk, Diagnostics, System Health
- Designed for long-running sessions (hours to full day)
"""

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import json
import asyncio
from fastapi.templating import Jinja2Templates
import asyncio
import json
import time
import uuid
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

# Suppress noisy but benign uvicorn/Starlette CancelledError on clean shutdown (Ctrl+C)
# These are normal ASGI disconnects during graceful exit; trading loop is unaffected.
logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("starlette").setLevel(logging.ERROR)

import pandas as pd  # ensure pd is available in backtest jobs

# Backtesting imports
from backtesting.walk_forward_runner import run_walk_forward
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.costs import TransactionCostModel, CostConfig
from backtesting.data_loader import (
    fetch_real_nifty_futures_data,
    fetch_real_index_futures_data,
    list_available_cached_datasets,
)
from backtesting.promotion_gates import load_candidates
from backtesting.backtest_memory import backtest_memory
from backtesting.job_store import save_job, load_all_jobs, load_job
from kiteconnect import KiteConnect
from dotenv import load_dotenv
import os

# Robust market status (senior finance requirement)
try:
    from app.market_calendar import get_market_status, is_market_open, is_expiry_day, is_safe_trading_window
except Exception:
    # Graceful fallback so GUI never crashes even if calendar has issues
    def get_market_status():
        return {"session_status": "UNKNOWN", "error": "market_calendar unavailable"}
    def is_market_open(): return False
    def is_expiry_day(): return False
    def is_safe_trading_window(): return False

# Import your existing singletons
try:
    from app.risk_gatekeeper import risk_gatekeeper
    from app.state_machine import state_machine
    from app.audit_logger import AuditLogger
except ImportError:
    # Fallback for when running standalone
    risk_gatekeeper = None
    state_machine = None

_API_THREAD_POOL = ThreadPoolExecutor(
    max_workers=max(12, int(os.getenv("API_THREAD_POOL_SIZE", "20"))),
    thread_name_prefix="aegis-api",
)


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.set_default_executor(_API_THREAD_POOL)
    yield
    _API_THREAD_POOL.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Aegis Terminal", version="0.4.0", lifespan=_app_lifespan)

# Allow Vite dev server (and production static hosting) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8050",
        "http://127.0.0.1:8050",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Persistent equity history (simple JSON for now)
EQUITY_HISTORY_FILE = Path("data/equity_history.json")
EQUITY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
EQUITY_HISTORY = []
MAX_HISTORY_POINTS = 500

def _load_equity_history():
    global EQUITY_HISTORY
    if EQUITY_HISTORY_FILE.exists():
        try:
            with open(EQUITY_HISTORY_FILE) as f:
                EQUITY_HISTORY = json.load(f)[-MAX_HISTORY_POINTS:]
        except:
            EQUITY_HISTORY = []

def _save_equity_history():
    try:
        with open(EQUITY_HISTORY_FILE, "w") as f:
            json.dump(EQUITY_HISTORY[-MAX_HISTORY_POINTS:], f)
    except:
        pass

_load_equity_history()

# Audit logger instance (reuse existing one if possible)
audit_logger = AuditLogger("data/audit_events.json")

MAX_JOBS = 8
# Backtest jobs (memory + disk persistence via backtesting/job_store.py)
BACKTEST_JOBS: Dict[str, Dict[str, Any]] = load_all_jobs(limit=MAX_JOBS)

# Cancellation support for long-running jobs
CANCEL_REQUESTED: Dict[str, bool] = {}

def _cleanup_jobs():
    if len(BACKTEST_JOBS) > MAX_JOBS:
        sorted_jobs = sorted(BACKTEST_JOBS.items(), key=lambda x: x[1].get("started_at", 0))
        for jid, _ in sorted_jobs[:len(sorted_jobs) - MAX_JOBS]:
            BACKTEST_JOBS.pop(jid, None)
            try:
                from backtesting.job_store import delete_job
                delete_job(jid)
            except Exception:
                pass


def _persist_job(job_id: str) -> None:
    job = BACKTEST_JOBS.get(job_id)
    if not job:
        return
    payload = {"job_id": job_id, **job}
    try:
        save_job(job_id, payload)
    except Exception as exc:
        print(f"[BACKTEST JOB] Persist failed for {job_id}: {exc}")


_kite_singleton: Dict[str, Any] = {"client": None, "token": "", "api_key": ""}
_kite_status_cache: Dict[str, Any] = {"payload": None, "ts": 0.0, "token_key": ""}
KITE_STATUS_CACHE_SEC = max(15.0, float(os.getenv("KITE_STATUS_CACHE_SEC", "45")))


def _reset_kite_singleton() -> None:
    _kite_singleton.update(client=None, token="", api_key="")


def invalidate_kite_dashboard_caches() -> None:
    """Call after auto-login saves a new access token."""
    _reset_kite_singleton()
    _kite_status_cache.update(payload=None, ts=0.0, token_key="")
    _token_valid_cache["ts"] = 0.0


def _get_kite_client():
    """Return cached authenticated KiteConnect — one client per access token."""
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("KITE_API_KEY", "") or ""
        access_token = os.getenv("KITE_ACCESS_TOKEN", "") or ""
        if not api_key or not access_token:
            _reset_kite_singleton()
            return None
        if (
            _kite_singleton.get("client") is not None
            and _kite_singleton.get("token") == access_token
            and _kite_singleton.get("api_key") == api_key
        ):
            return _kite_singleton["client"]
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        _kite_singleton.update(client=kite, token=access_token, api_key=api_key)
        return kite
    except Exception:
        return None


def _get_engine_ws_feed():
    """Return the trading engine WebSocket feed when run.py shares the process."""
    try:
        from app.data_feed import get_active_ws_feed

        feed = get_active_ws_feed()
        if feed is not None and hasattr(feed, "is_connected") and feed.is_connected():
            return feed
        return feed if feed is not None else None
    except Exception:
        return None


def _safe_kite_call(callable_fn, operation_name: str = "kite_api"):
    """
    Senior finance dev pattern: Every external broker call is wrapped.
    Always returns structured result + explicit error_code. Never lets the GUI blow up.
    """
    try:
        kite = _get_kite_client()
        if not kite:
            return {
                "error": "Missing KITE_API_KEY or KITE_ACCESS_TOKEN in environment",
                "error_code": "KITE_CREDENTIALS_MISSING",
            }
        result = callable_fn(kite)
        return {"data": result, "error": None, "error_code": None}
    except Exception as e:
        err = str(e)
        code = f"KITE_{operation_name.upper()}_FAILED"
        try:
            from kiteconnect.exceptions import TokenException
            if isinstance(e, TokenException):
                code = "KITE_TOKEN_EXPIRED"
                from app.kite_connect_rules import on_token_exception
                on_token_exception(operation_name)
        except Exception:
            pass
        return {"data": None, "error": err, "error_code": code}

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "title": "AEGIS — TERMINAL"
    })

def _clamp_limit(limit: int, default: int = 50, max_limit: int = 500) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, max_limit))


_POSTURE_RANK = {"contingency": 0, "defensive": 1, "normal": 2, "aggressive": 3}
_POSTURE_INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


def _build_posture_snapshot(multi_risk_manager, live_snapshots_data: dict) -> dict:
    """Live FRIDAY posture from regime_orchestrator — per symbol + portfolio rollup."""
    try:
        from app.regime_orchestrator import classify_market_color, posture_for_symbol
    except Exception:
        return {}

    per_symbol = {}
    portfolio_posture = "aggressive"
    colors = []

    for sym in _POSTURE_INDICES:
        regime = (live_snapshots_data.get(sym) or {}).get("regime") or {}
        if not regime and multi_risk_manager:
            regime = getattr(multi_risk_manager, "_regime_by_symbol", {}).get(sym, {})
        ctx = {}
        if multi_risk_manager and hasattr(multi_risk_manager, "_build_budget_context"):
            try:
                ctx = multi_risk_manager._build_budget_context(sym)
            except Exception:
                ctx = {}
        posture = posture_for_symbol(sym, regime, ctx)
        per_symbol[sym] = posture
        tier = posture.get("posture", "normal")
        if _POSTURE_RANK.get(tier, 2) < _POSTURE_RANK.get(portfolio_posture, 2):
            portfolio_posture = tier
        colors.append(classify_market_color(regime))

    color_priority = {"red": 0, "sideways": 1, "green": 2}
    portfolio_color = "green"
    for c in colors:
        if color_priority.get(c, 1) < color_priority.get(portfolio_color, 2):
            portfolio_color = c

    contingencies: list = []
    reasons: list = []
    for p in per_symbol.values():
        contingencies.extend(p.get("contingencies") or [])
        reasons.extend(p.get("reasons") or [])
    seen_c, seen_r = set(), set()
    uniq_cont = [x for x in contingencies if x not in seen_c and not seen_c.add(x)]
    uniq_reasons = [x for x in reasons if x not in seen_r and not seen_r.add(x)]

    risk_hints = [
        float(p.get("risk_multiplier_hint") or 1.0) for p in per_symbol.values()
    ]
    return {
        "portfolio": {
            "posture": portfolio_posture,
            "market_color": portfolio_color,
            "risk_multiplier_hint": round(min(risk_hints), 3) if risk_hints else 1.0,
            "reasons": uniq_reasons[:6],
            "contingencies": uniq_cont[:5],
        },
        "per_symbol": per_symbol,
    }


_fo_mood_cache: Dict[str, Any] = {"payload": None, "ts": 0.0}
FO_MOOD_CACHE_SEC = 30.0
FO_MOOD_CACHE_CLOSED_SEC = 120.0
SSE_INTERVAL_SEC = max(2.0, float(os.getenv("SSE_INTERVAL_SEC", "5")))
_sse_payload_cache: Dict[str, Any] = {"payload": None, "ts": 0.0}
SSE_PAYLOAD_CACHE_SEC = max(1.0, float(os.getenv("SSE_PAYLOAD_CACHE_SEC", "2.5")))


def _fo_mood_cache_ttl() -> float:
    try:
        market = get_market_status()
        if not market.get("is_market_open"):
            return FO_MOOD_CACHE_CLOSED_SEC
    except Exception:
        pass
    return FO_MOOD_CACHE_SEC


def _get_fo_mood_cached(live_snapshots_data: dict) -> dict:
    """Throttle expensive fo_mood computation — REST /api/status uses full build; SSE uses cache."""
    now = time.time()
    cached = _fo_mood_cache.get("payload")
    if cached and now - float(_fo_mood_cache.get("ts") or 0) < _fo_mood_cache_ttl():
        return cached

    fo_guards: dict = {}
    posture_snapshot: dict = {}
    state_value = ""
    trading_allowed = True
    try:
        if multi_risk_manager:
            from app.fo_guard_status import build_portfolio_guard_snapshot
            fo_guards = build_portfolio_guard_snapshot(multi_risk_manager)
            posture_snapshot = _build_posture_snapshot(multi_risk_manager, live_snapshots_data or {})
        if state_machine:
            state_value = state_machine.get_state().value
            trading_allowed = state_machine.is_trading_allowed()
    except Exception:
        pass

    mood = _build_fo_mood_snapshot(
        live_snapshots_data or {},
        fo_guards,
        posture_snapshot,
        state_value=state_value,
        trading_allowed=trading_allowed,
    )
    _fo_mood_cache["payload"] = mood
    _fo_mood_cache["ts"] = now
    return mood


def _build_fo_mood_snapshot(
    live_snapshots_data: dict,
    fo_guards: dict,
    posture_snapshot: dict,
    *,
    state_value: str = "",
    trading_allowed: bool = True,
) -> dict:
    """Market F&O Mood — tape readability vs algo tradeability for dashboard."""
    try:
        from app.fo_market_mood import compute_fo_market_mood, fetch_macro_cached

        market = get_market_status()
        market["engine_state"] = state_value
        market["engine_trading_allowed"] = trading_allowed
        macro = fetch_macro_cached()
        raw = compute_fo_market_mood(
            live_snapshots_data or {},
            market,
            fo_guards or {},
            posture_snapshot or {},
            macro_context=macro,
        )
        indices = {}
        for sym, row in (raw.get("per_index") or {}).items():
            regime = row.get("regime") or {}
            indices[sym] = {
                "symbol": sym,
                "trend": regime.get("trend"),
                "chop_score": regime.get("chop_score"),
                "proposed": row.get("proposed"),
                "tape_mood": row.get("tape_mood"),
                "tape_zone": row.get("tape_zone"),
                "algo_trend": row.get("algo_trend"),
                "brother_bias": row.get("brother_bias"),
                "guard_allowed": row.get("guard_allowed"),
            }
        components = []
        for comp in raw.get("components") or []:
            components.append({
                "id": comp.get("key") or comp.get("label", ""),
                "label": comp.get("label", ""),
                "score": comp.get("score", 0),
                "weight": comp.get("weight"),
                "detail": comp.get("detail"),
                "scope": comp.get("scope"),
            })
        tape = float(raw.get("tape_mood") or 0)
        trade = float(raw.get("tradeability") or 0)
        return {
            "available": True,
            "timestamp": raw.get("computed_at"),
            "tape_mood": tape,
            "tradeability": trade,
            "tape_zone": raw.get("tape_zone"),
            "tradeability_zone": raw.get("tradeability_zone"),
            "divergence": round(abs(tape - trade), 1),
            "human_summary": raw.get("human_summary"),
            "algo_summary": raw.get("algo_summary"),
            "mismatch": raw.get("mismatch"),
            "mismatch_detail": raw.get("mismatch_detail"),
            "components": components,
            "indices": indices,
            "per_index": raw.get("per_index"),
            "macro": macro,
            "cached": raw.get("cached"),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc), "tape_mood": 0, "tradeability": 0}


def _engine_ready() -> bool:
    return bool(risk_gatekeeper and state_machine)


_token_valid_cache: Dict[str, Any] = {"valid": False, "ts": 0.0}
TOKEN_VALID_CACHE_SEC = 60.0


def _cached_token_valid(*, network: bool = True) -> bool:
    """Avoid kite.profile() on hot paths — cache for 60s; quick bootstrap skips network."""
    now = time.time()
    if now - float(_token_valid_cache.get("ts") or 0) < TOKEN_VALID_CACHE_SEC:
        return bool(_token_valid_cache.get("valid"))
    if not network:
        return bool(os.getenv("KITE_ACCESS_TOKEN"))
    valid = False
    try:
        from app.kite_auth import validate_access_token
        valid, _, _ = validate_access_token()
    except Exception:
        valid = bool(os.getenv("KITE_ACCESS_TOKEN"))
    _token_valid_cache["valid"] = valid
    _token_valid_cache["ts"] = now
    return valid


def _options_mtm_from_legs(options_legs: dict) -> dict:
    """Fast MTM from in-memory leg snapshots — no Kite REST."""
    summary = options_legs.get("summary") or {}
    legs = int(summary.get("legs") or 0)
    if legs <= 0:
        return {"available": False, "mtm_net": 0.0, "mtm_gross": 0.0, "legs": 0}
    return {
        "available": True,
        "mtm_net": float(summary.get("mtm_net") or 0),
        "mtm_gross": float(summary.get("mtm_gross") or 0),
        "legs": legs,
    }


def _default_options_algo_payload() -> dict:
    return {
        "available": False,
        "enabled": {
            "options_trading": False,
            "config_trading_enabled": False,
            "env_trading_enabled": False,
            "futures_trading": False,
        },
        "structures_today": 0,
        "open_structures": [],
        "open_count": 0,
        "last_cycle_result": None,
        "last_cycle_at": None,
        "regime_gates": {"allowed": False, "reasons": [], "gates": {}},
    }


_RECENT_EXECUTION_EVENT_TYPES = frozenset({
    "signal.accepted",
    "signal.rejected",
    "order.placed",
    "order.exit",
    "options.structure.open",
    "options.structure.close",
    "options.cycle.skip",
    "options.cycle.fail",
    "options.eod.flatten",
})

_TRADES_API_EVENT_TYPES = _RECENT_EXECUTION_EVENT_TYPES | frozenset({
    "order.dry_run",
    "trade.closed",
    "session.start",
})


def _today_ist() -> str:
    from app.market_calendar import now_ist
    return now_ist().strftime("%Y-%m-%d")


def _ledger_events_today(
    limit: int,
    *,
    event_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    from app.trade_ledger import trade_ledger
    return trade_ledger.read_events_today(limit=limit, event_types=event_types)


def _format_options_reason(payload: Dict[str, Any]) -> Optional[str]:
    reason = payload.get("reason")
    details = payload.get("details")
    if isinstance(details, list):
        detail_text = "; ".join(str(d) for d in details)
        if reason and detail_text:
            return f"{reason}: {detail_text}"
        return detail_text or (str(reason) if reason else None)
    if reason:
        sid = payload.get("structure_id")
        if sid:
            return f"{reason} ({sid})"
        return str(reason)
    message = payload.get("message")
    if message:
        action = payload.get("action")
        if action:
            return f"{action}: {message}"
        return str(message)
    return payload.get("structure_id")


def _map_ledger_event_to_recent_exec(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    et = event.get("event_type", "")
    if et not in _RECENT_EXECUTION_EVENT_TYPES:
        return None
    p = event.get("payload", {}) or {}
    base: Dict[str, Any] = {"ts": event.get("ts"), "type": et}
    if et.startswith("options."):
        side = "SKIP"
        if et == "options.cycle.fail":
            side = "FAIL"
        elif "open" in et:
            side = "OPEN"
        elif "close" in et or "flatten" in et:
            side = "CLOSE"
        base.update({
            "side": side,
            "symbol": p.get("underlying") or p.get("index") or p.get("symbol"),
            "price": p.get("credit"),
            "reason": _format_options_reason(p),
            "qty": p.get("legs"),
            "structure_id": p.get("structure_id"),
        })
        return base
    base.update({
        "side": p.get("side"),
        "symbol": p.get("index") or p.get("symbol"),
        "price": p.get("price"),
        "reason": p.get("reason") or p.get("filter") or p.get("message"),
        "regime": p.get("regime"),
        "qty": p.get("quantity"),
        "quantity": p.get("quantity"),
    })
    return base


def _build_recent_execution_from_ledger(limit: int = 8, *, tail_n: Optional[int] = None) -> List[Dict[str, Any]]:
    recent_exec: List[Dict[str, Any]] = []
    try:
        events = _ledger_events_today(
            tail_n or max(limit * 3, 24),
            event_types=list(_RECENT_EXECUTION_EVENT_TYPES),
        )
        for e in events:
            mapped = _map_ledger_event_to_recent_exec(e)
            if mapped:
                recent_exec.append(mapped)
            if len(recent_exec) >= limit:
                break
    except Exception:
        pass
    return recent_exec


def _last_action_from_ledger_event(event: Dict[str, Any]) -> Optional[str]:
    et = event.get("event_type", "")
    p = event.get("payload", {}) or {}
    if et == "signal.accepted":
        return f"Signal Accepted: {p.get('side')} @ {p.get('price')}"
    if et == "order.placed":
        return f"Order Placed: {p.get('side')} {p.get('quantity')}"
    if et == "order.exit":
        return "Exit Order Submitted"
    if et == "signal.rejected":
        return f"Signal Rejected: {p.get('reason', 'unknown')}"
    if et == "options.structure.open":
        credit = float(p.get("credit") or 0)
        return f"Options IC opened on {p.get('underlying', '?')} (credit ₹{credit:,.0f})"
    if et == "options.structure.close":
        return f"Options IC closed: {p.get('reason', 'manual')}"
    if et == "options.cycle.skip":
        reason = _format_options_reason(p) or "unknown"
        return f"Options cycle skipped: {reason}"
    if et == "options.cycle.fail":
        action = p.get("action", "cycle")
        msg = p.get("message") or "unknown"
        return f"Options cycle failed ({action}): {msg}"
    if et == "options.eod.flatten":
        return "Options EOD flatten"
    return None


def _get_options_algo_status(*, fast: bool = False, include_market_context: bool = False) -> dict:
    """Automated iron-condor structures — alongside manual options_legs sheet."""
    try:
        from app.options_strategy_runner import get_options_algo_status_payload

        kite = None if fast else _get_kite_client()
        market_context = None
        if include_market_context and not fast:
            try:
                from app.market_context import load_market_context
                market_context = load_market_context()
            except Exception:
                pass
        ws_feed = None if fast else _get_engine_ws_feed()
        return get_options_algo_status_payload(
            fast=fast,
            kite=kite,
            ws_feed=ws_feed,
            market_context=market_context,
        )
    except Exception:
        return _default_options_algo_payload()


def _build_status_quick_payload() -> dict:
    """Lightweight status for dashboard bootstrap — no NSE macro or Kite profile calls."""
    if not _engine_ready():
        return {
            "error": "Trading engine not loaded",
            "engine_ready": False,
            "timestamp": datetime.now().isoformat(),
            "mode": "PAPER",
            "state": "OFFLINE",
            "market": get_market_status(),
            "token_valid": False,
            "daily_pnl": 0,
            "combined_daily_pnl": 0,
            "daily_loss": 0,
            "current_equity": 0,
            "trades_today": 0,
            "max_drawdown": 0,
            "capital": 0,
            "equity_history": [],
            "last_action": "Engine not loaded — run python run.py",
            "recent_execution": [],
            "per_symbol_status": {},
            "live_snapshots": {},
        }

    multi_risk_manager = None
    try:
        from app.multi_symbol_risk import multi_risk_manager as _mrm
        multi_risk_manager = _mrm
    except Exception:
        pass

    effective_daily_pnl = multi_risk_manager.daily_pnl if multi_risk_manager else risk_gatekeeper.daily_pnl
    effective_daily_loss = multi_risk_manager.daily_loss if multi_risk_manager else risk_gatekeeper.daily_loss
    effective_trades = multi_risk_manager.trades_today if multi_risk_manager else risk_gatekeeper.trades_today
    current_equity = risk_gatekeeper.capital + effective_daily_pnl

    live_snapshots_data = {}
    try:
        from app import live_snapshots
        live_snapshots_data = live_snapshots.get_all_snapshots()
    except Exception:
        pass

    per_symbol_rich_status = {}
    if multi_risk_manager:
        per_symbol_rich_status = multi_risk_manager.get_per_symbol_status()

    safe_cards = {}
    for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        safe_cards[sym] = per_symbol_rich_status.get(sym, {
            "position": 0,
            "avg_price": 0,
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "daily_loss": 0.0,
        })
        snap = live_snapshots_data.get(sym, {}) or {}
        if snap.get("unrealized_pnl") is not None and abs(snap.get("unrealized_pnl", 0)) > 0.5:
            safe_cards[sym]["live_unrealized_pnl"] = round(snap["unrealized_pnl"], 2)

    options_mtm = {"available": False, "mtm_net": 0.0, "mtm_gross": 0.0, "legs": 0}
    options_net = 0.0

    trade_budget = {}
    try:
        if multi_risk_manager and hasattr(multi_risk_manager, "get_budget_summary"):
            trade_budget = multi_risk_manager.get_budget_summary()
    except Exception:
        pass

    recent_exec = _build_recent_execution_from_ledger(8)
    last_action = "No recent activity"
    try:
        events = _ledger_events_today(10, event_types=list(_RECENT_EXECUTION_EVENT_TYPES))
        if events:
            action = _last_action_from_ledger_event(events[0])
            if action:
                last_action = action
    except Exception:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "engine_ready": True,
        "mode": "PAPER" if risk_gatekeeper.config.force_dry_run else "LIVE",
        "state": state_machine.get_state().value,
        "capital": risk_gatekeeper.capital,
        "daily_pnl": round(effective_daily_pnl, 2),
        "options_mtm": options_mtm,
        "combined_daily_pnl": round(effective_daily_pnl + options_net, 2),
        "daily_loss": round(effective_daily_loss, 2),
        "current_equity": round(current_equity + options_net, 2),
        "trades_today": effective_trades,
        "max_drawdown": round(
            (multi_risk_manager._current_drawdown_pct() * 100) if multi_risk_manager
            else risk_gatekeeper._current_drawdown_pct() * 100,
            2,
        ),
        "token_valid": _cached_token_valid(network=False),
        "equity_history": EQUITY_HISTORY[-100:],
        "last_action": last_action,
        "recent_execution": recent_exec,
        "market": get_market_status(),
        "per_symbol_status": safe_cards,
        "live_snapshots": live_snapshots_data,
        "trade_budget": trade_budget,
        "fo_mood": _fo_mood_cache.get("payload"),
    }


def _build_status_payload(record_equity: bool = False) -> dict:
    """Build status JSON. Equity history is appended only when record_equity=True."""
    if not _engine_ready():
        options_legs = {"available": False, "legs": {}, "summary": {}}
        try:
            from app.options_legs_engine import options_legs_engine
            options_legs = options_legs_engine.get_status_payload()
        except Exception:
            pass
        options_algo = _get_options_algo_status(fast=False, include_market_context=True)
        return {
            "error": "Trading engine not loaded",
            "engine_ready": False,
            "timestamp": datetime.now().isoformat(),
            "mode": "PAPER",
            "state": "OFFLINE",
            "market": get_market_status(),
            "options_legs": options_legs,
            "options_algo": options_algo,
            "token_valid": False,
            "daily_pnl": 0,
            "combined_daily_pnl": 0,
            "daily_loss": 0,
            "current_equity": 0,
            "trades_today": 0,
            "max_drawdown": 0,
            "capital": 0,
            "equity_history": [],
            "last_action": "Engine not loaded — run python run.py",
            "recent_execution": [],
            "per_symbol_status": {},
            "live_snapshots": {},
        }

    multi_risk_manager = None
    try:
        from app.multi_symbol_risk import multi_risk_manager as _mrm
        multi_risk_manager = _mrm
    except Exception:
        pass

    effective_daily_pnl = multi_risk_manager.daily_pnl if multi_risk_manager else risk_gatekeeper.daily_pnl
    effective_daily_loss = multi_risk_manager.daily_loss if multi_risk_manager else risk_gatekeeper.daily_loss
    effective_trades = multi_risk_manager.trades_today if multi_risk_manager else risk_gatekeeper.trades_today
    current_equity = risk_gatekeeper.capital + effective_daily_pnl

    token_valid = _cached_token_valid()

    if record_equity:
        now_ts = time.time()
        EQUITY_HISTORY.append({"ts": now_ts, "equity": current_equity})
        if len(EQUITY_HISTORY) > MAX_HISTORY_POINTS:
            EQUITY_HISTORY.pop(0)
        _save_equity_history()

    # Get the most recent meaningful action from the ledger
    last_action = "No recent activity"
    active_symbol = None
    last_ltp = None
    last_regime = None
    try:
        recent = _ledger_events_today(10, event_types=list(_RECENT_EXECUTION_EVENT_TYPES))
        if recent:
            latest = recent[0]
            payload = latest.get("payload", {}) or {}
            action = _last_action_from_ledger_event(latest)
            if action:
                last_action = action
            # Capture any symbol/ltp/regime we logged
            if "symbol" in payload:
                active_symbol = payload.get("symbol")
            if "price" in payload and isinstance(payload.get("price"), (int, float)):
                last_ltp = payload.get("price")
            if "regime" in payload:
                last_regime = payload.get("regime")
    except:
        pass

    # Multi-symbol support for 3 indices (Monday paper trading)
    multi_symbol_status = {}
    per_symbol_rich_status = {}
    if multi_risk_manager:
        multi_symbol_status = multi_risk_manager.get_all_positions_summary()
        per_symbol_rich_status = multi_risk_manager.get_per_symbol_status()

    # Live snapshots from running strategies (best source for Target/SL)
    live_snapshots_data = {}
    try:
        from app import live_snapshots
        live_snapshots_data = live_snapshots.get_all_snapshots()
    except:
        pass

    # Fallback to ledger if no live snapshots yet
    last_signals = {}
    try:
        recent = _ledger_events_today(30, event_types=["signal.accepted"])
        for event in recent:
            if event.get("event_type") == "signal.accepted":
                raw_sym = event.get("payload", {}).get("symbol") or "NIFTY"
                # Normalize to short index name for dashboard cards (handles NIFTY26JUNFUT -> NIFTY)
                sym = "NIFTY" if "NIFTY" in str(raw_sym).upper() and "BANK" not in str(raw_sym).upper() else \
                      ("BANKNIFTY" if "BANKNIFTY" in str(raw_sym).upper() else \
                       ("SENSEX" if "SENSEX" in str(raw_sym).upper() else str(raw_sym).upper()[:10]))
                if sym not in last_signals:
                    p = event.get("payload", {})
                    last_signals[sym] = {
                        "side": p.get("side"),
                        "price": p.get("price"),
                        "atr": p.get("atr"),
                        "regime": p.get("regime"),
                        "ts": event.get("ts"),
                        "proposed": p.get("side"),
                        "ltp": p.get("price")
                    }
    except:
        pass

    # Active symbol: first open multi-symbol position, else legacy gatekeeper
    if not active_symbol and multi_risk_manager:
        for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
            if multi_risk_manager.get_position(sym).quantity != 0:
                active_symbol = sym
                break
    if not active_symbol and risk_gatekeeper and risk_gatekeeper.position:
        active_symbol = risk_gatekeeper.position.get("symbol")

    open_positions = []
    if multi_risk_manager:
        for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
            p = multi_risk_manager.get_position(sym)
            if p.quantity != 0:
                open_positions.append({
                    "symbol": sym,
                    "quantity": p.quantity,
                    "avg_price": round(p.avg_price, 2),
                })

    # Build a tiny recent execution log (futures + options) for the live GUI
    recent_exec = _build_recent_execution_from_ledger(8, tail_n=15)

    # Build a safe default for the three cards even on first run or when data is sparse
    safe_cards = {}
    for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        safe_cards[sym] = per_symbol_rich_status.get(sym, {
            "position": 0,
            "avg_price": 0,
            "daily_pnl": 0.0,
            "daily_trades": 0,
            "daily_loss": 0.0
        })

    # Enrich with live unrealized P&L from fresh snapshots (so open LONG/SHORT positions show mark-to-market in the 3 cards)
    try:
        for sym in safe_cards:
            snap = live_snapshots_data.get(sym, {}) or {}
            if snap.get("unrealized_pnl") is not None and abs(snap.get("unrealized_pnl", 0)) > 0.5:
                safe_cards[sym]["live_unrealized_pnl"] = round(snap["unrealized_pnl"], 2)
                # For display, the card JS will prefer this for open positions
    except:
        pass

    options_mtm = {"available": False, "mtm_net": 0.0, "mtm_gross": 0.0}
    try:
        from app.external_signals import get_today_options_mtm
        options_mtm = get_today_options_mtm()
    except Exception:
        pass
    options_net = float(options_mtm.get("mtm_net") or 0)
    combined_daily_pnl = effective_daily_pnl + options_net

    trade_budget = {}
    fo_guards = {}
    posture_snapshot = {}
    try:
        if multi_risk_manager and hasattr(multi_risk_manager, "get_budget_summary"):
            trade_budget = multi_risk_manager.get_budget_summary()
        if multi_risk_manager:
            from app.fo_guard_status import build_portfolio_guard_snapshot
            fo_guards = build_portfolio_guard_snapshot(multi_risk_manager)
        posture_snapshot = _build_posture_snapshot(multi_risk_manager, live_snapshots_data or {})
    except Exception:
        pass

    fo_mood = _get_fo_mood_cached(live_snapshots_data or {})

    options_legs = {"available": False, "legs": {}, "summary": {}}
    try:
        from app.options_legs_engine import options_legs_engine
        options_legs = options_legs_engine.get_status_payload(fast=True)
    except Exception:
        pass

    options_algo = _get_options_algo_status(fast=True, include_market_context=False)

    return {
        "timestamp": datetime.now().isoformat(),
        "engine_ready": True,
        "mode": "PAPER" if risk_gatekeeper.config.force_dry_run else "LIVE",
        "state": state_machine.get_state().value,
        "capital": risk_gatekeeper.capital,
        "daily_pnl": round(effective_daily_pnl, 2),
        "options_mtm": options_mtm,
        "combined_daily_pnl": round(combined_daily_pnl, 2),
        "daily_loss": round(effective_daily_loss, 2),
        "current_equity": round(current_equity + options_net, 2),
        "position": {
            "quantity": open_positions[0]["quantity"] if open_positions else 0,
            "symbol": open_positions[0]["symbol"] if open_positions else None,
            "avg_price": open_positions[0]["avg_price"] if open_positions else 0,
        },
        "open_positions": open_positions,
        "trades_today": effective_trades,
        "max_drawdown": round(
            (multi_risk_manager._current_drawdown_pct() * 100) if multi_risk_manager
            else risk_gatekeeper._current_drawdown_pct() * 100,
            2,
        ),
        "token_valid": token_valid,
        "equity_history": EQUITY_HISTORY[-100:],
        "last_action": last_action,
        "active_symbol": active_symbol,
        "last_ltp": last_ltp,
        "last_regime": last_regime,
        "recent_execution": recent_exec,
        "vol_regime": last_regime or "normal",
        "risk_mult": 1.0,
        "market": get_market_status(),
        "multi_symbol_positions": multi_symbol_status,
        "per_symbol_status": safe_cards,
        "last_proposed_signals": last_signals,
        "live_snapshots": live_snapshots_data or {},
        "trade_budget": trade_budget,
        "fo_guards": fo_guards,
        "posture_snapshot": posture_snapshot,
        "fo_mood": fo_mood,
        "options_legs": options_legs,
        "options_algo": options_algo,
    }


# === Real-time Status Endpoint (JSON) ===
@app.get("/api/status/quick")
async def get_status_quick():
    """Fast bootstrap status — no blocking NSE/Kite network on the event loop."""
    return await asyncio.to_thread(_build_status_quick_payload)


@app.get("/api/status")
async def get_status():
    """Current system state + equity history + last action for charting"""
    return await asyncio.to_thread(_build_status_payload, True)


# === Dedicated Market Status (used by both terminals for the persistent banner) ===
@app.get("/api/market/fo-mood")
async def fo_market_mood():
    """F&O tape mood vs tradeability — 30s cached composite score."""
    try:
        if not _engine_ready():
            return {"available": False, "error": "Trading engine not loaded", "tape_mood": 0, "tradeability": 0}
        live_snapshots_data = {}
        try:
            from app import live_snapshots
            live_snapshots_data = live_snapshots.get_all_snapshots()
        except Exception:
            pass
        mood = await asyncio.to_thread(_get_fo_mood_cached, live_snapshots_data)
        if not mood.get("available", True) and mood.get("error"):
            return mood
        return mood
    except Exception as exc:
        return {"available": False, "error": str(exc), "tape_mood": 0, "tradeability": 0}


@app.get("/api/market/status")
async def market_status():
    """Always-on market context for the GUI. Never throws."""
    try:
        status = get_market_status()
        status["timestamp"] = datetime.now().isoformat()
        return status
    except Exception as e:
        return {
            "session_status": "ERROR",
            "error": str(e),
            "is_market_open": False,
            "is_safe_trading_window": False,
            "is_expiry_day": False,
            "timestamp": datetime.now().isoformat()
        }


_sse_equity_buffer: List[Dict[str, Any]] = []


def _build_sse_payload() -> dict:
    """Sync SSE snapshot — must stay fast; never call Kite/NSE here."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "engine_ready": _engine_ready(),
        "per_symbol_status": {},
        "live_snapshots": {},
        "last_action": "No recent activity",
        "recent_execution": [],
        "last_proposed_signals": {},
        "options_legs": {"available": False, "legs": {}, "summary": {}},
        "options_algo": _default_options_algo_payload(),
    }
    try:
        from app.multi_symbol_risk import multi_risk_manager
        data["per_symbol_status"] = multi_risk_manager.get_per_symbol_status()
    except Exception:
        pass
    try:
        from app import live_snapshots
        data["live_snapshots"] = live_snapshots.get_all_snapshots()
    except Exception:
        pass

    try:
        snaps = data.get("live_snapshots", {})
        for sym in list(data.get("per_symbol_status", {}).keys()):
            s = snaps.get(sym, {})
            if s.get("unrealized_pnl") is not None and abs(s.get("unrealized_pnl", 0)) > 0.5:
                data["per_symbol_status"][sym]["live_unrealized_pnl"] = round(s["unrealized_pnl"], 2)
    except Exception:
        pass

    try:
        snaps = data.get("live_snapshots", {})
        any_snap = next(iter(snaps.values()), {}) if snaps else {}
        from app.risk_gatekeeper import risk_gatekeeper
        current_equity = risk_gatekeeper.capital + risk_gatekeeper.daily_pnl if risk_gatekeeper else 1_000_000
        global _sse_equity_buffer
        _sse_equity_buffer.append({"ts": time.time(), "equity": current_equity})
        if len(_sse_equity_buffer) > 40:
            _sse_equity_buffer.pop(0)
        data["global_params"] = {
            "vol_regime": (any_snap.get("regime") or {}).get("volatility", "normal"),
            "risk_mult": 0.75,
            "equity_recent": list(_sse_equity_buffer),
        }
    except Exception:
        pass

    try:
        recent = _ledger_events_today(10, event_types=list(_RECENT_EXECUTION_EVENT_TYPES))
        if recent:
            action = _last_action_from_ledger_event(recent[0])
            if action:
                data["last_action"] = action
        data["recent_execution"] = _build_recent_execution_from_ledger(8, tail_n=10)
        if data["recent_execution"] and not data.get("last_action", "").startswith(("Signal", "Options")):
            latest = data["recent_execution"][0]
            data["last_action"] = latest.get("reason") or data["last_action"]
        last_sig = {}
        for e in recent:
            if e.get("event_type") == "signal.accepted":
                raw = e.get("payload", {}).get("symbol") or "NIFTY"
                sym = "NIFTY" if "NIFTY" in str(raw).upper() and "BANK" not in str(raw).upper() else (
                    "BANKNIFTY" if "BANKNIFTY" in str(raw).upper() else (
                        "SENSEX" if "SENSEX" in str(raw).upper() else "NIFTY"
                    )
                )
                if sym not in last_sig:
                    pp = e.get("payload", {})
                    last_sig[sym] = {
                        "side": pp.get("side"), "price": pp.get("price"), "atr": pp.get("atr"),
                        "regime": pp.get("regime"), "proposed": pp.get("side"), "ltp": pp.get("price"), "ts": e.get("ts"),
                    }
        data["last_proposed_signals"] = last_sig
    except Exception:
        pass

    cached_mood = _fo_mood_cache.get("payload")
    if cached_mood and time.time() - float(_fo_mood_cache.get("ts") or 0) < _fo_mood_cache_ttl():
        data["fo_mood"] = cached_mood

    try:
        from app.options_legs_engine import options_legs_engine
        data["options_legs"] = options_legs_engine.get_status_payload(fast=True)
    except Exception:
        pass

    return data


def _build_sse_payload_cached() -> dict:
    """Reuse snapshot between SSE ticks — ledger + risk reads are not free."""
    now = time.time()
    cached = _sse_payload_cache.get("payload")
    if cached is not None and now - float(_sse_payload_cache.get("ts") or 0) < SSE_PAYLOAD_CACHE_SEC:
        payload = dict(cached)
        payload["timestamp"] = datetime.now().isoformat()
        return payload
    payload = _build_sse_payload()
    _sse_payload_cache["payload"] = payload
    _sse_payload_cache["ts"] = now
    return payload


# === Real-time SSE endpoint for the 3 index cards (WebSocket-style, much better than polling) ===
@app.get("/api/status/stream")
async def status_stream(request: Request):
    """
    Server-Sent Events (SSE) for smooth real-time updates of the 3 index cards.
    This is the recommended "WebSocket-style" approach for this architecture.
    Frontend uses EventSource.
    """
    async def event_generator():
        # Flush headers immediately so browsers mark EventSource open (don't wait for slow snapshot).
        yield ": connected\n\n"
        heartbeat_sec = max(8.0, min(15.0, SSE_INTERVAL_SEC))
        while True:
            if await request.is_disconnected():
                break
            try:
                data = await asyncio.to_thread(_build_sse_payload_cached)
                yield f"data: {json.dumps(data)}\n\n"
                elapsed = 0.0
                while elapsed < SSE_INTERVAL_SEC:
                    if await request.is_disconnected():
                        break
                    wait = min(heartbeat_sec, SSE_INTERVAL_SEC - elapsed)
                    await asyncio.sleep(wait)
                    elapsed += wait
                    if elapsed < SSE_INTERVAL_SEC:
                        yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                break
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
            except Exception:
                await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# === Recent Trades (from improved Trade Ledger) ===
def _get_trades_payload(limit: int, trade_date: str) -> dict:
    from app.trade_ledger import trade_ledger
    events = trade_ledger.read_events(
        limit=limit * 2,
        date_ist=trade_date,
        event_types=list(_TRADES_API_EVENT_TYPES),
    )
    return {
        "trades": events[:limit],
        "date": trade_date,
        "total_events": trade_ledger.event_count(),
    }


@app.get("/api/trades")
async def get_trades(limit: int = 50, date: Optional[str] = None):
    """Read from the improved trade ledger — defaults to today IST."""
    limit = _clamp_limit(limit)
    trade_date = date or _today_ist()
    try:
        return await asyncio.to_thread(_get_trades_payload, limit, trade_date)
    except Exception as e:
        return {"error": str(e), "trades": [], "total_events": 0, "date": trade_date}


@app.get("/api/ledger")
async def get_ledger(
    limit: int = 200,
    date: Optional[str] = None,
    event_type: Optional[str] = None,
):
    """Full trade ledger history (persisted JSONL) — survives restarts."""
    limit = max(10, min(limit, 2000))
    try:
        from app.trade_ledger import trade_ledger
        types = [event_type] if event_type else None
        events = trade_ledger.read_events(limit=limit, date_ist=date, event_types=types)
        return {
            "events": events,
            "total_events": trade_ledger.event_count(),
            "ledger_path": str(trade_ledger.path),
        }
    except Exception as exc:
        return {"error": str(exc), "events": [], "total_events": 0}


# === Manual external options sheet (brother's daily signals) ===

def _build_external_signals_response(date: Optional[str], with_pnl: bool) -> dict:
    from app.external_signals import external_signals_store, DISPLAY_NAMES, apply_pnl_to_sheet
    sheet = external_signals_store.get(date)
    if with_pnl:
        try:
            kite = _get_kite_client()
            if kite:
                from app.instruments_manager import instruments_manager
                instruments_manager.bind(kite)
            sheet = apply_pnl_to_sheet(sheet)
        except Exception:
            pass
    return {"sheet": sheet, "display_names": DISPLAY_NAMES}


@app.get("/api/external-signals")
async def get_external_signals(date: Optional[str] = None, with_pnl: bool = True):
    """Load manual CE/PE sheet for a trade date (default: today IST)."""
    return await asyncio.to_thread(_build_external_signals_response, date, with_pnl)


@app.get("/api/external-signals/dates")
async def list_external_signal_dates():
    from app.external_signals import external_signals_store
    return {"dates": external_signals_store.list_dates()}


def _save_external_signals_sync(body: dict) -> dict:
    from app.external_signals import external_signals_store
    from app.options_legs_engine import options_legs_engine
    sheet = body.get("sheet") or body
    saved = external_signals_store.save(sheet)
    options_legs_engine.on_sheet_saved(saved)
    rows = external_signals_store.journal_for_date(saved["date"])
    return {"ok": True, "sheet": saved, "journal_rows": rows}


@app.post("/api/external-signals")
async def save_external_signals(request: Request):
    """Save manual options sheet for the given date."""
    try:
        body = await request.json()
        return await asyncio.to_thread(_save_external_signals_sync, body)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _delete_external_signals_response(trade_date: str) -> Dict[str, Any]:
    from app.external_signals import external_signals_store, _empty_sheet
    from datetime import date as date_type

    if not trade_date:
        return {"ok": False, "error": "date required"}
    key = str(trade_date)[:10]
    deleted = external_signals_store.delete(key)
    empty = _empty_sheet(date_type.fromisoformat(key))
    return {"ok": True, "deleted": deleted, "date": key, "sheet": empty}


@app.delete("/api/external-signals")
async def delete_external_signals(date: str):
    """Delete saved options sheet for a trade date."""
    result = _delete_external_signals_response(date)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/external-signals/delete")
async def delete_external_signals_post(date: str):
    """Delete saved options sheet (POST fallback when DELETE is blocked or unavailable)."""
    result = _delete_external_signals_response(date)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


def _external_signals_premiums_sync(date: Optional[str]) -> dict:
    from app.external_signals import external_signals_store, fetch_live_premiums, apply_pnl_to_sheet
    sheet = external_signals_store.get(date)
    try:
        kite = _get_kite_client()
        if kite:
            from app.instruments_manager import instruments_manager
            instruments_manager.bind(kite)
        premiums = fetch_live_premiums(sheet)
        enriched = apply_pnl_to_sheet(sheet, premiums)
        return {
            "sheet_date": sheet.get("date"),
            "premiums": premiums,
            "sheet": enriched,
            "pnl_summary": enriched.get("pnl_summary"),
        }
    except Exception as exc:
        return {"sheet_date": sheet.get("date"), "premiums": {"available": False, "error": str(exc)}}


@app.get("/api/external-signals/premiums")
async def external_signals_premiums(date: Optional[str] = None):
    """Live Kite CE/PE LTP for strikes on the saved sheet (optional cross-check)."""
    return await asyncio.to_thread(_external_signals_premiums_sync, date)


def _evaluate_external_signals_sync(date: Optional[str]) -> dict:
    from app.external_signals import evaluate_and_save
    from app.options_legs_engine import options_legs_engine
    kite = _get_kite_client()
    if kite:
        from app.instruments_manager import instruments_manager
        instruments_manager.bind(kite)
    sheet, premiums, rows = evaluate_and_save(date)
    options_legs_engine.refresh_from_sheet(sheet, premiums)
    return {
        "ok": True,
        "sheet": sheet,
        "premiums": premiums,
        "journal_rows": rows,
        "pnl_summary": sheet.get("pnl_summary"),
    }


@app.post("/api/external-signals/evaluate")
async def evaluate_external_signals(date: Optional[str] = None):
    """
    Check live premiums vs entry/target/stop, update journal on the sheet, and save.
    Call during market hours (or anytime for snapshot). History accumulates per saved date.
    """
    try:
        return await asyncio.to_thread(_evaluate_external_signals_sync, date)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/external-signals/journal")
async def external_signals_journal(limit: int = 90, date: Optional[str] = None):
    """Journal rows for one trade date, or all saved days when date is omitted."""
    from app.external_signals import external_signals_store
    limit = _clamp_limit(limit, default=90, max_limit=500)
    rows = external_signals_store.journal(limit=limit, trade_date=date)
    return {"rows": rows, "dates": external_signals_store.list_dates(), "filtered_date": date}


@app.get("/api/external-signals/comparison")
async def external_signals_comparison(date: Optional[str] = None):
    """Sheet vs algo scoreboard — who wins today per index."""
    from app.sheet_algo_bridge import build_sheet_vs_algo_scoreboard, get_sheet_inputs_for_symbol
    from app.config_loader import get_external_signals_config

    return await asyncio.to_thread(
        lambda: {
            **build_sheet_vs_algo_scoreboard(date),
            "config": get_external_signals_config(),
            "inputs": {
                sym: get_sheet_inputs_for_symbol(sym)
                for sym in ("NIFTY", "BANKNIFTY", "SENSEX")
            },
        }
    )


def _build_options_legs_live_fast() -> dict:
    """In-memory leg snapshots — warm cache if engine has not ticked yet."""
    from app.options_legs_engine import options_legs_engine
    options_legs_engine._ensure_loaded()
    status = options_legs_engine.get_status_payload(fast=True)
    if not status.get("available"):
        status = options_legs_engine.get_status_payload(fast=False)
    return {
        "available": status.get("available", False),
        "date": status.get("date"),
        "timestamp": datetime.now().isoformat(),
        "legs": status.get("legs", {}),
        "summary": status.get("summary", {}),
        "subscribed_tokens": status.get("subscribed_tokens", 0),
    }


def _build_options_legs_live_full(date: Optional[str]) -> dict:
    from app.options_legs_engine import options_legs_engine
    from app.external_signals import external_signals_store
    sheet = external_signals_store.get(date)
    try:
        kite = _get_kite_client()
        if kite:
            from app.instruments_manager import instruments_manager
            instruments_manager.bind(kite)
    except Exception:
        pass
    return options_legs_engine.build_live_response(sheet)


@app.get("/api/options-legs/live")
async def options_legs_live(date: Optional[str] = None, refresh: bool = False):
    """Live 6-leg options desk snapshot (CE/PE per index)."""
    if refresh:
        return await asyncio.to_thread(_build_options_legs_live_full, date)
    return await asyncio.to_thread(_build_options_legs_live_fast)


# === Legacy stream alias (redirect clients to the lightweight SSE path) ===
@app.get("/api/stream")
async def legacy_status_stream(request: Request):
    return await status_stream(request)

# ==================== INTERACTIVE BACKTEST ENDPOINTS ====================

@app.post("/api/backtest/run")
async def trigger_backtest(
    background_tasks: BackgroundTasks,
    months: int = Form(4),
    folds: int = Form(4),
    risk_low: float = Form(0.003),
    risk_high: float = Form(0.004),
    max_trades: int = Form(3),
    vol_strict: float = Form(0.55),
    research_mode: bool = Form(False),
    cost_multiplier: float = Form(1.0),
    entry_on_next_bar: bool = Form(False),
    quick_mode: bool = Form(False),
    use_real_data: bool = Form(True),
    force_refresh: bool = Form(False),
    underlying: str = Form("NIFTY"),
    wfo_objective: str = Form("calmar"),
):
    """
    Trigger a walk-forward + regime backtest from the web UI.
    Runs in background so the UI stays responsive.
    """
    job_id = str(uuid.uuid4())[:8]
    started_at = time.time()

    BACKTEST_JOBS[job_id] = {
        "status": "running",
        "progress": 0,
        "started_at": started_at,
        "params": {
            "months": months,
            "folds": folds,
            "risk_range": [risk_low, risk_high],
            "max_trades": max_trades,
            "vol_strict": vol_strict,
            "research_mode": research_mode,
            "cost_multiplier": cost_multiplier,
            "entry_on_next_bar": entry_on_next_bar,
            "use_real_data": use_real_data,
            "force_refresh": force_refresh,
            "underlying": underlying.upper(),
            "wfo_objective": wfo_objective,
        },
        "result": None,
        "error": None,
        "error_code": None,
    }
    _cleanup_jobs()
    _persist_job(job_id)

    def run_backtest_job():
        try:
            BACKTEST_JOBS[job_id]["progress"] = 5
            BACKTEST_JOBS[job_id]["stage"] = "preparing"

            def _check_cancel():
                if CANCEL_REQUESTED.get(job_id):
                    print(f"[BACKTEST JOB] Cancel detected for {job_id} — aborting cleanly.")
                    BACKTEST_JOBS[job_id]["status"] = "cancelled"
                    BACKTEST_JOBS[job_id]["stage"] = "cancelled"
                    raise SystemExit("Job cancelled by user")  # clean exit from the job

            # GPU / acceleration status (for results + export)
            gpu_available = False
            gpu_name = None
            try:
                import torch
                if torch.cuda.is_available():
                    gpu_available = True
                    gpu_name = torch.cuda.get_device_name(0)
            except Exception:
                pass
            BACKTEST_JOBS[job_id]["gpu_available"] = gpu_available
            BACKTEST_JOBS[job_id]["gpu_name"] = gpu_name

            # === Quick Mode overrides MUST happen FIRST (before any reads) ===
            # This fixes the UnboundLocalError on research_mode/folds and makes Quick Mode actually work.
            # We use distinct "effective_" names so we don't turn the closed-over outer names into locals.
            effective_folds = folds
            effective_research_mode = research_mode
            effective_use_real_data = use_real_data
            effective_param_grid = None

            if quick_mode:
                effective_folds = 1
                effective_research_mode = True
                effective_use_real_data = False
                effective_param_grid = {
                    "risk_per_trade_pct": [risk_low, risk_high],
                    "breakout_atr_mult": [0.7, 0.8],
                    "profit_target_atr_mult": [2.0],
                    "stop_loss_atr_mult": [1.0],
                    "max_trades_per_day": [max_trades],
                    "min_prev_candle_range_atr": [vol_strict],
                    "volume_mult": [1.1],
                    "use_trend_filter": [True],
                    "research_mode": [True],
                    "entry_on_next_bar": [entry_on_next_bar],
                }

            # === CRITICAL: Always import the BACKTEST version explicitly inside the job ===
            # This prevents any name shadowing from the live trading imports (app/strategy.py)
            from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy as BacktestStrategy

            cost_model = TransactionCostModel(CostConfig(
                brokerage_per_order=20.0,
                other_charges_per_lot_round_turn=55.0,
                default_slippage_points=4.0,
            ))

            _check_cancel()

            # Expanded grid — these parameters now actually affect trade frequency
            # (the previous grid was too narrow and the strategy too constrained)
            param_grid = effective_param_grid or {
                "risk_per_trade_pct": [risk_low, (risk_low + risk_high) / 2, risk_high],
                "breakout_atr_mult": [0.65, 0.75, 0.85],
                "profit_target_atr_mult": [1.8, 2.2],
                "stop_loss_atr_mult": [0.95, 1.15],
                # These now come from the UI and will actually change behavior
                "max_trades_per_day": [max(1, max_trades - 1), max_trades, max_trades + 1],
                "min_prev_candle_range_atr": [vol_strict - 0.1, vol_strict, vol_strict + 0.1],
                "volume_mult": [1.05, 1.15],
                "use_trend_filter": [True, False],
                # Research Mode flag — only affects the backtest strategy
                "research_mode": [effective_research_mode],
                "entry_on_next_bar": [entry_on_next_bar],
            }

            data = None
            data_source = "synthetic"
            if effective_use_real_data:
                BACKTEST_JOBS[job_id]["stage"] = "fetching_real_data"
                print(f"[BACKTEST JOB] Real data requested (months={months}, force_refresh={force_refresh}). Smart cache will be used if available.")
                def _do_fetch(kite):
                    to_dt = datetime.now()
                    from_dt = to_dt - timedelta(days=30 * months)
                    return fetch_real_index_futures_data(
                        kite, from_dt, to_dt,
                        underlying=underlying.upper(),
                        interval="5minute",
                        use_cache=not force_refresh,
                        force_refresh=force_refresh,
                    )
                res = _safe_kite_call(_do_fetch, "historical_multi_contract")
                if not res.get("error_code"):
                    print("[BACKTEST JOB] Real data fetch completed. See [CACHE] / [DATA] logs for exact cache hit details.")
                if res.get("error_code"):
                    BACKTEST_JOBS[job_id]["error"] = res["error"]
                    BACKTEST_JOBS[job_id]["error_code"] = res["error_code"]
                else:
                    data = res.get("data")
                    data_source = "kite_or_cache"
                    BACKTEST_JOBS[job_id]["progress"] = 35

            MIN_REAL_BARS = 500
            used_partial_cache = False
            if effective_use_real_data and data is not None and 200 <= len(data) < MIN_REAL_BARS:
                data_source = "partial_cache"
                used_partial_cache = True

            if data is None or len(data) < MIN_REAL_BARS:
                if effective_use_real_data and data is not None and len(data) >= 200:
                    used_partial_cache = True
                    data_source = "partial_cache"
                else:
                    BACKTEST_JOBS[job_id]["stage"] = "generating_synthetic"
                    data_source = "synthetic"
                    import numpy as np
                    np.random.seed(42)
                    n_bars = 8000
                    prices = [24000.0]
                    current_vol = 5.0
                    trend = 0.0
                    regime = "normal"
                    vol_regime = "normal"

                    for i in range(n_bars - 1):
                        if i % 450 == 0:
                            regime = np.random.choice(["low", "normal", "high"], p=[0.20, 0.55, 0.25])
                            current_vol = {"low": 2.5, "normal": 5.2, "high": 11.0}[regime]

                        if np.random.rand() < 0.018:
                            vol_regime = "burst"
                            current_vol *= 1.8
                        elif vol_regime == "burst" and np.random.rand() < 0.35:
                            vol_regime = "normal"

                        if i % 950 == 0:
                            trend = np.random.uniform(-1.8, 1.8)

                        prev_ret = (prices[-1] - prices[-2]) if len(prices) > 1 else 0
                        momentum = prev_ret * np.random.uniform(0.08, 0.22)

                        ret = np.random.normal(trend + momentum, current_vol)
                        new_price = prices[-1] + ret
                        prices.append(new_price)

                    prices = np.array(prices)
                    noise = np.random.normal(0, 2.1, n_bars)
                    range_mult = np.abs(np.random.normal(1.0, 0.6, n_bars)) + 0.3
                    vol_spike = np.random.choice([1, 1.8, 2.6], size=n_bars, p=[0.78, 0.15, 0.07])

                    data = pd.DataFrame({
                        "open": prices + noise * 0.35,
                        "high": prices + np.abs(noise) * range_mult + np.random.uniform(0.8, 4.2, n_bars),
                        "low": prices - np.abs(noise) * range_mult - np.random.uniform(0.8, 4.2, n_bars),
                        "close": prices,
                        "volume": (np.random.randint(38000, 220000, n_bars) * vol_spike).astype(int),
                        "rollover": False,
                        "front_month": "SYNTHETIC"
                    }, index=pd.date_range("2026-01-01", periods=n_bars, freq="5min"))
                    BACKTEST_JOBS[job_id]["progress"] = 45

            BACKTEST_JOBS[job_id]["progress"] = 50
            BACKTEST_JOBS[job_id]["stage"] = "running_walk_forward"

            _check_cancel()

            def _update_progress(pct: int, stage: str):
                BACKTEST_JOBS[job_id]["progress"] = min(95, pct)
                BACKTEST_JOBS[job_id]["stage"] = stage

            # Post-run statistical power diagnostic (very important for honest WFA)
            total_trades = sum(f.get("trades", 0) for f in [])  # will be filled after WFA
            # (actual computation happens after result is available below)

            result = run_walk_forward(
                strategy_class=BacktestStrategy,   # <-- Always the correct backtest class
                data=data,
                param_grid=param_grid,
                n_folds=effective_folds,
                train_size=0.60,
                cost_model=cost_model,
                progress_callback=_update_progress,
                cost_multiplier=cost_multiplier,
                wfo_mode="rolling_purged",
                objective=wfo_objective,
                underlying=underlying.upper(),
            )

            # Attach research mode flag + hard Statistical Power Warning (final professional polish)
            if isinstance(result, dict):
                result["research_mode_used"] = effective_research_mode
                result["data_source"] = data_source
                result["data_bars"] = len(data) if data is not None else 0
                if data_source == "synthetic":
                    result["data_warning"] = (
                        "SYNTHETIC DATA: Results are for smoke testing only. "
                        "Enable 'Use real Kite historical data' and download cache in the Data tab."
                    )
                elif used_partial_cache:
                    result["data_warning"] = (
                        f"PARTIAL CACHE ({len(data)} bars): Consider downloading more history "
                        "for reliable walk-forward conclusions."
                    )

                total_trades = result.get("total_trades", 0)
                if total_trades < 20:
                    result["statistical_power"] = {
                        "total_trades": total_trades,
                        "warning_level": "critical",
                        "message": "CRITICAL: Far too few trades for reliable Walk-Forward conclusions.",
                        "recommendation": "Enable Research Mode + increase Max Trades per Day significantly. Use longer real data."
                    }
                elif total_trades < 40:
                    result["statistical_power"] = {
                        "total_trades": total_trades,
                        "warning_level": "strong",
                        "message": "STRONG WARNING: Low sample size. High risk of overfitting.",
                        "recommendation": "Enable Research Mode and collect more trades before trusting results."
                    }
                elif total_trades < 60:
                    result["statistical_power"] = {
                        "total_trades": total_trades,
                        "warning_level": "caution",
                        "message": "Caution: Moderate sample size. Results have meaningful uncertainty.",
                        "recommendation": "Acceptable for exploration but increase sample size for decisions."
                    }
                else:
                    result["statistical_power"] = {
                        "total_trades": total_trades,
                        "warning_level": "ok",
                        "message": "Sample size is reasonable for directional conclusions.",
                        "recommendation": "Continue with periodic re-validation as more data becomes available."
                    }

            # (Quick Mode overrides are now applied at the very top of the job so they actually affect
            # param_grid, data source, and folds. The old late block has been removed.)

            # === Top-level robustness metrics (cost sensitivity + pooled MC + GPU status) ===
            # Collect any trades we can find (per-fold lists if the runner bubbled them, or counts)
            pooled_trades = []
            if isinstance(result, dict):
                for f in result.get("folds", []):
                    # Some backtester paths attach the actual trade list under the fold
                    if isinstance(f, dict) and f.get("trades_list"):
                        pooled_trades.extend(f["trades_list"])
                    # Fallback: we at least have counts; MC will be low-sample but will now run

                # Always compute a top-level MC (even on tiny samples — now produces stats + gpu_used + warning)
                try:
                    from backtesting.metrics import monte_carlo_simulation
                    top_mc = monte_carlo_simulation(pooled_trades or [], n_sims=1000, initial_capital=1_000_000)
                    result["monte_carlo"] = top_mc
                    result["gpu_used"] = top_mc.get("gpu_used", False)
                    result["gpu_device"] = top_mc.get("gpu_device")
                except Exception as e:
                    result["monte_carlo"] = {"message": f"Top-level MC failed: {e}", "gpu_used": False}

                result["gpu_available"] = gpu_available
                result["gpu_name"] = gpu_name

                # Cost sensitivity on whatever we have (or a note)
                if pooled_trades:
                    cost_summary = {}
                    for mult in [1.0, 2.0, 3.0]:
                        temp_model = TransactionCostModel.with_multiplier(cost_model.config, mult) if cost_model else TransactionCostModel(CostConfig(cost_multiplier=mult))
                        adjusted_pnl = 0.0
                        for t in pooled_trades:
                            gross = t.get('gross_pnl', t.get('pnl', 0))
                            cost = temp_model.estimate_cost_for_trade(
                                quantity=t.get('quantity', 75),
                                entry_price=t.get('entry_price', 0),
                                exit_price=t.get('exit_price', 0)
                            )
                            adjusted_pnl += (gross - cost)
                        cost_summary[f"{int(mult)}x"] = round(adjusted_pnl, 2)
                    result["cost_sensitivity_summary"] = cost_summary
                else:
                    result["cost_sensitivity_summary"] = {
                        "note": "No detailed trades available for cost re-simulation (very low sample run). Per-fold MC already computed with cost model."
                    }

            BACKTEST_JOBS[job_id]["progress"] = 100
            BACKTEST_JOBS[job_id]["status"] = "completed"
            BACKTEST_JOBS[job_id]["result"] = result
            BACKTEST_JOBS[job_id]["completed_at"] = time.time()
            BACKTEST_JOBS[job_id]["stage"] = "done"
            _persist_job(job_id)

        except SystemExit as se:
            # User-initiated cancellation
            if "cancel" in str(se).lower():
                BACKTEST_JOBS[job_id]["status"] = "cancelled"
                BACKTEST_JOBS[job_id]["stage"] = "cancelled"
                print(f"[BACKTEST JOB] Job {job_id} was cancelled by user.")
            else:
                BACKTEST_JOBS[job_id]["status"] = "failed"
                BACKTEST_JOBS[job_id]["error"] = str(se)
                BACKTEST_JOBS[job_id]["error_code"] = "BACKTEST_FAILED"
            _persist_job(job_id)
        except Exception as e:
            BACKTEST_JOBS[job_id]["status"] = "failed"
            BACKTEST_JOBS[job_id]["error"] = str(e)
            BACKTEST_JOBS[job_id]["error_code"] = "BACKTEST_FAILED"
            _persist_job(job_id)

    background_tasks.add_task(run_backtest_job)
    return {"job_id": job_id, "status": "started"}


@app.get("/api/backtest/result/{job_id}")
async def get_backtest_result(job_id: str):
    job = BACKTEST_JOBS.get(job_id)
    if not job:
        job = load_job(job_id)
        if job:
            BACKTEST_JOBS[job_id] = job
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@app.get("/api/backtest/jobs")
async def list_backtest_jobs():
    """List recent backtest/data jobs (memory + persisted)."""
    merged = {**load_all_jobs(limit=MAX_JOBS), **BACKTEST_JOBS}
    items = sorted(
        [{"job_id": jid, **{k: v for k, v in job.items() if k != "result"}} for jid, job in merged.items()],
        key=lambda x: x.get("started_at", 0),
        reverse=True,
    )
    return {"jobs": items[:MAX_JOBS]}


@app.post("/api/backtest/cancel/{job_id}")
async def cancel_backtest_job(job_id: str):
    """Request cancellation of a running backtest or data job."""
    if job_id not in BACKTEST_JOBS:
        return {"status": "not_found"}

    CANCEL_REQUESTED[job_id] = True
    BACKTEST_JOBS[job_id]["cancel_requested"] = True
    BACKTEST_JOBS[job_id]["stage"] = "cancelling"

    print(f"[CANCEL] Cancellation requested for job {job_id}")
    return {"status": "cancellation_requested", "job_id": job_id}


# ==================== MEMORY / LEARNING ENDPOINTS (powers the super GUI Learnings tab) ====================

@app.get("/api/memory/insights")
async def get_memory_insights(regime: Optional[str] = None):
    """Returns rich regime statistics + auto-generated natural language documentation notes."""
    try:
        insights = backtest_memory.generate_insights(regime)
        return insights
    except Exception as e:
        return {"error": str(e), "message": "Learning layer unavailable"}

@app.get("/api/memory/report")
async def get_full_learning_report():
    """Full exportable learning report for documentation or further analysis."""
    try:
        return backtest_memory.get_learning_report()
    except Exception as e:
        return {"error": str(e)}


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request):
    """Interactive backtest + data fetch control panel. Aegis with full learning + Kite integration."""
    return templates.TemplateResponse("backtest.html", {"request": request})


@app.get("/api/data/health")
async def get_data_health(stale_days: int = 5):
    """
    Validate all local market data (parquet caches + JSON runtime files).
    Powers the Data Health panel in Aegis → Presets & Data.
    """
    try:
        from backtesting.data_health import scan_data_health
        return scan_data_health(stale_days=stale_days)
    except Exception as e:
        return {"overall": "error", "error": str(e)}


@app.post("/api/data/fetch")
async def fetch_historical_data(
    background_tasks: BackgroundTasks,
    months: int = Form(3),
    use_local_only: bool = Form(False),
    force_refresh: bool = Form(False),
):
    """
    Trigger data loading (supports cache discipline).
    - If use_local_only=True and not force → purely loads from data/historical_cache (fastest, zero Kite).
    - force_refresh=True → always hit Kite, ignore cache (explicit fresh pull).
    - Default (neither) → smart: auto-prefer best local overlap, only Kite on gap.
    """

    job_id = str(uuid.uuid4())[:8]
    _cleanup_jobs()

    def _update_job(pct: int, stage: str, extra: Optional[Dict[str, Any]] = None):
        BACKTEST_JOBS[job_id]["progress"] = pct
        BACKTEST_JOBS[job_id]["stage"] = stage
        if extra:
            BACKTEST_JOBS[job_id].update(extra)

    def fetch_job():
        BACKTEST_JOBS[job_id].update({
            "status": "running",
            "type": "data_fetch",
            "progress": 2,
            "stage": "preparing",
            "started_at": time.time(),
        })

        def _check_data_cancel():
            if CANCEL_REQUESTED.get(job_id):
                print(f"[DATA JOB] Cancel detected for {job_id}")
                BACKTEST_JOBS[job_id]["status"] = "cancelled"
                BACKTEST_JOBS[job_id]["stage"] = "cancelled"
                raise SystemExit("Data job cancelled")

        def _on_progress(pct: int, stage: str, extra: Optional[Dict[str, Any]] = None):
            _check_data_cancel()
            _update_job(pct, stage, extra)

        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=30 * months)
            _check_data_cancel()

            if use_local_only and not force_refresh:
                from backtesting.data_loader import _load_overlapping_cached_data
                _update_job(20, "scanning_local_cache")
                print(f"[DATA JOB] Attempting FULL local cache load only (months={months})")
                data = _load_overlapping_cached_data(from_dt, to_dt, "5minute")
                if data is None or len(data) < 500:
                    raise ValueError("No sufficient overlapping data found in local cache (data/historical_cache).")
                print(f"[DATA JOB] ✅ FULL CACHE HIT - {len(data)} rows loaded from local overlapping cache. No Kite call made.")
                BACKTEST_JOBS[job_id].update({
                    "status": "completed",
                    "type": "data_fetch",
                    "rows": len(data),
                    "from": str(from_dt.date()),
                    "to": str(to_dt.date()),
                    "source": "local_cache_only",
                    "progress": 100,
                    "stage": "done",
                    "force_refresh": False,
                    "cache_hit": "full",
                })
            else:
                load_dotenv()
                kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
                kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

                print(f"[DATA JOB] Smart data load starting (months={months}, force_refresh={force_refresh})")
                data = fetch_real_nifty_futures_data(
                    kite, from_dt, to_dt, "5minute",
                    use_cache=not force_refresh,
                    force_refresh=force_refresh,
                    progress_callback=_on_progress,
                )

                print(f"[DATA JOB] Data load complete via smart path → {len(data)} rows.")
                BACKTEST_JOBS[job_id].update({
                    "status": "completed",
                    "type": "data_fetch",
                    "rows": len(data),
                    "from": str(from_dt.date()),
                    "to": str(to_dt.date()),
                    "source": "force_kite" if force_refresh else "smart_cache_or_kite",
                    "progress": 100,
                    "stage": "done",
                    "force_refresh": force_refresh,
                    "cache_hit": "force" if force_refresh else "smart_or_partial",
                })
        except SystemExit as se:
            if "cancel" in str(se).lower():
                BACKTEST_JOBS[job_id].update({"status": "cancelled", "stage": "cancelled", "progress": 100})
            else:
                BACKTEST_JOBS[job_id].update({"status": "failed", "error": str(se), "progress": 100, "stage": "failed"})
        except Exception as e:
            BACKTEST_JOBS[job_id].update({"status": "failed", "error": str(e), "progress": 100, "stage": "failed"})

    BACKTEST_JOBS[job_id] = {
        "status": "queued",
        "type": "data_fetch",
        "progress": 0,
        "stage": "queued",
        "started_at": time.time(),
    }
    background_tasks.add_task(fetch_job)
    return {"job_id": job_id, "status": "started"}


@app.get("/api/data/cached_datasets")
async def get_cached_datasets():
    """
    Returns list of all locally available cached parquet datasets.
    Powers the 'Available Cached Datasets' panel in the GUI.
    Backtests and data fetches automatically prefer the best overlap from these.
    Only Force Refresh bypasses them.
    """
    try:
        datasets = list_available_cached_datasets()
        return {
            "datasets": datasets,
            "count": len(datasets),
            "cache_dir": "data/historical_cache",
            "note": "Backtest with 'Use real data' checked will auto-use the best overlapping cache without any extra action. Check 'Force Refresh' only when you explicitly need fresh Kite pulls."
        }
    except Exception as e:
        return {"datasets": [], "count": 0, "error": str(e)}


# ==================== KITE API ENHANCED ENDPOINTS ====================

@app.post("/api/kite/postback")
async def receive_postback(request: Request):
    """
    Postback receiver for Kite order updates.
    This is far more reliable and efficient than polling for order status.
    """
    try:
        from app.postback_checksum import verify_postback_checksum

        payload = await request.json()
        order_id = payload.get("order_id")

        ok, reason, computed = verify_postback_checksum(payload)
        if not ok:
            audit_logger.record("kite.postback.rejected", {
                "reason": reason,
                "order_id": order_id,
                "status": payload.get("status"),
                "received_checksum": payload.get("checksum"),
                "computed_checksum": computed,
            })
            if reason == "checksum_mismatch":
                print("[POSTBACK] Checksum mismatch — possible tampering")
                return {"status": "invalid_checksum"}
            return {"status": "invalid"}

        # Process the update
        print(f"[POSTBACK] Order update received: {payload.get('status')} for {order_id}")

        from app.order_lifecycle import order_lifecycle
        from app.trade_ledger import trade_ledger

        trade_ledger.record("kite.postback", payload)
        kite_client = _get_kite_client()
        if kite_client:
            order_lifecycle.bind_kite(kite_client)
        order_lifecycle.handle_postback(payload)

        return {"status": "ok"}

    except Exception as e:
        print(f"[POSTBACK] Error: {e}")
        return {"status": "error"}


@app.get("/api/kite/trades")
async def get_real_trades():
    """Fetch actual executed trades from Kite for accurate P&L and tax reporting."""
    res = _safe_kite_call(lambda k: k.trades(), "trades")
    if res.get("error_code"):
        return {"trades": [], "count": 0, **res}
    trades = res["data"] or []
    return {"trades": trades, "count": len(trades)}


@app.get("/api/kite/orders")
async def get_orders_history():
    """Fetch recent order history (useful for reconciliation and fill quality learning)."""
    res = _safe_kite_call(lambda k: k.orders(), "orders")
    if res.get("error_code"):
        return {"orders": [], "count": 0, **res}
    orders = res["data"] or []
    return {"orders": orders, "count": len(orders)}


@app.get("/api/kite/margins")
async def get_margins():
    """Get current margin details from Kite (very useful for risk view)."""
    res = _safe_kite_call(lambda k: k.margins(), "margins")
    if res.get("error_code"):
        return {"error": res["error"], "error_code": res["error_code"]}
    return res.get("data") or {}


@app.get("/api/backtest/candidates")
async def get_strategy_candidates():
    """Promotion gate results — params eligible for paper deployment."""
    return {"candidates": load_candidates()}


@app.get("/api/agent/brief/latest")
async def get_latest_brief():
    """Latest saved FO market brief (Phase 2 intelligence loop)."""
    try:
        from app.intelligence_loop import intelligence_loop

        brief = intelligence_loop.load_latest_brief()
        if brief is None:
            return {"brief": None, "message": "No brief generated yet. POST /api/agent/brief/generate"}
        return {"brief": brief, "text": intelligence_loop.format_brief_text(brief)}
    except Exception as exc:
        return {"brief": None, "error": str(exc)}


@app.post("/api/agent/brief/generate")
async def generate_brief():
    """Build and persist today's deterministic market brief."""
    try:
        from app.intelligence_loop import intelligence_loop

        brief = intelligence_loop.build_market_brief()
        path = intelligence_loop.save_market_brief(brief)
        return {
            "success": True,
            "path": str(path),
            "brief": brief,
            "text": intelligence_loop.format_brief_text(brief),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/agent/safe-deploy")
async def get_safe_deploy_checklist():
    """Pre-deployment safety checklist (paper or live)."""
    try:
        from app.intelligence_loop import intelligence_loop

        return intelligence_loop.run_safe_deploy_checklist()
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


@app.get("/api/micro-live/status")
async def get_micro_live_status():
    """Micro-live readiness: human gates, promotion, and deploy checklist."""
    try:
        from app.micro_live import validate_micro_live_ready

        return validate_micro_live_ready()
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


@app.get("/api/agent/insights")
async def get_agent_insights(refresh: bool = False):
    """Aegis agent insights — promotion, WFO, proposals, lunar, market context."""
    try:
        from app.agent_insights import build_agent_insights, load_agent_insights, save_agent_insights

        if refresh:
            insights = build_agent_insights(refresh_lunar=True)
            save_agent_insights(insights)
            return insights

        saved = load_agent_insights()
        if saved:
            return saved

        insights = build_agent_insights()
        save_agent_insights(insights)
        return insights
    except Exception as exc:
        return {"error": str(exc)}


# ==================== PHASE 4C — CONTINUOUS IMPROVEMENT ====================

@app.get("/api/improvement/daily")
async def get_improvement_daily():
    """Latest or on-demand daily session quality report."""
    try:
        from app.session_tracker import session_tracker

        today = datetime.now().strftime("%Y-%m-%d")
        saved = session_tracker.load_daily_session_report(today)
        if saved:
            path = session_tracker.reports_dir / f"{today}.json"
            return {"report": saved, "path": str(path), "source": "saved"}

        report = session_tracker.build_daily_session_report()
        path = session_tracker.save_daily_session_report(report)
        return {"report": report, "path": str(path), "source": "built_on_demand"}
    except Exception as exc:
        return {"error": str(exc), "report": None}


@app.get("/api/journal")
async def get_trading_journal(date: Optional[str] = None):
    """Trading journal for a session — performance, feedback, notes."""
    try:
        from app.trading_journal import trading_journal
        from app.market_calendar import now_ist

        target = date or now_ist().strftime("%Y-%m-%d")
        entry = trading_journal.load_journal(target)
        if entry is None:
            entry = trading_journal.build_journal_entry(target)
            trading_journal.save_journal(entry)
        return {"journal": entry, "date_ist": target}
    except Exception as exc:
        return {"error": str(exc), "journal": None}


@app.get("/api/journal/list")
async def list_trading_journals(limit: int = 30):
    try:
        from app.trading_journal import trading_journal
        limit = _clamp_limit(limit, default=30, max_limit=90)
        return {"journals": trading_journal.list_journals(limit=limit)}
    except Exception as exc:
        return {"error": str(exc), "journals": []}


@app.post("/api/journal/note")
async def add_journal_note(request: Request):
    try:
        from app.trading_journal import trading_journal
        body = await request.json()
        note = (body.get("note") or body.get("text") or "").strip()
        if not note:
            return JSONResponse({"ok": False, "error": "note required"}, status_code=400)
        date_ist = body.get("date_ist")
        entry = trading_journal.add_trader_note(note, date_ist=date_ist)
        return {"ok": True, "journal": entry}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/journal/build")
async def build_trading_journal_endpoint(date: Optional[str] = None):
    try:
        from app.trading_journal import trading_journal
        path = trading_journal.build_and_save(date)
        entry = trading_journal.load_journal(path.stem)
        return {"ok": True, "path": str(path), "journal": entry}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/data/eod-audit")
async def get_eod_audit(date: Optional[str] = None):
    try:
        from backtesting.eod_audit import load_eod_audit_report, run_eod_audit, previous_trading_day
        if date:
            report = load_eod_audit_report(date)
            if report is None:
                from datetime import datetime as dt
                report = run_eod_audit(trade_date=dt.strptime(date, "%Y-%m-%d").date())
        else:
            report = load_eod_audit_report(previous_trading_day().isoformat())
            if report is None:
                report = run_eod_audit()
        return {"report": report}
    except Exception as exc:
        return {"error": str(exc), "report": None}


@app.get("/api/data/holidays")
async def get_synced_holidays():
    try:
        from app.nse_data import load_synced_holidays
        from app.market_calendar import MARKET_HOLIDAYS, reload_holidays_from_disk
        payload = load_synced_holidays()
        return {
            "holiday_count": payload.get("holiday_count", 0),
            "synced_at": payload.get("synced_at"),
            "holidays": payload.get("holidays", []),
            "calendar_total": len(MARKET_HOLIDAYS),
            "errors": payload.get("errors", []),
        }
    except Exception as exc:
        return {"error": str(exc), "holidays": [], "holiday_count": 0}


@app.post("/api/data/holidays/sync")
async def sync_holidays_endpoint():
    try:
        from app.nse_data import sync_holidays_from_nse
        from app.market_calendar import reload_holidays_from_disk, MARKET_HOLIDAYS
        result = sync_holidays_from_nse()
        added = reload_holidays_from_disk()
        return {
            "ok": True,
            "holiday_count": result.get("holiday_count"),
            "calendar_total": len(MARKET_HOLIDAYS),
            "new_from_file": added,
            "errors": result.get("errors", []),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/improvement/weekly")
async def get_improvement_weekly():
    """Latest weekly earn report or build on demand."""
    try:
        from app.improvement_loop import improvement_loop

        latest = improvement_loop.load_latest_weekly_report()
        if latest:
            return {
                "report": latest,
                "path": latest.get("saved_path"),
                "source": "saved",
            }

        report = improvement_loop.build_weekly_report()
        path = improvement_loop.save_weekly_report(report)
        return {"report": report, "path": str(path), "source": "built_on_demand"}
    except Exception as exc:
        return {"error": str(exc), "report": None}


@app.post("/api/improvement/weekly/generate")
async def generate_improvement_weekly():
    """Explicitly build and persist a fresh weekly earn report."""
    try:
        from app.improvement_loop import improvement_loop

        report = improvement_loop.build_weekly_report()
        path = improvement_loop.save_weekly_report(report)
        return {"success": True, "report": report, "path": str(path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/improvement/proposals")
async def get_improvement_proposals():
    """List pending improvement proposals from data/improvement_proposals/."""
    try:
        from app.improvement_loop import improvement_loop

        proposals = improvement_loop.list_pending_proposals()
        return {
            "proposals": proposals,
            "count": len(proposals),
            "pending_count": len(proposals),
            "directory": str(improvement_loop.proposals_dir),
        }
    except Exception as exc:
        return {"error": str(exc), "proposals": [], "count": 0, "pending_count": 0}


@app.post("/api/improvement/proposals/apply")
async def apply_improvement_proposal(payload: dict):
    """
    Human-gated: record approval manifest only.
    Body: {"proposal_id": "...", "confirmed": true}
    """
    try:
        from app.improvement_loop import improvement_loop

        proposal_id = (payload or {}).get("proposal_id", "")
        confirmed = bool((payload or {}).get("confirmed"))
        if not proposal_id:
            return {"success": False, "error": "proposal_id_required"}
        return improvement_loop.apply_proposal_manifest(proposal_id, human_confirmed=confirmed)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/improvement/fill-learning")
async def get_improvement_fill_learning():
    """Latest fill learning snapshot (Kite fills vs cost model)."""
    try:
        from app.improvement_loop import improvement_loop

        snapshot = improvement_loop.get_latest_fill_learning()
        return {"snapshot": snapshot, "has_data": bool((snapshot.get("summary") or {}).get("fills_analyzed"))}
    except Exception as exc:
        return {"error": str(exc), "snapshot": None, "has_data": False}


@app.post("/api/agent/promoted-params/apply")
async def apply_promoted_params(payload: dict):
    """
    Human-gated: write promoted overlay for an index.
    Body: {"underlying": "NIFTY", "confirmed": true}
    """
    try:
        from app.promoted_params import apply_promoted_overlay

        underlying = (payload or {}).get("underlying", "NIFTY")
        confirmed = bool((payload or {}).get("confirmed"))
        return apply_promoted_overlay(underlying, human_confirmed=confirmed)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/fo/rules")
async def get_fo_rules():
    """Retail failure-pattern rules loaded from knowledge base JSON."""
    try:
        from app.fo_rules_engine import fo_rules_engine
        if fo_rules_engine is None:
            return {"rules": [], "error": "rules_file_missing"}
        return {
            "metadata": fo_rules_engine.metadata,
            "rules": fo_rules_engine.list_rules(),
        }
    except Exception as exc:
        return {"rules": [], "error": str(exc)}


def _build_options_algo_status_full() -> dict:
    """Full automated options status — file-backed regime context + optional Kite MTM."""
    return _get_options_algo_status(fast=False, include_market_context=True)


def _close_options_algo_structure(structure_id: str, *, reason: str = "manual_close") -> dict:
    """Paper-safe close routed through OptionsExecutionEngine."""
    from app.config_loader import get_options_config
    from app.options_execution_engine import options_execution_engine

    force_dry_run = True
    if risk_gatekeeper:
        force_dry_run = bool(risk_gatekeeper.config.force_dry_run)

    cfg = get_options_config()
    kite = _get_kite_client()
    return options_execution_engine.close_structure(
        kite,
        structure_id,
        reason=reason,
        force_dry_run=force_dry_run,
        product=cfg.get("product", "NRML"),
    )


@app.get("/api/options/algo/status")
async def options_algo_status(fast: bool = False):
    """Automated options structures (iron condor) — open positions, gates, last cycle."""
    if fast:
        return await asyncio.to_thread(
            lambda: _get_options_algo_status(fast=True, include_market_context=False)
        )
    return await asyncio.to_thread(_build_options_algo_status_full)


@app.post("/api/options/algo/close/{structure_id}")
async def options_algo_close(structure_id: str):
    """Manually flatten an open automated structure (respects FORCE_DRY_RUN / paper mode)."""
    result = await asyncio.to_thread(_close_options_algo_structure, structure_id)
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@app.get("/api/settings/trading")
async def get_trading_settings():
    """Portal-editable trading toggles (options/futures/sheet) — no restart required."""
    from app.trading_controls import get_trading_controls_status

    return await asyncio.to_thread(get_trading_controls_status)


@app.patch("/api/settings/trading")
async def patch_trading_settings(request: Request):
    """Update runtime trading controls; persists to data/trading_controls.json."""
    from app.trading_controls import update_trading_controls

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"success": False, "message": "JSON body required"}, status_code=400)
    result = await asyncio.to_thread(update_trading_controls, body, updated_by="settings_ui")
    if not result.get("success"):
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/settings/trading/reset")
async def reset_trading_settings():
    """Clear portal overrides — revert to .env + strategy_config.yaml."""
    from app.trading_controls import reset_trading_controls

    return await asyncio.to_thread(reset_trading_controls, updated_by="settings_ui")


@app.get("/api/options/desk/tickers")
async def get_options_desk_tickers():
    """Live ATM CE/PE tickers for NIFTY, BANKNIFTY, and SENSEX."""
    from app.options_desk_tickers import get_index_option_tickers

    kite = _get_kite_client()
    ws_feed = _get_engine_ws_feed()
    return await asyncio.to_thread(get_index_option_tickers, kite, ws_feed)


@app.get("/api/options/chain/{underlying}")
async def get_options_chain(underlying: str, expiry: Optional[str] = None, spot: Optional[float] = None):
    """Option chain snapshot for NIFTY / BANKNIFTY / SENSEX (Phase 0B infrastructure)."""
    key = underlying.upper()
    if key not in {"NIFTY", "BANKNIFTY", "SENSEX"}:
        return JSONResponse({"error": "unsupported underlying"}, status_code=400)

    kite = _get_kite_client()
    if kite is None:
        return {"error": "kite_unavailable", "error_code": "NO_KITE"}

    try:
        from app.options_chain import options_chain_manager
        options_chain_manager.bind(kite)
        exp = expiry if expiry else None
        resolved = options_chain_manager.resolve_expiry(key, exp)
        chain = options_chain_manager.fetch_and_cache_chain(kite, key, expiry=exp)
        atm = options_chain_manager.get_atm_strike(key, spot_price=spot)
        near = options_chain_manager.get_strikes_near_atm(key, n=5, spot_price=spot)
        return {
            "underlying": key,
            "expiry": str(resolved) if resolved else None,
            "atm_strike": atm,
            "strikes_near_atm": near,
            "row_count": len(chain) if chain is not None else 0,
            "columns": list(chain.columns) if chain is not None and hasattr(chain, "columns") else [],
        }
    except Exception as exc:
        return {"error": str(exc), "underlying": key}


# ==================== REAL FILLS + COST LEARNING (uses Kite /trades + /orders + our model) ====================

from app.fill_learning import analyze_fills


@app.get("/api/kite/real_fills_analysis")
async def real_fills_analysis(limit: int = 40):
    """
    Pulls real fills via Kite /trades, runs them through our cost model,
    returns table data + robust auto-generated learning notes for the GUI.
    This is how the system 'learns' from actual market interaction costs & timing.
    """
    limit = _clamp_limit(limit, default=40, max_limit=200)
    # Use the hardened helper for the primary call
    trades_res = _safe_kite_call(lambda k: k.trades(), "trades")
    if trades_res.get("error_code"):
        return {
            "error": trades_res.get("error"),
            "error_code": trades_res.get("error_code"),
            "documentation_notes": [f"Kite error during fills pull: {trades_res.get('error_code')}"],
            "fills": []
        }

    raw = trades_res.get("data") or []
    analysis = analyze_fills(raw, underlying="NIFTY", limit=limit)

    # Orders for context (best effort)
    orders_res = _safe_kite_call(lambda k: k.orders(), "orders")
    analysis["recent_orders_count"] = len(orders_res.get("data") or []) if not orders_res.get("error_code") else "unavailable (auth or network)"

    analysis["error_code"] = None
    return analysis


# ==================== RISK CONFIG (powers React Risk Guard page) ====================

@app.get("/api/risk/config")
async def get_risk_config():
    """Expose current risk gatekeeper limits without secrets."""
    if not risk_gatekeeper:
        return {"error": "Trading engine not loaded", "loaded": False}
    cfg = risk_gatekeeper.config
    lot_sizes = {"NIFTY": cfg.lot_size, "BANKNIFTY": 30, "SENSEX": 20}
    daily_pnl = risk_gatekeeper.daily_pnl
    daily_loss = risk_gatekeeper.daily_loss
    trades_today = risk_gatekeeper.trades_today
    drawdown_pct = risk_gatekeeper._current_drawdown_pct() * 100
    max_trades_per_symbol = cfg.max_trades_per_day
    trade_budget = {}

    try:
        from app.multi_symbol_risk import multi_risk_manager as mrm
        lot_sizes = mrm.get_lot_sizes()
        daily_pnl = mrm.daily_pnl
        daily_loss = mrm.daily_loss
        trades_today = mrm.trades_today
        drawdown_pct = mrm._current_drawdown_pct() * 100
        max_trades_per_symbol = mrm.max_trades_per_symbol_per_day
        if hasattr(mrm, "get_budget_summary"):
            trade_budget = mrm.get_budget_summary()
    except Exception:
        pass

    return {
        "loaded": True,
        "capital": risk_gatekeeper.capital,
        "max_daily_loss_pct": cfg.max_daily_loss_pct,
        "max_daily_loss_rs": round(risk_gatekeeper.capital * cfg.max_daily_loss_pct, 2),
        "max_drawdown_pct": cfg.max_drawdown_pct,
        "max_drawdown_rs": round(risk_gatekeeper.capital * cfg.max_drawdown_pct, 2),
        "risk_per_trade_pct": cfg.risk_per_trade_pct,
        "max_trades_per_day": cfg.max_trades_per_day,
        "max_trades_per_symbol": max_trades_per_symbol,
        "max_order_quantity": cfg.max_order_quantity,
        "lot_size": lot_sizes.get("NIFTY", cfg.lot_size),
        "lot_sizes": lot_sizes,
        "max_lots": cfg.max_lots,
        "force_dry_run": cfg.force_dry_run,
        "daily_pnl": round(daily_pnl, 2),
        "daily_loss": round(daily_loss, 2),
        "trades_today": trades_today,
        "current_drawdown_pct": round(drawdown_pct, 2),
        "state": state_machine.get_state().value if state_machine else "UNKNOWN",
        "trading_allowed": state_machine.is_trading_allowed() if state_machine else False,
        "trade_budget": trade_budget,
    }


@app.post("/api/emergency/halt")
async def emergency_halt_endpoint():
    """Kill switch — halt engine and square off all paper positions."""
    from app.emergency import execute_emergency_halt
    return execute_emergency_halt("Kill switch triggered from UI/API")


# ==================== KITE CONNECTION STATUS (powers React Settings sidebar) ====================

@app.post("/api/kite/login/start")
async def kite_login_start():
    """
    Start browser-based Kite login with automatic request_token capture.
    User still completes Zerodha credentials + 2FA in browser once.
    Poll /api/kite/login/status until success or error.
    """
    from app.kite_auth import start_auto_login_async, get_redirect_url

    status = start_auto_login_async(open_browser=True)
    status["redirect_url_required"] = get_redirect_url()
    status["setup_note"] = (
        "Set this Redirect URL in your Kite developer console (one-time): "
        + get_redirect_url()
    )
    return status


@app.get("/api/kite/login/status")
async def kite_login_status():
    """Poll login progress after POST /api/kite/login/start."""
    from app.kite_auth import get_login_status, get_redirect_url
    status = get_login_status()
    status["redirect_url_required"] = get_redirect_url()
    return status


def _build_kite_connection_status(*, network: bool = True) -> dict:
    """Kite profile probe — cached to avoid hammering Kite on sidebar polls."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    api_key = os.getenv("KITE_API_KEY", "") or ""
    access_token = os.getenv("KITE_ACCESS_TOKEN", "") or ""
    has_secret = bool(os.getenv("KITE_API_SECRET", ""))
    token_key = f"{api_key[:6]}:{access_token[:12]}"

    now = time.time()
    cached = _kite_status_cache.get("payload")
    if (
        cached
        and _kite_status_cache.get("token_key") == token_key
        and now - float(_kite_status_cache.get("ts") or 0) < KITE_STATUS_CACHE_SEC
    ):
        out = dict(cached)
        out["timestamp"] = datetime.now().isoformat()
        out["cached"] = True
        return out

    status = {
        "api_key_configured": bool(api_key),
        "api_key_preview": f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else None,
        "api_secret_configured": has_secret,
        "access_token_configured": bool(access_token),
        "connected": False,
        "latency_ms": None,
        "error": None,
        "error_code": None,
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }

    if not api_key or not access_token:
        status["error"] = "Missing KITE_API_KEY or KITE_ACCESS_TOKEN"
        status["error_code"] = "KITE_CREDENTIALS_MISSING"
        return status

    try:
        from app.token_manager import get_token_manager
        mgr = get_token_manager()
        if mgr and not network:
            status["connected"] = bool(mgr.token_valid)
            status["needs_relogin"] = bool(mgr.needs_relogin)
            if not mgr.token_valid:
                status["error"] = "Kite session invalid — run generate_token.py or Settings → Auto Login"
                status["error_code"] = "KITE_TOKEN_EXPIRED" if mgr.needs_relogin else "KITE_CREDENTIALS_MISSING"
            return status
    except Exception:
        pass

    if _get_kite_client() is None:
        status["error"] = "Missing KITE_API_KEY or KITE_ACCESS_TOKEN"
        status["error_code"] = "KITE_CREDENTIALS_MISSING"
        return status

    start = time.time()
    res = _safe_kite_call(lambda k: k.profile(), "profile")
    status["latency_ms"] = round((time.time() - start) * 1000, 1)

    if res.get("error_code"):
        status["error"] = res.get("error")
        status["error_code"] = res.get("error_code")
        status["needs_relogin"] = res.get("error_code") == "KITE_TOKEN_EXPIRED"
        _kite_status_cache.update(payload=dict(status), ts=now, token_key=token_key)
        return status

    profile = res.get("data") or {}
    status["connected"] = True
    status["user_id"] = profile.get("user_id")
    status["user_name"] = profile.get("user_name")
    status["broker"] = profile.get("broker")
    try:
        from app.kite_connect_rules import session_guidance, faq_checklist
        status["session_guidance"] = session_guidance()
        status["faq_checklist"] = faq_checklist()
    except Exception:
        pass
    _kite_status_cache.update(payload=dict(status), ts=now, token_key=token_key)
    _token_valid_cache["valid"] = True
    _token_valid_cache["ts"] = now
    return status


@app.get("/api/kite/status")
async def kite_connection_status(quick: bool = False):
    """Kite connectivity for React UI — cached profile probe (45s default)."""
    if quick:
        return await asyncio.to_thread(_build_kite_connection_status, network=False)
    return await asyncio.to_thread(_build_kite_connection_status)


# ==================== OPS HUB (scripts/algo_lab_ops.py parity) ====================

def _clamp_audit_days(days: int, *, default: int = 1, max_days: int = 14) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, max_days))


@app.get("/api/ops/preflight")
async def get_ops_preflight(days: int = 1, skip_token: bool = False):
    """Morning readiness: status + compliance + data-health + WFO (ops_hub.run_preflight)."""
    try:
        from app.ops_hub import run_preflight

        return await asyncio.to_thread(
            run_preflight,
            validate_token=not skip_token,
            audit_days=_clamp_audit_days(days),
        )
    except Exception as exc:
        return {
            "ready": False,
            "error": str(exc),
            "blockers": [str(exc)],
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
        }


@app.get("/api/ops/status")
async def get_ops_status(skip_token: bool = False):
    """System health snapshot (ops_hub.build_status_report)."""
    try:
        from app.ops_hub import build_status_report

        report = await asyncio.to_thread(build_status_report, validate_token=not skip_token)
        report["timestamp"] = datetime.now().isoformat()
        return report
    except Exception as exc:
        return {
            "healthy": False,
            "error": str(exc),
            "blockers": [str(exc)],
            "warnings": [],
            "timestamp": datetime.now().isoformat(),
        }


@app.get("/api/ops/compliance")
async def get_ops_compliance():
    """Code-checkable COMPLIANCE.md items (ops_hub.run_compliance_checks)."""
    try:
        from app.ops_hub import run_compliance_checks

        report = await asyncio.to_thread(run_compliance_checks)
        report["timestamp"] = datetime.now().isoformat()
        return report
    except Exception as exc:
        return {
            "passed": False,
            "error": str(exc),
            "automated_passed": 0,
            "automated_total": 0,
            "checks": [],
            "timestamp": datetime.now().isoformat(),
        }


# Health check + system diagnostics (extremely useful when GUI is "deployed" and things feel broken)
@app.get("/health")
async def health():
    # Keep this path free of thread-pool work, Kite, or ledger I/O.
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "engine_ready": _engine_ready(),
        "singletons_loaded": _engine_ready(),
    }


@app.get("/api/system/info")
async def system_info():
    """Deployment diagnostics — helps senior devs debug runtime issues fast."""
    info = {
        "version": "0.5.1-gui-hardened",
        "market": get_market_status(),
        "singletons_loaded": bool(risk_gatekeeper and state_machine),
        "memory_runs": len(backtest_memory.get_all_runs(limit=5)) if hasattr(backtest_memory, 'get_all_runs') else "n/a",
        "timestamp": datetime.now().isoformat(),
    }
    return info


# React Aegis UI (production build) — single-port at :8050/ui/
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    _ui_assets = FRONTEND_DIST / "assets"
    if _ui_assets.exists():
        app.mount("/ui/assets", StaticFiles(directory=str(_ui_assets)), name="ui-assets")

    @app.get("/ui")
    @app.get("/ui/{full_path:path}")
    async def serve_react_ui(full_path: str = ""):
        if full_path and "." in full_path.split("/")[-1]:
            candidate = FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8050, log_level="info")