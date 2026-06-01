# NiftyFuturesAlgo

NiftyFuturesAlgo is a **deterministic, risk-first** algorithmic trading system for Indian index F&O (starting with Nifty Futures) using Zerodha Kite Connect.

> **2026 Founder Vision**: We are evolving this into a self-improving, agentic platform for **NIFTY, BankNifty, and Sensex in full F&O**. The system will deeply understand candles, learn from public retail failure patterns (Reddit etc.), make gated predictions, and operate a true "learn & earn" loop using Grok Build skills and agents — while protecting the existing RiskGatekeeper as sacred.
>
> Start here: [VISION_AND_STRATEGY.md](VISION_AND_STRATEGY.md) | [ROADMAP.md](ROADMAP.md) | [AGENTS_AND_SKILLS.md](AGENTS_AND_SKILLS.md) | [ARCHITECTURE.md](ARCHITECTURE.md) | [KITE_INTEGRATION.md](KITE_INTEGRATION.md) | [INDIAN_FO_KNOWLEDGE_BASE.md](INDIAN_FO_KNOWLEDGE_BASE.md) | [LEARNING_AND_PREDICTION.md](LEARNING_AND_PREDICTION.md)

The project is built as **trading infrastructure first** and strategy logic second. The focus is on survivability, operational reliability, strict risk controls, and auditability before any real capital is used.

## Current Status

- **Phase 1 – Core Infrastructure**: **Completed and hardened** (risk-first, state machine, reconciliation, calendar, audit, token, guarded execution).
- **Phase 2 – Strategy & Backtesting**: **Completed** (Previous Candle Breakout fully functional with real previous-candle seeding + rolling, dynamic risk sizing, no silent simulation in LIVE, realistic backtester with slippage/costs, metrics).
- **Edge Cases**: Extensively covered (see below + code comments).
- **Live Trading Readiness**: **Dry-run / paper only**. Full validation + manual approval required before any LIVE capital.

**Important**: This system is now a solid foundation. **Do not use real capital** until you have:
1. Valid historical backtests with real NIFTY futures data over multiple regimes **with the cost model enabled**.
2. Extended paper trading (weeks) with live data feed + real reconciliation.
3. Reviewed all audit logs and recon behavior.
4. Independent review of risk parameters and compliance.

### Trader Notes — Pain Points We Explicitly Addressed
- Opening 30 minutes on Nifty is toxic for breakout systems (fake volume, auction imbalance).
- Last 15 minutes often sees violent gamma hedging by option writers.
- Expiry weeks destroy many "good on paper" strategies due to vol crush + pinning.
- Gross P&L backtests are lying to you. Real Zerodha all-in round-turn + slippage on Nifty futures is rarely under ₹350–600 per lot.
- Rate limits + slow iteration kill research velocity — hence the cache layer.

### Running Paper Trading (Current Recommended Approach)

After the 90-day backtest reality check, we now have dedicated paper trading parameters:

```python
from app.paper_trading_params import DEFAULT_PAPER_PARAMS, AGGRESSIVE_PAPER_PARAMS
from app.strategy import PreviousCandleBreakoutStrategy

# Recommended for most people starting paper trading
strategy = PreviousCandleBreakoutStrategy(kite, paper_params=DEFAULT_PAPER_PARAMS)

# More active (only after you are comfortable)
# strategy = PreviousCandleBreakoutStrategy(kite, paper_params=AGGRESSIVE_PAPER_PARAMS)
```

See `app/paper_trading_params.py` for details on the presets.

## Core Principles

- Infrastructure before prediction
- Risk management before win rate
- Survivability before short-term profit
- Deterministic rules before black-box automation
- Broker state is authoritative
- Human oversight remains mandatory

## Key Features

