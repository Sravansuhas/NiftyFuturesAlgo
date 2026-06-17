"""
Kite order tag resolution — SEBI algo ID / audit tagging.

Kite Connect: tag is alphanumeric, max 20 characters on place_order.
"""

from __future__ import annotations

import os
from typing import Optional

from app.branding import DEFAULT_ALGO_ID

DEFAULT_ALGO_TAG = DEFAULT_ALGO_ID


def resolve_order_tag(explicit: Optional[str] = None) -> str:
    """Resolve Kite order tag from explicit arg, ALGO_ID env, or default."""
    raw = (explicit if explicit and explicit != DEFAULT_ALGO_TAG else None)
    raw = raw or os.getenv("ALGO_ID") or DEFAULT_ALGO_TAG
    cleaned = "".join(ch for ch in raw.strip() if ch.isalnum() or ch in "-_") or DEFAULT_ALGO_TAG
    return cleaned[:20]


def resolve_protection_tag(suffix: str = "SLM") -> str:
    """Protection orders (SL-M) use ALGO_ID + suffix, truncated to 20 chars."""
    base = resolve_order_tag()
    combined = f"{base}-{suffix}"
    return combined[:20]