---
name: fo-safe-deploy
description: "Run full pre-deployment checklist before paper size increase or live enable: token, state machine, promotion gates, broker connection. Use with /fo-safe-deploy or before market open."
version: "0.1"
tags: [fo, deploy, safety, checklist]
---

# fo-safe-deploy

## Purpose
Fail-closed checklist before trusting the system with capital or larger paper size.

## Run (CLI)
```powershell
python scripts/fo_safe_deploy.py
# Exit code 0 = ready, 1 = blockers present
```

## Run (API)
```
GET http://localhost:8050/api/agent/safe-deploy
```

## Checks
| ID | Severity | What |
|----|----------|------|
| state_machine | blocker | Not EMERGENCY_HALT / trading allowed |
| live_gate | blocker | LIVE requires LIVE_TRADING_CONFIRMED |
| kite_token | warning (paper) / blocker (live) | Valid Kite access token |
| promotion_* | warning | WFA promotion per index |
| broker_connected | warning | API session healthy |

## Safety
- Warnings do not block paper mode; blockers always fail the checklist.
- Live enable still requires explicit env gates in `app/main.py`.

## Version History
- 0.1 (2026-06): Initial implementation via intelligence_loop