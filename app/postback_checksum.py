"""
Kite Connect postback checksum validation.

Docs: https://kite.trade/docs/connect/v3/postbacks/
Formula: SHA-256 hex digest of (order_id + order_timestamp + api_secret)
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional, Tuple


def compute_postback_checksum(
    order_id: str,
    order_timestamp: str,
    api_secret: str,
) -> str:
    """Return SHA-256 hex of concatenated order_id, order_timestamp, api_secret."""
    raw = f"{order_id}{order_timestamp}{api_secret}"
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_postback_checksum(
    payload: Dict[str, Any],
    api_secret: Optional[str] = None,
) -> Tuple[bool, str, Optional[str]]:
    """
    Validate a Kite postback payload checksum.

    Returns:
        (ok, reason, computed_checksum)
        reason is empty when ok; otherwise one of:
        missing_fields, missing_api_secret, checksum_mismatch
    """
    order_id = payload.get("order_id")
    order_timestamp = payload.get("order_timestamp")
    received = payload.get("checksum")
    secret = api_secret if api_secret is not None else os.getenv("KITE_API_SECRET", "")

    if not order_id or not order_timestamp or not received:
        return False, "missing_fields", None
    if not secret:
        return False, "missing_api_secret", None

    computed = compute_postback_checksum(str(order_id), str(order_timestamp), secret)
    if computed != received:
        return False, "checksum_mismatch", computed
    return True, "", computed