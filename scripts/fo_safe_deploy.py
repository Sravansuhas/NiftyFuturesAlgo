#!/usr/bin/env python3
"""Run pre-deployment safety checklist. Invoke: python scripts/fo_safe_deploy.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.intelligence_loop import intelligence_loop


def main() -> int:
    result = intelligence_loop.run_safe_deploy_checklist()
    print(f"=== FO SAFE DEPLOY CHECKLIST ===")
    print(f"Mode: {result.get('mode')}")
    print(f"Ready: {result.get('ready')}")
    print()
    for check in result.get("checks", []):
        status = "PASS" if check.get("passed") else "FAIL"
        print(f"  [{status}] {check['id']}: {check.get('detail')}")
    if result.get("blockers"):
        print("\nBLOCKERS:")
        for b in result["blockers"]:
            print(f"  ! {b}")
    if result.get("warnings"):
        print("\nWARNINGS:")
        for w in result["warnings"]:
            print(f"  * {w}")
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())