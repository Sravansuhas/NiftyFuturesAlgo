# Phase 0: Futures Foundation — Diagnostics & Logging Guide

**Goal of this phase:** Make NIFTY + BANKNIFTY + SENSEX futures paper trading **boringly reliable and fully diagnosable**.

Every time the user runs the system in paper mode, we generate rich, timestamped logs so that "just send me the latest log file" is enough for deep diagnosis.

---

## 1. How Logging Works Now (Best Practice)

### Per-Run Log Files
- Location: `logs/run_YYYYMMDD_HHMMSS.log`
- One file per execution of `run.py`
- Contains:
  - Every LTP fetch (REAL vs SIMULATED) with exact price, source, duration, token
  - Every ATR update (slow 5-min + fast tick-level)
  - Full signal decision context (the dict that decides ACCEPTED / REJECTED)
  - Every snapshot that feeds the dashboard and 3-index terminal
  - Risk checks
  - Kite API call timings (important for rate limit diagnosis)

### Why This Matters (Kite Best Practices Applied)
Kite docs strongly recommend:
- Logging every external call with timing (we now do this for `ltp()`).
- Having visibility into why decisions were made (we now dump full context dicts).
- Being able to replay what happened after the fact without asking the user 20 questions.

---

## 2. How to Share Logs With Me (Super Simple)

When something feels "hardcoded", "frozen", "stuck at 92%", or GUI is not updating:

1. Stop the run (Ctrl+C is fine).
2. Go to the `logs/` folder.
3. Send me the **most recent** `run_*.log` file (zip it if it's large).

I can then search the file for specific symbols, time ranges, price sequences, ATR evolution, signal rejections, etc.

---

## 3. Key Things Logged (Search These Strings in Logs)

| Search String | What It Tells You |
|---------------|-------------------|
| `[PRICE]` | Every single price fetch for every symbol. Look for gaps, repeated same price, long durations, or sudden jumps to simulation. |
| `[ATR]` | ATR updates (both slow and fast). This directly attacks the "all ATR = 32.0" complaint. |
| `[SIGNAL]` | Full decision context for every potential trade. Look at `buffer`, `vol_ok`, `atr`, `regime`, `risk_mult`. |
| `[SNAPSHOT]` | The exact dictionary sent to the GUI every cycle. This is the source of truth for what the cards should show. |
| `[RISK]` | Every risk gate evaluation. |
| `[KITE]` | Every actual call to Kite (timing + success/failure). Critical for rate limit and data freshness issues. |
| `Real data failed` | When we fall back to simulation (and why). |

---

## 4. Current Best Practices Implemented (from Kite Docs)

- All external `kite.ltp()` calls are now timed and logged.
- Simulation fallback is volatility-scaled per symbol (not a global magic number).
- We prefer fast tick-level ATR for display when the 5-min roll is stale.
- Dynamic position health confidence instead of entry-time signal confidence.
- Clean per-run logs instead of one giant growing file.
- Future path noted: WebSocket (MODE_LTP / MODE_QUOTE) is the official recommendation over polling. We are currently on the safe polling + excellent diagnostics path because of uvicorn + Twisted conflicts.

---

## 5. What "Rock Solid Futures" Looks Like (Success Criteria for Phase 0)

- Each of the 3 symbols shows independent, moving LTP in both terminal and GUI.
- ATR values differ between symbols and visibly react to price movement.
- Confidence for held positions changes over time (not stuck at 92%).
- Recent Trades table and Live Diagnostics update live via SSE (no manual refresh needed).
- When user sends one log file, I can explain exactly why a price didn't move, why a signal was rejected, or why ATR looked flat.

---

## 6. Next Steps After Phase 0

Once the above is boringly true for futures:

- Phase 1: Proper multi-symbol risk + full option chain support in `instruments_manager.py`
- Phase 2: Safe Options Buying strategy (signal on underlying, execute on ATM CE/PE)

---

**Latest improvements (this run):**
- Granular logging inside `should_enter_long/short` and multi-symbol risk decisions.
- Special SENSEX lag detection + warnings.
- GUI now clearly shows "pos health" for dynamic confidence and strength bars.
- WebSocket migration preparation comments added (Kite best practice path documented).
- At end of every run: exact cross-platform zip commands are printed.

**How to run with maximum diagnostics right now:**

```powershell
set FORCE_DRY_RUN=true
py run.py
```

You will immediately see:
```
[DIAG] Full diagnostic log for this run: logs\run_20260601_105550.log
```

Everything important (price fetches, ATR evolution, every signal decision with full context, snapshots, risk checks, Kite call timings) is now written to that file **in addition** to the terminal.

When you see anything strange (frozen price, stuck ATR, weird confidence, GUI not matching terminal), just stop the run and send me the latest file in the `logs/` folder.

This is the Kite-recommended + practical way to debug remote algo systems quickly.

---

This document will be updated as we complete Phase 0 milestones.