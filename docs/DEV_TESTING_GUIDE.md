# Aegis — Closed-Market Development & Testing Guide

**Purpose**: Enable fast, safe, high-fidelity development and testing of the full trading engine (strategy logic, ATR, regime detection, risk gates, dashboard, logging, state machine, etc.) **without waiting for market hours**.

This guide documents every developer tool added in June 2026 for exactly this use case.

---

## Why This Matters

Retail algo developers waste enormous time waiting for market hours. The Aegis system now provides first-class support for closed-market development while preserving every safety guarantee around real capital.

All features below are:
- **Only active in paper/forced-dry-run mode**
- **Completely ignored in LIVE_MODE** (hard safety checks)
- Loudly announced at startup and written to the per-run diagnostic log

---

## The One Command You Should Use

```powershell
# PowerShell (recommended for Windows users)
$env:FORCE_DRY_RUN = "true"
python run.py --dev

# Or even shorter (the --dev flag sets FORCE_DRY_RUN for you)
python run.py --dev
```

This single flag gives you the best possible closed-market experience.

---

## All Available Developer Flags & Environment Variables

### Primary Flags (via `python run.py`)

| Flag                    | What It Does                                                                 | Recommended For |
|-------------------------|-------------------------------------------------------------------------------|-----------------|
| `--dev`, `-d`           | Master switch. Enables full testing mode + rich diagnostics                  | Daily development |
| `--sim-vol 1.8`         | Makes simulated prices move 1.8× more (or less)                              | Testing ATR / breakout sensitivity |
| `--fixed-time "..."`    | Forces the entire system to believe it is this specific IST time             | Testing time windows, expiry logic, daily reset |
| `--help-dev`            | Prints detailed help for all dev features                                     | Discovery |

### Environment Variables (can be used without `--dev`)

| Variable                        | Default | Effect |
|---------------------------------|---------|--------|
| `DEV_MODE=true`                 | false   | Same as `--dev` |
| `DEV_FORCE_MARKET_OPEN=true`    | false   | Bypasses `is_market_open()`, entry windows, safe windows (paper only) |
| `DEV_SIM_VOL_MULTIPLIER=1.8`    | 1.0     | Scales the random jitter in simulated price movement |
| `DEV_FIXED_SIM_TIME=...`        | —       | `"2026-06-02 11:45:00"` — makes `now_ist()` return this fixed moment |
| `DEV_USE_CACHED_VOL=true`       | false   | Seeds realistic ATR from your local `data/historical_cache/` files |
| `FORCE_DRY_RUN=true`            | true    | **Required** for any dev features to be active |

**Safety rule**: If `FORCE_DRY_RUN` is false (or LIVE_MODE is detected), **all** of the above are ignored.

---

## Detailed Feature Descriptions

### 1. `DEV_FORCE_MARKET_OPEN` + `--dev`

Located in: `app/market_calendar.py`

When active:
- `is_market_open()` → always returns `True` (in paper)
- `is_entry_window_open()` → always returns `True`
- `is_safe_trading_window()` → always returns `True`
- Strategy `_is_edge_case()` no longer rejects with "market_closed"

You will now see the **full** decision chain execute:
- `should_enter_long()` / `should_enter_short()`
- Regime detection + adaptive buffers
- RiskGatekeeper / MultiSymbolRiskManager checks
- Rich `[SIGNAL] PROPOSED_BUT_REJECTED_BY_GATES` logs (see earlier improvements)

**Visible output**:
```
🧪  DEV_FORCE_MARKET_OPEN=true  —  Calendar checks bypassed for testing
```

### 2. `DEV_SIM_VOL_MULTIPLIER` + `--sim-vol`

Located in: `app/strategy.py` (simulation fallback in `_get_current_price`)

Use this when the default simulation feels too quiet for testing breakout logic.

Examples:
```powershell
python run.py --dev --sim-vol 2.0
# or
$env:DEV_SIM_VOL_MULTIPLIER = "2.5"; python run.py --dev
```

Higher values = faster ATR changes, more regime flips, more frequent "proposed" directions in the 3-index block.

Logged as:
```
[DEV] DEV_SIM_VOL_MULTIPLIER=2.00 active for BANKNIFTY26JUNFUT (jitter scaled by 2.00x)
```

### 3. `DEV_FIXED_SIM_TIME` + `--fixed-time`

Located in: `app/market_calendar.py` (`now_ist()` override) + `get_market_status()`

This is one of the most powerful testing tools.

```powershell
python run.py --dev --fixed-time "2026-06-02 11:30:00"
```

Now every part of the system (strategy session filters, risk daily reset logic, dashboard market status rail, expiry checks, etc.) will behave exactly as if it is 11:30 IST on that date.

Perfect for:
- Verifying `paper_params.session_start` / `session_end` behavior
- Testing expiry day cutoff logic without waiting for actual expiry
- Reproducing a specific bug that only happens at 14:55
- Testing daily reset at midnight transitions

The dashboard and terminal will clearly show:
```
ist_time: 2026-06-02 11:30:00 (FIXED for testing)
```

### 4. Realistic Volatility from Local Cache (`DEV_USE_CACHED_VOL`)

Located in: `app/strategy.py` (`_try_load_realistic_vol_from_cache`)

