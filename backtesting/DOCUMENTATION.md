# Aegis — Backtesting Documentation

**Professional Walk-Forward Analysis & Regime-Aware Validation Framework**

**Version**: May 2026 (Final Polished Release with Statistical Power Guardrails)  
**Purpose**: This document explains the complete backtesting system so that any quant, developer, or serious trader can understand the methodology, techniques, architecture, and how to run meaningful validations.

---

## 1. Philosophy & Design Principles

This backtesting system was built after a real 90-day live/paper trading disaster (approximately -182% return). The core lessons that shaped every design decision:

- **Conservatism is survival** — Nifty 5-minute breakouts are mostly noise outside clear regimes.
- **Costs destroy most edges** — Especially asymmetric Indian F&O costs (STT on sell side only).
- **Small samples lie** — Never trust results with < 30–50 trades across folds.
- **Regimes matter more than parameters** — A strategy that works in high-vol trending markets can destroy capital in low-vol chop.
- **Honesty over hope** — The system must repeatedly tell you when results are statistically meaningless.
- **Research Mode ≠ Production** — Exploration and validation must be deliberately separated from live trading.

The system prioritizes **statistical validity and realism** over pretty numbers.

---

## 2. High-Level Architecture

```
GUI (web/templates/backtest.html)
        ↓
FastAPI Dashboard (web/dashboard.py)
        ↓
Walk-Forward Runner (backtesting/walk_forward_runner.py)
        ↓
Backtester + Strategy (backtesting/backtester.py + previous_candle_backtest_strategy.py)
        ↓
Cost Model (backtesting/costs.py)
        ↓
Data Layer (backtesting/data_loader.py + synthetic generator)
        ↓
Metrics + MC + Memory (metrics.py, backtest_memory.py)
```

**Key Singletons Shared with Live Trading**:
- RiskGatekeeper
- StateMachine
- Market Calendar

This ensures backtest assumptions stay as close as possible to live reality.

---

## 3. Core Strategy: Previous Candle Breakout

**File**: `backtesting/previous_candle_backtest_strategy.py`

### Philosophy
- Only trade breakouts that follow a **meaningful previous candle** (range + volume).
- Demand volatility confirmation before risking capital.
- Use ATR-adaptive exits (never fixed points).
- Strict session discipline (avoid 9:15–10:00 and 15:00–15:30).
- Hard limits on daily trades and expiry day exposure.

### Key Parameters (StrategyParams)

| Parameter                    | Default (Production) | Research Mode Relaxation      | Why It Matters |
|-----------------------------|----------------------|-------------------------------|---------------|
| `session_start / end`       | 10:00 – 15:00       | 9:15 – 15:30                 | Avoids toxic opening/closing noise |
| `breakout_atr_mult`         | 0.85                | Down to ~0.50                | Controls how strong the breakout must be |
| `min_prev_candle_range_atr` | 0.55                | Down to ~0.28                | Filters dead candles |
| `volume_mult`               | 1.15                | Slightly relaxed             | Volume confirmation |
| `max_trades_per_day`        | 2–3                 | Up to 10                     | Prevents over-trading |
| `profit_target_atr_mult`    | 2.0                 | Same                         | Adaptive target |
| `stop_loss_atr_mult`        | 1.1                 | Same                         | Adaptive stop |
| `use_trend_filter`          | True                | Optional                     | Only trade in direction of higher-timeframe bias |
| `research_mode`             | False               | True                         | **Critical flag** — see below |

### Research Mode
When `research_mode=True` (only for backtesting):

- Session widened dramatically
- Volatility and range filters relaxed
- `max_trades_per_day` increased significantly
- Expiry day trading allowed

**Warning**: This flag **must never** be enabled in live or paper trading.

### Next-Bar Execution (`entry_on_next_bar`)
Realistic modeling: the signal is generated on bar `t`, but execution happens on bar `t+1`. This avoids look-ahead bias on the breakout bar itself.

---

## 4. Walk-Forward Analysis (WFA) Methodology