- State Machine with clear modes (PAPER_MODE, LIVE_MODE, etc.)
- Central Risk Gatekeeper with daily loss limits, position tracking, and reconciliation
- Broker Reconciliation Service (syncs internal state with Zerodha)
- Dynamic active Nifty futures contract selection
- Previous Candle Breakout Strategy with real LTP
- Modular Backtesting Framework with metrics
- Guarded order placement with `force_dry_run` safety switch
- Audit logging for all important events

## Architecture

```text
Market Data (LTP + Dynamic Contract)
    |
    v
Strategy Layer (Previous Candle Breakout)
    |
    v
Risk Gatekeeper (All orders must pass here)
    |
    v
Execution Layer (Guarded orders)
    |
    v
Zerodha Kite Connect
    |
    v
Broker Reconciliation + Audit Log

```

All trading decisions must pass through the Risk Gatekeeper. Strategy code does not place orders directly.

## Main Modules

### `app/main.py`

Application entry point. It initializes Kite Connect, token handling, broker reconciliation, system state, and the active strategy loop.

### `app/state_machine.py`

Controls global trading state. Trading is allowed only in approved states such as `PAPER_MODE`, `TRADING_ENABLED`, or `LIVE_MODE`.

Important states include:

- `BOOTING`
- `PAPER_MODE`
- `LIVE_MODE`
- `TRADING_DISABLED`
- `CIRCUIT_BREAKER_TRIGGERED`
- `RECONCILIATION_FAILED`
- `BROKER_DISCONNECTED`
- `EMERGENCY_HALT`

### `app/risk_gatekeeper.py`

Central risk and compliance layer. This is the most important module in the project.

It handles:

- Daily loss limit
- Max drawdown circuit breaker
- Lot-size validation
- Max order quantity
- Max trades per day
- Position conflict checks
- Pending order checks
- Risk-based position sizing
- Consecutive-loss risk reduction
- Dry-run simulation
- Guarded live order submission

Live orders do not update internal position immediately. They are tracked as pending until broker reconciliation confirms the actual broker state.

### `app/broker_reconciliation.py`

Compares internal state with Zerodha broker state.

It handles:

- Broker position sync
- Pending order status checks
- Rejected and cancelled order cleanup
- Reconciliation failure tracking
- Circuit breaker transition after repeated failures

### `app/strategy.py`

Contains the strategy framework and the current strategy implementation.

Current strategy:

```text
Previous Candle Breakout
```

The strategy checks:

- Previous candle high / low breakout
- Volume confirmation
- Entry window filters
- Expiry / edge-case filters
- Profit target
- Stop loss

The live strategy still has simulated fallback behavior for paper mode. In `LIVE_MODE`, failed LTP access is not silently simulated.

### `app/market_calendar.py`

NSE F&O market calendar utilities.

It handles:

- IST timezone normalization
- Regular market hours
- Entry window checks
- 2026 NSE F&O trading holidays
- Muhurat trading date placeholder until official timings are published

### `app/token_manager.py`

Handles Kite access token setup and expiry hooks.

### `app/audit_logger.py`

Writes JSONL audit events for important runtime actions such as:

- Blocked orders
- Dry-run orders
- Submitted orders
- Failed orders
- Order status updates
- Broker position sync

Runtime audit output is written under `data/`, which is intentionally ignored by Git.

### `backtesting/`

Backtesting framework for strategy validation.

Important files:

- `backtesting/backtester.py` - futures-style backtesting engine
- `backtesting/previous_candle_backtest_strategy.py` - deterministic backtest strategy
- `backtesting/metrics.py` - performance metrics
- `backtesting/example_backtest.py` - simple generated-data example
- `backtesting/run_real_strategy_backtest.py` - parameter tuning example

Current backtests use generated sample data. Real historical NIFTY futures data ingestion is still required before strategy conclusions are meaningful.

## Risk Rules

Default risk posture:

- Force dry-run enabled by default
- Risk per trade: `0.5%`
- Daily loss limit: `2%`
- Max drawdown limit: `8%`
- Lot size: `75`
- Max lots: `4`
- Max order quantity: `300`
- Max trades per day: `3`
- Risk is reduced after consecutive losses

