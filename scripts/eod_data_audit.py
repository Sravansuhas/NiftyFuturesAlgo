#!/usr/bin/env python3
"""Compare historical_cache vs NSE official EOD bhavcopy."""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.eod_audit import previous_trading_day, run_eod_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="EOD cache vs NSE bhavcopy audit")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: previous trading day)")
    parser.add_argument("--days", type=int, default=1, help="Rolling audit over N days")
    args = parser.parse_args()

    if args.date:
        start = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        start = previous_trading_day()

    exit_code = 0
    for offset in range(args.days):
        d = start - timedelta(days=offset)
        report = run_eod_audit(trade_date=d)
        overall = report.get("overall", "unknown")
        print(f"\n=== EOD AUDIT {d} — {overall.upper()} ===")
        for sym, info in (report.get("indices") or {}).items():
            print(f"  {sym}: {info.get('status')} {info.get('issues', [])}")
        if overall not in ("healthy", "skipped"):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())