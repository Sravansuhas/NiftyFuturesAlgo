# Aegis — Architecture (Current + Vision)

**Status**: v1.0 — May 2026 (post vision lock)

This document describes the actual current architecture (what exists and is hardened) and the target agentic architecture that realizes the founder vision.

---

## Current Architecture (May 2026 — Excellent Foundation)

The system is deliberately built **infrastructure-first**.

```
Market Data Sources
├── Kite instruments("NFO") → dynamic front-month NIFTY FUT selection
├── Kite historical_data (5m, with seeding) + time-bucket "candle" rolling
├── Kite ltp polling (5s in main loop) + occasional historical bootstrap
│
▼
Strategy Layer (PreviousCandleBreakoutStrategy)
├── ATR-based breakout + volume filter + trend filter
├── Regime detection (vol + lightweight trend)
├── Adaptive exits (ATR targets, trailing, breakeven, time stops)
├── Session discipline + expiry caution
│
▼
RiskGatekeeper (The Sacred Core — app/risk_gatekeeper.py)
├── Daily loss limit, max drawdown circuit breaker
├── Position sizing (risk % of capital, loss-streak de-risk)
├── Lot/quantity validation, max trades/day, pending order checks
├── Guarded order placement (dry-run simulation or real)
├── All orders **must** pass here. Strategy never places directly.
│
▼
BrokerReconciliation + StateMachine + Audit
├── Broker state is always authoritative
├── State transitions: BOOTING → PAPER_MODE / LIVE_MODE → CIRCUIT_BREAKER etc.
├── JSONL audit for every blocked/submitted/reconciled event
│
▼
Execution
├── Zerodha Kite Connect (orders, postbacks)
│
▼
Observability & Learning
├── Web dashboard ("Aegis" — 5 tabs: validation, results, market learnings, real fills, presets)
├── backtest_memory.py (auto-generates regime notes + confidence from every WFA run)
├── Real /trades + /orders analysis for cost calibration
├── Trade ledger + equity curve + diagnostics
│
Backtesting Path (Parallel, Shares Risk + Calendar)
├── data_loader (smart cache)
├── previous_candle_backtest_strategy (research_mode flag)
├── walk_forward_runner + regime detection
├── costs.py (realistic Indian F&O model)
├── metrics (MC + GPU)
└── feeds the same memory layer
```

**Key Strengths (Already 90%+ of what most projects never build)**:
- RiskGatekeeper + Reconciliation as single source of truth.
- No silent simulation in LIVE mode (DataFeedError + state transition).
- Professional validation (WFA, costs at 1x/2x/3x, statistical power guards that *block* weak results).
- Emerging "learn & document" loop from both backtests and real fills.
- Calm terminal + rich Bloomberg-style dashboard.
- Docker + unified runner + restart recovery.

**Current Limitations (Narrow Scope)**:
- Single instrument (NIFTY FUT only).
- Single deterministic strategy.
- Polling + wall-time candles (no true WS candle builder yet).
- No options awareness.
- No BankNifty / Sensex.
- Learning is backtest + fills only (no public failure mining, no agent proposals).
- Hardcoded lot assumptions in places (75).

---

## Target Architecture (Agentic F&O Intelligence Platform)

The goal is to add a **parallel intelligence layer** that proposes, validates, and improves — while the sacred risk/infra layer remains the final arbiter.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HUMAN + GOVERNANCE LAYER                         │
│  Founder / Reviewer  |  /fo-safe-deploy skill  |  Kill switches     │
│  GitHub MCP (issues, PRs from agents)                               │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  AGENTIC INTELLIGENCE LAYER (New)                   │
│                                                                     │
│  Market Knowledge & Failure Miner Skill/Agent                       │
│    • Mines Reddit, Varsity, SEBI, market microstructure             │
│    • Produces structured anti-patterns (encodable filters)          │
│                                                                     │
│  Candle & Regime Intelligence                                       │
│    • WS-based multi-timeframe candles + OI + depth                  │
│    • Regime classifier, short-horizon bias, IV crush risk           │
│    • Outputs: probabilities + confidence (gated only)               │
│                                                                     │
│  Strategy Generator & Planner (uses /design + best-of-n)            │
│    • Proposes mutations or entirely new strategies                  │
│    • Every candidate routed through full WFA + MC + costs           │
│                                                                     │
│  Risk & Portfolio Predictor                                         │
│    • Cross-index correlation (Nifty-BankNifty-Sensex)               │
│    • Pre-trade /margins/basket simulation for options               │
│    • Path-dependent risk forecasting                                │
│                                                                     │
│  Learning & Earn Orchestrator                                       │
│    • Extends backtest_memory into full agent memory + RAG           │
│    • Real fills + paper outcomes + agent notes → auto-docs          │
│    • Proposes evolutions (GitHub issues / PR drafts via MCP)        │
└─────────────────────────────────────────────────────────────────────┘
                                    │ (all proposals)
                                    ▼ (must pass or be vetoed)
