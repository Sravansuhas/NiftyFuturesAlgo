#!/usr/bin/env python3
"""
Lunar phase / Indian panchang metadata for NSE F&O research.

Usage:
    python scripts/lunar_calendar.py
    python scripts/lunar_calendar.py --date 2026-11-08
    python scripts/lunar_calendar.py --from 2026-01-01 --to 2026-12-31
    python scripts/lunar_calendar.py --events --from 2026-01-01 --to 2026-12-31
    python scripts/lunar_calendar.py --refresh
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.lunar_calendar import (
    build_lunar_context,
    build_lunar_range,
    format_lunar_summary,
    list_lunar_events,
    save_lunar_context,
    save_lunar_range,
)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute lunar phase and Indian panchang metadata (research only).",
    )
    parser.add_argument("--date", type=_parse_date, help="Single date YYYY-MM-DD (default: today IST)")
    parser.add_argument("--from", dest="from_date", type=_parse_date, help="Range start YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=_parse_date, help="Range end YYYY-MM-DD")
    parser.add_argument("--refresh", action="store_true", help="Ignore same-day cache")
    parser.add_argument("--events", action="store_true", help="List Amavasya/Purnima/synodic events only")
    args = parser.parse_args()

    if args.from_date and args.to_date:
        if args.from_date > args.to_date:
            print("[LUNAR] --from must be <= --to")
            return 1

        if args.events:
            events = list_lunar_events(args.from_date, args.to_date)
            print(f"[LUNAR] Events {args.from_date} → {args.to_date} ({len(events)} total)")
            for ev in events:
                phase = ev.get("phase", "?")
                when = ev.get("civil_date") or ev.get("instant_ist", "?")
                extra = ""
                if ev.get("is_friday"):
                    extra = " (Friday — IIMB abstinence factor)"
                print(f"  {when}  {phase}{extra}")
            return 0

        payload = build_lunar_range(args.from_date, args.to_date)
        path = save_lunar_range(payload)
        print(f"[LUNAR] Range index saved: {path}")
        print(f"  Days: {len(payload['days'])} | Trading days: {payload['trading_day_count']}")
        ev = payload.get("events", {})
        print(f"  Amavasya anchors: {len(ev.get('amavasya_anchors', []))}")
        print(f"  Purnima anchors: {len(ev.get('purnima_anchors', []))}")
        print(f"  Synodic new moon anchors: {len(ev.get('synodic_new_moon_anchors', []))}")
        return 0

    payload = build_lunar_context(for_date=args.date, refresh=args.refresh)
    path = save_lunar_context(payload)
    print(f"[LUNAR] Lunar context saved: {path}")
    print(format_lunar_summary(payload))
    print("  Note: research metadata only — not a primary trading signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())