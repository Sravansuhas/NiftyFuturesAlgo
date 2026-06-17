# Aegis — SEBI Algo Trading Compliance Checklist

Aegis compliance posture for Indian exchange algo registration and operational audit. Cross-references: [ARCHITECTURE.md](ARCHITECTURE.md), [KITE_INTEGRATION.md](KITE_INTEGRATION.md), [MORNING_TRADING_GUIDE.md](MORNING_TRADING_GUIDE.md).

**Status:** Infrastructure embodies compliance spirit; explicit code-level enforcement is in progress. Run this checklist before any live capital enablement.

---

## 1. Algo ID & Order Tagging

| Item | Status | Implementation |
|------|--------|----------------|
| Unique algo identifier | Implemented | `ALGO_ID` env var (default `AEGIS`) |
| Tag on every Kite order | Implemented | `app/order_tags.py` → `place_guarded_order` |
| Tag on SL-M protection orders | Implemented | `resolve_protection_tag()` → `exchange_protection.py` |
| Tag in audit trail | Implemented | `order.submitted` audit events include `tag` |

```bash
# .env
ALGO_ID=AEGIS          # alphanumeric, max 20 chars (Kite API limit)
STATIC_OUTBOUND_IP=     # optional: VPS static IP for audit log
```

---

## 2. Static IP / VPS

| Item | Status | Notes |
|------|--------|-------|
| Document VPS requirement | Documented | Zerodha algo registration expects stable outbound IP |
| Startup IP log | Implemented | `app/startup_checks.py` logs outbound IP or `STATIC_OUTBOUND_IP` |
| Infra enforcement | Operator | Configure firewall/NAT at VPS provider level |

---

## 3. Rate Limiting

| Item | Status | Implementation |
|------|--------|----------------|
| Per-second order spacing | Implemented | `order_limiter` @ 10 req/s (`kite_rate_limit.py`) |
| Rolling 10s burst guard | Implemented | `order_burst_tracker` — rejects before broker call |
| Safe target | Configured | 80 orders / 10s (conservative; tune via `RATE_LIMIT_ORDERS_PER_10S`) |

---

## 4. Audit & Logs

| Item | Status | Implementation |
|------|--------|----------------|
| JSONL audit events | Implemented | `data/audit_events.json` via `audit_logger.py` |
| Trade ledger | Implemented | `data/trade_ledger.jsonl` via `trade_ledger.py` |
| Structured Postgres mirror | Scaffolded | Optional `DATABASE_URL` + `migrations/001_*.sql` |
| EOD compliance report | Partial | `scripts/eod_data_audit.py`, `scripts/fo_daily_review.py` |
| Decision context in audit | Partial | Blocked orders logged; enrich with regime/ATR features (roadmap) |

---

## 5. Risk & Validation Gates

| Item | Status | Implementation |
|------|--------|----------------|
| Central RiskGatekeeper | Implemented | All orders pass `can_place_order` + `_validate_order` |
| Daily loss / drawdown CB | Implemented | `risk_gatekeeper.py` |
| State machine veto | Implemented | `state_machine.py` |
| Broker reconciliation | Implemented | `broker_reconciliation.py` |
| Walk-forward promotion gates | Implemented | `backtesting/promotion_gates.py` |
| Pre-order margin check | Planned | Kite margins API when options added |
| Portfolio Greeks limit | Planned | Options module (roadmap) |

---

## 6. Testing & Validation Evidence

| Item | Status | Target |
|------|--------|--------|
| Unit tests (risk, calendar, lifecycle) | Implemented | 34+ test modules, CI |
| Extended paper trading | Required | 4–8 weeks live data before size increase |
| Reconciliation audit | Required | Zero silent failures in paper period |
| WFO with realistic costs | Required | PF ≥ 1.2 OOS, 2× cost stress |
| Founder / peer review | Required | Document sign-off before `LIVE_TRADING_CONFIRMED` |

---

## 7. Human Oversight & Kill Switches

| Item | Status | Implementation |
|------|--------|----------------|
| `FORCE_DRY_RUN=true` default | Implemented | `.env.example` |
| `LIVE_TRADING_CONFIRMED` gate | Implemented | Explicit env for live |
| Circuit breaker | Implemented | Drawdown / recon failure → halt |
| EOD MIS flatten | Implemented | `app/eod_flatten.py` |
| Emergency halt API | Implemented | `app/emergency.py` |
| UI risk params + halt | Partial | Dashboard shows status; one-click halt (roadmap) |

---

## 8. Documentation & Versioning

| Doc | Purpose |
|-----|---------|
| This file | Compliance checklist |
| `config/strategy_config.yaml` | Versioned risk/strategy params |
| `data/strategy_candidates.json` | Promotion gate outcomes |
| `ROADMAP.md` | Phased rollout plan |

---

## Pre-Live Enablement Checklist

Run in order before setting `FORCE_DRY_RUN=false` and `LIVE_TRADING_CONFIRMED=true`:

- [ ] `ALGO_ID` registered with broker and matches env
- [ ] VPS static IP documented and logged at startup
- [ ] Extended paper trading completed (≥4 weeks)
- [ ] Reconciliation audit clean (no unexplained position drift)
- [ ] WFO promotion gates passed on NIFTY + cross-checked BANKNIFTY/SENSEX
- [ ] All unit tests + smoke backtests green
- [ ] `scripts/fo_safe_deploy.py` passes
- [ ] Risk parameters reviewed and recorded in `strategy_config.yaml`
- [ ] Emergency halt tested manually
- [ ] Founder sign-off documented (date + reviewer name)

---

## Disclaimer

This checklist supports operational discipline. It is not legal advice. Consult a qualified compliance professional for SEBI exchange algo registration requirements.