┌─────────────────────────────────────────────────────────────────────┐
│                  SACRED INFRASTRUCTURE LAYER (Unchanged Core)       │
│                                                                     │
│  RiskGatekeeper (augmented with new multipliers from agents)        │
│  BrokerReconciliation (still source of truth)                       │
│  StateMachine + Audit + MarketCalendar (extended for 3 indices)     │
│  Guarded Execution (still the only path to orders)                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  DATA & EXECUTION LAYER (Expanded)                  │
│                                                                     │
│  Multi-Index Instrument Manager (NIFTY / BANKNIFTY / SENSEX)        │
│    • Dynamic lot_size, tick_size, active contracts from Kite        │
│                                                                     │
│  WebSocket Candle + OI Builder (new primary feed)                   │
│    • Proper 5m/15m candles, volume, OI delta, depth                 │
│                                                                     │
│  Historical + Cache (extended to 3 indices + options)               │
│                                                                     │
│  Kite Execution (orders, margins/basket, postbacks, /trades)        │
│                                                                     │
│  Paper Trading Presets (now symbol-aware)                           │
└─────────────────────────────────────────────────────────────────────┘

Backtesting Path remains the "validation oracle" for every agent proposal.
Dashboard extended with Agent Insights tab + Knowledge Browser.
```

**Key Invariant**:
Nothing in the new intelligence layer can place an order, change a risk limit, or bypass reconciliation without going through the existing RiskGatekeeper and human gates.

---

## Data Flow for "Learn & Earn"

1. Public data (Reddit etc.) + market data (Kite WS + historical) → Knowledge Miner + Candle Intelligence.
2. Intelligence outputs → Strategy proposals + risk adjustments.
3. All proposals → full backtest rigour (WFA + costs + MC + statistical power).
4. Survivors → paper deployment (still through RiskGatekeeper).
5. Real fills + paper outcomes + agent notes → Memory + auto-generated documentation.
6. Documentation + new patterns → updated filters + next round of proposals.
7. High-confidence improvements → GitHub issues / PRs via MCP for founder review.

---

## Technology & Tooling Choices (Aligned with Grok Build)

- Core language/runtime: Python (existing).
- Agentic evolution: Grok subagents + bundled skills (`/implement --effort 3-5`, `/design`, `/review`, `best-of-n`, `create-skill`).
- Custom skills: Project `.grok/skills/fo-*` (highest priority) + user scope for founder workflows.
- Memory: Extend `backtest_memory.jsonl` + structured knowledge base (this file + JSONL exports).
- GitHub automation: MCP grok_com_github for issues/PRs generated by agents.
- Dashboard: FastAPI + existing templates (extend, do not replace).

---

## Migration Principles

- Phase 0 (this session): Documentation lock (VISION, this file, KITE, KNOWLEDGE_BASE, AGENTS_AND_SKILLS, etc.).
- Phase 1: Data layer multi-index + WS + dynamic contracts (no behavior change for Nifty paper trading).
- Phase 2: Intelligence layer (agents + skills) — every change still goes through full Grok review loops.
- Phase 3: Validation gates + paper expansion.
- Never weaken the sacred layer.

---

This architecture lets us keep everything that already works (and is rare in this domain) while adding the intelligence and breadth the founder vision requires.

*Diagrams are intentionally simple ASCII for longevity. Detailed sequence diagrams can be added later if needed.*