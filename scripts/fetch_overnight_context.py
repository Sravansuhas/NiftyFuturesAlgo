#!/usr/bin/env python3
"""Fetch GIFT Nifty overnight gap context (~08:55 IST)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.overnight_context import build_overnight_context, save_overnight_context


def main() -> int:
    payload = build_overnight_context()
    if payload.get("available"):
        path = save_overnight_context(payload)
        print(f"Overnight context saved: {path}")
        n = payload.get("NIFTY", {})
        print(f"  GIFT gap: {n.get('implied_gap_pct')}% ({n.get('gap_regime')})")
        print(f"  Posture floor: {payload.get('session_hints', {}).get('posture_floor')}")
        return 0
    print("Overnight context unavailable (Kite token or quotes missing)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())