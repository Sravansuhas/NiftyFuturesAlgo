---
name: fo-weekly-earn-report
description: "Generate weekly earn report aggregating session quality, WFA/promotion status, fill learning, and pending improvement proposals. Use weekly or when user asks what worked and what to improve."
version: "0.1"
tags: [fo, weekly, earn, wfa, phase4]
---

# fo-weekly-earn-report

## Purpose
Deterministic weekly synthesis (no LLM). Answers:
- Average session quality across the week
- Total P&L and trade count from daily reports
- WFA memory + promotion gate status per index
- Fill learning snapshot summary
- Pending improvement proposals count
- Founder action items for next week

## Run (CLI)
```powershell
cd C:\Projects\NiftyFuturesAlgo
python scripts/fo_weekly_earn_report.py
```

## Run (API)
```
GET  http://localhost:8050/api/improvement/weekly
POST http://localhost:8050/api/improvement/weekly/generate   (via dashboard button)
```

## Output
- `data/weekly_reports/weekly_{YYYY-MM-DD}.json`
- `data/weekly_reports/latest.json`

## Implementation
- `app/improvement_loop.py` — `build_weekly_report()`, `save_weekly_report()`
- Reads: `session_tracker`, `backtest_memory`, `promotion_gates`, `fill_learning`

## Safety
- Read-only on trading logic and risk params.
- Improvement proposals require human `confirmed: true` before manifest is recorded.

## Version History
- 0.1 (2026-06): Phase 4C — CLI + dashboard API + Continuous Improvement panel