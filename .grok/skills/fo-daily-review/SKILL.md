---
name: fo-daily-review
description: "Build and save today's session quality report from trade ledger and risk state. Use after each trading session or when user asks for daily review / session quality score."
version: "0.1"
tags: [fo, daily, session, quality, phase4]
---

# fo-daily-review

## Purpose
Deterministic end-of-session review (no LLM). Answers:
- Session quality score (0–100) and grade (A–D)
- Signal discipline (accept/reject ratio)
- Risk adherence (daily loss, drawdown)
- Per-index activity summary
- Founder action items

## Run (CLI)
```powershell
cd C:\Projects\NiftyFuturesAlgo
python scripts/fo_daily_review.py
```

## Run (API)
```
GET http://localhost:8050/api/improvement/daily
```

## Output
- `data/session_reports/{YYYY-MM-DD}.json`

## Implementation
- `app/session_tracker.py` — `build_daily_session_report()`, `save_daily_session_report()`
- Reads: `trade_ledger`, `risk_gatekeeper`, `multi_symbol_risk`

## Safety
- Read-only on trading logic. Never places orders or changes risk params.
- Quality score weights discipline over raw P&L.

## Version History
- 0.1 (2026-06): Phase 4C — CLI + dashboard API + AUTO_DAILY_REVIEW hook