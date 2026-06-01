# Kite Connect Integration — Full Capabilities & Project Usage

**Purpose**: Single source of truth for what Zerodha Kite Connect v3 can do (as of 2026) and exactly how NiftyFuturesAlgo currently uses it — plus the migration plan to support the full founder vision (BankNifty, Sensex, options, real-time candles, pre-trade risk).

**Last Updated**: 2026-05

**Official Reference**: https://kite.trade/docs/connect/v3/

---

## 1. What Kite Actually Gives Us (Production-Grade for Indian F&O)

### Instruments Master (The Foundation)
- `GET /instruments` (all) or `/instruments/NFO` (F&O only) — returns CSV.
- Critical fields for our vision:
  - `instrument_token`, `tradingsymbol`, `name`, `expiry`, `strike`, `tick_size`, `lot_size`, `instrument_type` (FUT / CE / PE), `segment` (NFO-FUT, NFO-OPT, and BSE equivalents).
- **Current project usage**: Only filters `name == "NIFTY" and segment == "NFO-FUT"`. Hardcoded fallback "NIFTY26JUNFUT". **No BankNifty, no Sensex, no options**.
- **Vision requirement**: Dynamic loader for NIFTY, BANKNIFTY, SENSEX across FUT + high-liquidity OPT strikes. Must read `lot_size` live (2026 values: Nifty 65, BankNifty 30, Sensex 20 — code currently assumes 75).

### Historical Candle Data
- `GET /instruments/historical/:instrument_token/:interval`
- Intervals: minute, 3m, 5m, 10m, 15m, 30m, 60m, day.
- Parameters: `from`, `to`, `continuous=1` (essential for futures — stitches across expiries), `oi=1`.
- **Current usage**: 5-minute for seeding previous candle + ATR bootstrap in `app/strategy.py` and `backtesting/data_loader.py`. Smart overlapping Parquet cache in `data/historical_cache/`. Good caching discipline already.
- **Gaps for vision**: No multi-symbol (BankNifty/Sensex), limited OI history in current flow, no options historical in production paths.

### WebSocket (The Missing Real-Time Layer)
- `wss://ws.kite.trade`
- Modes: `ltp`, `quote`, `full` (includes 5-level depth + OI — gold for F&O).
- Limits: 3000 instruments per connection, 3 connections per key.
- **Current usage**: None. Project uses 5s polling for LTP + time-bucket "candles" + occasional historical seeding. This is the biggest live-data limitation.
- **Vision requirement**: Primary real-time feed for proper 5m/15m candle construction, volume, OI change (conviction proxy), and depth for the three indices + key strikes. Enables much richer candle understanding.

### Market Quotes (REST)
- `/quote`, `/quote/ohlc`, `/ltp` (up to 500–1000 instruments).
- Returns OI, depth, volume — very useful for options and regime detection.
- Already used indirectly via dashboard for fills analysis.

### Orders, Trades, Positions (Execution & Learning Gold)
- Full varieties: regular, amo, bo (bracket), co (cover), iceberg.
- Products critical for F&O: MIS (intraday), NRML (carry).
- Order types: MARKET, LIMIT, SL, SL-M.
- Endpoints used today:
  - `kite.trades()` → real fills for P&L calibration and tax learning.
  - `kite.orders()` → order status.
  - `kite.margins()` → margin view.
  - Postback receiver at `/api/kite/postback`.
- **Vision power**: Real outcome telemetry is already flowing into the learning/memory layer. This is a massive existing advantage.

### Margins & Risk (Non-Negotiable for Options)
- `POST /margins/orders`, `POST /margins/basket`, `/user/margins/:segment`.
- Computes SPAN + exposure + premium + additional margins, considers existing positions, returns initial vs final after offsets.
- **Current usage**: Only basic `margins()` call in dashboard.
- **Vision requirement**: Pre-trade simulation of any new options position or multi-leg structure (iron condor, straddle hedge, etc.) *before* it ever reaches the RiskGatekeeper. This is how we safely add options without blowing up on margin surprises.