These values are defined in `RiskConfig` inside `app/risk_gatekeeper.py`.

## Strategy Logic

Current strategy: Previous Candle Breakout.

Basic long setup:

1. Market is open.
2. Entry window is valid.
3. No existing position.
4. Price breaks above previous candle high.
5. Volume confirms the breakout.
6. Risk Gatekeeper approves the order.

Basic short setup:

1. Market is open.
2. Entry window is valid.
3. No existing position.
4. Price breaks below previous candle low.
5. Volume confirms the breakout.
6. Risk Gatekeeper approves the order.

Exit conditions:

- Profit target hit
- Stop loss hit
- Risk gate or system state blocks further trading
- Broker reconciliation detects unsafe state

## Safety Model

The system intentionally fails closed.

Trading can be blocked by:

- State machine veto
- Daily loss limit
- Max drawdown limit
- Existing open position
- Pending order
- Invalid quantity
- Invalid lot size
- Invalid symbol
- Invalid product type
- Max trades per day
- Broker reconciliation failure
- Broker disconnection

## Project Structure

```text
.
|-- app/
|   |-- __init__.py          # package init + version
|   |-- audit_logger.py
|   |-- broker_reconciliation.py
|   |-- main.py
|   |-- market_calendar.py
|   |-- risk_gatekeeper.py
|   |-- state_machine.py
|   |-- strategy.py
|   |-- token_manager.py
|   `-- retrieving_token.py  # legacy token helper (prefer generate_token.py)
|-- backtesting/
|   |-- backtester.py
|   |-- metrics.py
|   |-- previous_candle_backtest_strategy.py
|   `-- run_real_strategy_backtest.py
|-- tests/
|   |-- test_market_calendar.py
|   `-- test_risk_gatekeeper.py
|-- config.py
|-- docker-compose.yml
|-- Dockerfile
|-- generate_token.py
|-- requirements.txt
`-- README.md
```

## Setup

### 1. Create environment file

Copy `.env.example` to `.env` and fill in local credentials:

```bash
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
KITE_REFRESH_TOKEN=
FORCE_DRY_RUN=true
```

Keep `.env` out of Git.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Generate Kite access token

```bash
python generate_token.py
```

This updates `.env` with the latest token values.

### 4. Run tests

```bash
python -m unittest discover -s tests -v
```

### 5. Run sample backtests

```bash
PYTHONPATH=. python backtesting/example_backtest.py
PYTHONPATH=. python backtesting/run_real_strategy_backtest.py
```

### 6. Run the main driver (paper)

```bash
PYTHONPATH=. python -m app.main
```

### 7. Run with Docker

```bash
docker-compose up --build
```

**Always run from the repository root** with PYTHONPATH=. for clean imports and relative package support.

## Environment Variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `KITE_API_KEY` | Yes | Zerodha Kite API key |
| `KITE_API_SECRET` | Yes | Zerodha Kite API secret |
| `KITE_ACCESS_TOKEN` | For broker calls | Daily Kite access token |
| `KITE_REFRESH_TOKEN` | Optional | Token refresh support |
| `FORCE_DRY_RUN` | Recommended | Keeps all orders simulated when `true` |

## Verification Commands

Use these before pushing or running the app (from project root):

```bash
PYTHONPATH=. python -m compileall app backtesting tests generate_token.py config.py
PYTHONPATH=. python -m unittest discover -s tests -v
PYTHONPATH=. python backtesting/example_backtest.py
PYTHONPATH=. python backtesting/run_real_strategy_backtest.py
```

For the main app (paper mode, requires valid .env tokens):

```bash
PYTHONPATH=. python -m app.main
```

**Note**: backtest scripts that require real Kite data will fall back to synthetic when token missing/invalid or outside market hours — this is intentional graceful behavior.

