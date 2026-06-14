#!/usr/bin/env python3
"""Generate and save today's FO market brief. Invoke: python scripts/fo_market_brief.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.intelligence_loop import intelligence_loop


def main() -> int:
    brief = intelligence_loop.build_market_brief()
    path = intelligence_loop.save_market_brief(brief)
    print(intelligence_loop.format_brief_text(brief))
    print(f"\n[BRIEF] Saved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())