### Other Relevant
- GTT, alerts (2025+), user profile, funds, holdings.
- Excellent official Python SDK (`pykiteconnect`) with WebSocket support.

**Rate Limits Note**: Historical ~3 req/s; orders ~10/s. Our existing cache layer + smart overlapping logic is the right philosophy — extend it, do not remove it.

---

## 2. Current Project Usage Map (Honest Audit)

| Area                    | Files                          | What We Do Well                     | Gaps vs Vision                          |
|-------------------------|--------------------------------|-------------------------------------|-----------------------------------------|
| Instruments             | `app/strategy.py`, `data_loader.py` | Dynamic front-month Nifty FUT      | Only NIFTY, hardcoded lot, no OPT, no BNF/SENSEX |
| Historical + Cache      | `data_loader.py`, dashboard    | Excellent overlapping Parquet cache, continuous awareness | Single symbol, limited OI, no options history |
| Live Data               | `app/main.py` (5s poll)        | Simple, safe fallback in paper     | No WebSocket, no OI, no depth, no multi-symbol |
| Execution               | `risk_gatekeeper.py`, `strategy.py` | Full guard + validation + dry-run | Only futures MIS/NRML, no pre-margin checks for complex options |
| Learning from Broker    | dashboard Kite endpoints + `backtest_memory` | Real fills vs model calibration    | Only Nifty futures; not yet feeding agent memory |
| Token & Auth            | `token_manager.py`, `generate_token.py` | Proper expiry hook                 | Good |

**Overall**: We use a powerful subset extremely well (especially the learning-from-real-fills path and caching). We are not wasting the API — we are just using it narrowly for one instrument and one style.

---

## 3. Migration Plan (Aligned to Founder Vision)

### Phase 1 (Data & Contract Layer — No Live Capital Change)
- Create `app/instruments_manager.py`: load full NFO + BSE F&O, index by name ("NIFTY", "BANKNIFTY", "SENSEX"), expose `get_active_futures()`, `get_option_chain()`, dynamic `lot_size`.
- Update RiskConfig, PaperTradingParams, CostConfig to be symbol-aware.
- Add WebSocket client (pykiteconnect KiteTicker) — start with ltp + quote for the three indices + top strikes. Build proper candle aggregator + OI delta.
- Extend market_calendar for BankNifty/Sensex weekly expiries.
- All changes via `/implement --effort 3-4` with security + tests reviewers.

### Phase 2 (Intelligence)
- Feed richer WS data (OI, depth, multi-timeframe) into Candle/Regime Intelligence agent.
- Use `/margins/basket` inside Risk & Portfolio Predictor before any new options proposal reaches the gatekeeper.
- Real fills from all three indices + options flow into the same memory + documentation generator.

### Phase 3 (Production Polish)
- Connection management, reconnection, mode switching (full vs quote for bandwidth).
- Per-symbol rate-limit budgeting and cache strategies.
- Audit every new Kite call path through the existing audit_logger.

**Never** remove the `force_dry_run` path or the reconciliation circuit breaker while adding these capabilities.

---

## 4. Recommended First Experiments (Low Risk)

1. Run `kite.instruments("NFO")` + instruments for BSE F&O segment and print lot_size + tradingsymbol for the three indices.
2. Add a one-off dashboard endpoint: "Show me current active contracts + lots for NIFTY / BANKNIFTY / SENSEX".
3. Prototype a minimal WebSocket subscription for NIFTY + BANKNIFTY ltp + oi.

---

## 5. References & Notes

- Always prefer the official SDK over raw requests.
- Instruments master should be cached daily (it changes with new expiries/strikes).
- For options strategies, the basket margin API is one of Kite's strongest differentiators — we must use it.
- Historical data has practical limits on very old expired options; focus on futures + near-term liquid options first.

**This document is the contract between the founder vision and the actual API.** Every new feature must map back to a real Kite capability listed here and must improve (never weaken) the existing safety and learning posture.

---

*Update whenever we discover new useful endpoints or change usage patterns.*