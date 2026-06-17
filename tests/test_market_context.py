import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_context import (
    _open_bias,
    _session_hints,
    build_market_context,
    fetch_fii_dii_flows,
    fetch_india_vix,
    load_market_context,
    save_market_context,
)


class MarketContextTests(unittest.TestCase):
    def test_open_bias_fii_selling(self):
        self.assertEqual(_open_bias("fii_selling", "normal"), "bearish_open")

    def test_open_bias_risk_on(self):
        self.assertEqual(_open_bias("risk_on", "normal"), "bullish_open")

    def test_session_hints_vix_extreme(self):
        hints = _session_hints(
            {"available": True, "level": 22.5, "zone": "extreme"},
            {"available": True, "flow_bias": "neutral"},
        )
        self.assertEqual(hints["posture_floor"], "contingency")
        self.assertLess(hints["max_trades_delta"], 0)

    def test_session_hints_fii_risk_off(self):
        hints = _session_hints(
            {"available": True, "level": 13.0, "zone": "normal"},
            {"available": True, "flow_bias": "risk_off"},
        )
        self.assertEqual(hints["open_bias"], "bearish_open")
        self.assertEqual(hints["posture_floor"], "defensive")

    def test_fetch_india_vix_nse_path(self):
        nse_payload = {
            "available": True,
            "level": 14.2,
            "zone": "normal",
            "change_pct": 1.1,
            "fetched_at": "t",
        }
        with patch("app.nse_data.fetch_india_vix", return_value=nse_payload):
            result = fetch_india_vix()
        self.assertTrue(result["available"])
        self.assertEqual(result["level"], 14.2)
        self.assertEqual(result["source"], "nse_all_indices")

    def test_fetch_india_vix_kite_fallback(self):
        kite = MagicMock()
        kite.quote.return_value = {
            "NSE:INDIA VIX": {
                "last_price": 16.5,
                "ohlc": {"close": 15.8},
            }
        }
        with patch("app.nse_data.fetch_india_vix", return_value={"available": False}):
            result = fetch_india_vix(kite=kite)
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "kite_quote")
        self.assertEqual(result["zone"], "elevated")

    def test_fetch_fii_dii_flows_delegates(self):
        flows = {
            "available": True,
            "fii_net_crores": -1200.0,
            "dii_net_crores": 800.0,
            "flow_bias": "fii_selling",
            "trade_date": "15-Jun-2026",
        }
        with patch("app.market_context.fetch_fii_dii_flow", return_value=flows):
            result = fetch_fii_dii_flows()
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "nse_fiidii_trade")

    def test_build_and_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market_context.json"
            vix = {"available": True, "level": 18.0, "zone": "elevated", "source": "nse_all_indices"}
            fii = {
                "available": True,
                "fii_net_crores": -900.0,
                "dii_net_crores": 500.0,
                "flow_bias": "fii_selling",
                "source": "nse_fiidii_trade",
            }
            with patch("app.market_context.fetch_india_vix", return_value=vix):
                with patch("app.market_context.fetch_fii_dii_flows", return_value=fii):
                    with patch("app.market_context.MARKET_CONTEXT_FILE", path):
                        payload = build_market_context(force_refresh=True)
            self.assertTrue(payload["available"])
            self.assertIn("session_hints", payload)
            loaded = load_market_context(path=path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["india_vix"]["level"], 18.0)


if __name__ == "__main__":
    unittest.main()