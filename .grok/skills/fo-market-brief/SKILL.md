---
name: fo-market-brief
description: "Generate a concise morning market regime brief for NIFTY/BANKNIFTY/SENSEX futures using backtest memory, promotion gates, FO rules, and calendar. Use before paper trading sessions or when user asks for today's market brief."
version: "0.2"
tags: [fo, market, regime, morning]
---

# fo-market-brief

## Purpose
Deterministic pre-market brief (no LLM). Answers:
- What regime memory says from recent WFA runs
- Which tier-1 failure patterns are active
- Promotion status per index
- Session posture (max trades, buffer bias, caution level)

## Run (CLI)
```powershell
cd C:\Projects\NiftyFuturesAlgo
python scripts/fo_market_brief.py
```

## Run (API)
```
POST http://localhost:8050/api/agent/brief/generate
GET  http://localhost:8050/api/agent/brief/latest
```

## Output
- `data/briefs/{YYYY-MM-DD}.json`
- `data/briefs/{YYYY-MM-DD}.txt`

## Implementation
- `app/intelligence_loop.py` — `build_market_brief()`, `save_market_brief()`
- Reads: `backtest_memory`, `indian_fo_rules.json`, `strategy_candidates.json`, `market_calendar`

## Safety
- Read-only on trading logic. Never places orders.
- Parameter suggestions require human review before config changes.

## Version History
- 0.2 (2026-06): Runnable script + API + intelligence_loop integration
- 0.1 (2026-06): Skeleton