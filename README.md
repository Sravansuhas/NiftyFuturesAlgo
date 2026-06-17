# Aegis

**Version 0.3.0** — **Aegis** is the official platform name: infrastructure-first algorithmic trading guardianship for Indian index F&O (NIFTY, BankNifty, Sensex futures) using Zerodha Kite Connect. **RiskGatekeeper** is the shield — fail-closed, broker-authoritative, audit-ready — and every order must pass it before strategy logic reaches the broker.

> **2026 Founder Vision**: Evolving into a self-improving, agentic platform for full F&O — walk-forward validation, failure-pattern mining, Grok skills for operational workflows, and an **Aegis UI** — while keeping **RiskGatekeeper** as sacred.
>
> Start here: [VISION_AND_STRATEGY.md](VISION_AND_STRATEGY.md) | [ROADMAP.md](ROADMAP.md) | [AGENTS_AND_SKILLS.md](AGENTS_AND_SKILLS.md) | [ARCHITECTURE.md](ARCHITECTURE.md) | [KITE_INTEGRATION.md](KITE_INTEGRATION.md) | [docs/BUILD_REFERENCE.md](docs/BUILD_REFERENCE.md)

The project is built as **trading infrastructure first**, strategy logic second. Survivability, broker reconciliation, auditability, and guarded execution come before any real capital.

**Do not use real capital** until you have validated with real historical data, extended paper trading, and independent risk review.

---

## Quick Start

**Prerequisites:** Python 3.11+, Node.js 18+ (for dev UI), Zerodha Kite API app with redirect URL configured.

```powershell
# 1. Clone and configure
copy .env.example .env          # Fill KITE_API_KEY, KITE_API_SECRET

# 2. Install dependencies
pip install -r requirements.txt
pip install pyarrow             # Optional: parquet cache (falls back to CSV)

# 3. Generate daily Kite token (before market open)
python generate_token.py        # Auto-login via browser
python generate_token.py --validate

# 4. Run engine + dashboard (recommended)
python run.py --ensure-token    # Validates token; auto-login if expired

# 5. Open UI
#    http://localhost:8050       — Legacy dashboard + built Aegis UI
#    http://localhost:8050/ui/     — React Aegis UI (after npm run build)
```

**Closed-market development:**

```powershell
python run.py --dev                        # Safe paper + calendar bypass
python run.py --dev --sim-vol 1.8          # Livelier simulated prices
.\scripts\start_dev.ps1                    # Windows shortcut (token + dev mode)
```

**Frontend dev (second terminal):**

```powershell
cd frontend && npm install && npm run dev  # http://localhost:5173
```

Full operational guide: [docs/BUILD_REFERENCE.md](docs/BUILD_REFERENCE.md) | Daily routine: [MORNING_TRADING_GUIDE.md](MORNING_TRADING_GUIDE.md)

---

## Current Status

| Phase | Status |
|-------|--------|
| Core infrastructure (risk, state machine, recon, calendar, audit) | **Complete** |
| Strategy & backtesting (Previous Candle Breakout, costs, WFO) | **Complete** |
| Multi-index paper trading (NIFTY, BANKNIFTY, SENSEX) | **Complete** |
| Aegis UI + FastAPI dashboard | **Complete** |
| Agent/skills operational layer + insights dashboard | **Complete** (proposals human-gated) |
| Live trading | **Dry-run / paper only** |

**Important:** `FORCE_DRY_RUN=true` by default. Live requires explicit `LIVE_TRADING_CONFIRMED` and manual approval.

---

## What This Project Does

- **3-index futures paper engine** — NIFTY (NFO), BankNifty (NFO), Sensex (BFO) front-month futures via `instruments_manager`
- **Previous Candle Breakout** — ATR buffers, volume/trend filters, regime detection, adaptive exits, session/expiry discipline
- **Risk-first execution** — Every order passes `RiskGatekeeper`; broker state is authoritative; fail-closed by design
- **Aegis dashboard** — Walk-forward optimization, promotion gates, backtest jobs, risk monitoring, trading journal, options sheet
- **Learning loop** — Fill learning, rolling edge, promoted params overlays, failure-pattern mining (proposals only — human-gated)
- **Operational scripts** — Safe deploy, morning brief, daily review, weekly earn report, EOD data audit

