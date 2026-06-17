#!/usr/bin/env python3
"""Fetch India VIX + FII/DII market context for pre-open brief."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_context import build_market_context, save_market_context


def main() -> int:
    payload = build_market_context(force_refresh=True)
    if payload.get("available"):
        path = save_market_context(payload)
        print(f"Market context saved: {path}")
        vix = payload.get("india_vix") or {}
        fii = payload.get("fii_dii") or {}
        hints = payload.get("session_hints") or {}
        if vix.get("available"):
            print(f"  India VIX: {vix.get('level')} ({vix.get('zone')}) chg={vix.get('change_pct')}%")
        if fii.get("available"):
            print(
                f"  FII/DII: FII ₹{fii.get('fii_net_crores')} Cr | "
                f"DII ₹{fii.get('dii_net_crores')} Cr | bias={fii.get('flow_bias')}"
            )
        print(f"  Open bias: {hints.get('open_bias')} | posture floor: {hints.get('posture_floor')}")
        return 0
    print("Market context unavailable (NSE and Kite sources failed)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())