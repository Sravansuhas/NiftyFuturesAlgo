# Aegis — Implementation Log

Chronological record of shipped changes. Use this to remember what was built, why, and how to verify.

**Maintained by:** Agent + founder review  
**Format:** Phase → date → files → verification command

---

## Phase 0A — Bulletproof 3-Index Futures Paper (2026-06-11)

### Per-symbol state persistence
- **Files:** `app/state_persistence.py`, `app/strategy.py`, `app/main.py`
- **Behavior:** `data/state/{NIFTY,BANKNIFTY,SENSEX}.json`; broker recon runs before `restore_from_persistence()`
- **Verify:** `python -m unittest tests.test_state_persistence tests.test_restart_recovery -v`

### Paper P&L fix
- **Files:** `app/strategy.py`, `app/multi_symbol_risk.py`
- **Behavior:** Entry/exit orders pass `price=self._last_known_price`; realized P&L computed when `avg_price > 0`
- **Verify:** `python -m unittest tests.test_multi_symbol_risk.MultiSymbolRiskTests.test_paper_exit_records_pnl -v`

### WebSocket feed (Kite threaded pattern)
- **Files:** `app/data_feed.py`, `app/main.py`, `app/strategy.py`
- **Reference:** [pykiteconnect threaded_ticker.py](https://github.com/zerodha/pykiteconnect/blob/master/examples/threaded_ticker.py)
- **Behavior:** `ENABLE_WEBSOCKET=true` (default); WS LTP preferred; REST fallback if stale >15s
- **Verify:** Run `python run.py --ensure-token` during market hours; check logs for `[WS]` and `data_source: WS`

### Recon mismatch → halt (live only)
- **Files:** `app/broker_reconciliation.py`, `app/multi_symbol_risk.py`
- **Behavior:** `detect_broker_mismatches()` → `EMERGENCY_HALT` when not paper mode
- **Verify:** Unit logic in `multi_risk_manager.detect_broker_mismatches` (paper returns `[]`)

### Retail failure-pattern rules (runtime)
- **Files:** `data/knowledge_base/indian_fo_rules.json`, `app/fo_rules_engine.py`, `app/multi_symbol_risk.py`, `app/strategy.py`
- **Sources:** r/IndiaAlgoTrading (slippage, paper/live divergence), SEBI FY25, Zerodha Varsity, Groww revenge-trading guides
- **Rules:** 10 encodable rules (5 tier-1 blocks, 5 tier-2 de-risk)
- **Verify:** `python -m unittest tests.test_fo_rules_engine -v`  
- **API:** `GET /api/fo/rules`

---

## Phase 0B — Options Infrastructure (2026-06-11)

### Option chain + risk (no live orders)
- **Files:** `app/options_chain.py`, `app/options_risk.py`, `app/instruments_manager.py`
- **Behavior:** Chain cache `data/options_chain/{SYMBOL}_{expiry}.parquet`; defined-risk-only validation
- **API:** `GET /api/options/chain/{underlying}?spot=25100`

---

## Phase 1 — Institutional Validation Engine (2026-06-11)

### Rolling purged walk-forward optimization
- **Files:** `backtesting/wfo_splits.py`, `backtesting/walk_forward_runner.py`
- **Math:**
  - Anchored train from bar 0; OOS test windows in last 40% of series
  - Embargo gap ≥ 78 bars (≈1 session) between train end and test start — no label leakage
  - Train objective: **Calmar** = `return% / max(DD%, 0.1)` (default); alt: `sharpe_penalized`
  - Parameter stability: CV < 0.25 across folds flagged stable
- **Verify:** `python -m unittest tests.test_wfo_splits -v`

### Conservative intrabar exits
- **Files:** `app/breakout_core.py`, `backtesting/backtester.py`
- **Logic:** Stop-first on bar high/low (long: low hits stop before high hits target)
- **Also:** `entry_on_next_bar` fills at next bar **open** (not close)
- **Verify:** `python -m unittest tests.test_backtest_engine.TestIntrabarExits -v` (if class exists) or full backtest engine tests

### Promotion gates (deployment oracle)
- **Files:** `backtesting/promotion_gates.py`, `data/strategy_candidates.json` (written on WFA complete)
- **Gates (default):** OOS PF ≥ 1.2, DD ≤ 8%, ≥5 trades/fold, ≥2 folds pass, MC 5th pct return > 0
- **API:** `GET /api/backtest/candidates`
- **Verify:** `python -m unittest tests.test_promotion_gates -v`

### Multi-index WFA data
- **Files:** `backtesting/data_loader.py`, `web/dashboard.py`
- **Behavior:** `fetch_real_index_futures_data(underlying=NIFTY|BANKNIFTY|SENSEX)`; backtest form accepts `underlying` + `wfo_objective`
- **Cache paths:** `data/historical/{underlying}_futures_{from}_{to}_5minute.parquet`

---

## Phase 2 — Agentic Intelligence & Closed Loop (2026-06-11)

### Intelligence loop (core brain)
- **Files:** `app/intelligence_loop.py`, `app/strategy.py` (`get_risk_multiplier`)
- **Rules:**
  - Learning layer **never increases** risk (multiplier capped at 1.0)
  - Unvalidated params (no promotion): 0.85×
  - Negative regime memory (medium+ confidence): 0.70×
  - Composes with existing vol/trend regime multipliers
- **Verify:** `python -m unittest tests.test_intelligence_loop -v`

### Skills + CLI scripts
| Skill | Script | Output |
|-------|--------|--------|
| fo-market-brief | `scripts/fo_market_brief.py` | `data/briefs/{date}.json` |
| fo-safe-deploy | `scripts/fo_safe_deploy.py` | stdout checklist |
| fo-failure-pattern-miner | `scripts/fo_failure_pattern_miner.py` | `data/knowledge_base/proposals/` |

### Dashboard APIs
- `POST /api/agent/brief/generate`
- `GET /api/agent/brief/latest`
- `GET /api/agent/safe-deploy`

### Closed loop path
```
WFA → promotion_gates → strategy_candidates.json
                ↓
intelligence_loop.get_learning_risk_multiplier()
                ↓
strategy.get_risk_multiplier() → order sizing
```

---

## Test suite snapshot

```powershell
python -m unittest discover -s tests -v
```

| Suite | Purpose |
|-------|---------|
| `test_wfo_splits` | Purged rolling fold geometry |
| `test_promotion_gates` | OOS promotion math |
| `test_backtest_engine` | Intrabar exits + parity |
| `test_fo_rules_engine` | Failure-pattern gates |
| `test_multi_symbol_risk` | Paper P&L + FO rules |

---

## What NOT to do (scope discipline)

- Do not enable live options until Phase 4 gates pass
- Do not bypass `RiskGatekeeper` / `fo_rules_engine` for entries
- Do not treat synthetic WFA results as deployment-ready

---

## Phase 5 — Options-Only Runtime & API Performance (2026-06-17)

Audit and fixes after Options Sheet tab showed "Cannot reach API on port 8050", stale yesterday ledger/events, futures terminal noise with `FUTURES_TRADING_ENABLED=false`, and UI lag.

### Root causes (confirmed)

| Symptom | Root cause |
|---------|------------|
| "Cannot reach API on port 8050" on Options Sheet | Global `/health` poll fails when the single uvicorn worker event loop is blocked by synchronous Kite calls in `external-signals` handlers — not a dead server |
| Algo events / ledger show yesterday | Dashboard used `trade_ledger.tail()` (last N lines, any day); JSONL persists across days |
| `[3-INDEX PAPER]` FUT logs with futures paused | `FUTURES_TRADING_ENABLED=false` only gates `strat.run_once()` — monitoring stack (strategies, WS, snapshots, terminal) still ran |
| UI lag | Request storm on Options Sheet + blocking handlers on asyncio loop |

### Backend changes

**`app/main.py` — options-only mode**
- When `futures_trading_enabled()` is false: skip 3-index strategy init, futures WS subscriptions, per-loop `get_signal_snapshot()`, `[3-INDEX PAPER]` terminal block, and EOD MIS flatten
- Options legs engine + iron condor cycle unchanged; terminal shows `[OPTIONS-ONLY]` + `[6-LEG OPTIONS]` / `[OPTIONS ALGO]` only

**`app/trade_ledger.py`**
- `read_events_today()` — newest-first events for current IST day
- `_event_date_ist()` — legacy rows without `date_ist` fall back to `ts` in IST

**`web/dashboard.py`**
- `_ledger_events_today()` helper; all live dashboard reads (`recent_execution`, SSE, `/api/status`) filter to today IST
- `GET /api/trades?date=` — defaults to today IST (was unfiltered `tail()`)
- External-signals handlers (`get`, `premiums`, `evaluate`, `save`) run in `asyncio.to_thread` so `/health` and SSE stay responsive
- Ops endpoints (`/api/ops/preflight`, `status`, `compliance`) also use `to_thread`
- SSE payload now includes `options_legs` from in-memory engine (fast path)

### Frontend changes

**`frontend/src/api/client.ts`**
- `getExternalSignals(date, { withPnl: false })` — fast sheet load without blocking Kite PnL
- `getTrades` / `getTradesCached` — include `date=todayIst()` in URL and cache key

**`frontend/src/pages/ExternalSignals.tsx`**
- Two-phase load: sheet first (no PnL), premiums in background
- `OptionsLegsPanel` receives SSE `stream.options_legs` via outlet context
- Auto-evaluate interval 30s → 60s

**`frontend/src/components/OptionsAlgoPanel.tsx`**
- Client-side today filter on algo events and ledger (defense in depth)

**`frontend/src/components/EngineBanner.tsx`**
- Clearer message when already on `:8050/ui` (overload vs not running)

**`frontend/src/components/OptionsLegsPanel.tsx`**
- Poll interval 60s when SSE legs are available (was 15s always)

### Verify

```powershell
# Restart engine (rebuilds UI if needed)
python run.py --dev

# Open built UI (recommended — same origin as API)
start http://127.0.0.1:8050/ui/options-sheet

# Health must return quickly even while sheet loads
curl http://127.0.0.1:8050/health

# Trades default to today IST only
curl "http://127.0.0.1:8050/api/trades?limit=10"

python -m pytest tests/test_dashboard_options_events.py -q
```

### Expected terminal with futures off

```
[MAIN] === FUTURES TRADING PAUSED (FUTURES_TRADING_ENABLED=false) ===
[MAIN] Futures strategies skipped — options-only mode (spot + option legs)
...
[OPTIONS-ONLY] 09:51:24 | futures paused — options desk active
[6-LEG OPTIONS] 09:51:25
```

No `[3-INDEX PAPER]` or `FUT:24075` lines unless `FUTURES_TRADING_ENABLED=true`.

### Follow-up — intermittent "API down" / SSE stream down (2026-06-17 PM)

**Root cause (confirmed in profiling):**
- SSE did not send any bytes until the first snapshot finished (~650ms+, up to 15s when thread pool saturated) → browser `EventSource` looked dead
- `read_events_today()` scanned the full 10k-line JSONL on hot paths (SSE every 3s + status polls)
- `/api/trades` ran ledger I/O on the asyncio event loop (blocking `/health` under load)
- UI treated **one** failed `/health` poll as total outage

**Additional fixes:**
- `trade_ledger._read_events_tail_filtered()` — today reads scan tail only (~37ms vs ~180ms)
- SSE sends `: connected` heartbeat immediately; payload cached 2.5s; interval 5s
- Dedicated `ThreadPoolExecutor` (20 workers) via FastAPI lifespan
- `/api/trades` moved to `asyncio.to_thread`
- Frontend: health requires 3 consecutive failures before "API down"; health timeout 15s; SSE stale window 30s; `onopen` counts as connected

**You must restart `run.py` after pulling these changes** — the running process does not hot-reload dashboard code.

### Kite "Offline" / Feed unavailable fix (2026-06-17 PM — final)

**Root cause:** Kite token was **valid** but UI showed offline because:
- Each API call created a **new** `KiteConnect` → forced full NFO+BFO instrument reload
- `/api/kite/status` hit `kite.profile()` on every 45s poll with **8s client timeout**
- Options panels fired duplicate heavy Kite calls → 20s timeouts → "Feed unavailable"

**Fixes:**
- `web/dashboard.py` — singleton Kite client, 45s status cache, `?quick=true` uses engine `TokenManager`
- `app/instruments_manager.py` — `bind()` compares access token, not object identity
- `app/options_strategy_runner.py` — full algo status skips duplicate ticker fetch
- `app/main.py` — late WS start always subscribes option leg tokens (options-only mode)
- `frontend` — Kite status cached 30s, stale-not-null on timeout, fast-only algo poll

**Daily Kite ritual (Zerodha):** token expires ~**6 AM IST** — after **07:35 IST** run `python generate_token.py` or Settings → Auto Login, then restart `run.py`.