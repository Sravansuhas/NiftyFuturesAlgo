"""
Integration tests: CandleBuilder ↔ PreviousCandleBreakoutStrategy (no live Kite).
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candle_builder import CandleBuilder, get_previous_candle_for_symbol
from app.paper_trading_params import DEFAULT_PAPER_PARAMS
from app.strategy import PreviousCandleBreakoutStrategy


IST = ZoneInfo("Asia/Kolkata")
TOKEN = 256265


def _ist(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=IST)


class _MockWsFeed:
    def __init__(self, builder: CandleBuilder):
        self._builder = builder

    def get_candle_builder(self):
        return self._builder


def _seed_completed_5m_bar(builder: CandleBuilder, high: float, low: float, close: float):
    """Build one completed 5m bar then start the next bucket (forming)."""
    builder.on_tick(TOKEN, close, _ist(2026, 6, 15, 9, 15, 10))
    builder.on_tick(TOKEN, high, _ist(2026, 6, 15, 9, 17, 0))
    builder.on_tick(TOKEN, low, _ist(2026, 6, 15, 9, 18, 0))
    builder.on_tick(TOKEN, close, _ist(2026, 6, 15, 9, 19, 0))
    completed, _ = builder.on_tick(TOKEN, high, _ist(2026, 6, 15, 9, 20, 0))
    assert "5m" in completed
    assert completed["5m"]["high"] == high
    assert completed["5m"]["low"] == low
    assert completed["5m"]["close"] == close


class GetPreviousCandleHelperTests(unittest.TestCase):
    def test_returns_none_without_builder(self):
        self.assertIsNone(get_previous_candle_for_symbol(TOKEN, None))

    def test_returns_none_without_completed_candles(self):
        builder = CandleBuilder(intervals=["5m"])
        builder.on_tick(TOKEN, 100.0, _ist(2026, 6, 15, 9, 15, 0))
        self.assertIsNone(get_previous_candle_for_symbol(TOKEN, builder))

    def test_returns_last_completed_not_forming(self):
        builder = CandleBuilder(intervals=["5m"])
        _seed_completed_5m_bar(builder, high=105.0, low=98.0, close=103.0)

        prev = get_previous_candle_for_symbol(TOKEN, builder)
        self.assertIsNotNone(prev)
        self.assertEqual(prev["source"], "ws_candle")
        self.assertEqual(prev["high"], 105.0)
        self.assertEqual(prev["low"], 98.0)
        self.assertEqual(prev["close"], 103.0)
        self.assertEqual(prev["open_time"], _ist(2026, 6, 15, 9, 15, 0))


class StrategyCandleIntegrationTests(unittest.TestCase):
    def _make_strategy(self, builder: CandleBuilder | None = None) -> PreviousCandleBreakoutStrategy:
        kite = MagicMock()
        strat = PreviousCandleBreakoutStrategy(
            kite=kite,
            paper_params=DEFAULT_PAPER_PARAMS,
            risk_manager=None,
        )
        strat.symbol = "NIFTY26JUNFUT"
        strat.instrument_token = TOKEN
        strat._index_key = "NIFTY"
        if builder is not None:
            strat.ws_feed = _MockWsFeed(builder)
        return strat

    def test_seed_prefers_ws_candle_over_rest(self):
        builder = CandleBuilder(intervals=["5m"])
        _seed_completed_5m_bar(builder, high=23480.0, low=23420.0, close=23450.0)

        strat = self._make_strategy(builder)
        strat.kite.historical_data.return_value = []
        strat._seed_previous_candle()

        self.assertEqual(strat.prev_high, 23480.0)
        self.assertEqual(strat.prev_low, 23420.0)
        self.assertEqual(strat._prev_candle_source, "ws_candle")

    def test_seed_falls_back_to_rest_historical(self):
        strat = self._make_strategy(builder=None)
        strat.kite.historical_data.return_value = [
            {"high": 100.0, "low": 90.0, "close": 95.0, "volume": 1000},
            {"high": 110.0, "low": 105.0, "close": 108.0, "volume": 2000},
            {"high": 120.0, "low": 115.0, "close": 118.0, "volume": 3000},
        ]

        strat._seed_previous_candle()

        strat.kite.historical_data.assert_called_once()
        self.assertEqual(strat.prev_high, 110.0)
        self.assertEqual(strat.prev_low, 105.0)
        self.assertEqual(strat._prev_candle_source, "rest_historical")

    def test_roll_syncs_new_completed_ws_candle(self):
        builder = CandleBuilder(intervals=["5m"])
        _seed_completed_5m_bar(builder, high=100.0, low=90.0, close=95.0)

        strat = self._make_strategy(builder)
        strat._seed_previous_candle()
        self.assertEqual(strat.prev_high, 100.0)

        builder.on_tick(TOKEN, 112.0, _ist(2026, 6, 15, 9, 20, 30))
        builder.on_tick(TOKEN, 108.0, _ist(2026, 6, 15, 9, 21, 0))
        builder.on_tick(TOKEN, 112.0, _ist(2026, 6, 15, 9, 22, 0))
        completed, _ = builder.on_tick(TOKEN, 110.0, _ist(2026, 6, 15, 9, 25, 0))
        self.assertIn("5m", completed)
        self.assertEqual(completed["5m"]["high"], 112.0)
        self.assertEqual(completed["5m"]["low"], 100.0)

        strat._roll_previous_candle_if_needed(111.0)

        self.assertEqual(strat.prev_high, 112.0)
        self.assertEqual(strat.prev_low, 100.0)
        self.assertEqual(strat._prev_candle_source, "ws_candle")

    def test_snapshot_includes_prev_candle_source(self):
        builder = CandleBuilder(intervals=["5m"])
        _seed_completed_5m_bar(builder, high=200.0, low=190.0, close=195.0)

        strat = self._make_strategy(builder)
        strat._seed_previous_candle()
        strat._last_known_price = 196.0
        strat._last_price_source = "WS"
        strat.current_atr = 25.0
        strat.fast_atr = 20.0

        snap = strat.get_signal_snapshot()
        self.assertEqual(snap["prev_candle_source"], "ws_candle")
        self.assertEqual(snap["prev_high"], 200.0)
        self.assertEqual(snap["prev_low"], 190.0)


if __name__ == "__main__":
    unittest.main()