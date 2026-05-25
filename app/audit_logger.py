import json
import time
from pathlib import Path
from typing import Any, Dict


class AuditLogger:
    def __init__(self, path: str = "data/audit_events.json"):
        self.path = Path(path)

    def record(self, event_type: str, payload: Dict[str, Any]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": time.time(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")


audit_logger = AuditLogger()
