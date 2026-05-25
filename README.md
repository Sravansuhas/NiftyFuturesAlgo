# NiftyFuturesAlgo

Deterministic, risk-first algorithmic trading infrastructure for NIFTY futures on Zerodha Kite Connect.

This project is not trying to predict every market move. It is building the safety rails first: state control, risk gates, reconciliation, auditability, repeatable backtests, and strict human oversight before any live capital is allowed.

## PRD Status

Source of truth: `PRD_v1_4.pdf` reviewed on May 26, 2026.

Current scope:

- Single deterministic Previous Candle Breakout strategy
- Dry-run / paper-mode execution by default
- Dynamic NIFTY futures contract discovery
- Guarded order placement through a central risk gatekeeper
- Broker reconciliation against Zerodha positions and order statuses
- Futures-style backtesting with P&L-based equity accounting
- Audit log events for blocked, simulated, submitted, synced, and failed orders

Planned direction:

- Real historical NIFTY futures data ingestion
- Stronger paper trading validation
- Multi-strategy aggregator with deterministic voting
- Optional non-authoritative meta-filter only after robust evidence
- Persistent trade/state storage in Postgres

## Safety Posture

The system is intentionally fail-safe:

- `FORCE_DRY_RUN=true` by default
- No real order assumes a fill just because Zerodha returns an order ID
- Pending orders are reconciled through broker order/position state
- Trading is disabled by state machine veto, daily loss limit, drawdown limit, position conflicts, pending orders, lot-size validation, and max trade count
- Live mode should not be used until real-data backtests and extended paper trading are stable

## Core Philosophy

> Infrastructure > Prediction  
> Risk Management > Win Rate  
> Survivability > Short-term profits  
> Human oversight > autonomous capital control

## Tech Stack

- Python 3.12
- Zerodha Kite Connect API
- pandas / numpy for backtesting
- Docker / Docker Compose
- PostgreSQL and Redis services for upcoming persistence/runtime support

## Setup

Create `.env` from `.env.example`:

```bash
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
KITE_REFRESH_TOKEN=
FORCE_DRY_RUN=true
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

Run sample backtests:

```bash
python backtesting/example_backtest.py
python backtesting/run_real_strategy_backtest.py
```

Run the app in Docker:

```bash
docker-compose up --build
```

## Important Warning

This is a work-in-progress trading system. Do not use real capital until:

- previously exposed Kite credentials are rotated
- real historical data tests are complete
- paper-mode execution is stable over many sessions
- reconciliation and audit logs are reviewed
- live mode requires an explicit human decision
