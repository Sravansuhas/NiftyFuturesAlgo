# Aegis — Indian F&O Knowledge Base — Rules, Realities & Retail Failure Patterns

**Purpose**: The living, versioned brain of anti-patterns, market mechanics, costs, and hard rules that the agentic system must internalize and enforce. This is not generic education — it is operational, encodable knowledge that directly influences risk multipliers, filters, and strategy vetoes.

**Owner**: Failure Miner agent + founder review. Updated whenever new credible patterns or regulatory changes appear.

**Last Updated**: June 2026 (updated with latest lot sizes from NSE/BSE circulars, revised expiry schedules, STT rates effective April 2026, and SEBI FY25 retail loss study)

> **Important**: Market rules (lot sizes, STT, expiry days, margins) change periodically. Always cross-verify critical numbers with the latest exchange circulars or broker data before using in production risk/strategy logic.

---

## 1. Core Market Mechanics (Must Be Hard-Coded or Dynamically Queried)

### Index Lot Sizes (as of June 2026 — always verify live via instruments or NSE/BSE files)
- **NIFTY 50**: 65 (revised downward from 75 effective 30 Dec 2025)
- **BankNifty**: 30 (temporarily increased to 35 in April 2025, revised back to 30 from 30 Dec 2025)
- **Sensex (BSE)**: 20 (increased from 10 in late 2024/early 2025)

**Rule**: Never hardcode these in strategy or risk code after Phase 1. Always read from Kite instruments response or the daily NSE/BSE market lot files. Lot sizes are reviewed periodically by SEBI to maintain minimum contract value norms (~₹15 lakhs+).

### Expiry & Trading Calendar (as of 2026)
- **Nifty 50**: Weekly expiries continue (every Tuesday). Monthly contracts expire on the **last Tuesday** of the month.
- **BankNifty**: Weekly options were **discontinued** effective late 2024. Only monthly (and longer tenor) contracts remain. These expire on the **last Tuesday** of the month.
- **Sensex (BSE)**: Weekly expiries continue every **Thursday**. Monthly contracts expire on the **last Thursday** of the month.
- If the scheduled expiry day is a holiday, expiry shifts to the previous trading day.
- **Note**: Expiry pinning and gamma risk remain highest on weekly expiry days (especially Thursdays for Sensex and Tuesdays for Nifty).

**How Aegis encodes this (June 2026)** — see `docs/EXPIRY_GAMMA_CAUTION.md`:

| Level | Meaning | Enforcement |
|-------|---------|-------------|
| **0** | Normal session | No expiry flag |
| **1 Soft** | Expiry morning before cutoff (default 12:00 IST) | `FO_EXPIRY_DAY_DE_RISK` (0.5× size); options `expiry_caution` amber badge; defensive posture |
| **2 Hard** | After cutoff or legacy full-day block | Futures `expiry_day_safety`; options regime gate — *no new entries* |

**Not yet implemented**: portfolio net-gamma limits. Greeks in `app/greeks.py` support IC proposals and research; gating is **calendar + VIX proxy**, not live gamma aggregation.

**Current strength**: `app/market_calendar.py` already has excellent 2026 NSE F&O holiday handling and safe-window logic. Extend it for the revised Tuesday/Thursday schedules.

### Product Types (Critical)
- **MIS**: Intraday only, higher leverage, must square off by end of day.
- **NRML**: Carry forward allowed. Required for any overnight or multi-day thesis.

### Costs (Already Excellent in `backtesting/costs.py`)
- **Brokerage** (Zerodha and most discount brokers): ₹20 or 0.03% per executed order, whichever is lower (Futures). Flat ₹20 for Options.
- **STT (revised w.e.f. 1 April 2026)**:
  - Futures: **0.05%** on the sell side.
  - Options: 0.15% on premium (sell side) + 0.15% of intrinsic value on exercised options.
- Other charges (Transaction charges, SEBI fees, GST, Stamp Duty) + slippage: Typically ₹60–110+ round-turn in normal conditions; significantly higher during high-volatility or illiquid periods.
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

According to the latest comprehensive SEBI study (FY25 / April 2024 – March 2025, released July 2025):
- Approximately **91%** of individual (retail) traders in the equity derivatives (F&O) segment incurred net losses.
- Aggregate losses by individual traders: ~₹1.06 lakh crore (up 41% YoY).
- Average loss per loss-making trader: ~₹1.1 lakh.

This continues the long-standing pattern (previous studies showed 89–93% loss rates). SEBI-mandated risk disclosures on broker platforms now prominently state that a large majority of individual traders lose money in F&O.

Sourced additionally from thousands of Reddit loss confessions (r/IndianStockMarket, r/NSEbets, etc., 2024–2026).

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

### Structured Export (Recommended for Agents)
A machine-readable version is maintained at:
- `data/knowledge_base/indian_fo_knowledge.json`

This JSON contains:
- Current lot sizes
- Expiry rules
- Product types
- Costs & STT
- Options specifics
- Tiered retail failure patterns
- Positive behaviors

**Agents should load the JSON version** for runtime use (RiskGatekeeper filters, strategy vetoes, risk multipliers, etc.).

---

Outputs feed directly into:
- RiskGatekeeper (new filters or multipliers)
- Strategy entry/exit conditions
- BacktestMemory documentation generator
- Morning brief skill

Version both the Markdown and the JSON so agents can track improvement over time.

---

**This document is not for reading once.** It is the contract the agents must honor.

Update ruthlessly. Cite sources when adding new patterns.