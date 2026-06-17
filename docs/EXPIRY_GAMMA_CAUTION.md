# Expiry & Gamma Caution System

**Purpose**: Document how Aegis handles expiry-day risk for **futures breakout** and **automated iron condor** trading.

**Last updated**: June 2026

---

## What “gamma caution” means in *this* project

In retail F&O, **gamma** is the rate at which an option’s delta changes as spot moves. Near expiry, ATM options have very high gamma; dealer hedging can cause violent, pin-prone moves in the last hours of a weekly expiry.

**Aegis does not yet gate trades on live portfolio net gamma.** There is no running sum of structure gamma that blocks or sizes positions.

What we implement today (`app/expiry_risk.py`):

| Mechanism | What it is | Where |
|-----------|------------|--------|
| **Calendar cutoff** | Level 1 before cutoff hour; Level 2 after — blocks *new* IC entries | `app/expiry_risk.py`, `app/options_strategy_runner.py` |
| **Optional gamma proxy** | BS ATM gamma + OI concentration on expiry when `enable_gamma_proxy: true` | `app/expiry_risk.py`, `app/greeks.py` |
| **VIX regime gates** | `min_vix` / `max_vix` hard blocks (independent of expiry) | `options.regime_gates` |
| **Futures mirror** | Same 12:00 IST cutoff via `paper_trading.avoid_expiry_day` | `app/strategy.py` |
| **Audit** | `options.cycle.skip` includes `trigger_type`, `gamma_caution_level`, `expiry_triggers` | `trade_ledger.jsonl` |

**Portfolio net-gamma gating is not implemented.** Calendar triggers are labeled honestly in the UI as “calendar discipline only” unless `gamma_proxy_*` fired.

---

## Caution levels (0 / 1 / 2)

The API exposes `gamma_caution_level` on `regime_gates` (0 / 1 / 2) plus `trigger_type` (`calendar_soft`, `calendar_hard`, `gamma_proxy_soft`, `gamma_proxy_hard`, `none`).

### Level 0 — Normal (clear)

- Today is **not** an expiry session for the symbol/underlying being evaluated.
- **Futures**: no expiry de-risk rule; standard posture unless other FO rules fire.
- **Options desk**: regime badge **Clear** (green) if VIX and market gates pass.

### Level 1 — Soft caution (trade smaller / watch closely)

Expiry day **before** the configured cutoff hour (default **12:00 IST**).

| Layer | Behaviour |
|-------|-----------|
| **Futures** | `FO_EXPIRY_DAY_DE_RISK` (tier-2 rule) applies **0.5×** risk multiplier on expiry days. Regime orchestrator may switch to **defensive** posture (lower trade cap, wider breakout buffer). |
| **Options** | `regime_gates.expiry_caution = true` — amber **Caution** badge; **new iron condors still allowed** if VIX gates pass and runner is flat. |
| **Market status** | `get_market_status()` sets `is_expiry_day: true` and `next_event: "EXPIRY DAY — trade with extreme caution"`. |
| **Morning brief** | `intelligence_loop` adds warning: *Expiry day — elevated gamma/pinning risk*. |

Soft caution **reduces size or signals risk**; it does **not** hard-block entries (unless another rule also fires).

### Level 2 — Hard block (no new entries)

Any of:

1. **After cutoff on expiry day** — `now.hour >= expiry_day_entry_cutoff_hour` (options) or `expiry_day_cutoff_hour` (futures paper params).
2. **Legacy full-day block** — `block_expiry_day_entries: true` under `options.regime_gates` (discouraged; use cutoff instead).
3. **Outside safe trading window** — separate from expiry but same “gamma hedging noise” family (e.g. after ~15:15 IST safe close).

| Layer | Behaviour |
|-------|-----------|
| **Futures** | `_is_edge_case()` rejects with `expiry_day_safety` after cutoff. |
| **Options** | `check_regime_gates()` fails with reason e.g. `Expiry day — no new entries after 12:00 IST (gamma caution)`. Cycle returns `skipped: true, reason: regime_gate`. |
| **Options desk** | Regime badge **Blocked** (red); guard chips show the cutoff reason. |

**Exits and MTM management continue** after Level 2 — open structures are not force-closed by this gate alone.

