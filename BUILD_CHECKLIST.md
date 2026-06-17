# Aegis — Master Build Checklist

Living checklist derived from the Grok principal-quant audit, ROADMAP.md, and COMPLIANCE.md.  
**One command to check everything:** `python scripts/algo_lab_ops.py preflight`

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Done and tested |
| 🔄 | In progress / scaffolded |
| ⬜ | Not started |
| ❌ | Run complete — failed gate (falsified) |
| 🔒 | Gated — do not enable live until complete |

---

## Phase 0 — Futures Foundation (Current Gate)

> ROADMAP: Do not advance until 3-index paper trading is boringly reliable.

| # | Item | Status | Verify |
|---|------|--------|--------|
| 0.1 | Per-run diagnostic logs (`logs/run_*.log`) | ✅ | `PHASE0_DIAGNOSTICS_AND_LOGGING.md` |
| 0.2 | Independent LTP per NIFTY/BANKNIFTY/SENSEX | 🔄 | Send latest `run_*.log` for review |
| 0.3 | Dynamic ATR (not stuck at single value) | 🔄 | Search `[ATR]` in logs |
| 0.4 | Dynamic position confidence (not stuck 92%) | 🔄 | GUI + `[SNAPSHOT]` logs |
| 0.5 | SSE live dashboard updates | ✅ | `http://localhost:8050` |
| 0.6 | Multi-index WFO with real cached data | 🔄 | `python scripts/algo_lab_ops.py wfo-status` |

**Phase 0 exit gate:** Founder confirms 3-index paper feels alive + one log file suffices for diagnosis.

---

## Week 1 — Compliance & Infrastructure (Grok Report)

| # | Item | Status | Verify |
|---|------|--------|--------|
| 1.1 | `ALGO_ID` env + Kite order tagging | ✅ | `algo_lab_ops.py compliance` |
| 1.2 | SL-M protection order tagging | ✅ | `exchange_protection.py` |
| 1.3 | Rolling 10s order burst limiter | ✅ | `test_kite_rate_limit.py` |
| 1.4 | Startup compliance logging (IP, algo_id) | ✅ | `python run.py` startup line |
| 1.5 | `COMPLIANCE.md` checklist | ✅ | Read + `compliance` subcommand |
| 1.6 | Postgres schema + migrate script | ✅ | `python scripts/db_migrate.py` |
| 1.7 | Postgres dual-write (JSONL + DB) | ✅ | `PERSISTENCE_BACKEND=dual` |
| 1.8 | Lunar calendar research utility | ✅ | `scripts/lunar_calendar.py` |
| 1.9 | Expanded `calculate_order_quantity` tests | ✅ | `test_risk_gatekeeper.py` |

---

## Phase 1 — Data & Real-Time Foundation (2–6 weeks)

| # | Item | Status | Verify |
|---|------|--------|--------|
| 2.1 | `instruments_manager` — 3-index lots/tokens | ✅ | `algo_lab_ops.py status` |
| 2.2 | KiteTicker WebSocket LTP feed | ✅ | `ENABLE_WEBSOCKET=true` |
| 2.3 | **Candle + OI aggregator** | ✅ | `app/candle_builder.py` |
| 2.4 | WebSocket MODE_QUOTE (volume/OI) | ✅ | `ENABLE_WS_QUOTE=true` |
| 2.5 | Wire candles into strategy (WS-first prev candle) | ✅ | `prev_candle_source=ws_candle` in logs |
| 2.6 | Symbol-aware risk/config overrides | ✅ | `get_symbol_config()` + `test_symbol_config.py` |
| 2.7 | Multi-index WFO pipeline (NIFTY/BNF/SENSEX) | ✅ | `algo_lab_ops.py wfo-run` |
| 2.7b | Multi-index WFO **promotion pass** | ❌ FAILED | `multi_index_20260615_030636` — all REJECTED @ 2× costs |
| 2.11 | Dashboard ops API (`/api/ops/preflight`) | ✅ | Settings page + legacy dashboard |
| 2.8 | India VIX time series | ✅ | `app/market_context.py` + `fetch_market_context.py` |
| 2.9 | FII/DII flow context | ✅ | `market_context.json` + `intelligence_loop` hook |
| 2.10 | Historical options chain archive | 🔄 | `app/options_chain_archive.py` scaffold |

