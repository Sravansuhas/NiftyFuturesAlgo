"""
Clean, efficient configuration loader for Aegis.

Supports YAML (preferred) and falls back to JSON.
Keeps the system configurable without hardcoding parameters in code.
"""

import os
from copy import deepcopy
from datetime import date, time
from pathlib import Path
from typing import Dict, Any, Optional, Union
import json

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "strategy_config.yaml"

KNOWN_INDEX_KEYS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})

# Fields that symbols: may override (subset of paper_trading / strategy gates)
SYMBOL_CONFIG_KEYS = frozenset({
    "min_atr_points",
    "breakout_atr_mult",
    "stop_loss_atr_mult",
    "profit_target_atr_mult",
    "max_trades_per_day",
    "session_start",
    "session_end",
    "lot_size",
})


def load_config(config_path: Path = None) -> Dict[str, Any]:
    """
    Load strategy configuration.
    Priority: explicit path > STRATEGY_CONFIG_PATH env > default config/strategy_config.yaml
    """
    if config_path is None:
        env_path = os.getenv("STRATEGY_CONFIG_PATH")
        config_path = Path(env_path) if env_path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        print(f"[CONFIG] Warning: Config file not found at {config_path}. Using defaults.")
        return _get_default_config()

    try:
        if config_path.suffix in (".yaml", ".yml") and YAML_AVAILABLE:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
        else:
            with open(config_path, "r") as f:
                config = json.load(f)

        print(f"[CONFIG] Loaded configuration from {config_path}")
        return config

    except Exception as e:
        print(f"[CONFIG] Failed to load {config_path}: {e}. Using defaults.")
        return _get_default_config()


def _get_default_config() -> Dict[str, Any]:
    """Safe minimal defaults if config loading fails."""
    return {
        "paper_trading": {
            "session_start": "09:45",
            "session_end": "15:10",
            "breakout_atr_mult": 0.78,
            "min_atr_points": 6.0,
            "risk_per_trade_pct": 0.0035,
            "max_trades_per_day": 3,
        },
        "risk": {
            "capital": 1_000_000,
            "max_daily_loss_pct": 0.02,
            "max_drawdown_pct": 0.08,
        }
    }


def get_authoritative_session_end(for_date: Optional[Union[date, str]] = None) -> str:
    """
    Conservative NSE F&O entry cutoff (HH:MM) for the active session regime.

    Aligns strategy ``session_end`` with ``market_calendar.get_entry_window_end``:
    15:00 before 2026-08-03; 15:10 on/after extended close (15:40).
    """
    from app.market_calendar import get_entry_window_end, now_ist

    if for_date is None:
        day = now_ist().date()
    elif isinstance(for_date, str):
        day = date.fromisoformat(for_date)
    else:
        day = for_date
    return get_entry_window_end(day).strftime("%H:%M")


def normalize_index_key(index_key: str) -> str:
    """Map broker / strategy strings to canonical index keys (BANKNIFTY before NIFTY)."""
    s = (index_key or "NIFTY").upper()
    if s.startswith("BANKNIFTY") or s.startswith("BNF"):
        return "BANKNIFTY"
    if s.startswith("SENSEX"):
        return "SENSEX"
    if s.startswith("NIFTY"):
        return "NIFTY"
    return s


def _parse_session_time(value: Any) -> Optional[time]:
    """Parse HH:MM strings or datetime.time for session bounds."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str) and ":" in value:
        parts = value.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    return None


def get_symbol_config(
    index_key: str,
    config_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Merge global ``paper_trading`` with per-symbol overrides from ``symbols:``.

    Unknown symbols (not in ``symbols:``) receive global defaults only — no crash,
    no silent remap to NIFTY overrides.
    """
    cfg = config_data if config_data is not None else config
    global_paper = dict(cfg.get("paper_trading") or {})
    key = normalize_index_key(index_key)
    symbols = cfg.get("symbols") or {}
    sym_overrides = dict(symbols.get(key) or {}) if key in symbols else {}
    merged = {**global_paper, **sym_overrides}
    merged["_index_key"] = key
    merged["_has_symbol_overrides"] = bool(sym_overrides)
    return merged