**File**: `backtesting/walk_forward_runner.py`

### Why WFA?
Simple in-sample optimization lies. Walk-forward gives a more honest estimate of how a strategy would have performed if re-optimized periodically.

### Process
1. Split data into `n_folds` (typically 4–5).
2. For each fold:
   - Train on first 60% of the fold window (optimize parameters).
   - Test on the remaining 40% (out-of-sample).
3. Aggregate results across folds.
4. Per-regime breakdown (low/normal/high volatility + trend direction).

### Regime Detection
Simple but effective:
- Volatility regime: ATR ratio vs slow moving average (low < 0.7, high > 1.4).
- Trend bias: Close vs 30-period MA.

### Scoring & Selection
- Primary: risk-adjusted return on test set.
- Penalty for folds that fail `min_trades_for_validity`.
- Best parameter set per fold is chosen, then applied to the test segment.

### Minimum Trades Guard
Folds with too few trades are flagged (`min_trades_met: false`). The system will still run, but results should be treated with extreme skepticism.

---

## 5. Transaction Cost & Slippage Modeling

**File**: `backtesting/costs.py`

This is one of the most important parts of the system.

### Indian F&O Reality (Zerodha/Kite as reference)
- Brokerage: ₹20 per executed order (or 0.03%, whichever lower) → usually flat ₹20.
- STT: 0.0125% on sell side only for futures.
- Other charges (txn, GST, SEBI, stamp): ~₹35–55 round-turn buffer.
- **Total round-turn cost per lot (conservative)**: ~₹55–90 before slippage.

### Slippage Model
- Base: 3.5 points (index points) for Nifty futures market orders.
- High-uncertainty multiplier: 2.0× during opening 30 min, expiry, news.
- Time-of-day awareness (in more advanced versions).

### Cost Multiplier (Sensitivity Analysis)
You can run the same backtest at 1×, 2×, and 3× costs. This is extremely revealing.

**Golden Rule**: If your edge disappears at 2× costs, you probably don't have a real edge.

---

## 6. Monte Carlo Simulation & GPU Acceleration

**File**: `backtesting/metrics.py`

### Purpose
Bootstrap resampling of actual trade P&Ls to understand the distribution of possible outcomes (not just the point estimate).

### Features
- 1000 simulations by default.
- Reports: mean, median, 5th/95th percentile return, average max drawdown.
- **GPU acceleration**: Uses PyTorch CUDA when available (detects `RTX 3050 Ti`, etc.).
- Graceful fallback to NumPy.

### Low-Sample Handling
If < 5 trades, it still runs but surfaces `low_sample_warning: true` and a clear message.

---

## 7. Data Layer & Caching (The Efficiency Engine)

**File**: `backtesting/data_loader.py`

### Local-First Philosophy
Repeated Kite calls are slow and quota-expensive. The system **always prefers** local Parquet cache.

### Two Cache Systems
1. `data/historical_cache/` — Smart overlapping cache (recommended).
2. `data/historical/` — Exact-range cache (legacy).

### Key Features
- `_load_overlapping_cached_data`: Scans all local files and merges any that overlap your requested window.
- Timezone normalization (IST-aware) to prevent comparison bugs.
- `force_refresh` flag to bypass cache and hit Kite.
- Explicit logging: `FULL CACHE HIT`, `smart_cache_or_kite`, etc.

### Available Cached Datasets Panel (GUI)
In the Aegis → Presets & Data tab, you can see exactly what local data you have, with date ranges, row counts, and file sizes.

---

## 8. Synthetic Data Generator (Fast Iteration)

Located inside `web/dashboard.py` (inside the backtest job).

### Why It Exists
When you just want to test the full pipeline (WFA + MC + costs + GPU reporting) in < 60 seconds without waiting for real data downloads.

### Characteristics
- Regime-switching volatility (low/normal/high + burst periods).
- Momentum + mean-reversion behavior at 5-min scale.
- Realistic volume spikes.
- Good enough for pipeline validation and parameter exploration when using Research Mode.

