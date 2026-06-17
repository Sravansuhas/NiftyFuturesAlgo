"""
Persistent state for open multi-leg options structures.
"""

from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRUCTURES_FILE = PROJECT_ROOT / "data" / "options_structures.json"


@dataclass
class OptionsStructure:
    structure_id: str
    structure_type: str
    underlying: str
    legs: List[Dict[str, Any]]
    status: str  # OPEN | CLOSED | FAILED
    entry_credit: float = 0.0
    max_loss: float = 0.0
    expiry: Optional[str] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    order_ids: List[str] = field(default_factory=list)
    leg_results: List[Dict[str, Any]] = field(default_factory=list)
    economics: Dict[str, Any] = field(default_factory=dict)
    close_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OptionsStructure":
        return cls(
            structure_id=data["structure_id"],
            structure_type=data.get("structure_type", "iron_condor"),
            underlying=data.get("underlying", "NIFTY"),
            legs=list(data.get("legs") or []),
            status=data.get("status", "OPEN"),
            entry_credit=float(data.get("entry_credit") or 0.0),
            max_loss=float(data.get("max_loss") or 0.0),
            expiry=data.get("expiry"),
            opened_at=data.get("opened_at"),
            closed_at=data.get("closed_at"),
            order_ids=list(data.get("order_ids") or []),
            leg_results=list(data.get("leg_results") or []),
            economics=dict(data.get("economics") or {}),
            close_reason=data.get("close_reason"),
        )


class OptionsPositionStore:
    def __init__(self, path: Path = STRUCTURES_FILE):
        self.path = path
        self._structures: Dict[str, OptionsStructure] = {}
        self.structures_today: int = 0
        self._session_date: Optional[str] = None
        self.load()

    def _today_key(self) -> str:
        return now_ist().strftime("%Y-%m-%d")

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self.structures_today = int(payload.get("structures_today") or 0)
            self._session_date = payload.get("session_date")
            for row in payload.get("structures") or []:
                struct = OptionsStructure.from_dict(row)
                self._structures[struct.structure_id] = struct
        except Exception as exc:
            logger.warning("[OptionsPositions] load failed: %s", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_date": self._session_date or self._today_key(),
            "structures_today": self.structures_today,
            "structures": [s.to_dict() for s in self._structures.values()],
            "updated_at": now_ist().isoformat(),
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def maybe_reset_daily(self) -> None:
        today = self._today_key()
        if self._session_date != today:
            self._session_date = today
            self.structures_today = 0
            self.save()

    def list_open(self) -> List[OptionsStructure]:
        return [s for s in self._structures.values() if s.status == "OPEN"]

    def get(self, structure_id: str) -> Optional[OptionsStructure]:
        return self._structures.get(structure_id)

    def add_structure(self, proposal: Dict[str, Any], leg_results: List[Dict[str, Any]]) -> OptionsStructure:
        structure_id = f"OPT-{uuid.uuid4().hex[:12].upper()}"
        econ = proposal.get("economics") or {}
        struct = OptionsStructure(
            structure_id=structure_id,
            structure_type=proposal.get("structure", "iron_condor"),
            underlying=proposal.get("underlying", "NIFTY"),
            legs=deepcopy(proposal.get("legs") or []),
            status="OPEN",
            entry_credit=float(econ.get("net_credit") or 0.0),
            max_loss=float(econ.get("max_loss") or 0.0),
            expiry=proposal.get("expiry"),
            opened_at=now_ist().isoformat(),
            order_ids=[
                str(r.get("order_id"))
                for r in leg_results
                if r.get("success") and r.get("order_id")
            ],
            leg_results=leg_results,
            economics=econ,
        )
        self._structures[structure_id] = struct
        self.structures_today += 1
        self.save()
        return struct

    def close_structure(self, structure_id: str, reason: str) -> Optional[OptionsStructure]:
        struct = self._structures.get(structure_id)
        if not struct or struct.status != "OPEN":
            return None
        struct.status = "CLOSED"
        struct.closed_at = now_ist().isoformat()
        struct.close_reason = reason
        self.save()
        return struct

    def mark_failed(self, structure_id: str, reason: str) -> None:
        struct = self._structures.get(structure_id)
        if struct:
            struct.status = "FAILED"
            struct.close_reason = reason
            struct.closed_at = now_ist().isoformat()
            self.save()


options_position_store = OptionsPositionStore()