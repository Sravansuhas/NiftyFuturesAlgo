"""
Bloomberg Terminal Style Dashboard for NiftyFuturesAlgo

Run alongside your trading process:
    uvicorn web.dashboard:app --host 0.0.0.0 --port 8050 --reload

Features:
- Real-time updating metrics via SSE
- Clean, dark, professional terminal aesthetic
- Trades, Risk, Diagnostics, System Health
- Designed for long-running sessions (hours to full day)
"""

from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import pandas as pd  # ensure pd is available in backtest jobs

# Backtesting imports
from backtesting.walk_forward_runner import run_walk_forward
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.costs import TransactionCostModel, CostConfig
from backtesting.data_loader import fetch_real_nifty_futures_data, list_available_cached_datasets
from backtesting.backtest_memory import backtest_memory
from kiteconnect import KiteConnect
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

app = FastAPI(title="NiftyFuturesAlgo Terminal", version="0.4.0")

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

# In-memory storage for background backtest jobs (lightweight, no DB)
BACKTEST_JOBS: Dict[str, Dict[str, Any]] = {}
MAX_JOBS = 8

# Cancellation support for long-running jobs
CANCEL_REQUESTED: Dict[str, bool] = {}

def _cleanup_jobs():
    if len(BACKTEST_JOBS) > MAX_JOBS:
        sorted_jobs = sorted(BACKTEST_JOBS.items(), key=lambda x: x[1].get("started_at", 0))
        for jid, _ in sorted_jobs[:len(sorted_jobs) - MAX_JOBS]:
            BACKTEST_JOBS.pop(jid, None)