def apply_symbol_config_to_paper_params(
    index_key: str,
    base=None,
    config_data: Optional[Dict[str, Any]] = None,
):
    """
    Build ``PaperTradingParams`` with YAML global + per-symbol overrides applied.

    Accepts an optional ``base`` (e.g. promoted overlay) — symbol YAML overrides
    win for keys present under ``symbols:<key>``.
    """
    from .paper_trading_params import DEFAULT_PAPER_PARAMS, PaperTradingParams

    merged = get_symbol_config(index_key, config_data)
    params = deepcopy(base or DEFAULT_PAPER_PARAMS)

    for key in SYMBOL_CONFIG_KEYS:
        if key not in merged or key in ("lot_size",):
            continue
        raw = merged[key]
        if key in ("session_start", "session_end"):
            parsed = _parse_session_time(raw)
            if parsed is not None:
                setattr(params, key, parsed)
        elif hasattr(params, key):
            setattr(params, key, raw)

    return params


def get_symbol_max_trades(
    index_key: str,
    config_data: Optional[Dict[str, Any]] = None,
) -> int:
    """Per-symbol daily trade cap from merged config (falls back to global)."""
    merged = get_symbol_config(index_key, config_data)
    return int(merged.get("max_trades_per_day", 3))


def resolve_paper_session_bounds(
    for_date: Optional[Union[date, str]] = None,
    *,
    config_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Merge YAML paper_trading session bounds with date-aware NSE F&O close rules.

    ``session_end`` from config is capped at the authoritative entry cutoff so
    extended-session dates never allow entries past the safe pre-close buffer.
    """
    cfg = config_data if config_data is not None else config
    paper = dict(cfg.get("paper_trading") or {})
    auth_end = get_authoritative_session_end(for_date)
    yaml_end = str(paper.get("session_end") or auth_end)
    if yaml_end > auth_end:
        paper["session_end"] = auth_end
    else:
        paper["session_end"] = yaml_end
    paper.setdefault("session_start", "09:45")
    return paper


def get_options_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Options execution settings from strategy_config.yaml."""
    cfg = config_data if config_data is not None else config
    raw = dict(cfg.get("options") or {})
    defaults = {
        "trading_enabled": False,
        "futures_trading_enabled": False,
        "underlying": "NIFTY",
        "allowed_structures": ["iron_condor"],
        "product": "NRML",
        "max_structures_per_day": 1,
        "max_legs": 4,
        "max_premium_at_risk": 50_000.0,
        "max_structure_loss": 100_000.0,
        "max_margin_pct_of_capital": 0.15,
        "block_naked_short_vol": True,
        "evaluation_interval_sec": 300,
        "session_end": None,
        "default_iv": 0.16,
        "iv_floor": 0.12,
        "iv_cap": 0.35,
        "regime_gates": {
            "max_vix": 22.0,
            "min_vix": 10.0,
            "block_expiry_day_entries": False,
            "expiry_day_entry_cutoff_hour": 12,
            "enable_gamma_proxy": False,
            "gamma_proxy_hard_threshold": 0.00035,
            "gamma_proxy_soft_threshold": None,
            "gamma_proxy_oi_threshold": 8_000_000,
        },
        "iron_condor": {
            "wing_width": None,
            "body_width": None,
            "exit_profit_pct": 0.50,
            "exit_loss_pct": 1.0,
        },
    }
    merged = {**defaults, **raw}
    merged["regime_gates"] = {**defaults["regime_gates"], **(raw.get("regime_gates") or {})}
    merged["iron_condor"] = {**defaults["iron_condor"], **(raw.get("iron_condor") or {})}
    return merged


def _external_signals_yaml_defaults(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """YAML-only external_signals block (no portal overrides)."""
    cfg = config_data if config_data is not None else config
    raw = dict(cfg.get("external_signals") or {})
    defaults = {
        "enabled": True,
        "mode": "filter",
        "block_on_mismatch": True,
        "allow_when_empty": True,
    }
    return {**defaults, **raw}


def get_external_signals_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Options sheet → algo settings (YAML + portal overrides when config_data is None)."""
    merged = _external_signals_yaml_defaults(config_data)
    if config_data is not None:
        return merged
    try:
        from .trading_controls import load_controls

        controls = load_controls()
        if controls.get("external_signals_enabled") is not None:
            merged["enabled"] = bool(controls["external_signals_enabled"])
        if controls.get("external_signals_mode") is not None:
            merged["mode"] = str(controls["external_signals_mode"])
    except Exception:
        pass
    return merged


# Global config (loaded once at import)
config = load_config()
