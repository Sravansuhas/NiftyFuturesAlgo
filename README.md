```markdown
# Nifty Futures Algo Trading System

A **deterministic, risk-first algorithmic trading infrastructure** for Indian F&O (Nifty Futures) built on Zerodha Kite Connect.

This project focuses on **survivability, reliability, and operational discipline** rather than chasing prediction accuracy. It is designed as a production-grade foundation for retail algorithmic trading with strict risk controls.

## Project Philosophy

Most retail trading systems fail due to poor risk management, weak infrastructure, and over-reliance on signals. This project follows a different approach:

- **Infrastructure First** — Build a robust, observable, and fail-safe system before focusing heavily on strategy logic.
- **Deterministic Execution** — AI/ML can assist with intelligence, but trade execution remains rule-based and auditable.
- **Survival Over Optimization** — The primary goal is long-term survival with positive expectancy, not maximizing short-term returns.
- **Gradual & Disciplined Rollout** — Backtest → Paper Trade → Micro Live → Scaled Live.

## Key Features

- **State Machine Architecture** — Clean state transitions (`PAPER_MODE`, `LIVE_MODE`, `RECONCILIATION_FAILED`, etc.)
- **Risk Gatekeeper** — Enforces position limits, daily loss limits, and prevents over-leveraging.
- **Broker Reconciliation** — Periodically syncs internal state with Zerodha to detect mismatches.
- **Automatic Dry-Run Mode** — Automatically switches between simulation and real orders based on market hours + holidays.
- **Token Management** — Handles access token expiry with graceful fallback.
- **Modular Strategy Framework** — Easy to build and test new strategies on top of the guarded execution layer.
- **Market Calendar** — Supports Indian market holidays and Muhurat Trading.
- **Observability** — Clear logging of positions, PnL simulation, and system state.

## Project Goals

| Goal | Status | Description |
|------|--------|-------------|
| Build fail-safe trading infrastructure | In Progress | Core risk & execution layer mostly complete |
| Support both Dry-Run and Live trading | Done | Automatic switching implemented |
| Enable proper paper trading validation | In Progress | TestStrategy with dynamic simulation available |
| Develop backtesting capability | Pending | Planned |
| Create multiple condition-based strategies | Pending | Base class ready |
| Achieve consistent positive expectancy | Long-term | After proper validation |
| Deploy on VPS with monitoring | Pending | Future phase |

## Current Architecture

```
app/
├── main.py                  # Main worker loop
├── strategy.py              # BaseStrategy + TestStrategy
├── risk_gatekeeper.py       # Risk rules, position tracking, guarded orders
├── broker_reconciliation.py # Sync with Zerodha
├── state_machine.py         # System state management
├── token_manager.py         # Access token handling & refresh
├── market_calendar.py       # Indian market holidays + Muhurat
├── config.py                # Configuration & credentials
└── requirements.txt
```

## Tech Stack

- **Broker API**: Zerodha Kite Connect (Python)
- **Language**: Python 3.12
- **Database**: PostgreSQL + Redis
- **Containerization**: Docker + Docker Compose
- **Architecture**: Event-driven with central deterministic risk layer

## Current Status (as of May 2026)

- Core infrastructure is stable and well-tested in dry-run mode.
- A working `TestStrategy` with profit target and stop loss is available.
- The system can automatically run in dry-run mode outside market hours.
- Strong emphasis has been placed on risk management and operational safety.

## Roadmap

### Phase 1: Infrastructure (Current Focus)
- [x] Risk Gatekeeper + Position Tracking
- [x] Reconciliation & Mismatch Detection
- [x] Token Management
- [x] Dry-Run / Live Mode Switching
- [ ] Improve logging and monitoring

### Phase 2: Strategy Development
- [ ] Build backtesting framework
- [ ] Develop condition-based strategies (price action, indicators)
- [ ] Walk-forward optimization & robustness testing

### Phase 3: Reliability & Live Preparation
- [ ] Real-time LTP integration
- [ ] Advanced order management & retries
- [ ] Telegram alerts & structured logging
- [ ] Minimum 3–4 weeks of clean paper trading

### Phase 4: Live Deployment
- [ ] VPS setup with static IP
- [ ] Gradual capital allocation
- [ ] Full audit logging & compliance

## Important Notes

- This system is still under active development.
- **Do not use real capital** until proper backtesting and extended paper trading has been completed.
- The current `TestStrategy` is meant for validation of the infrastructure, not for live trading.
- Always review and understand the risk parameters before enabling live mode.

## Getting Started (Development)

```bash
# Clone the repository
git clone https://github.com/sravansuhas/nifty-futures-algo.git
cd nifty-futures-algo

# Start the system
docker-compose up --build
```

For strategy testing:
```bash
python app/strategy.py
```

## License

This project is currently private and for personal learning & development.

---

**Disclaimer**: Trading in the stock market involves substantial risk of loss and is not suitable for everyone. Past performance is not indicative of future results. This project is for educational and research purposes only.
```

---
