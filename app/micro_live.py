"""
Micro-live mode — controlled real capital with strict 1-lot caps and human gates.

Requires double confirmation (MICRO_LIVE_ENABLED + MICRO_LIVE_CONFIRMED) in addition
to the standard live trading gates (FORCE_DRY_RUN=false + LIVE_TRADING_CONFIRMED).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Optional

_current_config: Optional["MicroLiveConfig"] = None


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on", "confirmed"}


def _normalize_symbol(symbol: str) -> str:
    s = symbol.upper()
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "SENSEX" in s:
        return "SENSEX"
    return "NIFTY"


def _parse_allowed_symbols(raw: str) -> tuple:
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else ("NIFTY",)


@dataclass(frozen=True)
class MicroLiveConfig:
    enabled: bool = False
    max_lots: int = 1
    max_open_positions: int = 1  # across all indices
    allowed_symbols: tuple = ("NIFTY",)  # start with strongest index only
    require_promotion: bool = True


def load_micro_live_config() -> MicroLiveConfig:
    """Load micro-live config from environment (double-gated enable)."""
    env_enabled = _env_bool("MICRO_LIVE_ENABLED", False)
    confirmed = _env_bool("MICRO_LIVE_CONFIRMED", False)
    max_lots = max(1, int(os.getenv("MICRO_LIVE_MAX_LOTS", "1") or "1"))
    symbols = _parse_allowed_symbols(os.getenv("MICRO_LIVE_SYMBOLS", "NIFTY"))
    return MicroLiveConfig(
        enabled=env_enabled and confirmed,
        max_lots=max_lots,
        max_open_positions=1,
        allowed_symbols=symbols,
        require_promotion=True,
    )


def set_micro_live_config(config: MicroLiveConfig) -> None:
    """Inject runtime config (used by main.py and tests)."""
    global _current_config
    _current_config = config


def get_micro_live_config() -> MicroLiveConfig:
    if _current_config is not None:
        return _current_config
    return load_micro_live_config()


def cap_order_quantity(
    symbol: str,
    quantity: int,
    lot_size: int,
    open_positions_count: int,
    config: Optional[MicroLiveConfig] = None,
) -> int:
    """
    Enforce micro-live caps: allowed symbols only, max lots, max open positions.
    Returns 0 when a new entry would violate caps.
    """
    cfg = config or get_micro_live_config()
    if not cfg.enabled:
        return quantity

    sym = _normalize_symbol(symbol)
    if sym not in cfg.allowed_symbols:
        return 0

    if open_positions_count >= cfg.max_open_positions:
        return 0

    max_qty = cfg.max_lots * lot_size
    if quantity <= 0:
        return 0
    return min(quantity, max_qty)


def validate_micro_live_ready() -> dict:
    """
    Pre-flight check for micro-live deployment.
    Returns {ready, blockers, warnings, config}.
    """
    config = load_micro_live_config()
    blockers: list[str] = []
    warnings: list[str] = []

    if not _env_bool("MICRO_LIVE_ENABLED", False):
        blockers.append("MICRO_LIVE_ENABLED=true required")
    if not _env_bool("MICRO_LIVE_CONFIRMED", False):
        blockers.append("MICRO_LIVE_CONFIRMED=true required (human gate)")

    force_dry = os.getenv("FORCE_DRY_RUN", "true").lower() not in ("0", "false", "no")
    live_confirmed = _env_bool("LIVE_TRADING_CONFIRMED", False)
    if force_dry or not live_confirmed:
        blockers.append("Micro-live requires FORCE_DRY_RUN=false and LIVE_TRADING_CONFIRMED=true")

    from app.intelligence_loop import intelligence_loop

    deploy = intelligence_loop.run_safe_deploy_checklist(include_micro_live_gate=False)
    if not deploy.get("ready"):
        blockers.extend(deploy.get("blockers", []))
    warnings.extend(deploy.get("warnings", []))

    promo_passed = False
    if config.require_promotion:
        for sym in config.allowed_symbols:
            promo = intelligence_loop._get_promotion_for(sym)
            if promo and promo.get("passed"):
                promo_passed = True
                break
        if not promo_passed:
            blockers.append(
                "At least one allowed symbol must have promotion passed: "
                + ", ".join(config.allowed_symbols)
            )

    ready = len(blockers) == 0

    return {
        "ready": ready,
        "blockers": blockers,
        "warnings": warnings,
        "config": asdict(config),
    }