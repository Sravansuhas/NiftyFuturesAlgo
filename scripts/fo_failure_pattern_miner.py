#!/usr/bin/env python3
"""
Propose encodable failure-pattern updates (human review required).

Does NOT modify indian_fo_rules.json automatically.
Output: data/knowledge_base/proposals/proposal_*.json
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intelligence_loop import intelligence_loop

# Curated from r/IndiaAlgoTrading, SEBI FY25, Zerodha Varsity — encodable proposals
_SEED_PROPOSALS = [
    {
        "rule_id": "FO_ALGO_KILL_SWITCH_DRILL",
        "tier": 1,
        "description": "Require weekly manual kill-switch test in paper; algos that never test halt fail in real disconnects.",
        "source": "r/IndiaAlgoTrading API disconnect threads",
        "condition_type": "boolean_required",
        "params": {"field": "kill_switch_tested_this_week", "expected": True},
        "code_target": "risk_gatekeeper",
        "confidence": "medium",
    },
    {
        "rule_id": "FO_NO_SCALE_ON_GREEN_DAY",
        "tier": 2,
        "description": "Block lot-size increase on same day as first profitable trade (revenge/overconfidence scaling).",
        "source": "r/IndianStockMarket loss spiral confessions",
        "condition_type": "boolean_forbidden",
        "params": {"field": "scaling_same_day_as_first_win", "action": "de_risk"},
        "code_target": "risk_gatekeeper",
        "confidence": "medium",
    },
]


def main() -> int:
    saved = []
    for proposal in _SEED_PROPOSALS:
        path = intelligence_loop.record_failure_pattern_proposal(proposal)
        saved.append(str(path))
        print(f"[MINER] Proposal queued: {proposal['rule_id']} -> {path}")
    print(f"\n[MINER] {len(saved)} proposal(s) pending human review.")
    print("Review in data/knowledge_base/proposals/ then merge via /implement into indian_fo_rules.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())