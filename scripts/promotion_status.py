"""
Human-readable promotion gate status for all three indices.

Usage:
    python scripts/promotion_status.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.promoted_params import preview_promoted_overlay
from backtesting.promotion_gates import load_candidates

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")


def main() -> int:
    candidates = {c.get("underlying"): c for c in load_candidates()}
    print("=" * 60)
    print("PROMOTION STATUS")
    print("=" * 60)

    any_passed = False
    for idx in INDICES:
        cand = candidates.get(idx)
        preview = preview_promoted_overlay(idx)
        print(f"\n{idx}")
        if not cand:
            print("  Record: none — run scripts/run_promotion_wfo.py")
            continue
        status = cand.get("status", "unknown")
        passed = bool(cand.get("passed"))
        any_passed = any_passed or passed
        print(f"  Gate: {'PASSED' if passed else 'REJECTED'} ({status})")
        print(f"  Folds passing: {cand.get('fold_pass_count', 0)}")
        if cand.get("reasons"):
            print(f"  Reasons: {'; '.join(cand['reasons'])}")
        fold_reports = (cand.get("summary") or {}).get("fold_reports") or []
        for fr in fold_reports:
            tag = "PASS" if fr.get("passed") else "FAIL"
            detail = ", ".join(fr.get("reasons") or []) or "ok"
            print(f"    Fold {fr.get('fold')}: {tag} — {detail}")
        if preview.get("eligible"):
            print(f"  Overlay: eligible — {preview.get('proposed')}")
        else:
            print(f"  Overlay: not eligible ({preview.get('reason')})")

    print("\n" + "=" * 60)
    if not any_passed:
        print("No index passed promotion. Paper/live should use base params (0.85× de-risk).")
        print("Actions:")
        print("  1. python generate_token.py --auto")
        print("  2. python scripts/fetch_promotion_data.py")
        print("  3. python scripts/run_promotion_wfo.py")
    else:
        print("Passed indices can apply overlay:")
        print("  python scripts/run_promotion_wfo.py --apply-overlays")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())