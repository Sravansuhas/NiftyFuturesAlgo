---
name: fo-failure-pattern-miner
description: "Scan retail F&O failure patterns and propose encodable rule updates for indian_fo_rules.json. Outputs go to proposals/ for human review — never auto-applies. Use weekly or after major market events."
version: "0.2"
tags: [fo, failure-patterns, learning, anti-patterns]
---

# fo-failure-pattern-miner

## Purpose
Queue **reviewable** anti-pattern proposals — not live code changes.

## Run (CLI)
```powershell
python scripts/fo_failure_pattern_miner.py
```

## Output
`data/knowledge_base/proposals/proposal_*.json` with `status: pending_review`

## Review workflow
1. Read proposal JSON
2. Founder approves or rejects
3. Approved rules merged into `data/knowledge_base/indian_fo_rules.json` via `/implement`
4. Run `python -m unittest tests.test_fo_rules_engine -v`

## Safety (Non-Negotiable)
- Never edits `indian_fo_rules.json` or `risk_gatekeeper.py` directly
- All merges require `/implement --effort 3+` with reviewer

## Version History
- 0.2 (2026-06): Proposal queue via intelligence_loop
- 0.1 (2026-06): Skeleton