def _safe_kite_call(callable_fn, operation_name: str = "kite_api"):
    """
    Senior finance dev pattern: Every external broker call is wrapped.
    Always returns structured result + explicit error_code. Never lets the GUI blow up.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("KITE_API_KEY")
        access_token = os.getenv("KITE_ACCESS_TOKEN")
        if not api_key or not access_token:
            return {"error": "Missing KITE_API_KEY or KITE_ACCESS_TOKEN in environment", "error_code": "KITE_CREDENTIALS_MISSING"}

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        result = callable_fn(kite)
        return {"data": result, "error": None, "error_code": None}
    except Exception as e:
        return {"data": None, "error": str(e), "error_code": f"KITE_{operation_name.upper()}_FAILED"}

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "title": "NIFTY FUTURES ALGO — TERMINAL"
    })

# === Real-time Status Endpoint (JSON) ===
@app.get("/api/status")
async def get_status():
    """Current system state + equity history + last action for charting"""
    if not risk_gatekeeper or not state_machine:
        return {
            "error": "Trading engine not loaded",
            "timestamp": datetime.now().isoformat()
        }

    current_equity = risk_gatekeeper.capital + risk_gatekeeper.daily_pnl

    # Maintain equity history for the chart
    now = time.time()
    EQUITY_HISTORY.append({"ts": now, "equity": current_equity})
    if len(EQUITY_HISTORY) > MAX_HISTORY_POINTS:
        EQUITY_HISTORY.pop(0)
    _save_equity_history()

    # Get the most recent meaningful action from the ledger
    last_action = "No recent activity"
    active_symbol = None
    last_ltp = None
    last_regime = None
    try:
        from app.trade_ledger import trade_ledger
        recent = trade_ledger.tail(10)
        if recent:
            latest = recent[-1]
            etype = latest.get("event_type", "")
            payload = latest.get("payload", {})
            if etype == "signal.accepted":
                last_action = f"Signal Accepted: {payload.get('side')} @ {payload.get('price')}"
            elif etype == "order.placed":
                last_action = f"Order Placed: {payload.get('side')} {payload.get('quantity')}"
            elif etype == "order.exit":
                last_action = "Exit Order Submitted"
            elif etype == "signal.rejected":
                reason = payload.get("reason", "unknown")
                last_action = f"Signal Rejected: {reason}"
            # Capture any symbol/ltp/regime we logged
            if "symbol" in payload:
                active_symbol = payload.get("symbol")
            if "price" in payload and isinstance(payload.get("price"), (int, float)):
                last_ltp = payload.get("price")
            if "regime" in payload:
                last_regime = payload.get("regime")
    except:
        pass

    # Fallback: pull current position symbol from risk_gatekeeper if available
    if not active_symbol and risk_gatekeeper and risk_gatekeeper.position:
        active_symbol = risk_gatekeeper.position.get("symbol")

    # Build a tiny recent execution log (accepted + rejected) for the live GUI
    recent_exec = []
    try:
        from app.trade_ledger import trade_ledger
        events = trade_ledger.tail(15)
        for e in reversed(events[-8:]):
            et = e.get("event_type", "")
            p = e.get("payload", {})
            if et in ("signal.accepted", "signal.rejected", "order.placed", "order.exit"):
                recent_exec.append({
                    "ts": e.get("ts"),
                    "type": et,
                    "side": p.get("side"),
                    "price": p.get("price"),
                    "reason": p.get("reason") or p.get("filter") or p.get("message"),
                    "regime": p.get("regime"),
                    "qty": p.get("quantity"),
                })
    except:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "mode": "PAPER" if risk_gatekeeper.config.force_dry_run else "LIVE",
        "state": state_machine.get_state().value,
        "capital": risk_gatekeeper.capital,
        "daily_pnl": round(risk_gatekeeper.daily_pnl, 2),
        "daily_loss": round(risk_gatekeeper.daily_loss, 2),
        "current_equity": round(current_equity, 2),
        "position": {
            "quantity": risk_gatekeeper.get_position_quantity(),
            "symbol": risk_gatekeeper.position.get("symbol"),
            "avg_price": risk_gatekeeper.position.get("avg_price", 0)
        },
        "trades_today": risk_gatekeeper.trades_today,
        "max_drawdown": round(risk_gatekeeper._current_drawdown_pct() * 100, 2),
        "token_valid": True,
        "equity_history": EQUITY_HISTORY[-100:],
        "last_action": last_action,
        "active_symbol": active_symbol,
        "last_ltp": last_ltp,
        "last_regime": last_regime,
        "recent_execution": recent_exec,
        "vol_regime": last_regime or "normal",
        "risk_mult": 1.0,
        "market": get_market_status(),
    }


# === Dedicated Market Status (used by both terminals for the persistent banner) ===
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


# === Recent Trades (from improved Trade Ledger) ===
@app.get("/api/trades")
async def get_trades(limit: int = 50):
    """Read from the improved trade ledger (much richer than raw audit)"""
    try:
        from app.trade_ledger import trade_ledger
        events = trade_ledger.tail(limit * 2)  # Get more to filter

        # Filter for relevant events (include rejections so the GUI can show why no trades happened)
        relevant = [
            e for e in events 
            if e.get("event_type") in ["signal.accepted", "signal.rejected", "order.placed", "order.exit", "order.dry_run"]
        ]

        return {"trades": list(reversed(relevant[-limit:]))}
    except Exception as e:
        return {"error": str(e), "trades": []}

# === Live Diagnostics Stream (SSE) ===
@app.get("/api/stream")
async def status_stream():
    """Server-Sent Events for real-time updates"""
    async def event_generator():
        while True:
            try:
                status = await get_status()
                yield f"data: {json.dumps(status)}\n\n"
                await asyncio.sleep(3)  # Update every 3 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

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
    use_real_data: bool = Form(False),
    force_refresh: bool = Form(False),
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
        },
        "result": None,
        "error": None,
        "error_code": None,
    }
    _cleanup_jobs()

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
            if effective_use_real_data:
                BACKTEST_JOBS[job_id]["stage"] = "fetching_real_data"
                print(f"[BACKTEST JOB] Real data requested (months={months}, force_refresh={force_refresh}). Smart cache will be used if available.")
                def _do_fetch(kite):
                    to_dt = datetime.now()
                    from_dt = to_dt - timedelta(days=30 * months)
                    return fetch_real_nifty_futures_data(
                        kite, from_dt, to_dt, "5minute",
                        use_cache=not force_refresh,
                        force_refresh=force_refresh
                    )
                res = _safe_kite_call(_do_fetch, "historical_multi_contract")
                if not res.get("error_code"):
                    print("[BACKTEST JOB] Real data fetch completed. See [CACHE] / [DATA] logs for exact cache hit details.")
                if res.get("error_code"):
                    BACKTEST_JOBS[job_id]["error"] = res["error"]
                    BACKTEST_JOBS[job_id]["error_code"] = res["error_code"]
                else:
                    data = res.get("data")
                    BACKTEST_JOBS[job_id]["progress"] = 35

            if data is None or len(data) < 2000:
                BACKTEST_JOBS[job_id]["stage"] = "generating_synthetic"
                import numpy as np
                np.random.seed(42)
                n_bars = 8000
                prices = [24000.0]
                current_vol = 5.0
                trend = 0.0
                regime = "normal"
                vol_regime = "normal"

                for i in range(n_bars - 1):
                    # Regime switching with volatility clustering (much more realistic for Nifty)
                    if i % 450 == 0:
                        regime = np.random.choice(["low", "normal", "high"], p=[0.20, 0.55, 0.25])
                        current_vol = {"low": 2.5, "normal": 5.2, "high": 11.0}[regime]

                    # Occasional volatility bursts (what creates tradable breakouts)
                    if np.random.rand() < 0.018:
                        vol_regime = "burst"
                        current_vol *= 1.8
                    elif vol_regime == "burst" and np.random.rand() < 0.35:
                        vol_regime = "normal"

                    if i % 950 == 0:
                        trend = np.random.uniform(-1.8, 1.8)

                    # Momentum + mean reversion (real 5-min behavior)
                    prev_ret = (prices[-1] - prices[-2]) if len(prices) > 1 else 0
                    momentum = prev_ret * np.random.uniform(0.08, 0.22)

                    ret = np.random.normal(trend + momentum, current_vol)
                    new_price = prices[-1] + ret
                    prices.append(new_price)

                prices = np.array(prices)
                noise = np.random.normal(0, 2.1, n_bars)

                # Generate previous-candle-range friendly OHLC + realistic volume spikes
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
            )

            # Attach research mode flag + hard Statistical Power Warning (final professional polish)
            if isinstance(result, dict):
                result["research_mode_used"] = effective_research_mode

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
        except Exception as e:
            BACKTEST_JOBS[job_id]["status"] = "failed"
            BACKTEST_JOBS[job_id]["error"] = str(e)
            BACKTEST_JOBS[job_id]["error_code"] = "BACKTEST_FAILED"

    background_tasks.add_task(run_backtest_job)
    return {"job_id": job_id, "status": "started"}


@app.get("/api/backtest/result/{job_id}")
async def get_backtest_result(job_id: str):
    job = BACKTEST_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


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
    """Interactive backtest + data fetch control panel. Super clean Algo Lab with full learning + Kite integration."""
    return templates.TemplateResponse("backtest.html", {"request": request})


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

    def fetch_job():
        BACKTEST_JOBS[job_id]["progress"] = 10
        BACKTEST_JOBS[job_id]["stage"] = "preparing"

        def _check_data_cancel():
            if CANCEL_REQUESTED.get(job_id):
                print(f"[DATA JOB] Cancel detected for {job_id}")
                BACKTEST_JOBS[job_id]["status"] = "cancelled"
                BACKTEST_JOBS[job_id]["stage"] = "cancelled"
                raise SystemExit("Data job cancelled")

        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=30 * months)

            _check_data_cancel()

            if use_local_only and not force_refresh:
                from backtesting.data_loader import _load_overlapping_cached_data
                print(f"[DATA JOB] Attempting FULL local cache load only (months={months})")
                data = _load_overlapping_cached_data(from_dt, to_dt, "5minute")
                if data is None or len(data) < 500:
                    raise ValueError("No sufficient overlapping data found in local cache (data/historical_cache).")
                print(f"[DATA JOB] ✅ FULL CACHE HIT - {len(data)} rows loaded from local overlapping cache. No Kite call made.")
                BACKTEST_JOBS[job_id] = {
                    "status": "completed",
                    "type": "data_fetch",
                    "rows": len(data),
                    "from": str(from_dt.date()),
                    "to": str(to_dt.date()),
                    "source": "local_cache_only",
                    "progress": 100,
                    "stage": "done",
                    "force_refresh": False,
                    "cache_hit": "full"
                }
            else:
                # Smart path (default) or force: auto prefers local unless force_refresh
                load_dotenv()
                kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
                kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

                print(f"[DATA JOB] Smart data load starting (months={months}, force_refresh={force_refresh})")
                data = fetch_real_nifty_futures_data(
                    kite, from_dt, to_dt, "5minute",
                    use_cache=not force_refresh,
                    force_refresh=force_refresh
                )

                # The data_loader already prints detailed cache decisions.
                # We add a summary here for the dashboard job.
                print(f"[DATA JOB] Data load complete via smart path → {len(data)} rows. Check [CACHE]/[DATA] logs above for hit/miss details.")
                BACKTEST_JOBS[job_id] = {
                    "status": "completed",
                    "type": "data_fetch",
                    "rows": len(data),
                    "from": str(from_dt.date()),
                    "to": str(to_dt.date()),
                    "source": "force_kite" if force_refresh else "smart_cache_or_kite",
                    "progress": 100,
                    "stage": "done",
                    "force_refresh": force_refresh,
                    "cache_hit": "force" if force_refresh else "smart_or_partial"
                }
        except SystemExit as se:
            if "cancel" in str(se).lower():
                BACKTEST_JOBS[job_id] = {"status": "cancelled", "stage": "cancelled", "progress": 100}
            else:
                BACKTEST_JOBS[job_id] = {"status": "failed", "error": str(se), "progress": 100, "stage": "failed"}
        except Exception as e:
            BACKTEST_JOBS[job_id] = {"status": "failed", "error": str(e), "progress": 100, "stage": "failed"}

    BACKTEST_JOBS[job_id] = {"status": "running", "type": "data_fetch"}
    background_tasks.add_task(fetch_job)
    return {"job_id": job_id}


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
        payload = await request.json()
        received_checksum = payload.get("checksum")

        # Validate checksum (critical for security)
        order_id = payload.get("order_id")
        order_timestamp = payload.get("order_timestamp")
        api_secret = os.getenv("KITE_API_SECRET")

        if not all([order_id, order_timestamp, api_secret, received_checksum]):
            return {"status": "invalid"}

        import hashlib
        computed = hashlib.sha256(f"{order_id}{order_timestamp}{api_secret}".encode()).hexdigest()

        if computed != received_checksum:
            print("[POSTBACK] Checksum mismatch — possible tampering")
            return {"status": "invalid_checksum"}

        # Process the update
        print(f"[POSTBACK] Order update received: {payload.get('status')} for {order_id}")

        # You can update your internal state, send alerts, etc. here
        # For now we just log it
        from app.trade_ledger import trade_ledger
        trade_ledger.record("kite.postback", payload)

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


# ==================== REAL FILLS + COST LEARNING (uses Kite /trades + /orders + our model) ====================

def _analyze_real_fills_for_learning(raw_trades: List[Dict], limit: int = 50) -> Dict[str, Any]:
    """
    Takes raw response from kite.trades(), filters Nifty futures, estimates our
    TransactionCostModel costs on the observed fills, produces robust learning notes
    and calibration data for the 'repetitive learning' system.
    This closes the loop between backtest assumptions and reality.
    """
    from backtesting.costs import TransactionCostModel, CostConfig
    import statistics as stats

    cost_model = TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0,
    ))

    nifty_trades = []
    for t in raw_trades:
        sym = str(t.get("tradingsymbol", "")).upper()
        if "NIFTY" in sym and "FUT" in sym:
            nifty_trades.append(t)

    nifty_trades = nifty_trades[:limit]

    analyzed = []
    total_est_cost = 0.0
    hours = []
    for t in nifty_trades:
        try:
            qty = int(t.get("quantity", 0) or 0)
            price = float(t.get("average_price") or t.get("price") or 0)
            ts = t.get("order_timestamp") or t.get("fill_timestamp") or t.get("exchange_timestamp")
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hours.append(dt.hour + dt.minute / 60.0)
                except Exception:
                    pass

            # Estimate round-turn cost using our model (conservative: treat as one leg or full?)
            # For learning we estimate a one-way impact + assume round-turn ~2x one leg for comparison.
            est_round_cost = cost_model.round_turn_cost_per_lot(slippage_points=None) * max(1, qty // 75)
            total_est_cost += est_round_cost

            analyzed.append({
                "symbol": t.get("tradingsymbol"),
                "qty": qty,
                "price": round(price, 2),
                "ts": str(ts),
                "type": t.get("transaction_type"),
                "est_cost_round_turn_rs": round(est_round_cost, 2),
            })
        except Exception:
            continue

    # Time-of-day clustering insight
    hour_note = ""
    if hours:
        avg_h = stats.mean(hours)
        if 9.25 <= avg_h <= 10.0 or avg_h >= 15.0:
            hour_note = "Many fills occurred near session edges (toxic window per strategy rules)."
        else:
            hour_note = f"Avg fill hour ~{avg_h:.1f} IST — inside our 10:00-15:00 preferred window."

    # Generate learning documentation (robust, sample-aware)
    notes = []
    n = len(analyzed)
    if n == 0:
        notes.append("No Nifty futures fills found in recent /trades. Paper trading or different symbols?")
    else:
        notes.append(f"Analyzed {n} Nifty futures fills via Kite /trades API.")
        notes.append(f"Model-estimated total round-turn costs on these fills: ~₹{total_est_cost:,.0f} (brokerage+STT+slippage buffer).")
        notes.append("Note: Kite /trades response does not include itemized brokerage/STT per fill for all accounts. Use this + your broker P&L statement for true tax reconciliation.")
        if hour_note:
            notes.append(hour_note)
        if n >= 8:
            notes.append("[LEARNING] Sufficient real fills for cost calibration. Compare model vs actual STT+GST line items. If real costs 15%+ higher in high-vol, increase default_slippage in CostConfig for future backtests.")
        else:
            notes.append("[LEARNING] Small sample — run more paper trades then re-analyze to build confidence in cost model.")

    summary = {
        "nifty_fills_analyzed": n,
        "est_total_cost_rs": round(total_est_cost, 2),
        "avg_est_cost_per_fill": round(total_est_cost / max(1, n), 2),
    }

    return {
        "fills": analyzed,
        "summary": summary,
        "documentation_notes": notes,
        "source": "kite.trades() + backtest cost model v1",
    }


@app.get("/api/kite/real_fills_analysis")
async def real_fills_analysis(limit: int = 40):
    """
    Pulls real fills via Kite /trades, runs them through our cost model,
    returns table data + robust auto-generated learning notes for the GUI.
    This is how the system 'learns' from actual market interaction costs & timing.
    """
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
    analysis = _analyze_real_fills_for_learning(raw, limit=limit)

    # Orders for context (best effort)
    orders_res = _safe_kite_call(lambda k: k.orders(), "orders")
    analysis["recent_orders_count"] = len(orders_res.get("data") or []) if not orders_res.get("error_code") else "unavailable (auth or network)"

    analysis["error_code"] = None
    return analysis


# Health check + system diagnostics (extremely useful when GUI is "deployed" and things feel broken)
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8050, log_level="info")