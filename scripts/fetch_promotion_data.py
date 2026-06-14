"""
Prefetch 5-minute futures history for NIFTY, BANKNIFTY, and SENSEX into historical_cache.

Required before promotion WFO when offline cache is empty (especially BANKNIFTY/SENSEX).

Usage:
    python generate_token.py --auto          # refresh Kite session first
    python scripts/fetch_promotion_data.py
    python scripts/fetch_promotion_data.py --months 6 --underlying BANKNIFTY
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import os

from kiteconnect import KiteConnect

from app.kite_auth import validate_access_token
from backtesting.data_loader import fetch_real_index_futures_data, list_available_cached_datasets

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch index futures data for promotion WFO")
    parser.add_argument("--months", type=int, default=6, help="Months of history to fetch")
    parser.add_argument("--underlying", choices=INDICES, help="Single index (default: all three)")
    args = parser.parse_args()

    load_dotenv()
    ok, _, msg = validate_access_token()
    if not ok:
        print(f"[FAIL] Kite token invalid: {msg}")
        print("Run: python generate_token.py --auto")
        return 1

    kite = KiteConnect(api_key=os.getenv("KITE_API_KEY", ""))
    kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN", ""))

    targets = [args.underlying] if args.underlying else list(INDICES)
    to_date = datetime.now()
    from_date = to_date - timedelta(days=30 * args.months)

    print(f"Fetching {args.months} months ({from_date.date()} → {to_date.date()})")
    for underlying in targets:
        print(f"\n>>> {underlying}")
        try:
            df = fetch_real_index_futures_data(
                kite=kite,
                from_date=from_date,
                to_date=to_date,
                underlying=underlying,
                use_cache=True,
                force_refresh=True,
            )
            print(f"    OK — {len(df):,} bars | {df.index.min()} → {df.index.max()}")
        except Exception as exc:
            print(f"    FAIL — {exc}")
            return 1

    print("\n--- Cached datasets ---")
    for ds in list_available_cached_datasets():
        sym = ds.get("symbol", "")
        if any(sym.upper().startswith(u) for u in targets):
            print(f"  {ds['filename']}: {ds['rows']:,} rows ({ds['actual_from']} → {ds['actual_to']})")

    print("\nNext: python scripts/run_promotion_wfo.py --cache-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())