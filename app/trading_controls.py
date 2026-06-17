"""
Runtime trading controls — portal-editable without restarting run.py.

Persists to ``data/trading_controls.json``. When a key is set here it overrides
``.env`` / ``strategy_config.yaml`` for the running process and future starts.

Sacred rule: live capital still requires ``LIVE_TRADING_CONFIRMED`` — the UI
cannot bypass that gate.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from .audit_logger import audit_logger
from .market_calendar import now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLS_FILE = PROJECT_ROOT / "data" / "trading_controls.json"

VALID_EXTERNAL_SIGNALS_MODES = frozenset({"off", "advisory", "filter", "confirm"})

_DEFAULTS: Dict[str, Any] = {
    "options_trading_enabled": None,
    "futures_trading_enabled": None,
    "options_eod_flatten_enabled": None,
    "external_signals_enabled": None,
    "external_signals_mode": None,
}


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "confirmed"}


def _falsy_env(name: str, *, default: str = "false") -> bool:
    val = os.getenv(name, default).strip().lower()
    return val in {"0", "false", "no", "off"}


def load_controls() -> Dict[str, Any]:
    if not CONTROLS_FILE.exists():
        return deepcopy(_DEFAULTS)
    try:
        with open(CONTROLS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
        merged = deepcopy(_DEFAULTS)
        for key in _DEFAULTS:
            if key in raw:
                merged[key] = raw[key]
        merged["updated_at"] = raw.get("updated_at")
        merged["updated_by"] = raw.get("updated_by")
        return merged
    except Exception as exc:
        logger.warning("[TradingControls] load failed: %s", exc)
        return deepcopy(_DEFAULTS)


def save_controls(controls: Dict[str, Any], *, updated_by: str = "settings_ui") -> Path:
    CONTROLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: controls.get(k) for k in _DEFAULTS}
    payload["updated_at"] = now_ist().isoformat()
    payload["updated_by"] = updated_by
    with open(CONTROLS_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return CONTROLS_FILE


def apply_runtime_to_environment() -> None:
    """Sync persisted controls into os.environ at startup and after portal saves."""
    controls = load_controls()
    if controls.get("options_trading_enabled") is not None:
        os.environ["OPTIONS_TRADING_ENABLED"] = (
            "true" if controls["options_trading_enabled"] else "false"
        )
    if controls.get("futures_trading_enabled") is not None:
        os.environ["FUTURES_TRADING_ENABLED"] = (
            "true" if controls["futures_trading_enabled"] else "false"
        )
    if controls.get("options_eod_flatten_enabled") is not None:
        os.environ["OPTIONS_EOD_FLATTEN"] = (
            "true" if controls["options_eod_flatten_enabled"] else "false"
        )


def effective_options_trading_enabled() -> bool:
    controls = load_controls()
    if controls.get("options_trading_enabled") is not None:
        return bool(controls["options_trading_enabled"])
    from .config_loader import get_options_config

    cfg = get_options_config()
    env_ok = _truthy_env("OPTIONS_TRADING_ENABLED")
    return bool(cfg.get("trading_enabled")) and env_ok


def effective_options_eod_flatten_enabled() -> bool:
    controls = load_controls()
    if controls.get("options_eod_flatten_enabled") is not None:
        return bool(controls["options_eod_flatten_enabled"])
    return os.getenv("OPTIONS_EOD_FLATTEN", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }


def effective_futures_trading_enabled() -> bool:
    controls = load_controls()
    if controls.get("futures_trading_enabled") is not None:
        return bool(controls["futures_trading_enabled"])
    if _falsy_env("FUTURES_TRADING_ENABLED", default="false"):
        return False
    from .config_loader import get_options_config

    return bool(get_options_config().get("futures_trading_enabled", False))


def effective_external_signals_config() -> Dict[str, Any]:
    from .config_loader import get_external_signals_config

    return get_external_signals_config()


def get_trading_controls_status() -> Dict[str, Any]:
    """Full status for Settings UI — effective values + sources."""
    from .config_loader import get_options_config
    from .risk_gatekeeper import risk_gatekeeper

    controls = load_controls()
    yaml_opts = get_options_config()
    from .config_loader import get_external_signals_config

    yaml_ext = get_external_signals_config()
    live_confirmed = _truthy_env("LIVE_TRADING_CONFIRMED")
    force_dry = bool(risk_gatekeeper.config.force_dry_run)

    return {
        "available": True,
        "timestamp": now_ist().isoformat(),
        "persisted": {
            "options_trading_enabled": controls.get("options_trading_enabled"),
            "futures_trading_enabled": controls.get("futures_trading_enabled"),
            "options_eod_flatten_enabled": controls.get("options_eod_flatten_enabled"),
            "external_signals_enabled": controls.get("external_signals_enabled"),
            "external_signals_mode": controls.get("external_signals_mode"),
            "updated_at": controls.get("updated_at"),
            "updated_by": controls.get("updated_by"),
            "file": str(CONTROLS_FILE),
        },
        "effective": {
            "options_trading_enabled": effective_options_trading_enabled(),
            "futures_trading_enabled": effective_futures_trading_enabled(),
            "options_eod_flatten_enabled": effective_options_eod_flatten_enabled(),
            "external_signals_enabled": bool(yaml_ext.get("enabled")),
            "external_signals_mode": yaml_ext.get("mode", "filter"),
            "force_dry_run": force_dry,
            "paper_mode": force_dry,
            "live_trading_confirmed": live_confirmed,
        },
        "yaml_defaults": {
            "options_trading_enabled": bool(yaml_opts.get("trading_enabled")),
            "futures_trading_enabled": bool(yaml_opts.get("futures_trading_enabled")),
        },
        "env": {
            "OPTIONS_TRADING_ENABLED": os.getenv("OPTIONS_TRADING_ENABLED", ""),
            "FUTURES_TRADING_ENABLED": os.getenv("FUTURES_TRADING_ENABLED", ""),
            "OPTIONS_EOD_FLATTEN": os.getenv("OPTIONS_EOD_FLATTEN", "true"),
            "FORCE_DRY_RUN": os.getenv("FORCE_DRY_RUN", "true"),
            "LIVE_TRADING_CONFIRMED": os.getenv("LIVE_TRADING_CONFIRMED", ""),
        },
        "notes": [
            "Portal toggles save to data/trading_controls.json and apply immediately.",
            "Live orders still require LIVE_TRADING_CONFIRMED in .env (not toggleable here).",
            "Reset a toggle by clearing trading_controls.json or using Reset to file defaults.",
        ],
    }


def update_trading_controls(patch: Dict[str, Any], *, updated_by: str = "settings_ui") -> Dict[str, Any]:
    """Apply portal patch; returns updated status."""
    controls = load_controls()
    changed: Dict[str, Any] = {}

    if "options_trading_enabled" in patch:
        val = bool(patch["options_trading_enabled"])
        controls["options_trading_enabled"] = val
        os.environ["OPTIONS_TRADING_ENABLED"] = "true" if val else "false"
        changed["options_trading_enabled"] = val

    if "futures_trading_enabled" in patch:
        val = bool(patch["futures_trading_enabled"])
        controls["futures_trading_enabled"] = val
        os.environ["FUTURES_TRADING_ENABLED"] = "true" if val else "false"
        changed["futures_trading_enabled"] = val

    if "options_eod_flatten_enabled" in patch:
        val = bool(patch["options_eod_flatten_enabled"])
        controls["options_eod_flatten_enabled"] = val
        os.environ["OPTIONS_EOD_FLATTEN"] = "true" if val else "false"
        changed["options_eod_flatten_enabled"] = val

    if "external_signals_enabled" in patch:
        val = bool(patch["external_signals_enabled"])
        controls["external_signals_enabled"] = val
        changed["external_signals_enabled"] = val

    if "external_signals_mode" in patch:
        mode = str(patch["external_signals_mode"]).strip().lower()
        if mode not in VALID_EXTERNAL_SIGNALS_MODES:
            return {
                "success": False,
                "message": f"Invalid external_signals_mode: {mode}",
                "allowed": sorted(VALID_EXTERNAL_SIGNALS_MODES),
            }
        controls["external_signals_mode"] = mode
        changed["external_signals_mode"] = mode

    if not changed:
        return {"success": False, "message": "No valid fields in patch"}

    save_controls(controls, updated_by=updated_by)
    audit_logger.record("settings.trading_controls_updated", {
        "changed": changed,
        "updated_by": updated_by,
    })
    logger.info("[TradingControls] Updated from %s: %s", updated_by, changed)

    return {
        "success": True,
        "message": "Trading controls updated — effective immediately",
        "changed": changed,
        "status": get_trading_controls_status(),
    }


def reset_trading_controls(*, updated_by: str = "settings_ui") -> Dict[str, Any]:
    """Clear portal overrides — fall back to .env + YAML."""
    if CONTROLS_FILE.exists():
        CONTROLS_FILE.unlink()
    audit_logger.record("settings.trading_controls_reset", {"updated_by": updated_by})
    return {
        "success": True,
        "message": "Portal overrides cleared — using .env and strategy_config.yaml",
        "status": get_trading_controls_status(),
    }