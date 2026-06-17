# Aegis — Founder Vision & Strategy (v1.0)

**Status**: Vision locked — May 2026. This is the canonical reference for what we are building and why. All future work (agents, skills, code, backtests) must align to this.

**Last Updated**: 2026-05 (post full codebase review, Kite API research, Indian F&O retail failure analysis, and Grok Build capabilities audit).

---

## The Core Idea (Refreshed & Improved)

We are building a **self-improving, agentic algorithmic intelligence platform** for automated trading in the three major Indian index F&O markets:

- **NIFTY 50** (NSE)
- **BankNifty** (NSE)
- **Sensex** (BSE)

The system operates across **full F&O** (futures + options on these underlyings).

### Non-Negotiable DNA (Inherited & Strengthened from Current Project)
- **Risk-first, survivability-first, infrastructure-first**. The existing RiskGatekeeper, BrokerReconciliation (broker state is authoritative), StateMachine (fail-closed), audit logging, market calendar, and guarded execution are sacred. No new prediction, agent, or "smart" component may ever bypass or weaken them.
- **Human oversight remains mandatory**. There is always a human gate before real capital. Kill switches and emergency halts are first-class.
- **Honesty over hope**. Statistical power warnings, cost modeling, regime awareness, and "you don't have enough data yet" signals are features, not bugs.
- **Deterministic core with intelligence as a multiplier**. Hard rules and risk limits are never replaced by black boxes. Intelligence (predictions, strategy proposals, regime forecasts) only scales position size, tightens filters, or proposes candidates that still must survive the full existing validation rigour.

### What "Learn & Earn" Actually Means
Not vague ML. A closed, compounding loop:

1. **Mine public failure data** (Reddit threads, trader confessions, SEBI statistics, Zerodha Varsity lessons, market microstructure observations) → extract concrete, encodable anti-patterns.
2. **Understand candles deeply** (multi-timeframe, volume, OI proxies, volatility clustering, breakout quality, regime context).
3. **Predict risks and next-move probabilities** — but only as *gated inputs* to the risk layer (e.g., "high IV crush probability → reduce size 60% or block new shorts").
4. **Propose, validate, deploy, observe**:
   - Agents generate or mutate strategy ideas.
   - Every idea is brutally validated through the existing professional backtesting framework (WFA, realistic Zerodha costs, Monte Carlo, per-regime breakdown, statistical guards).
   - Survivors go to extended paper trading with full reconciliation and audit.
   - Real fills + paper outcomes flow back into permanent memory.
   - The system auto-generates documentation ("In high-vol trending regimes on BankNifty, previous-candle breakouts with volume confirmation show PF 2.1 after 2× costs across 4 folds. Retail failure pattern #3 (naked short vol into events) would have cost us X in the same window.").
5. **Earn** only after survival + positive expectancy + human approval for size increases. The primary output of "earn" is **better documentation and tighter anti-pattern defenses**, which compounds the edge over time.

The goal is not to be the highest-win-rate system. It is to be the system that **refuses to die where 91-93% of retail F&O traders lose money** (per repeated SEBI data), while still capturing asymmetric edge when the data genuinely supports it.

---

## Why This Project Exists (Founder Motivation)

The Indian index F&O market (especially weekly expiries on Nifty and BankNifty) is one of the most brutal retail wealth-transfer mechanisms in the world. Retail participants lose tens of thousands of crores annually. Most lose because of repeatable, avoidable patterns:

- Catastrophic risk management
- Emotional decision-making at 5-minute scale
- Complete ignorance of volatility, Greeks, and time decay when trading options
- Overtrading and lottery-ticket mentality
- Fighting institutions and algorithms with nothing but a phone and a gut feel

Existing "algo" projects in this space are usually either:
- Overfitted backtests that blow up live, or
- Black-box ML that cannot explain why it took a trade (fatal for risk and learning).

**Our differentiation**:
- We start with the hardest part already solved: industrial-grade risk, reconciliation, audit, and realistic validation infrastructure.
- We treat public knowledge of failure as a first-class data source (not just price candles).
- We use modern agentic tooling (Grok Build skills, subagents, design/implement/review loops, MCP GitHub) to make the *evolution* of the system itself systematic, auditable, and fast.
- We produce compounding documentation as a primary deliverable. The system gets smarter *and* explains what it has learned.

---

## Strategic Principles (2026+)

1. **Infrastructure before intelligence** (already true — protect it).
2. **Documentation before deployment**. No strategy or agent change ships without updated knowledge base entries and validation artifacts.
3. **Agents and skills are the velocity layer**. Custom fo-* skills and Grok subagents do the heavy lifting of research, proposal, and review. Humans (founder + reviewers) make the final calls on risk parameter changes and live capital.
4. **Hybrid, never pure black box**. Candle understanding + regime prediction + failure-pattern scoring feed the RiskGatekeeper; they do not replace it.
5. **Multi-instrument from day one in the new vision**. Nifty, BankNifty, and Sensex are peers. Options are tools for defined-risk overlays or volatility expressions, not naked gambling.
6. **Public failure data is alpha**. The patterns that destroy retail traders are well-documented on Reddit and in regulatory reports. We encode them explicitly.

