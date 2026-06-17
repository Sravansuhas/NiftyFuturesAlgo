"""
app/trade_ledger.py

Lightweight, append-only trade & signal ledger (JSONL).
Survives restarts — never wiped unless CLEAR_LEDGER_ON_START=true.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .market_calendar import IST, now_ist


class TradeLedger:
    def __init__(self, path: str = "data/trade_ledger.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._session_id: Optional[str] = None
        self._last_fsync = 0.0

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def archive_current(self, reason: str = "rotate") -> Optional[Path]:
        """Move current ledger to archive/ before optional clear."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        archive_dir = self.path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_ist().strftime("%Y%m%d_%H%M%S")
        dest = archive_dir / f"trade_ledger_{stamp}_{reason}.jsonl"
        shutil.move(str(self.path), str(dest))
        return dest

    def _build_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "ts": time.time(),
            "event_type": event_type,
            "payload": payload,
        }
        if self._session_id:
            event["session_id"] = self._session_id
        event["date_ist"] = now_ist().strftime("%Y-%m-%d")
        return event

    def _append_event(self, event: Dict[str, Any]) -> None:
        line = json.dumps(event, default=str) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            now = time.time()
            if now - self._last_fsync >= 2.0:
                os.fsync(handle.fileno())
                self._last_fsync = now

    def record(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = self._build_event(event_type, payload)
        try:
            from app.db.connection import is_db_enabled

            if is_db_enabled():
                from app.persistence.composite import persist_ledger_event

                persist_ledger_event(self, event)
                return
        except Exception:
            pass
        self._append_event(event)

    def tail(self, n: int = 50) -> List[Dict[str, Any]]:
        if not self.path.exists() or n <= 0:
            return []
        n = min(n, 5000)
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size == 0:
                    return []
                block = min(size, max(65536, n * 512))
                handle.seek(max(0, size - block))
                chunk = handle.read().decode("utf-8", errors="replace")
            lines = [ln for ln in chunk.splitlines() if ln.strip()][-n:]
            return [json.loads(ln) for ln in lines]
        except Exception:
            return []

    def _event_date_ist(self, event: Dict[str, Any]) -> str:
        """Resolve trading date — supports legacy rows missing date_ist."""
        stored = event.get("date_ist")
        if stored:
            return str(stored)
        ts = event.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d")
        return ""

    def _read_events_tail_filtered(
        self,
        *,
        limit: int,
        date_ist: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        scan_lines: int = 1500,
    ) -> List[Dict[str, Any]]:
        """Scan recent JSONL tail newest-first — avoids full-file reads on hot paths."""
        if not self.path.exists():
            return []
        limit = min(max(1, limit), 10000)
        scan_lines = min(max(scan_lines, limit * 6), 5000)
        matched: List[Dict[str, Any]] = []
        for event in reversed(self.tail(scan_lines)):
            if date_ist and self._event_date_ist(event) != date_ist:
                continue
            if event_types and event.get("event_type") not in event_types:
                continue
            matched.append(event)
            if len(matched) >= limit:
                break
        return matched

    def read_events(
        self,
        *,
        limit: int = 200,
        date_ist: Optional[str] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Read ledger newest-first with optional date/type filters."""
        if not self.path.exists():
            return []
        limit = min(max(1, limit), 10000)
        today = now_ist().strftime("%Y-%m-%d")
        if date_ist == today:
            return self._read_events_tail_filtered(
                limit=limit,
                date_ist=date_ist,
                event_types=event_types,
                scan_lines=max(1500, limit * 8),
            )
        matched: List[Dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if date_ist and self._event_date_ist(event) != date_ist:
                        continue
                    if event_types and event.get("event_type") not in event_types:
                        continue
                    matched.append(event)
            return list(reversed(matched[-limit:]))
        except Exception:
            return []

    def read_events_today(
        self,
        *,
        limit: int = 200,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Newest-first events for the current IST trading day."""
        return self._read_events_tail_filtered(
            limit=limit,
            date_ist=now_ist().strftime("%Y-%m-%d"),
            event_types=event_types,
            scan_lines=max(1500, limit * 8),
        )

    def event_count(self) -> int:
        if not self.path.exists():
            return 0
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except Exception:
            return 0


trade_ledger = TradeLedger()