---

## Architecture

```text
React Aegis UI (:5173 dev | :8050/ui built)
        │  REST + SSE (/api/*)
        ▼
FastAPI Dashboard (web/dashboard.py)  :8050
        │  shared process via run.py
        ▼
Trading Engine (app/main.py) — background thread
  3-index strategies → RegimeOrchestrator → FO rules → Risk gates
  → Order lifecycle → Kite execution (dry-run or live)
        │
   ┌────┴────┬──────────────┐
   ▼         ▼              ▼
Kite API   JSONL ledger   Parquet cache
```

All trading decisions pass through **RiskGatekeeper**. Strategy code does not place orders directly.

Parallel validation path: `backtesting/` — Kite/NSE/BSE EOD fetch → parquet cache → walk-forward optimization → promotion gates → `backtest_memory`.

---

## Running Modes

| Mode | Command | Notes |
|------|---------|-------|
| Paper (default) | `python run.py` | `FORCE_DRY_RUN=true` |
| Dev / closed market | `python run.py --dev` | Calendar bypass, sim prices |
| Live (gated) | `FORCE_DRY_RUN=false` + `LIVE_TRADING_CONFIRMED=true` | Explicit human approval required |
| Micro-live | `MICRO_LIVE_ENABLED` + `MICRO_LIVE_CONFIRMED` | Strict lot caps |
| Docker paper | `docker-compose -f docker-compose.paper.yml up --build` | No Postgres/Redis |
| Engine only | `PYTHONPATH=. python -m app.main` | No dashboard |

---

## Key Features

- **State machine** — PAPER_MODE, LIVE_MODE, CIRCUIT_BREAKER, EMERGENCY_HALT, and more
- **RiskGatekeeper** — Daily loss, drawdown CB, lot/qty validation, sizing, consecutive-loss reduction
- **Broker reconciliation** — Position sync, pending order tracking, circuit breaker on repeated failures
- **Backtesting** — Zerodha cost model, WFO, promotion gates, regime breakdown, Monte Carlo metrics
- **Data layer** — Kite WebSocket + REST, parquet cache (`data/historical_cache/`), NSE/BSE EOD audit
- **Grok skills** — 5 operational skills in `.grok/skills/fo-*` mirrored by `scripts/fo_*.py`
- **CI** — GitHub Actions: ruff, compile check, 298 unit tests, synthetic backtest smoke

---

## Trader Notes — Pain Points We Address

- Opening 30 minutes on Nifty is toxic for breakout systems (fake volume, auction imbalance).
- Last 15 minutes often sees violent gamma hedging by option writers.
- Expiry weeks destroy many "good on paper" strategies due to vol crush and pinning.
- Gross P&L backtests lie. Real Zerodha all-in round-turn + slippage on Nifty futures is rarely under ₹350–600 per lot.
- Rate limits kill research velocity — hence the parquet cache layer.

---

## Market Folklore & Calendar Effects

### Lunar cycles (full moon, new moon, quarter moons)

Many traders believe moon phases affect sentiment and volatility. In India, **Krishna Paksha** (waning) vs **Shukla Paksha** (waxing) are sometimes tied to market mood. Popular folklore:

- **New moon** → optimism, trend starts
- **Full moon** → pessimism, volatility, reversals
- **First / third quarter** → momentum build or consolidation

**What research actually shows:**

