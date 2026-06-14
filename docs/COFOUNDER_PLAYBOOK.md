# Co-Founder Playbook — Honest Status & Master Checklist

**For**: Srava (founder) + future you after 3 weeks of building  
**Last updated**: 2026-06-10  
**Tone**: Brutally honest. No "97% done" fiction.

---

## Why it feels unfinished after 3 weeks

You did not fail. You hit **three classic traps**:

### Trap 1 — Vision ahead of engine
The React UI (`frontend/`) was designed for a **full options platform** (Greeks, straddles, delta-neutral). The Python engine is a **3-index futures breakout bot**. The UI looked "done" while the backend was different scope → felt like dummy data.

### Trap 2 — Two dashboards
| URL | What it is |
|-----|------------|
| `http://localhost:5173` | New React UI (needs engine + Vite) |
| `http://localhost:8050` | Original live terminal (needs engine only) |

If you open only the React app without `python run.py`, **everything looks static**. That is not a bug — the engine is not running.

### Trap 3 — Market closed without `--dev`
When NSE is closed and you run `python run.py` (without `--dev`), calendar blocks entries and prices may show `SIMULATED`. Looks fake. **Solution**: always use `python run.py --dev` when market is closed.

---

## What actually works today (trust this)

| Capability | Status | How to verify |
|------------|--------|---------------|
| Paper trading engine (3 indices) | ✅ Works | `python run.py --dev` → logs show NIFTY/BNF/SENSEX |
| Risk limits enforced | ✅ Works | See Risk Guard page + `signal.rejected` in ledger |
| Kill switch | ✅ Works | UI button → `EMERGENCY_HALT` + square off |
| Kite auto-login | ✅ Works | `python generate_token.py` or Settings → Auto Login |
| Walk-forward backtest | ✅ Works | Backtest page or `http://localhost:8050/backtest` |
| Historical data download | ✅ Works | Backtest → Fetch Historical Data (needs Kite token) |
| React live dashboard | ✅ When engine on | SSE updates every ~1.2s |
| Live real money trading | ❌ Blocked | `FORCE_DRY_RUN` hardcoded — **by design** |

---

## Master checklist

Copy this. Check off as you go.

### Phase A — See live data tonight (market closed)

- [ ] **A1** Open terminal → `cd C:\Projects\NiftyFuturesAlgo`
- [ ] **A2** `python generate_token.py --validate` (re-login if invalid)
- [ ] **A3** `python run.py --dev` — wait for "Ready. Dashboard: http://localhost:8050"
- [ ] **A4** Second terminal → `cd frontend && npm run dev`
- [ ] **A5** Open `http://localhost:5173` — top banner should NOT say "Engine not connected"
- [ ] **A6** Dashboard → Market Intelligence shows LTP with source `SIMULATED` or `KITE` (both valid in dev)
- [ ] **A7** Watch terminal logs for `[SIGNAL]` accepted/rejected — proves strategy is thinking
- [ ] **A8** Open `http://localhost:8050` — compare with React (both should show same engine state)

### Phase B — Download historical F&O data

- [ ] **B1** Kite token valid (step A2)
- [ ] **B2** React → Backtest → set Months = 4 → click **Fetch Historical Data**
- [ ] **B3** Wait for "Loaded X rows" message
- [ ] **B4** Verify files in `data/historical_cache/` (parquet files appear)
- [ ] **B5** CLI alternative: `POST /api/data/fetch` via `http://localhost:8050/backtest` HTML page

**What gets downloaded**: NIFTY futures 5-minute candles (continuous contract stitching). BANKNIFTY/SENSEX historical in backtest still limited — known gap.

### Phase C — Test a strategy (closed market)

- [ ] **C1** Run engine with `--dev` (calendar bypassed)
- [ ] **C2** Optional: `python run.py --dev --sim-vol 2.0` for faster breakouts
- [ ] **C3** Optional: `python run.py --dev --fixed-time "2026-06-02 11:30:00"` for time-window tests
- [ ] **C4** Run backtest with **Use Kite Historical Data** checked (after Phase B)
- [ ] **C5** Check statistical power warning — need 40+ trades before trusting results
- [ ] **C6** Read `logs/run_*.log` for full decision chain

### Phase D — Create / tune your own strategy

Today there is **one live strategy class**: `PreviousCandleBreakoutStrategy` in `app/strategy.py`.

**To tune (no new code)**:
- [ ] **D1** Edit `app/paper_trading_params.py` → `DEFAULT_PAPER_PARAMS`
  - `breakout_atr_mult`, `profit_target_atr_mult`, `stop_loss_atr_mult`
  - `max_trades_per_day`, `risk_per_trade_pct`, `session_start/end`
- [ ] **D2** Restart `python run.py --dev`
- [ ] **D3** Validate in backtest with walk-forward before trusting live

**To add a new strategy (code)**:
- [ ] **D4** Copy pattern from `app/strategy.py` or `backtesting/previous_candle_backtest_strategy.py`
- [ ] **D5** Register in `app/main.py` strategies dict
- [ ] **D6** Add backtest class in `backtesting/` folder
- [ ] **D7** Walk-forward validate → then paper for 2+ weeks

*There is no drag-and-drop strategy builder yet — that is roadmap, not today.*

### Phase E — Paper → Live path (do NOT skip steps)

