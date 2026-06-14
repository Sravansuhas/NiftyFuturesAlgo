#!/usr/bin/env python3
"""
Build weekly earn report, generate improvement proposals, save, print summary.

Invoke: python scripts/fo_weekly_earn_report.py [--weeks-back 1]
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.improvement_loop import improvement_loop


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly earn report + improvement proposals")
    parser.add_argument(
        "--weeks-back",
        type=int,
        default=1,
        help="Number of weeks to aggregate (default: 1)",
    )
    args = parser.parse_args()

    report = improvement_loop.build_weekly_earn_report(weeks_back=args.weeks_back)
    proposals = report.get("improvement_proposals") or []

    saved_proposal_paths = []
    for proposal in proposals:
        saved_proposal_paths.append(improvement_loop.record_improvement_proposal(proposal))

    path = improvement_loop.save_weekly_report(report)

    session = report.get("session_summary") or {}
    wfa = report.get("wfa_summary") or {}
    fill = report.get("fill_calibration") or {}
    failure = report.get("failure_patterns_pending") or {}

    print(f"[WEEKLY EARN] {report.get('week_label')} ({report.get('week_start')} → {report.get('week_end')})")
    print(
        f"  Sessions: {session.get('reports_found', 0)} | "
        f"avg quality: {session.get('avg_quality_score', '—')} | "
        f"trend: {session.get('quality_trend', '—')}"
    )
    print(
        f"  Total P&L: ₹{session.get('total_pnl_rs', 0):,.0f} | "
        f"trades: {session.get('total_trades', 0)} | "
        f"recon halts: {session.get('recon_halts', 0)}"
    )
    print(
        f"  WFA memory runs: {wfa.get('memory_runs', 0)} | "
        f"fill fills: {fill.get('fills_analyzed', 0)} | "
        f"failure proposals pending: {failure.get('count', 0)}"
    )
    print(f"  Improvement proposals generated: {len(proposals)}")
    for proposal in proposals:
        print(f"    • [{proposal.get('severity')}] {proposal.get('id')}: {proposal.get('description')}")
    for note in report.get("documentation_notes", [])[:4]:
        print(f"  Note: {note}")
    for action in report.get("founder_actions", [])[:3]:
        print(f"  Action: {action}")
    print(f"\n[WEEKLY EARN] Saved to {path}")
    if saved_proposal_paths:
        print(f"[WEEKLY EARN] Recorded {len(saved_proposal_paths)} proposal(s) under data/improvement_proposals/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())