## Completed Improvements (This Iteration — Trader-Driven)

**High Priority Items Closed (per detailed review):**
- **Realistic transaction costs + slippage (biggest single gap)**: New `backtesting/costs.py` with Zerodha Nifty FUT modeling (flat ₹20 brokerage + statutory + realistic 3–4+ pt slippage round-turn). A previous gross PF 4.67 on 30 days now gets properly compressed. Every serious backtest must run with the cost model enabled.
- **Session time filters**: Signals blocked 9:15–9:45 (opening noise) and after 15:15 (gamma hedging / fakeouts). Both live strategy and backtest strategy respect this.
- **Expiry day special logic**: On/near last Thursday, new entries are blocked or heavily de-risked after ~13:45. This is a real-world pain point most retail systems ignore until it hurts them.
- **Data caching layer** (`backtesting/data_cache.py`): Parquet (preferred) or CSV local cache. 90–180 day multi-expiry studies are now fast and kind to your Kite rate limits.
- **Longer history default**: The main real-data runner now defaults to 90 days + cache.

**Other Hardening:**
- Previous candle breakout is now actually functional (seeded + rolled).
- No silent simulation in LIVE (DataFeedError + state transition).
- Daily risk reset, dynamic sizing, proper imports/package, recon robustness, etc. (carried forward and improved).

**Post 90-Day Reality Check (Critical Lesson)**
A 30-day run produced PF 4.67. The same logic on 90 days of real data produced **PF 0.43, -182% return, 869 trades**.

This is the single most important lesson so far:
- The original parameters were dangerously overfit to a short favorable regime.
- High trade frequency + no session discipline + fixed targets + no volatility filter = recipe for account destruction when regimes change.

The hardened version in `backtesting/previous_candle_backtest_strategy.py` (with `StrategyParams`) now includes:
- Strict 10:00–15:00 IST session filter
- ATR-based breakout threshold + minimum volatility filter
- ATR-based profit targets and stops
- Hard cap of 2 trades per day
- Lightweight trend filter + expiry day caution
- Significantly lower risk per trade

**Current Recommendation**: Use the hardened `StrategyParams` defaults. Re-test across multiple contracts. Only consider paper trading after seeing acceptable net expectancy (PF ≥ 1.8–2.0 after realistic costs) over at least 6–12 months of data with max DD under 8–10%.

## Remaining / Future (Explicitly Not Hallucinated as Done)

- Real historical NIFTY data ingestion & continuous contract building (scripts can fetch but analysis on your side).
- Postgres/Redis persistence for trades, signals, audit (provisioned in compose but unused).
- Full multi-strategy aggregator + voting (stub exists).
- Websocket / proper candle builder instead of polling + wall-time buckets.
- SEBI/compliance formal review & audit by professional.
- Real-money production deployment checklist.

**Do not treat this as "ready for capital".** It is now a trustworthy *infrastructure* on which you can build validated strategies.

## Roadmap (Post-Completion)

1. Real historical NIFTY futures data pipelines + continuous futures adjustment for backtests.
2. Postgres persistence layer (trades, orders, daily equity snapshots, recon events).
3. Websocket-based live candle builder + proper volume feed for strategy.
4. Paper trading dashboard/reports + daily P&L attribution.
5. Explicit "enable live" human gate (file or UI flag) + kill switch.
6. Multi-strategy aggregator with confidence voting (after single strategy proven over real data).
7. Full compliance documentation & third-party review.

## Security Notes

- Never commit `.env`.
- Rotate any Kite credentials that were previously committed.
- Keep `FORCE_DRY_RUN=true` unless live trading is explicitly approved.
- Treat broker reconciliation as mandatory, not optional.
- Treat audit logs as operational records.

## Disclaimer

This project is for research and controlled development. It is not financial advice. Trading futures and options involves substantial risk. Do not deploy with real capital until the system has been validated with real historical data, extended paper trading, operational monitoring, and independent compliance review.