- [ ] **E1** 4+ weeks paper with `--dev` off during market hours
- [ ] **E2** Backtest on real data: positive expectancy after 2x costs
- [ ] **E3** Walk-forward: 60+ trades, no critical statistical power warning
- [ ] **E4** Reconciliation clean for 2 weeks (`broker_reconciliation.py`)
- [ ] **E5** Manual review of `data/trade_ledger.jsonl` + `data/audit_events.json`
- [ ] **E6** Only then: set `FORCE_DRY_RUN=false` in `.env` (requires removing hardcode in `main.py` too)
- [ ] **E7** Start with 1 lot NIFTY only — not 3 indices

**Today: E6 is intentionally blocked in code. Good.**

### Phase F — Risk guard status

| Rule | Value | Enforced? | Where |
|------|-------|-----------|-------|
| Max daily loss | 2% of capital (₹20k on ₹10L) | ✅ | `multi_symbol_risk` + `risk_gatekeeper` |
| Max drawdown | 8% | ✅ | Global check |
| Risk per trade | 0.5% default | ✅ | `calculate_order_quantity` |
| Max trades/day/symbol | 3 | ✅ | Fixed in latest audit |
| One position per symbol | ✅ | `can_place_order` |
| Force dry run | Always on | ✅ | `main.py` hardcoded |
| Kill switch | ✅ | `POST /api/emergency/halt` |
| Pre-trade margin check | ❌ | Not for futures yet |
| Options risk | ❌ | Not implemented |

**Risk Guard UI**: Read-only display of real limits. You cannot edit limits from UI — edit `RiskConfig` in `app/risk_gatekeeper.py` or env.

### Phase G — Product gaps to close (priority order)

- [ ] **G1** WebSocket price feed (KiteTicker sidecar process)
- [ ] **G2** BANKNIFTY + SENSEX historical download in backtest
- [ ] **G3** Strategy params editor in UI (wired to `paper_trading_params`)
- [ ] **G4** Single `docker compose up` for engine + frontend
- [ ] **G5** Remove options/Greeks mock UI or label "Coming soon"
- [ ] **G6** 4+ weeks paper trading journal before live

---

## Your 5 questions — direct answers

### 1. How to test when market is closed?

```powershell
python run.py --dev
```

This sets:
- `FORCE_DRY_RUN=true` — no real money
- `DEV_FORCE_MARKET_OPEN=true` — calendar thinks market is open
- Engine uses **simulated prices** that move (or cached vol if `DEV_USE_CACHED_VOL=true`)

Add volatility for faster signals:
```powershell
python run.py --dev --sim-vol 2.0
```

Full guide: `docs/DEV_TESTING_GUIDE.md`

### 2. How to create our own strategies?

**Today**: Edit parameters in `app/paper_trading_params.py` (fastest) or modify `app/strategy.py` (full control).

**Backtest new logic**: Edit `backtesting/previous_candle_backtest_strategy.py`, run walk-forward from UI.

**Future**: Strategy canvas in React is visual only — not connected to execution yet.

### 3. How to download historical F&O data?

**With Kite token**:
1. UI: Backtest → **Fetch Historical Data**
2. Or: `http://localhost:8050/backtest` → Data tab
3. Files land in `data/historical_cache/*.parquet`

**Requires**: Valid `KITE_ACCESS_TOKEN` in `.env`

### 4. How to implement strategy in paper/live?

```
Edit params/code → Backtest (walk-forward) → Paper (run.py --dev, then run.py market hours) → Live (only after checklist E)
```

Paper and live use the **same** `app/main.py` loop. Difference is only `FORCE_DRY_RUN` and Kite order placement.

### 5. What is Risk Guard status?

**Backend**: Strong. Limits are real and enforced on every order.

**Frontend**: Honest read-only view. Shows live PnL, trades today, drawdown from engine.

**Gap**: Cannot tune limits from UI. Must edit code/env.

---

## The one workflow for tonight (30 minutes)

```powershell
# Terminal 1
cd C:\Projects\NiftyFuturesAlgo
python generate_token.py --validate
python run.py --ensure-token --dev --sim-vol 1.5

# Terminal 2
cd frontend
npm run dev
```

1. Open `http://localhost:5173/dashboard` — confirm no red "Engine not connected" banner
2. Go to Backtest → Fetch Historical Data (4 months)
3. Run Quick Mode backtest
4. Go to Risk Guard — confirm limits show
5. Watch terminal for signals

If step 1 fails → engine is not running. **Not a frontend bug.**

---

## What to stop doing (co-founder advice)

1. **Stop building new UI pages** until engine runs end-to-end for 2 weeks
2. **Stop trusting gross PnL** — always use cost model in backtest
3. **Stop aiming for options** until futures paper is boringly consistent
4. **Stop running only frontend** — it will always look dummy without `run.py`

## What to start doing

1. **Same ritual daily**: `run.py --ensure-token --dev` + journal one page in `logs/`
2. **One strategy, one metric**: Previous candle breakout → track win rate + avg R in paper
3. **Weekly**: One walk-forward on cached real data
4. **Check this file** — tick off checklist items

---

## Related docs

- [BUILD_REFERENCE.md](./BUILD_REFERENCE.md) — Technical build log
- [DEV_TESTING_GUIDE.md](./DEV_TESTING_GUIDE.md) — Closed market testing
- [MORNING_TRADING_GUIDE.md](../MORNING_TRADING_GUIDE.md) — Market open routine
- [backtesting/DOCUMENTATION.md](../backtesting/DOCUMENTATION.md) — Backtest engine

---

*You are building a real system. It is 60% to "trustworthy paper", not 97% to "live trading". That is normal for algo. The checklist above is the path to money you can trust.*