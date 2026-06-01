# NiftyFuturesAlgo — Roadmap (Post Vision Lock)

**Version**: 1.1 — June 2026 (Updated after live paper runs)

**Current Active Focus (as of this log):**  
**Phase 0 — Futures Foundation + World-Class Diagnostics**

We are not advancing to options or big refactors until the 3 futures (NIFTY/BANKNIFTY/SENSEX) feel completely alive, independent, and trivially diagnosable in paper mode.

See the detailed working document: **[PHASE0_DIAGNOSTICS_AND_LOGGING.md](./PHASE0_DIAGNOSTICS_AND_LOGGING.md)**

This document explains:
- Per-run timestamped log files (`logs/run_*.log`)
- What is now logged on every price fetch, ATR update, signal decision, and snapshot
- Exact strings to search when debugging
- How to give me one file for fast root cause analysis

**Success criteria for closing Phase 0**:
- Each symbol shows independent moving LTP and ATR in both terminal and GUI.
- Confidence for open positions is dynamic (not stuck at 92%).
- User can run once, see something weird, stop, and just send the latest log file.
- I can diagnose 90%+ of issues from that single file + the 3-index block.

---

**Original Roadmap continues below (Phases are now strictly gated behind Phase 0 completion).**

---

## Phase 0 — Documentation & Vision Lock (Current — Immediate)

**Goal**: Proper founder documentation exists and is the single source of truth.

**Deliverables**:
- VISION_AND_STRATEGY.md (founder artifact with improved idea + anti-pattern seed)
- ARCHITECTURE.md (current vs target)
- KITE_INTEGRATION.md (full capabilities + current usage + migration)
- INDIAN_FO_KNOWLEDGE_BASE.md (mechanics + initial failure patterns with sources)
- AGENTS_AND_SKILLS.md (how we will actually build using Grok latest)
- LEARNING_AND_PREDICTION.md
- ROADMAP.md (this file)
- Major updates to:
  - README.md (new vision section + links)
  - PROJECT_STATUS.md (honest "Founder Vision Alignment" section)
  - docs/PRD.md (v0.5)
  - backtesting/DOCUMENTATION.md (position as validation oracle)
  - MORNING_TRADING_GUIDE.md (light agentic note)

**Success Gate**: Founder can point to VISION_AND_STRATEGY.md and the supporting docs and say "this is what we are building and how" with no major caveats. All docs cross-reference each other and the actual code.

---

## Phase 1 — Multi-Index Data & Contract Foundation (2–6 weeks)

**Goal**: The engine can work with NIFTY, BANKNIFTY, and SENSEX (futures first) using real Kite data, with no regression on existing Nifty paper behavior.

**Must-Have Deliverables**:
1. `instruments_manager.py` (or major extension of data_loader) that:
   - Loads instruments for NFO + relevant BSE segment.
   - Exposes dynamic active futures for the three indices.
   - Returns correct `lot_size`, `tick_size`, `instrument_token` per symbol.
2. RiskGatekeeper, PaperTradingParams, CostConfig, and strategy params become symbol-aware (or have clean per-symbol overrides).
3. WebSocket foundation (KiteTicker) for at minimum ltp + quote on the three indices + key strikes. Begin building proper candle + OI aggregator (replace or augment polling).
4. Market calendar extended for BankNifty/Sensex expiries and safe windows.
5. Dashboard shows multi-symbol status (even if only one is actively trading).
6. All changes produced via `/design` → `/implement --effort 3-4` (with security + tests + plan alignment reviewers) + memory updates.
7. Existing Nifty paper trading path remains 100% functional and untouched in behavior.

**Validation**:
- Can fetch and display correct 2026 lots (Nifty 65, BankNifty 30, Sensex 20) live from Kite.
- Can run a walk-forward on BankNifty using real cached data through the full pipeline.
- Zero bypasses of RiskGatekeeper introduced.

**Out of Scope for Phase 1**: Options trading, live capital on new indices, predictive agents.

