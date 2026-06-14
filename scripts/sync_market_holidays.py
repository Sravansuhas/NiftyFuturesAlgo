#!/usr/bin/env python3
"""Sync NSE trading holidays into data/market_holidays.json and reload calendar."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_calendar import MARKET_HOLIDAYS, reload_holidays_from_disk
from app.nse_data import sync_holidays_from_nse


def main() -> int:
    print("Syncing NSE holiday-master...")
    result = sync_holidays_from_nse()
    added = reload_holidays_from_disk()
    print(f"  Synced {result.get('holiday_count', 0)} holidays from NSE")
    if result.get("errors"):
        print(f"  Warnings: {result['errors']}")
    print(f"  Calendar now has {len(MARKET_HOLIDAYS)} dates (+{added} new from file)")
    return 0 if result.get("holiday_count", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())