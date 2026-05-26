"""
Clean, efficient configuration loader for NiftyFuturesAlgo.

Supports YAML (preferred) and falls back to JSON.
Keeps the system configurable without hardcoding parameters in code.
"""

import os
from pathlib import Path
from typing import Dict, Any
import json

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


DEFAULT_CONFIG_PATH = Path("config/strategy_config.yaml")


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


# Global config (loaded once at import)
config = load_config()