---

## Configuration (`config/strategy_config.yaml`)

### Futures / paper breakout (`paper_trading`)

```yaml
paper_trading:
  avoid_expiry_day: true          # Enable expiry-day cutoff (Level 2 after hour)
  expiry_day_cutoff_hour: 12      # IST hour; block new futures entries when hour >= this on expiry days
```

- Per-index overrides can be added under `symbols.NIFTY` etc. (merged into paper params).
- `avoid_expiry_day: false` disables the hard cutoff entirely (not recommended).

### Automated options (`options.regime_gates`)

```yaml
options:
  regime_gates:
    max_vix: 22.0                 # Hard block new IC if India VIX above this
    min_vix: 10.0                 # Hard block if VIX below this (vol too crushed)
    block_expiry_day_entries: false   # false = morning window OK (recommended)
    expiry_day_entry_cutoff_hour: 12  # Level 2 after this IST hour on expiry days
```

| Knob | Default | Effect |
|------|---------|--------|
| `avoid_expiry_day` | `true` | Futures Level 2 after cutoff |
| `expiry_day_cutoff_hour` | `12` | Futures cutoff (IST, inclusive: 12:00+ blocked) |
| `block_expiry_day_entries` | `false` | `true` → options Level 2 all day (legacy) |
| `expiry_day_entry_cutoff_hour` | `12` | Options cutoff (IST) |
| `max_vix` / `min_vix` | `22` / `10` | Independent of expiry; blocks when VIX out of band |

Also relevant: `options.default_iv`, `iv_floor`, `iv_cap` — IV bounds when using VIX proxy or blended chain IV.

### FO rules JSON (futures de-risk)

`data/knowledge_base/indian_fo_rules.json` → `FO_EXPIRY_DAY_DE_RISK` (tier 2, `risk_multiplier: 0.5` on `is_expiry_day`).

---

## Tuesday Nifty weekly expiry behaviour

As of 2026 NSE schedule (see `app/market_calendar.py`):

- **NIFTY** weekly options expire every **Tuesday**.
- **NIFTY** monthly contracts expire on the **last Tuesday** of the month (holiday-shifted to previous trading day if needed).
- **BANKNIFTY**: weekly options discontinued — only **monthly** (last Tuesday). A Tuesday may be Nifty weekly expiry but **not** BankNifty expiry unless it is also the monthly date.
- **SENSEX** (BSE): weekly **Thursday**; monthly last **Thursday**.

`is_expiry_day(date, underlying="NIFTY", include_weekly=True)` returns true on:

- Any Tuesday that is a Nifty weekly expiry session, **or**
- The holiday-adjusted monthly expiry date.

**Practical Tuesday flow (Nifty-focused algo)**:

| Time (IST) | Futures (NIFTY) | Options (underlying NIFTY) |
|------------|-----------------|----------------------------|
| 09:15–09:45 | Safe window closed (opening noise) | Market closed gate until real open |
| 09:45–12:00 | Level 1 — entries allowed, 0.5× de-risk | Level 1 — **Caution** badge, IC entries allowed if flat + VIX OK |
| ≥ 12:00 | Level 2 — `expiry_day_safety` | Level 2 — regime gate blocks new IC |
| Open IC / futures | Manage exits normally | Runner still evaluates profit/loss exits each cycle |

On a Tuesday that is **only** a Nifty weekly (not monthly), pinning risk is elevated but often less extreme than monthly “triple witching” style sessions — still treat as Level 1 minimum.

---

## Dashboard & API surfaces

### Main market rail (`/api/market/status`, SSE `status.market`)

- `is_expiry_day` — any of NIFTY/SENSEX weekly or monthly expiry today
- `is_weekly_expiry_day` / `is_monthly_expiry_day` — granular flags
- `next_event` — shows **EXPIRY DAY — trade with extreme caution** when `is_expiry_day`
- `is_safe_trading_window` — false in opening/closing gamma-noise windows
- `trading_allowed` — false when outside safe window, pre-event block, or EOD flatten window

### Live Options Desk (`OptionsAlgoPanel`, `/api/options/algo/status`)

`regime_gates` payload from `get_options_algo_status_payload()`:

