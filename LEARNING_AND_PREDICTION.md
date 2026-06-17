# Aegis — Learning & Prediction — The "Learn & Earn" Engine

**Purpose**: Defines how the system actually learns from candles, real outcomes, public failure data, and agent reasoning — and how (lightweight, gated) prediction feeds the sacred risk layer without ever replacing it.

**Last Updated**: 2026-05 (vision lock)

---

## Current State (Excellent Starting Point)

The project already has a real (if narrow) learning loop:

- `backtesting/backtest_memory.py`
  - Every walk-forward run is recorded as JSONL.
  - Auto-generates natural language `documentation_notes` with regime breakdowns and explicit confidence levels.
  - Only surfaces claims when sample size is meaningful.

- Real broker feedback loop (already powerful):
  - Dashboard endpoints for `kite.trades()`, `kite.orders()`, and real fills analysis.
  - Compares actual fills against our cost model → produces calibration notes that flow into memory.

**What it does well**: Honest, falsifiable, regime-aware documentation.

**What it lacks** (the vision gap):
- No public failure data (Reddit, etc.).
- No agent reasoning or proposals.
- Only one instrument and one strategy style.
- Prediction is almost non-existent (simple regime detection only).

---

## Target "Learn & Earn" Architecture

### 1. Multi-Source Learning Inputs
- Backtest WFA runs (all indices)
- Real Kite fills (/trades + /orders)
- Public failure data (Reddit, Varsity, SEBI)
- Agent reasoning & proposals
- Live paper outcomes
- Candle microstructure (WS OI, depth, volume profile)

### 2. Memory & Knowledge Base (The Brain)
- **Structured Layer**: `INDIAN_FO_KNOWLEDGE_BASE.md` + versioned JSONL of anti-patterns and rules.
- **Outcome Layer**: Evolution of `backtest_memory.jsonl` with richer, multi-source notes.
- **Queryable by Agents**: Future agents can retrieve relevant knowledge ("What do we know about BankNifty expiry weeks?").

### 3. Prediction Layer (Lightweight + Strictly Gated)
**Philosophy**: Prediction is never the decision maker. It is a *context provider* that the RiskGatekeeper can use to scale risk up or down (mostly down).

Initial prediction heads:
- Regime classifier (already partially exists)
- Short-horizon directional bias probability
- IV crush / event risk score
- "Retail failure pattern activation" score

**Gating Rules (Non-Negotiable)**:
- Predictions can only *reduce* risk or tighten filters.
- No prediction ever bypasses daily loss limits, drawdown breakers, or reconciliation.
- Every used prediction must be logged with confidence.

---

## "Earn" Definition (Beyond P&L)

The system earns when it produces **compounding, usable knowledge**:

1. Avoided loss documentation with concrete numbers.
2. Higher-quality regime notes with statistical backing.
3. Improved anti-pattern catalog.
4. Better strategy survival rate in validation.
5. Tighter cost model calibration.

---

## Implementation Path

- Phase 1 work (multi-index data) expands raw material for memory.
- Phase 2 introduces the first custom skills that read from and write to the knowledge base.
- Every new insight or pattern must go through founder review before becoming an active rule.

---

**This is the heart of the founder vision.**

We are building a system that systematically studies how retail traders get destroyed in Indian F&O, encodes that knowledge, and uses it — along with honest candle understanding and rigorous validation — to stay alive and compound edge.

The documentation it produces is a primary output and a core part of the edge.