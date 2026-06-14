#!/usr/bin/env python3
"""Build trading journal for today or a given date."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading_journal import trading_journal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (default: today IST)")
    parser.add_argument("--note", help="Append trader note")
    args = parser.parse_args()

    if args.note:
        entry = trading_journal.add_trader_note(args.note, date_ist=args.date)
        print(f"Note added to {entry.get('date_ist')}")
        return 0

    path = trading_journal.build_and_save(args.date)
    entry = trading_journal.load_journal(path.stem)
    print(f"Journal saved: {path}")
    if entry:
        print(f"  Quality: {entry.get('session_summary', {}).get('quality_score')}")
        print(f"  Feedback: {entry.get('feedback_summary')}")
        for note in (entry.get("system_feedback") or {}).get("notes", [])[:3]:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())