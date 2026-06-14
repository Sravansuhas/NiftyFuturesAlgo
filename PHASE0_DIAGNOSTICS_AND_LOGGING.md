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

**Latest improvements (June 2026 session):**
- Granular logging inside `should_enter_long/short` and multi-symbol risk decisions.
- Special SENSEX lag detection + warnings.
- GUI now clearly shows "pos health" for dynamic confidence and strength bars.
- WebSocket migration preparation comments added (Kite best practice path documented).
- At end of every run: exact cross-platform zip commands are printed.
- **New:** When snapshot heuristic shows "proposed LONG/SHORT" but strategy stays flat, the run log now emits `PROPOSED_BUT_REJECTED_BY_GATES` with full context (current_atr, fast_atr, regime, risk_mult, prev_h/l, would_enter results). This directly attacks the "I see proposed in GUI but no trades" mystery.
- Unconditional rejection logging for the critical volatility filter (no more 30s throttling hiding root causes).
- Per-symbol fast ATR now scale-aware for BANKNIFTY (~2x) and SENSEX (~2.2x) point movement + explicit `[ATR]` logging on every 5-min roll and frequent fast ticks.

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

## 7. Closed-Market Development & Testing (Added June 2026)

**Goal**: You must be able to develop and meaningfully test the *entire* system (strategy decisions, ATR/regime, risk gates, dashboard, logging, state, restarts) while the real market is closed.

### The Recommended Command

```powershell
python run.py --dev
```

This is now the primary way to work on the system outside market hours.

### What `--dev` (and the supporting env vars) Actually Do

| Feature                        | Env Var / Flag                        | Effect | Where Documented |
|--------------------------------|---------------------------------------|--------|------------------|
| Bypass all calendar checks     | `DEV_FORCE_MARKET_OPEN` + `--dev`     | `is_market_open()`, entry windows, safe windows all return True (paper only) | `market_calendar.py`, `strategy.py:_is_edge_case` |
| Controllable simulation speed  | `DEV_SIM_VOL_MULTIPLIER` + `--sim-vol 1.8` | Makes simulated prices more/less lively for faster ATR & regime testing | `strategy.py` simulation fallback |
| Fixed synthetic time           | `DEV_FIXED_SIM_TIME` + `--fixed-time "..."` | `now_ist()` returns a fixed moment → reproducible testing of every time-based rule | `market_calendar.py:now_ist()` |
| Realistic ATR from your cache  | `DEV_USE_CACHED_VOL=true`             | Seeds `current_atr`/`fast_atr` from real historical parquet files for the current symbol | `strategy.py:_try_load_realistic_vol_from_cache` |
| Master switch                  | `--dev` / `-d` / `DEV_MODE=true`      | Activates the above + rich diagnostic banners | `run.py` |

**Hard safety rules (never bypass these):**
- None of the above ever activate if `FORCE_DRY_RUN` is false.
- All of them are explicitly disabled when the system detects `LIVE_MODE`.
- Every activation prints a large visible banner at startup and writes to the run log.

### Full Documentation

See the dedicated guide:

**[docs/DEV_TESTING_GUIDE.md](../docs/DEV_TESTING_GUIDE.md)**

It contains:
- Every flag and environment variable with examples
- Recommended daily workflows (A/B/C/D)
- How to test specific time windows / expiry logic / daily resets
- Safety guarantees and contributor rules for adding new dev features

### New Diagnostic Strings You Will See in Dev Mode

Search your `logs/run_*.log` for:

- `[DEV] DEV_SIM_VOL_MULTIPLIER=... active`
- `[DEV] Loaded realistic vol from cache for ...`
- `DEV_FORCE_MARKET_OPEN=true` banner
- `DEV_FIXED_SIM_TIME active`
- The usual `[SIGNAL] PROPOSED_BUT_REJECTED_BY_GATES` (now fires much more because calendar no longer blocks)

### Updating This Document

Whenever a new closed-market dev capability is added:
1. Update the table above
2. Add the new diagnostic search strings
3. Update `docs/DEV_TESTING_GUIDE.md`
4. Update the argparse epilog + `--help-dev` text in `run.py`
5. Add a clear startup banner in `app/main.py`

### June 2026 Multi-Index Seeding Fix (Driven by These Diagnostics)

Early `--dev` runs exposed that BANKNIFTY and SENSEX were using Nifty's `prev_high`/`prev_low`. The new `PROPOSED_BUT_REJECTED_BY_GATES` + `[SEED]` logging made the bug obvious in one log file.

**Fix**: Seeding now happens inside `_initialize_index_future(sym)` *after* the correct symbol is set. Each instance now has independent state. See `docs/DEV_TESTING_GUIDE.md` for details.

---

This document will be updated as we complete Phase 0 milestones.