---

## Initial Anti-Pattern Catalog (Seed — May 2026)

These are the first patterns the Failure Miner agent and knowledge base must encode as measurable filters or risk multipliers. Sourced from real trader loss threads and SEBI observations:

### Tier 1 — Catastrophic (Encode as Hard Blocks or Extreme De-Risk First)
1. **No or broken risk management**
   - No hard stop loss (mental stops are lies).
   - Averaging down / "it will come back".
   - Position size that can wipe out 20–50%+ of capital on one thesis failure.
   - Carrying large naked or poorly hedged positions into known events (budget, elections, Fed, RBI policy).

2. **Emotional & revenge trading**
   - Revenge trading after a big loss day ("I need to make it back today").
   - FOMO entries on green candles or "breakout" with no volume/context.
   - Exiting winners at first sign of profit while letting losers run hoping for breakeven.

3. **Options lottery / IV blindness**
   - Buying deep OTM weeklies ("10x potential") when IV is already elevated.
   - Selling naked premium into high-IV events without defined risk or hedges.
   - No understanding of breakeven, theta, or IV crush.

### Tier 2 — Account Death by 1000 Cuts (Strong Filters + Risk Multipliers)
4. **Overtrading + weekly expiry gambling**
   - 10–30+ round turns per day, especially Thu expiries.
   - Treating every 5-minute candle as a new "setup".
   - High transaction costs (slippage + STT asymmetry) quietly destroy any small edge.

5. **No systematic process or edge validation**
   - No backtesting with realistic costs.
   - No trading journal.
   - Following YouTube/Telegram "sir" calls without verification.
   - Ignoring regime (high-vol trending vs low-vol chop vs expiry pinning).

6. **Fighting professionals with retail tools**
   - Manual phone trading vs HFTs, prop desks, and institutions with order-book, GEX, and latency advantages.
   - Liquidity sweeps, fakeouts at 9:30–10:00 and 15:00–15:15 are engineered to harvest retail stops.

### Tier 3 — Scaling & Psychology (Longer-Term Memory)
7. **Scaling too fast**
   - 1–2 lots works → 10–20 lots feels the same until a normal 8% drawdown becomes "I might lose my house" money.
   - Psychological ceiling hits even previously profitable traders.

8. **Lifestyle & isolation**
   - Full-time screen staring leads to boredom trades.
   - Family/financial stress amplifies bad decisions.

**How we use this catalog**:
- Hard blocks for Tier 1 in the RiskGatekeeper or strategy entry conditions.
- Dynamic risk multipliers (0.3×–0.7×) for Tier 2 conditions.
- Documentation and "earn report" commentary when the system successfully avoided a known failure pattern.
- The Failure Miner agent keeps this list alive with fresh examples and confidence scores ("Seen in 180+ Reddit loss posts since 2024, SEBI FY25 data corroborates").

---

## Architecture Vision (High Level)

**Sacred Base Layer** (already ~95% built):
- RiskGatekeeper + BrokerReconciliation + StateMachine + Audit + MarketCalendar + Guarded Execution + Paper/Live separation + Restart persistence.

**Data & Candle Layer** (Phase 1):
- Dynamic multi-index instrument discovery via Kite (NFO + BSE F&O segments).
- WebSocket-based real candle + OI + depth builder (replaces/augments polling).
- Smart overlapping cache for historical + continuous futures.

**Intelligence Layer** (Phase 2 — Agentic):
- **Market Knowledge & Failure Miner** (skill + subagent): Mines Reddit, Varsity, SEBI, etc. Produces structured anti-patterns.
- **Candle & Regime Intelligence**: Rich features + lightweight probabilistic forecasts (regime, short-horizon bias, IV risk), always gated.
- **Strategy Generator & Planner**: Proposes candidates (or mutates existing). Routes every one through the full WFA + MC + cost + regime + statistical-power pipeline. Only survivors get paper deployment proposals.
- **Risk & Portfolio Predictor**: Cross-instrument (Nifty-BankNifty-Sensex correlations), options Greeks/margin simulation via Kite /margins/basket before any new position, drawdown path forecasting from MC.
- **Learning & Earn Orchestrator**: Extends backtest_memory.py into a full agent memory (with MCP or local RAG). Real fills (Kite /trades + /orders) + paper outcomes + agent-generated notes all feed the same documentation generator. Weekly "earn report" (what worked, what the Reddit failures would have cost us, proposed evolutions).
- **Deployment Skills**: /fo-safe-deploy (runs full checklist: recon clean, token valid, risk params reviewed, recent memory clean, human sign-off file), /morning-brief (runs market status + knowledge refresh + regime snapshot + "retail failure watchlist for today").
- All new agents/skills live in project `.grok/skills/fo-*` (project scope) or user scope. Use create-skill for scaffolding. Daily ops use slash commands or TUI.