| Field | Meaning |
|-------|---------|
| `allowed` / `passed` | `false` → Level 2 or other hard gate |
| `expiry_caution` | `true` → Level 1 (expiry morning window) |
| `is_expiry_day` | Calendar flag for configured underlying |
| `expiry_entry_cutoff_hour` | Cutoff used for UI copy (default 12) |
| `vix_level` | Live India VIX when market context loaded |
| `reasons` | Human-readable block list when not allowed |

**Badge mapping**:

- **Clear** (green) — gates pass, not in expiry caution
- **Caution** (amber) — `expiry_caution === true`
- **Blocked** (red) — `reasons` non-empty (includes post-cutoff gamma message)

**Cycle status** card shows `last_cycle.skipped` + `reason` (e.g. `regime_gate`).

**Algo events / ledger** — see below.

### FO mood & posture

- Expiry contributes to **defensive** portfolio posture in `regime_orchestrator` (lower recommended max trades).
- FO mood panel does not show a separate gamma gauge; it reflects composite tradeability score.

---

## Options cycle skip events in the ledger

When `run_options_cycle()` skips, `_finish_options_cycle()` appends to `data/trade_ledger.jsonl` (via `trade_ledger.record`):

```json
{
  "event_type": "options.cycle.skip",
  "payload": {
    "reason": "regime_gate",
    "details": ["Expiry day — no new entries after 12:00 IST (gamma caution)"],
    "underlying": "NIFTY"
  }
}
```

### Common `reason` values

| `reason` | Meaning |
|----------|---------|
| `options_trading_disabled` | Env/config flag off |
| `max_structures_per_day_reached` | Daily IC cap hit |
| `open_structure_exists` | Already holding an open IC |
| `iron_condor_not_allowed` | Structure not in `allowed_structures` |
| `regime_gate` | `check_regime_gates` failed — see `details[]` |
| `validation_failed` | Margin / loss cap / risk gatekeeper |
| *(build errors)* | e.g. `No spot price for NIFTY`, `No option expiry for NIFTY` |

### Where skips appear in the UI

1. **Algo events** — label **SKIP**, text `Cycle skipped — regime_gate: Expiry day — …`
2. **Algo ledger** — raw `options.cycle.skip` rows with `underlying` and `reason`
3. **SSE / `recent_execution`** — mapped by `web/dashboard._map_ledger_event_to_recent_exec`
4. **Last action** — `Options cycle skipped: regime_gate: …`

Only cycles with `"skipped": true` are ledgered. Successful opens/closes use `options.structure.open` / `options.structure.close`.

---

## Code map (quick reference)

| Concern | Module |
|---------|--------|
| Expiry calendar (Tue/Thu, holidays) | `app/market_calendar.py` |
| Futures cutoff | `app/strategy.py` → `_is_edge_case()` |
| Options regime gates | `app/options_strategy_runner.py` → `check_regime_gates()` |
| Status / `expiry_caution` flag | `get_options_algo_status_payload()` |
| Cycle skip ledger | `_finish_options_cycle()` |
| VIX proxy IV | `app/options_iv.py` |
| Tier-2 expiry de-risk | `FO_EXPIRY_DAY_DE_RISK` in `indian_fo_rules.json` |
| Defensive posture on expiry | `app/regime_orchestrator.py` |

---

## Testing expiry logic (closed market)

```powershell
# Tuesday 10:30 IST — Level 1 (options caution, futures allowed with de-risk)
python run.py --dev --fixed-time "2026-06-16 10:30:00"

# Same Tuesday 12:30 IST — Level 2 (new entries blocked)
python run.py --dev --fixed-time "2026-06-16 12:30:00"
```

Unit tests: `tests/test_options_execution.py` (`test_check_regime_gates_allows_expiry_morning_window`, `test_check_regime_gates_blocks_expiry_after_cutoff`).

See also `docs/DEV_TESTING_GUIDE.md` → Workflow C.

---

## Future work (not implemented)

- Portfolio-level net gamma / vega limits across open structures
- Dynamic cutoff from DTE or ATM gamma instead of fixed hour
- Auto-flatten open short-vol on expiry afternoon

Until then, rely on calendar cutoffs, VIX gates, safe window, and manual oversight on Tuesdays.