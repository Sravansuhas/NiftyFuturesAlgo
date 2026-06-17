import json
import time
from pathlib import Path
from typing import Any, Dict


class AuditLogger:
    def __init__(self, path: str = "data/audit_events.json"):
        self.path = Path(path)

    def _build_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ts": time.time(),
            "event_type": event_type,
            "payload": payload,
        }

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")

    def record(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = self._build_event(event_type, payload)
        try:
            from app.db.connection import is_db_enabled

            if is_db_enabled():
                from app.persistence.composite import persist_audit_event

                persist_audit_event(self, event)
                return
        except Exception:
            pass
        self._append_event(event)


audit_logger = AuditLogger()
