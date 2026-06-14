"""
Persist backtest job state to disk so results survive API server restarts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

JOBS_DIR = Path("data/backtest_jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _job_path(job_id: str) -> Path:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    return JOBS_DIR / f"{safe}.json"


def save_job(job_id: str, job: Dict[str, Any]) -> None:
    path = _job_path(job_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(job, f, default=str, indent=2)


def load_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_all_jobs(limit: int = 50) -> Dict[str, Dict[str, Any]]:
    jobs: Dict[str, Dict[str, Any]] = {}
    files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:limit]:
        try:
            with path.open("r", encoding="utf-8") as f:
                job = json.load(f)
            jid = job.get("job_id") or path.stem
            jobs[jid] = job
        except Exception:
            continue
    return jobs


def delete_job(job_id: str) -> None:
    path = _job_path(job_id)
    if path.exists():
        path.unlink()