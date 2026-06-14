#!/usr/bin/env python3
"""Build and save today's daily session quality report. Invoke: python scripts/fo_daily_review.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.session_tracker import session_tracker


def main() -> int:
    report = session_tracker.build_daily_session_report()
    path = session_tracker.save_daily_session_report(report)
    score = report.get("quality_score") or report.get("session_quality")
    grade = report.get("quality_grade", "—")
    print(f"[DAILY REVIEW] {report['date_ist']} | score={score} grade={grade}")
    for note in report.get("quality_notes", [])[:4]:
        print(f"  • {note}")

    try:
        from app.fill_learning import fill_learning_store

        kite = None
        try:
            from web.dashboard import _get_kite_client
            kite = _get_kite_client()
        except Exception:
            pass
        if kite:
            snapshot = fill_learning_store.build_snapshot_from_kite(kite)
            snap_path = fill_learning_store.save_snapshot(snapshot)
            n = (snapshot.get("summary") or {}).get("fills_analyzed", 0)
            print(f"\n[FILL LEARNING] {n} fills analyzed → {snap_path}")
        else:
            print("\n[FILL LEARNING] Skipped (Kite unavailable)")
    except Exception as exc:
        print(f"\n[FILL LEARNING] Skipped: {exc}")

    print(f"\n[DAILY REVIEW] Saved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())