**Not a substitute** for real multi-year data when claiming robustness.

---

## 9. Learning & Memory Layer (Honest Documentation)

**File**: `backtesting/backtest_memory.py`

Every backtest run is recorded. The system auto-generates natural-language notes such as:

- `"[NORMAL VOL REGIME] Insufficient sample (X trades across Y runs). Ignore signals."`
- Cost calibration notes from real `/trades` data.
- Regime-specific observations with explicit confidence levels.

This prevents the common trap of "it worked in my backtest" becoming folklore.

---

## 10. How to Run Backtests

### Option A — GUI (Recommended for Daily Use)

1. Start the system:
   ```bash
   PYTHONPATH=. python run.py
   ```

2. Open http://localhost:8050/backtest

3. **Recommended Fast Validation Flow** (as of May 2026):
   - Go to **5. Presets & Data** tab.
   - Click **⚡ Load from Local Cache (Fastest)**.
   - Check **Quick Mode** (automatically enables Research Mode + Entry on Next Bar + forces synthetic + 1-fold).
   - Switch to Run tab → **RUN FULL VALIDATION**.
   - After completion, export the full professional report (JSON).

4. For serious validation:
   - Use real cached multi-month data.
   - Enable Research Mode manually.
   - Use 4–5 folds.
   - Review Learning Notes and per-regime breakdown.

### Option B — Programmatic (CLI / Scripts)

See `backtesting/examples/run_walk_forward_example.py`.

Basic pattern:

```python
from backtesting.walk_forward_runner import run_walk_forward
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.data_loader import fetch_real_nifty_futures_data

# Load data (will use local cache if available)
df = fetch_real_nifty_futures_data(kite, from_date, to_date)

results = run_walk_forward(
    strategy_class=PreviousCandleBacktestStrategy,
    data=df,
    param_grid={...},
    n_folds=5,
    cost_multiplier=1.0,
    research_mode=True,   # only for exploration
)
```

---

## 11. Recommended Professional Workflow

1. **Daily / Quick Exploration** → Quick Mode + Research Mode + Synthetic data.
2. **Weekly Validation** → Real cached data (last 4–8 months) + Research Mode + 5 folds.
3. **Monthly Deep Audit** → Multi-year real data + full cost sensitivity (1×/2×/3×) + Monte Carlo + regime notes review.
4. **Never** trust a strategy that only produces < 30–40 trades across the entire WFA.

Always export the full JSON report and store it with the date.

---

## 12. Common Pitfalls & How This System Addresses Them

- **Over-optimization** → WFA + out-of-sample per fold.
- **Ignoring costs** → Full realistic Indian F&O cost model + multipliers.
- **Small sample delusion** → Explicit warnings + min_trades guards.
- **Regime blindness** → Per-regime performance breakdown + learning notes.
- **Look-ahead bias** → Next-bar execution option.
- **Repeated expensive data fetches** → Local-first overlapping cache with force-refresh escape hatch.
- **UI/terminal noise** → Calm terminal + rich dashboard + explicit cache hit logging + Stop button.

## 13. Statistical Power Protection (Major Guardrail — May 2026 Polish)

The system now includes aggressive, hard-to-ignore protections against low-sample backtests:

### In the Results UI
- A **prominent hard "Statistical Power Warning" banner** appears automatically:
  - **< 20 trades** → Bright red "CRITICAL" banner with 🚨 icon (very visible).
  - **20–39 trades** → Strong amber warning.
  - **40–59 trades** → Caution note.
- When the warning is **Critical**, the **Export buttons (JSON & CSV) are disabled** and non-clickable. A clear message tells the user they must run with higher trade count first.

### In the Learnings Tab
- If the latest backtest had a critical statistical power warning, the warning banner is **automatically injected at the top** of the Learnings tab content when you switch to it.

### In Exported Reports
- Every JSON export now includes a top-level `statistical_power` object:
  ```json
  "statistical_power": {
    "total_trades": 6,
    "warning_level": "critical",
    "message": "...",
    "recommendation": "..."
  }
  ```

