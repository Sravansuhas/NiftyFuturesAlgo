# Build Reference — Major Steps & How Things Work

**Purpose**: Single reference doc for everything major built so far — especially the **live React frontend**, **FastAPI backend wiring**, and **Kite auto-login**. Use this when you forget how to run something or why a piece exists.

**Last updated**: 2026-06-11

---

## Table of Contents

1. [System at a Glance](#1-system-at-a-glance)
2. [How to Run Everything](#2-how-to-run-everything)
3. [Frontend → Backend Live Integration](#3-frontend--backend-live-integration)
4. [API Endpoints Reference](#4-api-endpoints-reference)
5. [Kite Token — Auto Login](#5-kite-token--auto-login)
6. [Files Created / Modified](#6-files-created--modified)
7. [Morning Routine (Algo Trader)](#7-morning-routine-algo-trader)
8. [Troubleshooting](#8-troubleshooting)
9. [Known Gaps (What's Next)](#9-known-gaps-whats-next)
10. [Quant Deep Dive](#10-quant-deep-dive--what-were-building)
11. [Options Sheet & Journal](#11-options-sheet--journal-separate-from-futures-engine)
12. [F&O Failure-Pattern Guards](#12-fo-failure-pattern-guards-encodable-rules)
13. [Adaptive Trade Budget](#13-adaptive-trade-budget)
14. [Related Docs](#14-related-docs)

---

## 1. System at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│  React Frontend (Vite)          http://localhost:5173             │
│  AG Quant UI — Dashboard, Options Sheet, Strategies, Risk, etc. │
└────────────────────────────┬────────────────────────────────────┘
                             │  /api/* proxied in dev
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Dashboard              http://localhost:8050           │
│  web/dashboard.py — REST + SSE + backtest jobs + Kite status    │
└────────────────────────────┬────────────────────────────────────┘
                             │  shared Python process
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Trading Engine                 app/main.py (background thread) │
│  3-index paper mode: NIFTY + BANKNIFTY + SENSEX futures         │
│  RiskGatekeeper, FO rules, adaptive trade budget, trade ledger   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Zerodha Kite Connect API  (orders, quotes, historical, profile) │
└─────────────────────────────────────────────────────────────────┘
```

**Single-process runner**: `run.py` starts both the trading engine and the API in one Python process so the UI always sees live engine state (no "dead GUI" problem).

| Component | Tech | Port |
|-----------|------|------|
| Frontend | React 18 + TypeScript + Vite | 5173 |
| Backend API + Engine | Python + FastAPI + uvicorn | 8050 |
| Kite login callback | Local HTTP server | 8765 |

---

## 2. How to Run Everything

### Prerequisites

- Python 3.11+ with dependencies from `requirements.txt`
- Node.js for frontend (`cd frontend && npm install`)
- `.env` with `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN`

### Daily startup (recommended)

```powershell
cd C:\Projects\NiftyFuturesAlgo

# Validates Kite token; opens browser auto-login if expired
python run.py --ensure-token --dev
```

In a **second terminal**:

```powershell
cd C:\Projects\NiftyFuturesAlgo\frontend
npm run dev
```

Open **http://localhost:5173** (dev) or **http://localhost:8050/ui** (built UI from `npm run build`)

### Other useful commands

| Command | What it does |
|---------|--------------|
| `python run.py` | Engine + API (paper mode, market calendar enforced) |
| `python run.py --dev` | Paper + bypass calendar for closed-market testing |
| `python run.py --login` | Force Kite browser login before starting |
| `python run.py --ensure-token` | Validate token; auto-login only if expired |
| `python generate_token.py` | Standalone auto-login (opens browser, saves `.env`) |
| `python generate_token.py --validate` | Check if current token works |
| `python generate_token.py --manual` | Old copy-paste `request_token` flow |

### Dev mode flags

```powershell
python run.py --dev --sim-vol 2.0
python run.py --dev --fixed-time "2026-06-02 11:30:00"
```

See `docs/DEV_TESTING_GUIDE.md` for full closed-market testing details.

---

## 3. Frontend → Backend Live Integration

### Problem (before)

The Vite frontend (`frontend/`) was **static mock data** — hardcoded P&L, fake positions, buttons that did nothing.

### Solution (built)

Wired every page to the existing FastAPI API in `web/dashboard.py`.

#### Infrastructure added

| Piece | Path | Role |
|-------|------|------|
| API types | `frontend/src/api/types.ts` | TypeScript interfaces for status, risk, backtest, Kite |
| API client | `frontend/src/api/client.ts` | `fetch` wrapper for all REST calls |
| SSE hook | `frontend/src/hooks/useStatusStream.ts` | Live updates via `EventSource` on `/api/status/stream` |
| Formatters | `frontend/src/utils/format.ts` | INR, prices, event labels |
| Vite proxy | `frontend/vite.config.ts` | Proxies `/api` and `/health` → `127.0.0.1:8050` |
| CORS | `web/dashboard.py` | Allows `localhost:5173` to call API directly |

#### What each page shows (live data)

| Page | Route | Data source |
|------|-------|-------------|
| **Layout** | (shell) | SSE stream, combined MTM (futures + options), Kite status, pre-event banner |
| **Dashboard** | `/dashboard` | Live LTP, adaptive trade budget, FO guards, futures + options MTM |
| **Options Sheet** | `/options-sheet` | Manual CE/PE sheet, journal, 1-lot P&L, target checking |
| **Strategies** | `/strategies` | NIFTY/BANKNIFTY/SENSEX engine snapshots (ATR, target, SL, regime) |
| **Risk Guard** | `/risk` | `/api/risk/config` — real RiskGatekeeper limits |
| **Backtest** | `/backtest` | `POST /api/backtest/run` + job polling |
| **Settings** | `/settings` | Kite status, auto-login, system diagnostics |

#### Real-time updates

- **SSE (preferred)**: `GET /api/status/stream` — updates ~every 1.2s for index cards, snapshots, execution feed
- **REST polling**: Layout also polls `/api/status` every 15s as fallback

#### Environment variable (optional)

```env
# Only needed if frontend talks to API on a different host (production)
VITE_API_BASE=http://your-server:8050
```

Leave unset for local dev — Vite proxy handles it.

---

## 4. API Endpoints Reference

### Engine & live trading

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | System state: P&L, `combined_daily_pnl`, `options_mtm`, `trade_budget`, `fo_guards` |
| GET | `/api/status/stream` | SSE stream for live dashboard updates |
| GET | `/api/market/status` | Market open/closed, expiry, safe window, pre-event block |
| GET | `/api/trades` | Trade ledger events (`?limit=50`) |
| GET | `/api/risk/config` | Risk limits, lot sizes, adaptive `trade_budget` |
| POST | `/api/emergency/halt` | Kill switch — halt engine, flatten paper positions |
| GET | `/health` | Simple health check |
| GET | `/api/system/info` | Version, engine loaded, memory runs |

### External options sheet (brother's daily signals — not the futures engine)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/external-signals` | Load CE/PE sheet for date (`?date=`, `?with_pnl=true`) |
| POST | `/api/external-signals` | Save sheet for a date |
| GET | `/api/external-signals/dates` | List saved dates |
| GET | `/api/external-signals/premiums` | Live Kite CE/PE LTP + 1-lot P&L enrichment |
| POST | `/api/external-signals/evaluate` | Check targets vs live premium, update journal, save |
| GET | `/api/external-signals/journal` | Flattened history across saved days |

### Backtesting

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/backtest/run` | Start walk-forward job (FormData params) |
| GET | `/api/backtest/result/{job_id}` | Poll job progress/result |
| POST | `/api/backtest/cancel/{job_id}` | Cancel running job |
| GET | `/api/data/cached_datasets` | List local parquet cache |
| POST | `/api/data/fetch` | Fetch/refresh historical data |

### Kite broker

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/kite/status` | Connection check (no secrets exposed) |
| POST | `/api/kite/login/start` | Start browser auto-login |
| GET | `/api/kite/login/status` | Poll login progress |
| GET | `/api/kite/margins` | Margin details |
| GET | `/api/kite/trades` | Real executed trades |
| GET | `/api/kite/orders` | Order history |
| POST | `/api/kite/postback` | Order update webhook receiver |

### Legacy HTML dashboard

| URL | Description |
|-----|-------------|
| `http://localhost:8050/` | Original Jinja terminal (still works) |
| `http://localhost:8050/ui/` | Built React UI (`frontend/dist` after `npm run build`) |
| `http://localhost:8050/ui/options-sheet` | Options Sheet page |
| `http://localhost:8050/backtest` | Original backtest lab HTML |

The React frontend is the primary UI; legacy HTML dashboards remain as fallback.

---

## 5. Kite Token — Auto Login

### Why manual copy-paste existed

Old `generate_token.py` flow:
1. Print login URL
2. User logs in → copies `request_token` from redirect URL
3. Pastes into terminal

### Kite regulatory limits (cannot bypass)

| Rule | Impact |
|------|--------|
| Access token expires **6 AM IST** daily | Must re-login each trading day |
| `refresh_token` only for **approved platforms** | Retail apps cannot auto-refresh silently |
| Zerodha login requires **credentials + 2FA** | Browser step is mandatory |

**What we automated**: everything after you click Login — capture token, exchange, save to `.env`.

### One-time Kite developer console setup

1. Go to [Kite Developer Apps](https://developers.kite.trade/apps)
2. Set **Redirect URL** to:

```
http://127.0.0.1:8765/callback
```

3. Optional — add to `.env`:

```env
KITE_REDIRECT_URL=http://127.0.0.1:8765/callback
```

### How auto-login works

```
You click "Auto Login" (or run generate_token.py)
        │
        ▼
Local server starts on :8765/callback
        │
        ▼
Browser opens kite.zerodha.com/connect/login
        │
        ▼
You enter Zerodha credentials + 2FA  ← only manual step
        │
        ▼
Kite redirects to http://127.0.0.1:8765/callback?request_token=xxx
        │
        ▼
app/kite_auth.py captures token → exchanges for access_token → saves .env
```

### Three ways to login

| Method | When to use |
|--------|-------------|
| `python generate_token.py` | Standalone, from terminal |
| `python run.py --ensure-token` | Before engine start; skips if token valid |
| Settings → **Auto Login** in React UI | From browser while engine is running |

### Core module

`app/kite_auth.py` exports:

- `start_auto_login()` — blocking CLI flow
- `start_auto_login_async()` — non-blocking for API/UI
- `validate_access_token()` — ping `kite.profile()`
- `exchange_request_token()` — manual fallback
- `get_login_status()` — poll async login state

### TokenManager improvements

`app/token_manager.py` now:
- Calls `kite.profile()` on startup to **actually validate** the token
- Sets `needs_relogin = True` when invalid
- Still attempts `renew_access_token()` if refresh token exists (rare for retail)

---

## 6. Files Created / Modified

### New — core backend (2026-06)

| File | Purpose |
|------|---------|
| `app/kite_auth.py` | Kite auto-login with local callback server |
| `app/external_signals.py` | Manual options sheet store + journal + target evaluation |
| `app/options_pnl.py` | 1-lot CE/PE P&L after brokerage + STT (April 2026 rates) |
| `app/adaptive_trade_budget.py` | Regime-aware per-index trade cap (base 3, tiered +1/+2 quality extension) |
| `app/rolling_edge.py` | Rolling 10-trade expectancy for FO_ROLLING_EDGE_HALT |
| `app/fo_guard_status.py` | Dashboard snapshot of active tier-1 FO guards |
| `data/external_options_signals.json` | Persisted options sheets by date (runtime; created on first Save) |
| `data/market_events.json` | RBI MPC, Budget, GDP — pre-event block calendar |
| `tests/test_external_signals.py` | Options sheet + `evaluate_side()` tests |
| `tests/test_options_pnl.py` | 1-lot P&L math tests |
| `tests/test_adaptive_trade_budget.py` | Adaptive budget score/cap tests |
| `tests/test_rolling_edge.py` | Rolling expectancy + FO rule integration |
| `tests/test_fo_guard_status.py` | FO guard snapshot tests |

### New — frontend

| File | Purpose |
|------|---------|
| `frontend/src/api/types.ts` | TypeScript API types (status, options sheet, FO guards, trade budget) |
| `frontend/src/api/client.ts` | REST API client |
| `frontend/src/hooks/useStatusStream.ts` | SSE live stream hook |
| `frontend/src/utils/format.ts` | INR, prices, timestamps (handles Unix + ISO) |
| `frontend/src/pages/ExternalSignals.tsx` | Options Sheet + Journal UI |
| `docs/BUILD_REFERENCE.md` | This file |

### Modified — backend (cumulative)

| File | Changes |
|------|---------|
| `web/dashboard.py` | CORS, Kite login, external-signals APIs, `combined_daily_pnl`, `fo_guards`, `trade_budget` in `/api/status` |
| `app/main.py` | Calmer terminal (60s summary), startup price check |
| `app/strategy.py` | Kite LTP key fix, `get_adx_proxy`/`get_chop_score`, adaptive cap after vol/breakout quality |
| `app/multi_symbol_risk.py` | Per-symbol PnL/trades, adaptive budget, FO context (chop, rolling edge, events) |
| `app/fo_rules_engine.py` | `compound_all` any_of, `rolling_edge_halt`, event calendar invert, dynamic trade cap |
| `app/instruments_manager.py` | `ltp_key()`, month-aware futures, `fetch_ltp`, option instruments |
| `app/market_calendar.py` | `data/market_events.json` loader, pre-event block in `get_market_status()` |
| `app/diagnostic_logger.py` | Console default WARNING; verbose via `DIAG_VERBOSE=true` |
| `data/knowledge_base/indian_fo_rules.json` | v2026.06.2 — CHOP_VETO, ROLLING_EDGE_HALT, EVENT_CALENDAR |
| `generate_token.py` | Auto-login default |
| `app/token_manager.py` | Real `kite.profile()` validation |
| `run.py` | `--login`, `--ensure-token` |

### Modified — frontend (cumulative)

| File | Changes |
|------|---------|
| `frontend/vite.config.ts` | Proxy `/api` → `:8050` |
| `frontend/src/App.tsx` | Route `/options-sheet` |
| `frontend/src/components/Layout.tsx` | Combined MTM, Options Sheet nav, pre-event banner |
| `frontend/src/components/EngineBanner.tsx` | Pre-event block banner (FO_EVENT_CALENDAR) |
| `frontend/src/pages/Dashboard.tsx` | Portfolio trade budget, FO guards, futures + options MTM split |
| `frontend/src/pages/ExternalSignals.tsx` | Sheet entry, journal, 1-lot P&L, Check targets |
| `frontend/src/pages/Strategies.tsx` | Live 3-index strategy cards |
| `frontend/src/pages/RiskManagement.tsx` | Live risk config |
| `frontend/src/pages/Settings.tsx` | Kite auto-login |

---

## 7. Morning Routine (Algo Trader)

```
08:45  python run.py --ensure-token --dev
       → Token valid? Engine starts.
       → Token expired? Browser opens → login + 2FA → auto-saved.

08:46  cd frontend && npm run dev
       → Open http://localhost:5173  (or http://localhost:8050/ui after npm run build)

08:50  Dashboard checks:
       - Kite: Connected (not SIMULATED)
       - Pre-event banner clear? (FO_EVENT_CALENDAR blocks 4h before RBI/Budget)
       - Psychological Guard: portfolio trades vs cap (e.g. 2/12)
       - F&O entry guards: chop veto / rolling edge status

08:55  Options Sheet (if using brother's CE/PE numbers):
       → /options-sheet → enter C/P/T/L + strike per index → Save
       → During session: Check targets (auto every 30s on today's date)
       → EOD: Journal tab for target_met / stop_hit history

09:15  Market open — futures engine evaluates breakouts on NIFTY/BNF/SENSEX
       - FO_CHOP_VETO blocks fake breakouts in ranging chop
       - Adaptive budget: base 3/index, +1 only in quality windows (not chop)
       - All orders FORCED DRY-RUN until FORCE_DRY_RUN=false in .env
```

---

## 8. Troubleshooting

### Frontend shows "Engine Offline"

- Backend not running → start `python run.py --dev`
- Check `http://localhost:8050/health` returns `{"status":"ok"}`

### "Live stream disconnected"

- SSE requires backend running on `:8050`
- Vite proxy only works in dev mode (`npm run dev`), not `npm run preview` without config

### Kite shows "Not Connected" / token errors

```powershell
python generate_token.py --validate
python generate_token.py          # re-login if invalid
```

Common causes:
- Token expired (after 6 AM IST)
- Redirect URL mismatch in Kite developer console
- Wrong `KITE_API_SECRET` in `.env`

### Auto-login times out

- Complete Zerodha login within 180 seconds (configurable via `KITE_LOGIN_TIMEOUT` env)
- Ensure nothing else is using port 8765
- Firewall blocking localhost callback

### Backtest stuck on "running"

- Check terminal logs for `[BACKTEST JOB]` progress
- Cancel via UI or `POST /api/backtest/cancel/{job_id}`
- Quick mode (`quick_mode=true`) is fastest for first test

### Dashboard shows "6 / 3 Trades" or "max trades per day reached" in a good market

- **Fixed**: UI shows **portfolio** trades vs cap (e.g. `6 / 12`), plus per-index breakdown (`NIFTY 2/3 · BNF 3/5 (+2 quality)`)
- Limit is **per index** (base 3, up to 5 with tiered quality extension), not 3 total
- **Fixed (2026-06-11)**: Entry gates no longer block at base cap **before** volume/breakout quality is scored — BANKNIFTY at 3/3 can earn a 4th trade when trending + volume confirmed
- Rejection copy now explains budget state: `3/4 trades used · regime score 0.87 · quality window +1`

### Prices don't match Kite portal

- Compare the **same FUT contract** (e.g. `NIFTY26JUNFUT`), not the index chart
- Token expired → `python generate_token.py`, restart `run.py`
- REST LTP uses `NFO:TRADINGSYMBOL` format (Kite Connect v3)

### Options sheet shows "No live premium"

- Valid Kite token required
- Strike must exist on current weekly/monthly expiry
- Run **Check targets** during market hours

### "Pre-event block active" banner

- Expected within 4 hours of RBI MPC, Union Budget, or GDP in `data/market_events.json`
- Update that file when RBI publishes new MPC dates

### Port already in use

| Port | Service |
|------|---------|
| 8050 | `run.py` / uvicorn — kill other Python processes |
| 5173 | Vite dev server |
| 8765 | Kite login callback — only active during login |

---

## 9. Known Gaps (What's Next)

| Gap | Priority | Status |
|-----|----------|--------|
| **WebSocket feed (KiteTicker)** | High | Open — REST polling + simulation; sidecar not wired |
| **Docker / docker-compose** | Medium | Open |
| **Kill switch** | High | **Done** — `POST /api/emergency/halt` + `app/emergency.py` |
| **Dynamic lot sizes** | Medium | **Done** — `instruments_manager` + fallbacks 65/30/20 |
| **Multi-symbol PnL/trades** | High | **Done** — `multi_symbol_risk.py` |
| **Token validity in UI** | Medium | **Done** — `kite.profile()` in `/api/status` |
| **Manual options sheet + journal** | Medium | **Done** — `/options-sheet`, target tracking, 1-lot P&L |
| **Adaptive trade budget** | Medium | **Done** — base 3/index, tiered +1/+2 quality, hard ceiling 5/index, portfolio cap 12 |
| **FO failure-pattern guards** | High | **Done** — chop veto, rolling edge halt, event calendar |
| **Combined dashboard MTM** | Medium | **Done** — futures + options sheet MTM |
| **Automated options trading** | Future | Sheet is manual/discretionary only; futures engine separate |
| **Production frontend** | Medium | `npm run build` → served at `/ui` on :8050 |
| **Scheduled morning login** | Low | Task Scheduler + `run.py --ensure-token` |
| **Mark skipped / manual entry on options legs** | Low | Open — only auto-enter rule today |

---

## 10. Quant Deep Dive — What We're Building

### Vision (honest)

A **risk-first Indian index futures** algo platform:
- **Live path**: Previous-candle breakout on NIFTY + BANKNIFTY + SENSEX (paper today)
- **Research path**: Walk-forward backtest with cost model + statistical power warnings
- **Learning path**: Trade ledger + backtest memory + Kite real-fills analysis
- **Control path**: React UI + FastAPI + single-process engine

Not yet: options Greeks, multi-leg strategies, colocation, ML alpha — those are in the UI vision doc but not the engine.

### Strategy logic (production path)

**Previous Candle Breakout** (`app/strategy.py`):
1. Seed previous 5m candle high/low + ATR from Kite historical or simulation
2. Detect regime (volatility: low/normal/high)
3. Entry: break above prev high (LONG) or below prev low (SHORT) with ATR buffer + volume filter
4. Exit: ATR-based target and stop-loss
5. Time filters: avoid first 30 min, last 15 min, expiry caution (`market_calendar.py`)

**Quant discipline encoded:**
- **Adaptive trade budget**: base 3 entries/index, +1 at score ≥ 0.65 (no green session required), +2nd at score ≥ 0.82 (green session required), hard ceiling 5/index, portfolio cap 12
- **No extension in chop** — ranging tape stays at base cap; FO_CHOP_VETO blocks breakout entries
- 0.5% risk per trade on ₹10L capital
- 2% max daily loss, 8% max drawdown
- All orders through `multi_risk_manager.place_guarded_order()` (dry-run in paper)

### Indian F&O realities we respect

| Reality | How we handle it |
|---------|------------------|
| Token expires 6 AM IST | Auto-login (`app/kite_auth.py`) |
| Lot sizes change | `instruments_manager.get_active_future().lot_size` |
| Opening 30 min toxic | `is_safe_trading_window()` + FO_OPENING_AUCTION_WINDOW de-risk |
| Expiry gamma | `is_expiry_day()` caution + intelligence_loop posture |
| RBI / Budget event risk | `FO_EVENT_CALENDAR` — block 4h before (`data/market_events.json`) |
| Chop / fake breakouts | `FO_CHOP_VETO` + wider breakout buffer in ranging |
| Death by 1000 cuts | `FO_ROLLING_EDGE_HALT` — halt if last 10 trades expectancy < 0 |
| Gross backtests lie | `backtesting/costs.py` + 2x stress toggle |
| Low sample WFA | Statistical power warnings in backtest results |

### App module map (quick reference)

| Module | Role |
|--------|------|
| `main.py` | Engine loop — 3 strategies, reconciliation, snapshots |
| `strategy.py` | Breakout logic, regime, ADX/chop metrics, price feed |
| `multi_symbol_risk.py` | Per-index positions, adaptive budget, FO rules context |
| `adaptive_trade_budget.py` | Regime score → effective trade cap per index |
| `fo_rules_engine.py` | Loads `indian_fo_rules.json`, evaluates before entry |
| `fo_guard_status.py` | Human-readable guard snapshot for dashboard |
| `rolling_edge.py` | Rolling expectancy from `trade.closed` ledger events |
| `external_signals.py` | Manual options sheet + journal + target evaluation |
| `options_pnl.py` | 1-lot buyer P&L after costs |
| `risk_gatekeeper.py` | Legacy global risk (still used for some gates) |
| `state_machine.py` | Trading lifecycle — `EMERGENCY_HALT` blocks entries |
| `emergency.py` | Kill switch — flatten all paper positions |
| `instruments_manager.py` | Kite contracts, LTP keys, lot sizes, options lookup |
| `market_calendar.py` | NSE hours, holidays, macro event calendar |
| `kite_auth.py` | Browser auto-login |
| `trade_ledger.py` | Append-only event log (`trade.closed` for rolling edge) |
| `live_snapshots.py` | In-memory LTP/signal cache for dashboard |
| `diagnostic_logger.py` | Structured logs; calm console by default |
| `broker_reconciliation.py` | Broker position sync (live path) |
| `data_feed.py` | WebSocket skeleton (not wired to strategy yet) |

### Frontend integration status (2026-06-11)

| Feature | Status |
|---------|--------|
| Live dashboard (SSE) | ✅ |
| Combined MTM (futures + options sheet) | ✅ |
| Adaptive trade budget + FO guards display | ✅ |
| Options Sheet + Journal | ✅ |
| 1-lot options P&L (after est. taxes) | ✅ |
| Pre-event macro banner | ✅ |
| Risk guard (read-only) | ✅ |
| Strategies (read-only) | ✅ |
| Backtest run/cancel | ✅ |
| Kite auto-login | ✅ |
| Kill switch | ✅ |
| Strategy pause toggle | ❌ Engine-controlled only |
| LIVE mode switch | ❌ Env-only (`FORCE_DRY_RUN`) |
| Automated options trading | ❌ Manual sheet only; futures engine separate |

### Bugs fixed (2026-06-10 → 2026-06-11)

| # | Issue | Fix |
|---|-------|-----|
| 1 | Duplicate `can_place_order` — limits bypassed | Removed duplicate in `multi_symbol_risk.py` |
| 2 | Per-symbol trades/PnL not updating | Fixed accounting in `multi_symbol_risk.py` |
| 3 | `get_gate_summary` UnboundLocalError | Removed shadowing import in `strategy.py` |
| 4 | Kite REST LTP wrong format | Use `NFO:TRADINGSYMBOL` via `ltp_key()` |
| 5 | Dashboard "6/3 trades" misleading | Portfolio vs per-index caps + adaptive budget UI |
| 6 | `FO_OPENING_AUCTION_WINDOW` inverted | `invert: false` — de-risk outside safe window |
| 7 | `safe_trading_window` field mismatch | Aliased in `fo_rules_engine._prepare_context()` |
| 8 | Invalid Date / UNKNOWN in trade ledger | `formatTime()` + symbol on rejections |
| 9 | Terminal log spam | `diagnostic_logger` WARNING default; 60s summary in `main.py` |
| 10 | Good market blocked at 3/3 trades | Early cap check removed; full adaptive budget scored after vol/breakout quality (`strategy.py`) |

### Research-backed next steps

1. **WebSocket sidecar** — KiteTicker feeding `live_snapshots`
2. **Walk-forward on real data** — 60+ trades before trusting WFA
3. **Broker reconciliation for multi_symbol** — before live capital
4. **Per-symbol state persistence** — fix `strategy_state.json` overwrite
5. **Docker compose** — engine + frontend + env volume
6. **Automated options** — only after margin basket checks; sheet stays discretionary

---

## 11. Options Sheet & Journal (separate from futures engine)

Brother's daily CE/PE numbers live **outside** the breakout futures engine. Use for tracking discretionary option alerts, not auto-execution.

### Sheet notation

| Code | Meaning |
|------|---------|
| **C** | Call entry premium |
| **P** | Put entry premium |
| **T** | Target premium |
| **L** | Stop (points on premium) |
| **Bold strike** | Strike price on the sheet |

### Data store

- File: `data/external_options_signals.json`
- One object per trade date (IST)
- Journal fields persist: `journal_status`, `session_high/low`, `target_met_at`, `stop_hit_at`

### Journal states

| Status | Meaning |
|--------|---------|
| `watching` | Waiting for entry (LTP ≤ C/P × 1.02) |
| `entered` | Treated as filled |
| `target_met` | Session high ≥ T after entry |
| `stop_hit` | Session low ≤ fill − L |
| `incomplete` | Missing strike or entry |

### 1-lot P&L (`app/options_pnl.py`)

Per leg shows:
- **Lot size** (from Kite or fallback 65/30/20)
- **Lot price** = premium × lot size
- **Max gain @ target (net)** / **Max loss @ stop (net)** after brokerage + STT 0.15% + charges
- **MTM now (net)** when in trade

Dashboard **Net MTM Today** = futures `daily_pnl` + options sheet `mtm_net` (entered legs only).

### Daily workflow

1. **Options Sheet** → enter numbers → **Save**
2. **Check targets** during market (auto-refresh every 30s on today's date)
3. **Journal** tab → EOD review across saved dates

---

## 12. F&O Failure-Pattern Guards (encodable rules)

Rules live in `data/knowledge_base/indian_fo_rules.json` (v2026.06.2). Evaluated by `fo_rules_engine.check_entry()` before every new futures entry.

### Tier-1 blocks (new in 2026-06-11)

| Rule ID | When it blocks |
|---------|----------------|
| **FO_CHOP_VETO** | Breakout entry + ranging trend + (low ADX proxy OR low vol OR chop score ≥ 0.60) |
| **FO_ROLLING_EDGE_HALT** | Last 10 closed trades have negative rolling expectancy |
| **FO_EVENT_CALENDAR** | Within 4 hours of high-impact event in `data/market_events.json` |

### Tier-1 blocks (existing)

| Rule ID | When it blocks |
|---------|----------------|
| FO_HARD_SL_REQUIRED | No broker-anchored SL |
| FO_REVENGE_TRADING_COOLDOWN | Re-entry within 5 min after loss |
| FO_OVERTRADING_DAILY_CAP | Per-index cap (uses adaptive `effective_max_trades`) |

### Tier-2 de-risk (size reduction, not block)

FO_SLIPPAGE_BUDGET, FO_EXPIRY_DAY_DE_RISK, FO_OPENING_AUCTION_WINDOW (outside 9:45–15:15), FO_LOSS_STREAK_DE_RISK, FO_PAPER_LIVE_DIVERGENCE

### Dashboard visibility

- `/api/status` → `fo_guards` per symbol
- Psychological Guard tile → active guard labels
- Engine banner → pre-event block when `within_pre_event_block_window`

### Tests

```powershell
python -m pytest tests/test_fo_rules_engine.py tests/test_rolling_edge.py tests/test_market_calendar.py tests/test_fo_guard_status.py -q
```

---

## 13. Adaptive Trade Budget

Replaces rigid "3 trades total" confusion with disciplined, regime-aware caps. In **good trending markets**, the engine can extend a few more entries — chop/ranging stays at base cap.

### Per index (NIFTY / BANKNIFTY / SENSEX)

| Parameter | Value |
|-----------|-------|
| Base cap | 3 entries/index/day |
| 1st quality extension | **+1** (4th trade) — score ≥ **0.65**, trending, volume/breakout confirmed; **no green session required** |
| 2nd quality extension | **+1** more (5th trade) — score ≥ **0.82** **and** green session P&L |
| Hard ceiling | **5**/index/day (absolute max) |
| Portfolio ceiling | **12** total entries/day across all indices |

### When extension is denied (still at base 3)

- **Ranging / chop** — `FO_CHOP_VETO` + no bonus in budget
- Regime score below 0.65
- 2nd bonus requested but session P&L not green
- 2+ consecutive losses (score penalty)
- Already at hard ceiling (5/5)

### Cap reduces when

- Session loss > 0.5% capital → base −1
- Session loss > 1% → base −1 more
- 2+ consecutive losses → lower regime score, no extension
- Expiry / intelligence posture → lower recommended max

### Entry gate order (why BANKNIFTY 3/3 can still trade)

1. `set_market_regime()` — publish trend/vol/HTF bias
2. **Hard ceiling only** early check (block at 5/5, not 3/3)
3. Cooldown, price, ATR filters
4. Volume confirmation + breakout confidence scored
5. `set_market_regime(..., {vol_ok, entry_confidence})` — full budget computed
6. Effective cap check + `can_place_order()` (FO rules + portfolio cap)

### Modules

| File | Role |
|------|------|
| `app/adaptive_trade_budget.py` | Regime score → `effective_cap`, `bonus_available`, `reasons` |
| `app/multi_symbol_risk.py` | `get_trade_budget()`, `can_place_order()` enforcement |
| `app/strategy.py` | `_passes_hard_trade_ceiling()`, `_passes_effective_trade_cap()` in `should_enter_*()` |
| `app/diagnostics.py` | Human-readable reject: `Trade budget full — 3/4 trades used · regime score 0.87 · …` |

### UI

```
6 / 12 portfolio trades
NIFTY 2/3 · BANKNIFTY 3/5 (+2 quality) · SENSEX 2/3
Adaptive budget: base 3/index, +1/+2 in quality windows (no chop).
```

### Tests

```powershell
python -m pytest tests/test_adaptive_trade_budget.py -q
```

---

## 14. Related Docs

| Doc | Covers |
|-----|--------|
| **[EXTERNAL_REPOS_REFERENCE.md](./EXTERNAL_REPOS_REFERENCE.md)** | Adjacent repos (`oi_tracker`, `AI-trader`) — what to borrow and when |
| **[COFOUNDER_PLAYBOOK.md](./COFOUNDER_PLAYBOOK.md)** | **Start here** — honest status, master checklist, closed-market workflow |
| [KITE_INTEGRATION.md](../KITE_INTEGRATION.md) | Full Kite API capabilities & project usage map |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | System design, risk-first philosophy |
| [DEV_TESTING_GUIDE.md](./DEV_TESTING_GUIDE.md) | Closed-market dev testing |
| [MORNING_TRADING_GUIDE.md](../MORNING_TRADING_GUIDE.md) | Pre-market checklist |
| [frontend/TECHNICAL_ARCHITECTURE.md](../frontend/TECHNICAL_ARCHITECTURE.md) | React UI architecture vision |
| [backtesting/DOCUMENTATION.md](../backtesting/DOCUMENTATION.md) | Backtest engine details |
| [README.md](../README.md) | Project overview & setup |
| [INDIAN_FO_KNOWLEDGE_BASE.md](../INDIAN_FO_KNOWLEDGE_BASE.md) | STT, costs, retail failure patterns |
| `data/knowledge_base/indian_fo_rules.json` | Machine-enforceable FO rules |
| `data/market_events.json` | Macro event calendar for pre-event blocks |

---

## Accomplishments Summary (2026-06-11)

| Area | What we built |
|------|----------------|
| **Live UI** | React dashboard wired to FastAPI + SSE; built UI at `/ui` |
| **Kite** | Auto-login, token validation, correct LTP quote keys |
| **Futures engine** | 3-index paper breakout, regime detection, calm logging |
| **Risk** | Multi-symbol PnL, kill switch, tiered adaptive trade budget (3→5/index) |
| **FO guards** | Chop veto, rolling edge halt, event calendar, revenge cooldown |
| **Options sheet** | Manual CE/PE entry, journal, target check, 1-lot P&L, combined MTM |
| **Tests** | 45+ tests on FO rules, rolling edge, adaptive budget, options sheet |

---

*Update this file whenever a major feature is built — new API, new page, new auth flow, Docker, WebSocket, etc.*