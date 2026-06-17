# Aegis — Product Requirements Document (v0.4)

**Status**: Core infrastructure complete + major robustness gaps closed (costs, filters, caching, expiry handling). Not yet ready for extended paper with real capital.

## 1. Vision
A **deterministic, risk-first, fully auditable** algorithmic trading system for Nifty futures that survives long before it tries to make money.

Philosophy (non-negotiable):
- Infrastructure & survivability > prediction
- Broker state is always authoritative
- Every order must pass through the Risk Gatekeeper
- Human oversight remains mandatory
- Fail closed on any doubt

## 2. Current State (as of this revision)

**Well Covered (~85-90% of safety cases)**
- Risk Gatekeeper (daily loss, drawdown circuit breaker, dynamic sizing, loss streak de-risking, lot validation, guarded execution)
- Broker Reconciliation + position sync + mismatch handling
- State Machine (PAPER / LIVE / CIRCUIT / RECON_FAILED / EMERGENCY)
- Audit logging (JSONL)
- Dynamic front-month Nifty contract selection
- Real historical data backtesting with proper `date` key handling
- Realistic transaction cost + slippage modeling (new)
- Session time filters + expiry day caution (new)
- Local historical data caching (new)

**Major Gaps Closed in This Iteration**
- No more gross P&L backtests (costs now modeled)
- Opening noise and late-day gamma hedging periods filtered
- Expiry day risk explicitly reduced
- 90+ day studies now practical thanks to caching

**Still Missing / Future**
- Full multi-expiry, multi-regime validation (ongoing)
- Persistent trade/signal/recon ledger (SQLite foundation exists in plan)
- Production observability + alerting
- Websocket candle builder (polling is a known limitation)
- Formal compliance review

## 3. High-Priority Requirements (Done or In Progress)

See the main README "Gaps" section for the prioritized list that drove this work.

## 4. Non-Functional Requirements
- Deterministic behavior (no ML/black boxes in core decision path)
- Excellent audit trail for every blocked/submitted/reconciled event
- Graceful degradation on data feed loss in LIVE mode (raises, does not simulate)
- Rate-limit friendly (caching + sensible polling)

## 5. Risk Parameters (Current Defaults — Must Be Tuned)
See `app/risk_gatekeeper.py:RiskConfig`.

## 6. Success Criteria for "Extended Paper Ready"
1. ≥ 120–180 trading days of real 5min data across ≥ 3 expiries with cost-adjusted metrics.
2. Max DD < 4–5% in backtest + paper.
3. Win rate + expectancy stable across regimes after costs.
4. Zero silent failures in 4+ weeks of live paper with real reconciliation.
5. Full review of risk parameters and operational procedures by a second person.

## 7. References
- Official Kite Connect v3 docs: https://kite.trade/docs/connect/v3/
- pykiteconnect: https://github.com/zerodha/pykiteconnect
- Zerodha support articles on product types (MIS/NRML), market protection, etc.

---

*This document lives in the repo so it evolves with the code.*