### Backend
- The result object from every backtest job always carries the `statistical_power` field.

These features were added as final professional polish so that users (and future auditors) cannot accidentally treat statistically weak results as actionable.

**Default Preset Behavior Change**
- The GUI no longer auto-forces "Current Live Params" on every page load. You now start with clean form defaults and can click the preset button when you want it. This makes toggling and experimentation much more natural.

**Research Mode Improvements**
- Further strengthened relaxation in research mode (max trades up to 10, wider session, lower breakout threshold, etc.) to help generate enough trades for meaningful validation.

**Improved Synthetic Data**
- The generator now includes realistic volatility clustering and volume spikes so Quick Mode + Research runs produce more tradable setups for pipeline testing.

---

## 14. Current Limitations (Honest)

- Top-level pooled Monte Carlo and cost sensitivity still have some data-shape limitations (trades are mostly kept per-fold for memory reasons).
- Synthetic data, while improved, is still not a perfect substitute for real multi-year data.
- GPU acceleration only helps Monte Carlo (not the core backtest loop yet).
- Export blocking and Learnings injection are client-side (currentResult based). Full server-enforced blocking is possible in future if needed.

---

**This is the full and final research-grade backtesting framework as of May 2026.**

Use it responsibly. The system is designed to tell you the truth — even when the truth is "you don't have enough data yet."

---

## 15. Accessing the System for Morning Market Trading (Baby Steps)

### Prerequisites (Do this once)
1. Have your `.env` file with valid `KITE_API_KEY` and `KITE_ACCESS_TOKEN`.
2. Make sure the latest code is pulled (`git pull` if using version control).
3. Recommended: Use a dedicated terminal window or tmux/screen for the runner.

### Baby Steps – Every Morning When Market Opens

**Step 1: Open your project terminal**
```bash
cd C:\Projects\NiftyFuturesAlgo     # or wherever your folder is
```

**Step 2: Start the unified system (one command)**
```bash
PYTHONPATH=. python run.py
```

- This starts **both** the trading engine (in background) and the web dashboard.
- You will see calm startup logs (we removed the noisy banners).
- It will say something like:
  ```
  [RUNNER] Aegis starting at ...
  [RISK] Gatekeeper ready...
  [MAIN] Paper trading engine initialized...
  [RUNNER] Ready. Dashboard: http://localhost:8050
  ```

**Step 3: Open the Dashboard in your browser**
- Go to: **http://localhost:8050**

You will see two main pages:
- **Main Terminal view** (`/`) – live risk, position, P&L, market status rail.
- **Aegis / Backtest** (`/backtest`) – for validation (you probably won't need this in the morning).

**Step 4: Quick Morning Safety Checks (in the Dashboard)**
- Confirm the top market rail shows **OPEN** + safe trading window (green/amber pill).
- Check **RiskGatekeeper** status (capital, daily loss so far, position = 0 is normal before first trade).
- Look at the **Reconciliation** status – it should say positions match (0 or whatever you expect).
- Verify your token is still valid (the dashboard will warn if not).

**Step 5: Monitor During the Day**
- The terminal will show important events at INFO level only.
- Most rejections and diagnostics are now at DEBUG (quiet terminal).
- Use the dashboard for live detailed view (much richer than terminal).
- If you ever want to stop a long backtest/validation job: Use the red **"STOP / CANCEL"** button in the Aegis.

**Step 6: Graceful Shutdown at End of Day**
- In the terminal where `run.py` is running, press **Ctrl + C**.
- It will trigger graceful shutdown, save state, and stop cleanly.

**Pro Tips for Morning**
- Start the system **before** 9:15 IST so it can warm up indicators and do first reconciliation.
- Keep the terminal window visible (or use tmux) so you see important messages.
- The new "Calm Terminal" design means you don't get flooded — rely on the dashboard for details.
- If something looks wrong, the first place to check is the **Risk** section and **Reconciliation** in the main dashboard.

---

*Document maintained alongside the code. Update when major methodology changes occur.*