When you have real historical cache files (from previous backtest runs or Aegis), this seeds `current_atr` and `fast_atr` with actual recent volatility for that specific index instead of generic defaults.

Activate with:
```powershell
$env:DEV_USE_CACHED_VOL = "true"
python run.py --dev
```

You will see in the diagnostic log:
```
[DEV] Loaded realistic vol from cache for SENSEX26JUNFUT: ATR≈68.4 (from SENSEX26JUNFUT_....parquet)
```

This dramatically improves the quality of closed-market testing for BankNifty and Sensex (whose natural point ATRs are much higher than Nifty's).

---

## Recommended Daily Development Workflows

### Workflow A – Quick UI / Logging / Risk Testing
```powershell
python run.py --dev
```
Then open http://localhost:8050 and watch the 3-index block + diagnostics.

### Workflow B – Strategy Logic + Parameter Tuning
```powershell
python run.py --dev --sim-vol 1.8
```
Focus on the latest `logs/run_*.log`. Search for `PROPOSED_BUT_REJECTED_BY_GATES`.

### Workflow C – Time-Sensitive Logic (Windows, Expiry, Resets)
```powershell
# Nifty weekly expiry Tuesday — Level 1 (soft caution, entries still allowed)
python run.py --dev --fixed-time "2026-06-16 10:30:00"

# Same Tuesday after noon — Level 2 (hard block on new entries)
python run.py --dev --fixed-time "2026-06-16 12:30:00"

# Monthly / other expiry dates
python run.py --dev --fixed-time "2026-06-26 09:50:00"
```

**What to verify for expiry / gamma caution** (see `docs/EXPIRY_GAMMA_CAUTION.md`):

| Check | 10:30 IST on expiry Tuesday | 12:30 IST on expiry Tuesday |
|-------|----------------------------|----------------------------|
| `check_regime_gates()` (options) | `allowed: true`, `expiry_caution: true` | `allowed: false`, reason contains *gamma caution* |
| Futures `_is_edge_case()` | No `expiry_day_safety` reject | `expiry_day_safety` reject |
| Options cycle (if enabled) | May propose IC when flat | `skipped: true`, `reason: regime_gate` → ledger `options.cycle.skip` |
| Dashboard Regime gates badge | **Caution** (amber) | **Blocked** (red) |

Unit tests: `tests/test_options_execution.py` (expiry morning vs after-cutoff).

### Workflow D – High-Fidelity Validation (Best Practice)
1. Start the system with `--dev`
2. Go to http://localhost:8050/backtest
3. Use "Load from Local Cache" + Research Mode + multiple folds
4. Study the Market Learnings tab + regime notes

This exercises the **real** strategy code path with proper costs and walk-forward.

---

## Safety Guarantees (Do Not Remove)

Every dev feature contains multiple hard checks:

1. `market_calendar.py` — `is_market_open()` etc. explicitly check for `LIVE_MODE` via `state_machine` and refuse to lie.
2. `strategy.py` — Simulation path is only reached when not in `LIVE_MODE`.
3. `run.py` + `main.py` — All banners and env var application happen **before** any trading loop starts.
4. `risk_gatekeeper.py` — `force_dry_run` path still dominates even if calendar lies.

If you ever see a dev flag having effect while real orders could be sent, that is a bug — report it immediately.

---

## June 2026 Multi-Index Seeding Bug Fix (Critical for `--dev` Users)

Early testing with `python run.py --dev` revealed that BANKNIFTY and SENSEX instances were using **Nifty's** previous candle levels (`prev_high ≈ 23469`) instead of their own. This caused `should_enter_long/short` to always fail even when the UI showed "proposed LONG".

**Root cause**: Seeding logic ran in `__init__` (always for NIFTY), then `main.py` only swapped the symbol without re-seeding the per-symbol state (`prev_high`, `prev_low`, ATR buffers, etc.).

**Fix**:
- All seeding (Kite historical + optional cache ATR) now happens **inside** `_initialize_index_future(sym)` **after** the correct symbol + token are set.
- Strong INFO logging for every symbol's seeding result.
- Cache loader symbol matching was made robust (same normalization as MultiSymbolRiskManager).

After this fix, each of the three strategy instances maintains fully independent and correct breakout state.

If you ever see `prev_high`/`prev_low` values in a dev log that are obviously wrong for the symbol's current price level, share the log immediately.

---

## Adding New Dev Features (Contributor Guide)

When adding future closed-market testing helpers:

1. Prefix with `DEV_`
2. Gate behind `FORCE_DRY_RUN` + ( `DEV_FORCE_MARKET_OPEN` or `DEV_MODE` or `DEV_SESSION_ACTIVE` )
3. Add an explicit hard check against `SystemState.LIVE_MODE`
4. Print a clear banner at startup in `main.py`
5. Document in this file + update the argparse epilog in `run.py`
6. Log the activation to the diagnostic run log at INFO level

---

## Quick Reference Card

```powershell
# Best daily command
python run.py --dev

# Lively prices + fixed afternoon time
python run.py --dev --sim-vol 2.2 --fixed-time "2026-06-02 14:20:00"

# Use real cached volatility stats
$env:DEV_USE_CACHED_VOL="true"; python run.py --dev

# See all options
python run.py --help-dev
```

**You now have a professional-grade closed-market development environment.** Use it.

---

*Last updated: June 2026 (as part of Phase 0 closed-market DX improvements)*
