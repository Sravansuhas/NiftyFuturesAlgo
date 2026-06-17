import sys
import unittest
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config_loader import (
    apply_symbol_config_to_paper_params,
    get_symbol_config,
    get_symbol_max_trades,
    normalize_index_key,
)
from app.paper_trading_params import DEFAULT_PAPER_PARAMS


SAMPLE_CONFIG = {
    "paper_trading": {
        "session_start": "09:45",
        "session_end": "15:10",
        "breakout_atr_mult": 0.78,
        "min_atr_points": 6.0,
        "stop_loss_atr_mult": 1.1,
        "profit_target_atr_mult": 2.1,
        "max_trades_per_day": 3,
    },
    "symbols": {
        "NIFTY": {
            "min_atr_points": 6.0,
            "max_trades_per_day": 3,
        },
        "BANKNIFTY": {
            "min_atr_points": 12.0,
            "breakout_atr_mult": 0.72,
            "stop_loss_atr_mult": 1.15,
            "max_trades_per_day": 2,
        },
        "SENSEX": {
            "min_atr_points": 18.0,
            "session_start": "09:55",
            "session_end": "15:00",
            "max_trades_per_day": 2,
        },
    },
}


class SymbolConfigTests(unittest.TestCase):
    def test_normalize_index_key_aliases(self):
        self.assertEqual(normalize_index_key("BANKNIFTY26JUNFUT"), "BANKNIFTY")
        self.assertEqual(normalize_index_key("BNF"), "BANKNIFTY")
        self.assertEqual(normalize_index_key("SENSEX26JUNFUT"), "SENSEX")
        self.assertEqual(normalize_index_key("NIFTY26JUNFUT"), "NIFTY")

    def test_merge_global_and_symbol_overrides(self):
        cfg = get_symbol_config("BANKNIFTY", SAMPLE_CONFIG)
        self.assertEqual(cfg["min_atr_points"], 12.0)
        self.assertEqual(cfg["breakout_atr_mult"], 0.72)
        self.assertEqual(cfg["stop_loss_atr_mult"], 1.15)
        self.assertEqual(cfg["max_trades_per_day"], 2)
        # Inherited from global paper_trading when not overridden
        self.assertEqual(cfg["profit_target_atr_mult"], 2.1)
        self.assertEqual(cfg["session_start"], "09:45")
        self.assertTrue(cfg["_has_symbol_overrides"])

    def test_global_defaults_when_symbol_key_missing_field(self):
        cfg = get_symbol_config("NIFTY", SAMPLE_CONFIG)
        self.assertEqual(cfg["breakout_atr_mult"], 0.78)
        self.assertEqual(cfg["stop_loss_atr_mult"], 1.1)
        self.assertEqual(cfg["profit_target_atr_mult"], 2.1)

    def test_unknown_symbol_falls_back_to_global_only(self):
        cfg = get_symbol_config("FINNIFTY", SAMPLE_CONFIG)
        self.assertEqual(cfg["min_atr_points"], 6.0)
        self.assertEqual(cfg["breakout_atr_mult"], 0.78)
        self.assertEqual(cfg["max_trades_per_day"], 3)
        self.assertFalse(cfg["_has_symbol_overrides"])
        self.assertEqual(cfg["_index_key"], "FINNIFTY")

    def test_apply_symbol_config_to_paper_params(self):
        params = apply_symbol_config_to_paper_params("SENSEX", config_data=SAMPLE_CONFIG)
        self.assertEqual(params.min_atr_points, 18.0)
        self.assertEqual(params.max_trades_per_day, 2)
        self.assertEqual(params.session_start, time(9, 55))
        self.assertEqual(params.session_end, time(15, 0))

    def test_apply_preserves_base_non_symbol_fields(self):
        params = apply_symbol_config_to_paper_params(
            "BANKNIFTY", base=DEFAULT_PAPER_PARAMS, config_data=SAMPLE_CONFIG
        )
        self.assertEqual(params.volume_confirmation, DEFAULT_PAPER_PARAMS.volume_confirmation)
        self.assertEqual(params.cooldown_minutes_after_trade, DEFAULT_PAPER_PARAMS.cooldown_minutes_after_trade)

    def test_get_symbol_max_trades(self):
        self.assertEqual(get_symbol_max_trades("BANKNIFTY", SAMPLE_CONFIG), 2)
        self.assertEqual(get_symbol_max_trades("NIFTY", SAMPLE_CONFIG), 3)
        self.assertEqual(get_symbol_max_trades("UNKNOWN", SAMPLE_CONFIG), 3)

    def test_live_yaml_banknifty_has_higher_min_atr(self):
        from app.config_loader import load_config

        live = load_config(ROOT / "config" / "strategy_config.yaml")
        nifty = get_symbol_config("NIFTY", live)
        bank = get_symbol_config("BANKNIFTY", live)
        sensex = get_symbol_config("SENSEX", live)
        self.assertGreater(bank["min_atr_points"], nifty["min_atr_points"])
        self.assertGreater(sensex["min_atr_points"], bank["min_atr_points"])
        self.assertEqual(sensex["session_start"], "09:55")


if __name__ == "__main__":
    unittest.main()