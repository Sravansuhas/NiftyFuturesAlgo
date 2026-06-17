"""
Startup compliance and infrastructure checks — logged once per run.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Dict, Optional
from urllib.request import urlopen

from .order_tags import resolve_order_tag

logger = logging.getLogger(__name__)


def _fetch_outbound_ip(timeout: float = 3.0) -> Optional[str]:
    """Best-effort outbound IP for VPS/static-IP audit trail."""
    configured = os.getenv("STATIC_OUTBOUND_IP", "").strip()
    if configured:
        return configured
    try:
        with urlopen("https://api.ipify.org", timeout=timeout) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return None


def run_startup_checks() -> Dict[str, Any]:
    """Log compliance context at process start. Never blocks trading."""
    context: Dict[str, Any] = {
        "algo_id": resolve_order_tag(),
        "force_dry_run": os.getenv("FORCE_DRY_RUN", "true"),
        "live_trading_confirmed": os.getenv("LIVE_TRADING_CONFIRMED", ""),
        "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "jsonl"),
        "database_configured": bool(os.getenv("DATABASE_URL", "").strip()),
        "outbound_ip": _fetch_outbound_ip(),
    }
    logger.info(
        "Startup compliance: algo_id=%s dry_run=%s persistence=%s db=%s ip=%s",
        context["algo_id"],
        context["force_dry_run"],
        context["persistence_backend"],
        context["database_configured"],
        context["outbound_ip"] or "unknown",
    )
    return context