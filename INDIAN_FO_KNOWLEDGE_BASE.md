# Indian F&O Knowledge Base — Rules, Realities & Retail Failure Patterns

**Purpose**: The living, versioned brain of anti-patterns, market mechanics, costs, and hard rules that the agentic system must internalize and enforce. This is not generic education — it is operational, encodable knowledge that directly influences risk multipliers, filters, and strategy vetoes.

**Owner**: Failure Miner agent + founder review. Updated whenever new credible patterns or regulatory changes appear.

**Last Updated**: 2026-05 (initial seed from codebase review + Reddit/SEBI research)

---

## 1. Core Market Mechanics (Must Be Hard-Coded or Dynamically Queried)

### Index Lot Sizes (as of late 2025 / 2026 — verify live via instruments)
- NIFTY 50: 65
- BankNifty: 30
- Sensex: 20

**Rule**: Never hardcode these in strategy or risk code after Phase 1. Always read from Kite instruments response.

### Expiry & Trading Calendar
- Major index F&O (Nifty, BankNifty): Weekly expiries on Thursdays (last Thursday of month is "expiry week" with higher pinning/gamma risk).
- Sensex F&O on BSE follows similar weekly structure.
- **Current strength**: `app/market_calendar.py` already has excellent 2026 NSE F&O holiday handling and safe-window logic. Extend it.

### Product Types (Critical)
- **MIS**: Intraday only, higher leverage, must square off by end of day.
- **NRML**: Carry forward allowed. Required for any overnight or multi-day thesis.

### Costs (Already Excellent in `backtesting/costs.py`)
- Brokerage: ₹20 or 0.03% per executed order.
- STT: 0.0125% on sell side only for futures.
- Other charges + slippage: ~₹55–90+ round-turn normal; much higher in toxic periods.
- **Golden Rule**: If your edge disappears at 2× modeled costs, you probably don't have a real edge.

### Margins & Risk
- SPAN + Exposure + Premium + Additional.
- Basket margin API gives net after offsets — essential for any options overlay.
- Naked short options have theoretically unlimited risk and terrible margin treatment.

### Options Specifics
- European style on indices (no early exercise).
- High liquidity concentrated in near-term, at-the-money to ~5–7% OTM strikes on weekly expiries.
- IV crush after events is brutal for long premium buyers.
- Theta decay accelerates dramatically in the final 2–3 days.

---

## 2. Retail Failure Patterns (The Real Alpha Source)

Sourced from repeated SEBI observations (91–93% of individual retail traders lose money in equity F&O) and thousands of Reddit loss confessions (r/IndianStockMarket, r/NSEbets, etc., 2024–2026).

### Tier 1 — Catastrophic (Encode as Hard Blocks First)
1. **No or broken risk management** — No hard SL, averaging down, oversized positions, carrying event risk overnight.
2. **Emotional & revenge trading** — Revenge after losses, FOMO entries, exiting winners early.
3. **Options lottery / IV blindness** — Buying deep OTM weeklies at high IV, naked short vol into events.

### Tier 2 — Death by 1000 Cuts
4. **Overtrading + weekly expiry gambling** — 10–50+ trades/day, especially on Thursdays.
5. **No systematic process** — No backtesting with real costs, no journal, following "sir" calls, ignoring regime.
6. **Fighting professionals with retail tools** — Manual phone trading vs HFTs/prop desks.

### Tier 3 — Scaling & Psychology
7. **Scaling too fast** — Works at small size, then larger size turns normal drawdowns into disasters.
8. **Lifestyle & isolation** — Constant screen time leads to boredom trades and burnout.

**How we use this catalog**:
- Hard blocks for Tier 1.
- Dynamic risk multipliers for Tier 2.
- The Failure Miner agent keeps this list alive with fresh evidence.

---

## 3. Positive Counter-Examples (What the Survivors Do)

- Strict daily loss limits and max trades per day (often 1–3).
- Small, consistent risk-reward ratios.
- Defined-risk structures only when trading options.
- Heavy use of journals and weekly reviews.
- Many eventually move to systematic/automated approaches.

---

## 4. Integration with the Rest of the System

This knowledge base is the primary input to the **Market Knowledge & Failure Miner** agent/skill.

Outputs feed directly into:
- RiskGatekeeper (new filters or multipliers)
- Strategy entry/exit conditions
- BacktestMemory documentation generator
- Morning brief skill

Version the file (or its structured JSONL export) so agents can track improvement over time.

---

**This document is not for reading once.** It is the contract the agents must honor.

Update ruthlessly. Cite sources when adding new patterns.