| Finding | Source |
|---------|--------|
| ~3–10% annualized return spread between new-moon and full-moon windows across international indices | [Dichev & Janes (2003)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=281665), [Yuan et al. (2006)](https://personal.lse.ac.uk/yuan/papers/lunar.pdf) |
| No consistent DJIA lunar pattern; full-moon effect often absent on replication | [Herbst (2007)](https://ideas.repec.org/a/kap/jbioec/v9y2007i1p1-18.html), [Keef & Khaled (2011)](https://ideas.repec.org/a/eee/empfin/v18y2011i1p56-63.html) |
| Lunar anomaly evidence is statistically fragile under robustness tests | [Kim & Shamsuddin (2023)](https://ideas.repec.org/a/eee/finana/v90y2023ics1057521923003575.html) |
| NIFTY 2008–2013: negligible mean-return differences; only Nifty/Energy pass 5% significance with tiny magnitudes | [Karamchandani et al. (2014)](https://ijarcsms.com/docs/paper/volume2/issue2/V2I2-0022.pdf) |
| Quarter moons: even less empirical support than new/full | [Fortanier thesis (2018)](https://content.meteoblue.com/assets/pdfs/20180708_NL_Stock-Returns_EN.pdf) |

**Consensus:** Not pure myth (several peer-reviewed papers find weak signals), but **far weaker and less reliable than folklore claims**. Daily effect sizes (~0.02%) are tiny vs daily volatility (~1%+). Transaction costs, slippage, and expiry-week confounds likely erase any edge on NIFTY futures.

**Project stance:** Document as behavioral calendar folklore. Optional metadata for research/backtests only — **never a primary signal** without out-of-sample validation on NIFTY with full transaction costs. Prioritize proven drivers: trend/vol regime, global risk, RBI, FII flows, expiry dynamics.

**Lunar calendar utility** (Indian market context):

```powershell
python scripts/lunar_calendar.py                              # Today's panchang + astronomical metadata
python scripts/lunar_calendar.py --date 2026-11-08            # Diwali Amavasya / Muhurat trading day
python scripts/lunar_calendar.py --from 2026-01-01 --to 2026-12-31   # Annual backtest enrichment index
python scripts/lunar_calendar.py --events --from 2026-01-01 --to 2026-12-31  # Amavasya/Purnima event list
```

Outputs JSON to `data/lunar_calendar.json` (single day) or `data/lunar_calendar/{year}.json` (range). Labels include:

- **Panchang (Mumbai sunrise):** Shukla/Krishna paksha, tithi, Amavasya, Purnima, Ekadashi, `is_amavasya_friday` (IIMB 2023 factor)
- **Astronomical (09:15 IST):** phase name, illumination, Dichev half-month bucket
- **Event windows:** ±3 and ±5 **trading-day** offsets around synodic new/full moon (Karamchandani 2014 NIFTY methodology)
- **Confound flags:** Muhurat trading, Holi/Diwali holidays, expiry-day cross-ref

Two calendar systems are emitted in parallel because Indian retail traders follow panchang Amavasya/Purnima while academic replication studies use astronomical syzygy — they can differ by 0–1 civil days.

---

## Core Principles

- Infrastructure before prediction
- Risk management before win rate
- Survivability before short-term profit
- Deterministic rules before black-box automation
- Broker state is authoritative
- Human oversight remains mandatory

---

## Risk Rules

Defaults (overridable via `config/strategy_config.yaml` and `.env`):

| Parameter | Default |
|-----------|---------|
| Force dry-run | `true` |
| Risk per trade | 0.35% |
| Daily loss limit | 2% |
| Max drawdown | 8% |
| Lot size | 65 (dynamic via `instruments_manager`) |
| Max lots | 4 |
| Max trades per day | 3 |

Defined in `RiskConfig` (`app/risk_gatekeeper.py`) and `config/strategy_config.yaml`.

---

## Strategy Logic

**Previous Candle Breakout** with ATR-based thresholds, volume confirmation, session filters (blocks 9:15–9:45 and after 15:15), and expiry-day caution.

**Long:** Market open → valid entry window → no position → price breaks above previous candle high + volume → RiskGatekeeper approves.

**Short:** Same logic below previous candle low.

**Exits:** Profit target, stop loss, risk gate veto, broker reconciliation failure.

Paper trading presets: `app/paper_trading_params.py` (`DEFAULT_PAPER_PARAMS`, `AGGRESSIVE_PAPER_PARAMS`).

---

## Post 90-Day Reality Check (Critical Lesson)

A 30-day run produced PF 4.67. The same logic on 90 days of real data produced **PF 0.43, -182% return, 869 trades**.

This is the single most important lesson:

- Original parameters were dangerously overfit to a short favorable regime.
- High trade frequency + no session discipline + fixed targets + no volatility filter = account destruction when regimes change.

The hardened `StrategyParams` in `backtesting/previous_candle_backtest_strategy.py` now includes strict session filters, ATR-based exits, hard trade caps, and trend/expiry caution.

**Recommendation:** Re-test across multiple contracts. Only consider paper trading after PF ≥ 1.8–2.0 after realistic costs over 6–12 months with max DD under 8–10%.

---

## Project Structure

```text
.
├── app/                    # Trading engine (68 modules)
│   ├── main.py             # Multi-index strategy loop
│   ├── risk_gatekeeper.py  # Sacred risk layer
│   ├── strategy.py         # Previous Candle Breakout (WS candle-first)
│   ├── greeks.py           # Black-Scholes index options Greeks
│   ├── market_context.py   # India VIX + FII/DII context
│   ├── agent_insights.py   # Promotion + WFO + proposals snapshot
│   ├── strategies/         # Iron condor + straddle proposals (research)
│   └── intelligence_loop.py, regime_orchestrator.py, ...
├── backtesting/            # WFO, costs, promotion, cache
├── web/                    # FastAPI dashboard + Jinja templates
├── frontend/               # React/TS Aegis UI (Vite)
├── scripts/                # Operational CLI (fo_*, promotion, audit)
├── tests/                  # 50 unittest modules (298 tests)
├── config/
│   └── strategy_config.yaml
├── docs/                   # BUILD_REFERENCE, DEV_TESTING_GUIDE, playbooks
├── .grok/skills/           # 5 Grok skills (fo-*)
├── run.py                  # Unified runner (primary entry)
├── generate_token.py       # Kite token management
└── requirements.txt
```

---

## Setup & Verification

```powershell
# Tests
$env:PYTHONPATH="."
python -m unittest discover -s tests -v

# Compile check
python -m compileall app backtesting tests generate_token.py config.py

# Sample backtests
python backtesting/example_backtest.py
python backtesting/run_real_strategy_backtest.py
python backtesting/examples/run_walk_forward_example.py
```

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `KITE_API_KEY` | Yes | Zerodha Kite API key |
| `KITE_API_SECRET` | Yes | Zerodha Kite API secret |
| `KITE_REDIRECT_URL` | Yes | OAuth redirect (default `http://127.0.0.1:8765/callback`) |
| `KITE_ACCESS_TOKEN` | For broker calls | Daily token via `generate_token.py` |
| `FORCE_DRY_RUN` | Recommended | Simulates all orders when `true` |
| `LIVE_TRADING_CONFIRMED` | For live | Explicit human gate |
| `USE_PROMOTED_PARAMS` | Optional | Apply WFA overlays from promotion gates |
| `RISK_CAPITAL` | Optional | Override default 10L capital |

See `.env.example` for EOD flatten, micro-live, and dev-mode variables.

---

## Aegis Ops (Flagship Tool)

One command center for research + operations:

```powershell
python scripts/algo_lab_ops.py preflight      # Morning readiness (status + compliance + data)
python scripts/algo_lab_ops.py status         # Token, market, WS, DB, promotion gates
python scripts/algo_lab_ops.py compliance     # Code-checkable SEBI checklist
python scripts/algo_lab_ops.py data-health    # Cache coverage + EOD audit
python scripts/algo_lab_ops.py wfo-status     # Promotion gates per index
python scripts/algo_lab_ops.py wfo-run --days 180 --cache-only  # Multi-index WFO
python scripts/algo_lab_ops.py lunar          # Today's panchang metadata
python scripts/algo_lab_ops.py insights       # Promotion + WFO + proposals snapshot
```

Dashboard ops API (when running `python run.py`):

```text
GET /api/ops/preflight   — morning readiness JSON
GET /api/ops/status      — token, market, WS, promotion
GET /api/ops/compliance  — SEBI checklist results
GET /api/agent/insights  — agent insights for Aegis /ui/insights
```

See [BUILD_CHECKLIST.md](BUILD_CHECKLIST.md) for the full phased build tracker.

---

## Operational Scripts

| Script | Purpose |
|--------|---------|
| `scripts/algo_lab_ops.py` | **Unified ops hub** — preflight, compliance, data-health, WFO status |
| `scripts/fo_safe_deploy.py` | Pre-deployment safety checklist |
| `scripts/fo_market_brief.py` | Morning market regime brief |
| `scripts/fo_daily_review.py` | End-of-day session quality report |
| `scripts/fo_weekly_earn_report.py` | Weekly improvement aggregation |
| `scripts/fo_failure_pattern_miner.py` | Retail failure pattern mining → proposals |
| `scripts/run_promotion_wfo.py` | Walk-forward + promotion gates (single index) |
| `scripts/run_multi_index_wfo.py` | WFO across NIFTY/BANKNIFTY/SENSEX |
| `scripts/eod_data_audit.py` | Cache vs NSE bhavcopy audit |
| `scripts/lunar_calendar.py` | Panchang + astronomical lunar metadata for backtest research |
| `scripts/fetch_market_context.py` | India VIX + FII/DII → `data/market_context.json` |
| `scripts/db_migrate.py` | Apply Postgres migrations when `DATABASE_URL` is set |

Each mirrors a Grok skill in `.grok/skills/`. See [AGENTS_AND_SKILLS.md](AGENTS_AND_SKILLS.md).

---

## Safety Model

Trading is blocked by: state machine veto, daily loss limit, max drawdown, open position, pending order, invalid quantity/symbol, max trades per day, broker reconciliation failure, or broker disconnection.

The system intentionally **fails closed**.

---

## Known Limitations

- Postgres dual-write available (`PERSISTENCE_BACKEND=dual`) but optional; Redis not wired
- Options modules (`options_*.py`, `strategies/*`) are research/proposal scaffolding — not live-traded
- Learning/agent layer proposes; humans still gate capital (`submit_wfo_candidate` human-gated)
- Multi-index WFO promotion not yet passed — do not size up until `wfo-status` is green
- Not production-ready for real money

---

## Documentation Index

| Doc | Audience |
|-----|----------|
| [docs/BUILD_REFERENCE.md](docs/BUILD_REFERENCE.md) | How to run everything |
| [MORNING_TRADING_GUIDE.md](MORNING_TRADING_GUIDE.md) | Daily ops |
| [docs/EXPIRY_GAMMA_CAUTION.md](docs/EXPIRY_GAMMA_CAUTION.md) | Expiry-day levels 0/1/2, cutoff, optional gamma proxy |
| [docs/DEV_TESTING_GUIDE.md](docs/DEV_TESTING_GUIDE.md) | Closed-market dev |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current vs target architecture |
| [VISION_AND_STRATEGY.md](VISION_AND_STRATEGY.md) | Founder vision |
| [AGENTS_AND_SKILLS.md](AGENTS_AND_SKILLS.md) | Grok skills playbook |
| [ROADMAP.md](ROADMAP.md) | Phase tracking |
| [KITE_INTEGRATION.md](KITE_INTEGRATION.md) | Broker integration |
| [COMPLIANCE.md](COMPLIANCE.md) | SEBI algo checklist + pre-live gates |
| [backtesting/DOCUMENTATION.md](backtesting/DOCUMENTATION.md) | Validation oracle |

---

## Security Notes

- Never commit `.env` or credentials.
- Keep `FORCE_DRY_RUN=true` unless live trading is explicitly approved.
- Treat broker reconciliation and audit logs as mandatory operational records.
- Rotate any credentials previously committed.

---

## Disclaimer

This project is for research and controlled development. It is not financial advice. Trading futures and options involves substantial risk. Do not deploy with real capital until the system has been validated with real historical data, extended paper trading, operational monitoring, and independent compliance review.