---

## Phase 2 — Intelligence & Neutral Strategies (6–16 weeks)

| # | Item | Status | Verify |
|---|------|--------|--------|
| 3.1 | Failure-pattern miner skill | ✅ | `scripts/fo_failure_pattern_miner.py` |
| 3.2 | Morning market brief skill | ✅ | `scripts/fo_market_brief.py` |
| 3.3 | Regime orchestrator + FO rules | ✅ | `app/regime_orchestrator.py` |
| 3.4 | Options chain skeleton | 🔄 | `app/options_*.py` + `options_chain_archive.py` |
| 3.5 | Greeks engine (BS delta/gamma/theta/vega) | ✅ | `app/greeks.py` + `test_greeks.py` |
| 3.6 | Iron Condor proposal module | ✅ | `app/strategies/iron_condor.py` (proposals only) |
| 3.7 | Delta-neutral straddle skeleton | ✅ | `app/strategies/straddle_proposal.py` (research) |
| 3.8 | Agent insights dashboard tab | ✅ | `/ui/insights` + `GET /api/agent/insights` |
| 3.9 | Strategy generator → WFO pipeline | 🔄 | `submit_wfo_candidate()` bridge (human-gated) |

---

## Phase 3 — Validation Before Live 🔒

| # | Item | Status | Verify |
|---|------|--------|--------|
| 4.1 | 6–12 month multi-index cached data | ⬜ | `data-health` cache coverage |
| 4.2 | WFO PF ≥ 1.2 OOS + 2× cost stress | ⬜ | `promotion_gates.py` |
| 4.3 | Extended paper 4–8 weeks | 🔒 MANUAL | Trade ledger review |
| 4.4 | Zero silent reconciliation failures | 🔒 MANUAL | `fo_daily_review.py` |
| 4.5 | Cross-index param stability | ⬜ | NIFTY params work on BANKNIFTY |
| 4.6 | Founder + peer risk review | 🔒 MANUAL | Sign-off in COMPLIANCE.md |
| 4.7 | `LIVE_TRADING_CONFIRMED=true` tested | 🔒 | `preflight` in live mode |

---

## The Flagship Tool — Aegis Ops

**`python scripts/algo_lab_ops.py`** — unified command center.

| Command | Purpose | When to run |
|---------|---------|-------------|
| `preflight` | Full morning readiness | Before market open |
| `status` | Token, market, WS, DB, promotion | Anytime |
| `compliance` | Code-checkable SEBI items | Before live enable |
| `data-health` | Cache + EOD audit | Weekly / before WFO |
| `wfo-run` | Multi-index WFO (180d cache) | After `fetch_promotion_data` |
| `wfo-status` | Promotion gates per index | After WFO runs |
| `lunar` | Panchang metadata for today | Research tagging |
| `insights` | Promotion + WFO + proposals + lunar snapshot | Weekly / before sizing decisions |
| `migrate-db` | Apply Postgres migrations | After `docker-compose up` |

```powershell
# Morning routine (replaces running 6 separate scripts)
python scripts/algo_lab_ops.py preflight

# Deep compliance audit
python scripts/algo_lab_ops.py compliance

# After promotion WFO
python scripts/algo_lab_ops.py wfo-status
```

---

## Next Build Sprint (Recommended Order)

1. **Run multi-index WFO on real cache** — `fetch_promotion_data` then `wfo-run --days 180 --cost-multiplier 2.0` (2.7b)
2. **Phase 0 exit review** — confirm independent LTP/ATR per index in one `run_*.log` (0.2–0.4)
3. **Deepen cache to 6–12 months** — `data-health` coverage for all 3 indices (4.1)
4. **Options chain archive** — daily parquet snapshots wired to research path (2.10)
5. **Wire options proposals to live** — only after Phase 0 gate + promotion pass (3.4, blocked)

---

## Quick Health Commands

```powershell
pip install -r requirements.txt
$env:PYTHONPATH="."
python -m unittest discover -s tests -v
python scripts/algo_lab_ops.py preflight
python scripts/algo_lab_ops.py compliance
```

---

*Update this file when checklist items change status. Last updated: 2026-06-15 (Sprint 2 sync).*