---

## Phase 2 — Agentic Intelligence & Learning Layer (6–16 weeks)

**Goal**: The "learn & earn" brain exists and is producing value.

**Must-Have Deliverables**:
1. At least 2–3 working custom project skills (starting with `fo-failure-pattern-miner` and `fo-market-brief`).
2. Failure Miner can search public sources (Reddit via tools + Varsity/SEBI) and propose concrete, encodable anti-pattern updates that are reviewed and merged into INDIAN_FO_KNOWLEDGE_BASE.md + RiskGatekeeper.
3. Candle/Regime Intelligence module (initially lightweight) that consumes WS data and produces gated probabilities (regime, bias, IV risk).
4. Strategy Generator workflow: proposes candidates → routes every one through existing WFA + MC + cost + statistical power pipeline → only survivors become paper proposals.
5. Extended memory system (backtest_memory + structured knowledge) that incorporates agent notes and produces richer "earn reports".
6. Dashboard "Agent Insights" and "Knowledge Browser" sections (even if simple at first).
7. MCP GitHub usage: agents create issues for discovered patterns or proposed improvements.
8. Every major piece goes through full Grok rigor (/design + /implement with high effort + reviewers).

**Validation**:
- First agent-proposed filter or small strategy mutation survives full validation and is active in paper.
- System can point to a specific avoided retail failure pattern in recent memory/docs.
- Documentation quality measurably improves (more falsifiable, higher-confidence notes).

**Out of Scope**: Live trading on new instruments, complex options strategies, fully autonomous proposal acceptance.

---

## Phase 3 — Validation, Paper Expansion & Controlled Live Prep (Ongoing, Parallel with Phase 2)

**Goal**: The broadened system is rigorously validated before any real capital sees it.

**Key Gates** (from VISION):
- 6–12+ months real multi-contract, multi-index cached data across regimes.
- Cost-adjusted WFA (1x + 2x) + MC shows acceptable expectancy and controlled DD on the three indices.
- Extended paper trading (minimum 4–8 weeks live data) on Nifty + at least one additional index with full reconciliation, audit, and zero silent failures.
- At least one defined-risk options overlay idea has survived the same process.
- All risk parameters and new intelligence have been through founder + peer (or professional) review.
- Explicit human "enable live" mechanism + tested kill switch.

**Process**: Every expansion still uses the same agentic + review discipline.

---

## Phase 4 — Controlled Live + Continuous Self-Improvement (Long Term)

**Goal**: Small, disciplined live deployment with the system actively making itself better.

**Characteristics**:
- Live trading only after all Phase 3 gates.
- Very small size initially (1 lot or less on the strongest validated setups).
- Daily/weekly memory + documentation review by founder.
- Agent-proposed improvements continue, but human gate remains on any risk or capital change.
- The system becomes a compounding knowledge asset (documentation + encoded anti-patterns) in addition to any P&L.

---

## Non-Goals / Explicitly Out of Scope (for Foreseeable Future)

- Full black-box ML replacing the risk layer.
- High-frequency or sub-second execution (our edge thesis is regime + failure pattern awareness + discipline, not latency).
- Crypto, commodities, or stock options (focus on the three major indices).
- "Set and forget" autonomous live trading without human oversight.
- Any change that weakens the sacred RiskGatekeeper / reconciliation / audit core.

---

## How to Read This Roadmap

- Items are **gates**, not dates.
- Every phase assumes the previous one is solid (no skipping because "it feels good").
- The Grok Build tooling (skills, /implement loops, subagents, MCP) is the *process* for delivering each item — not an afterthought.
- Documentation (this file + the others created in Phase 0) must be updated before moving to the next phase.

---

**This is the founder-approved path.** Deviations require an explicit update to VISION_AND_STRATEGY.md and this document, created in plan mode.

We already have something rare: hardened, honest trading infrastructure. The roadmap is how we turn that into the system that knows where retail dies in F&O — and systematically refuses to follow them there.