**Human + Deployment Layer**:
- Existing world-class dashboard ("Aegis") extended with agent insights and knowledge browser.
- Custom skills: /fo-safe-deploy, /morning-brief, /failure-pattern-refresh, /propose-and-validate-strategy.
- Explicit human sign-off gates + kill switches.

All new intelligence funnels into (or is vetoed by) the RiskGatekeeper.

---

## How We Build & Deploy (Grok Build + Latest Methods)

This project will be a showcase of using Grok's agentic capabilities to evolve a high-stakes financial system safely:

- **plan-mode** for all major architecture and vision work (this document itself was produced this way).
- **/design** + design-doc-reviewer persona for every significant module or agent.
- **/implement --effort 3–5** for all code changes (multiple reviewers + security + tests + plan alignment + memory of past trading-system bugs).
- **/review** (and security-auditor) on any PR or local diff touching trading logic.
- **/execute-plan** for roadmap items once a design doc exists (parallel worktree implementations + mandatory orchestrator review).
- **best-of-n** for competing strategy ideas or parameter sets.
- **create-skill** + /skillify to capture emerging workflows ("every time I run a 4-month WFA on BankNifty I do X Y Z" → reusable /fo-validate-banknifty skill).
- **Subagents** (explore for market research, general with implementer persona for spikes) in parallel without polluting main context.
- **MCP grok_com_github** (already connected) for: creating issues from agent findings ("Reddit failure pattern X needs a filter"), opening PRs from generated patches, searching prior discussions.
- Agent-mode / ACP if integrating with IDE for deep coding sessions.
- The project itself becomes a showcase of Grok Build: its own evolution is largely done *through* these tools, producing auditable artifacts (design docs, review files, memory, PRs).

**Risk Posture (non-negotiable)**: Same as current README. No real capital until multi-regime, multi-instrument, cost-adjusted, agent-audited validation + extended paper + third-party (or at least peer) review. The new intelligence makes the system *smarter about when to stay flat*, not more aggressive.

---

## Success Criteria (Measurable)

**Vision Locked (this milestone)**: All canonical docs created/updated, internally consistent, match actual codebase, accurately reflect Kite + Grok capabilities + Reddit realities, and the founder can point to them and say "this is what we are building and why" without caveats.

**Phase 1 Complete**: Multi-index (3) + basic options awareness live in data/contract layer. Dynamic lot sizes from Kite. WS candle foundation. All changes produced via /implement + reviewers. Zero bypasses of RiskGatekeeper.

**Phase 2 Complete**: At least one working custom fo-* skill (e.g. /failure-pattern-miner or /morning-brief). First agent-proposed strategy candidate survives full WFA + MC with acceptable expectancy after 2× costs. Anti-pattern catalog has ≥6 concrete encoded rules actively reducing risk in paper runs. Memory system produces richer, agent-augmented documentation.

**Paper-Ready Gate**: 6–12+ months of real multi-contract, multi-index cached data across regimes. Max DD < 5–8% in cost-adjusted WFA. All three indices + at least one defined-risk options overlay behaving reasonably in paper with full recon and audit. Zero silent failures in 4+ weeks of live paper.

**Controlled Live Gate** (much later): Independent review (peer or professional), explicit human "enable live" file + capital size approval, kill switch tested, small size only, continuous monitoring + daily memory/docs review.

**Long-term Earn Signal**: The system can point to specific avoided losses ("Retail pattern #3 on BankNifty expiry week would have cost us ₹X; our filters blocked it") and show improving documentation quality and strategy survival rate over time.

---

## Risk Philosophy (Unchanged, Reinforced)

Trading futures and options involves substantial risk of loss. This system is research and controlled development only. Do not deploy real capital until the full validation gates above are met.

We are not trying to be the smartest system in the room. We are trying to be the most disciplined system that refuses to make the mistakes that destroy almost everyone else.

---

## Immediate Next Steps (Post Vision Lock)

1. Create the supporting canonical documents (ARCHITECTURE.md, KITE_INTEGRATION.md, INDIAN_FO_KNOWLEDGE_BASE.md, AGENTS_AND_SKILLS.md, LEARNING_AND_PREDICTION.md, ROADMAP.md).
2. Update README.md, PROJECT_STATUS.md, docs/PRD.md, backtesting/DOCUMENTATION.md, and MORNING_TRADING_GUIDE.md.
3. Open GitHub issues (via MCP or manual) for Phase 1 work items, tagged against this vision.
4. Begin Phase 1 execution using /design → /implement loops.

This is not a pivot. It is the logical, ambitious, founder-level evolution of the excellent foundation that already exists.

**We have the infrastructure. Now we build the intelligence that knows where retail dies — and refuses to go there.**

---

*Maintained by the founder. Update with every major vision or strategy shift. This document takes precedence over older PRD/roadmap fragments.*