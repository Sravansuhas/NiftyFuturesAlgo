"""Integration tests for Phase A backtest fixes (audit 2026-06-10)."""

import unittest
from datetime import datetime, time
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytz

from backtesting.backtester import Backtester, BaseBacktestStrategy
from backtesting.data_loader import _annotate_front_month_and_rollover, _filter_to_front_month
from backtesting.previous_candle_backtest_strategy import (
    PreviousCandleBacktestStrategy,
    StrategyParams,
)

IST = pytz.FixedOffset(330)


def _make_bars(n: int, start: str = "2026-01-02 09:15:00", freq: str = "5min") -> pd.DataFrame:
    """Synthetic OHLCV with enough volatility for breakout signals."""
    idx = pd.date_range(start, periods=n, freq=freq, tz=IST)
    base = 24000 + np.cumsum(np.random.default_rng(7).normal(0, 8, n))
    noise = np.abs(np.random.default_rng(8).normal(0, 6, n)) + 2
    return pd.DataFrame(
        {
            "open": base,
            "high": base + noise,
            "low": base - noise,
            "close": base,
            "volume": np.random.default_rng(9).integers(80_000, 200_000, n),
        },
        index=idx,
    )


class TestStrategyParamsPreservation(unittest.TestCase):
    def test_non_research_mode_keeps_grid_params(self):
        s = PreviousCandleBacktestStrategy(
            risk_per_trade_pct=0.004,
            breakout_atr_mult=0.65,
            research_mode=False,
        )
        self.assertEqual(s.params.risk_per_trade_pct, 0.004)
        self.assertEqual(s.params.breakout_atr_mult, 0.65)
        self.assertFalse(s.params.research_mode)

    def test_research_mode_relaxes_without_wiping_custom_risk(self):
        s = PreviousCandleBacktestStrategy(
            risk_per_trade_pct=0.004,
            breakout_atr_mult=0.70,
            research_mode=True,
        )
        self.assertEqual(s.params.risk_per_trade_pct, 0.004)
        self.assertLessEqual(s.params.breakout_atr_mult, 0.70)
        self.assertGreaterEqual(s.params.max_trades_per_day, 10)

    def test_params_dataclass_path_preserved(self):
        sp = StrategyParams(
            risk_per_trade_pct=0.0045,
            breakout_atr_mult=0.62,
            research_mode=False,
            session_start=time(10, 30),
        )
        s = PreviousCandleBacktestStrategy(params=sp)
        self.assertEqual(s.params.risk_per_trade_pct, 0.0045)
        self.assertEqual(s.params.breakout_atr_mult, 0.62)
        self.assertEqual(s.params.session_start, time(10, 30))

    def test_default_init_without_args(self):
        s = PreviousCandleBacktestStrategy()
        self.assertIsInstance(s.params, StrategyParams)
        self.assertEqual(s.params.risk_per_trade_pct, 0.0035)


class TestBacktesterMetrics(unittest.TestCase):
    def test_run_includes_profit_factor_and_drawdown(self):
        params = StrategyParams(
            research_mode=True,
            max_trades_per_day=20,
            session_start=time(9, 15),
            session_end=time(15, 30),
            min_prev_candle_range_atr=0.1,
            breakout_atr_mult=0.4,
            use_trend_filter=False,
        )
        strategy = PreviousCandleBacktestStrategy(params=params)
        data = _make_bars(600)
        result = Backtester(strategy, verbose=False).run(data)

        self.assertIn("profit_factor", result)
        self.assertIn("max_drawdown_pct", result)
        self.assertIn("win_rate_pct", result)
        self.assertIsInstance(result["profit_factor"], (int, float))


class TestBacktesterDailyReset(unittest.TestCase):
    def test_reset_daily_called_on_new_trading_day(self):
        class CountingStrategy(BaseBacktestStrategy):
            def __init__(self):
                self.reset_count = 0

            def reset_daily(self):
                self.reset_count += 1

            def on_bar(self, bar):
                return None

            def on_exit(self, bar, position, entry_price):
                return False

        strategy = CountingStrategy()
        idx = pd.DatetimeIndex(
            [
                "2026-01-02 09:15:00",
                "2026-01-02 09:20:00",
                "2026-01-03 09:15:00",
                "2026-01-03 09:20:00",
            ],
            tz=IST,
        )
        data = pd.DataFrame(
            {
                "open": [1, 1, 1, 1],
                "high": [1, 1, 1, 1],
                "low": [1, 1, 1, 1],
                "close": [1, 1, 1, 1],
                "volume": [100, 100, 100, 100],
            },
            index=idx,
        )
        Backtester(strategy, verbose=False).run(data)
        self.assertEqual(strategy.reset_count, 1)


class TestFrontMonthFilter(unittest.TestCase):
    def test_filter_keeps_only_active_contract(self):
        ts = pd.date_range("2026-04-01 09:15", periods=4, freq="5min", tz=IST)
        df = pd.DataFrame(
            {
                "timestamp": list(ts) * 2,
                "symbol": ["NIFTY26APR26"] * 4 + ["NIFTY26MAY26"] * 4,
                "expiry": [pd.Timestamp("2026-04-24", tz=IST)] * 4
                + [pd.Timestamp("2026-05-29", tz=IST)] * 4,
                "open": np.arange(8, dtype=float) + 24000,
                "high": np.arange(8, dtype=float) + 24010,
                "low": np.arange(8, dtype=float) + 23990,
                "close": np.arange(8, dtype=float) + 24000,
                "volume": [100_000] * 8,
            }
        )
        annotated = _annotate_front_month_and_rollover(df.set_index("timestamp"))
        filtered = _filter_to_front_month(annotated)

        self.assertEqual(len(filtered), 4)
        self.assertTrue((filtered["symbol"] == filtered["front_month"]).all())


class TestWalkForwardTradesList(unittest.TestCase):
    def test_fold_results_include_trades_list(self):
        from backtesting.walk_forward_runner import run_walk_forward

        data = _make_bars(1200)
        param_grid = {
            "research_mode": [True],
            "max_trades_per_day": [15],
            "breakout_atr_mult": [0.45],
            "risk_per_trade_pct": [0.004],
            "use_trend_filter": [False],
            "min_prev_candle_range_atr": [0.1],
        }
        summary = run_walk_forward(
            strategy_class=PreviousCandleBacktestStrategy,
            data=data,
            param_grid=param_grid,
            n_folds=2,
            train_size=0.6,
            min_trades_for_validity=1,
        )
        folds = summary.get("folds", [])
        self.assertGreater(len(folds), 0)
        for fold in folds:
            self.assertIn("trades_list", fold)
            self.assertIsInstance(fold["trades_list"], list)
            if fold.get("test_pf"):
                self.assertIsInstance(fold["test_pf"], (int, float))


class TestIntrabarExits(unittest.TestCase):
    def test_intrabar_stop_hit_before_target_long(self):
        """Conservative stop-first: low hits stop before high would hit target."""
        from app.breakout_core import ExitConfig

        class StopFirstStrategy(BaseBacktestStrategy):
            use_intrabar_exits = True
            exit_config = ExitConfig(profit_target_pts=20.0, stop_loss_pts=10.0)

            def __init__(self):
                self.bars_seen = 0

            def on_bar(self, bar):
                self.bars_seen += 1
                if self.bars_seen == 1:
                    return {"signal": "BUY", "quantity": 75}
                return None

            def on_exit(self, bar, position, entry_price):
                return False

        idx = pd.DatetimeIndex(
            [
                "2026-01-02 09:15:00",
                "2026-01-02 09:20:00",
            ],
            tz=IST,
        )
        data = pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [101.0, 125.0],
                "low": [99.0, 89.0],
                "close": [100.0, 110.0],
                "volume": [100_000, 100_000],
            },
            index=idx,
        )

        result = Backtester(StopFirstStrategy(), verbose=False).run(data)
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_price"], 100.0)
        self.assertEqual(trade["exit_price"], 90.0)
        self.assertEqual(trade.get("exit_reason"), "stop_loss")

    def test_entry_on_next_bar_uses_open(self):
        class NextBarStrategy(BaseBacktestStrategy):
            entry_on_next_bar = True

            def __init__(self):
                self.bars_seen = 0

            def on_bar(self, bar):
                self.bars_seen += 1
                if self.bars_seen == 1:
                    return {"signal": "BUY", "quantity": 75}
                return None

            def on_exit(self, bar, position, entry_price):
                return False

        idx = pd.DatetimeIndex(
            [
                "2026-01-02 09:15:00",
                "2026-01-02 09:20:00",
                "2026-01-02 09:25:00",
            ],
            tz=IST,
        )
        data = pd.DataFrame(
            {
                "open": [100.0, 105.0, 106.0],
                "high": [101.0, 106.0, 107.0],
                "low": [99.0, 104.0, 105.0],
                "close": [100.5, 105.5, 106.5],
                "volume": [100_000, 100_000, 100_000],
            },
            index=idx,
        )

        backtester = Backtester(NextBarStrategy(), verbose=False)
        result = backtester.run(data)
        self.assertEqual(len(result["trades"]), 0)
        self.assertEqual(backtester.entry_price, 105.0)
        self.assertEqual(backtester.position, 75)


class TestBreakoutCore(unittest.TestCase):
    def test_trailing_stop_triggers(self):
        from app.breakout_core import ExitConfig, ExitState, should_exit_position
        import pandas as pd
        import pytz

        ist = pytz.FixedOffset(330)
        entry_t = pd.Timestamp("2026-01-02 10:00", tz=ist)
        bar_t = pd.Timestamp("2026-01-02 10:30", tz=ist)
        cfg = ExitConfig(profit_target_pts=100, stop_loss_pts=20, trail_atr_mult=1.5)
        state = ExitState(best_price=24100, entry_time=entry_t)
        exit_now, _, reason = should_exit_position(
            24070, 24000, True, 15.0, cfg, state, bar_t
        )
        self.assertTrue(exit_now)
        self.assertEqual(reason, "trailing_stop")


class TestJobStore(unittest.TestCase):
    def test_save_and_load_job(self):
        from backtesting.job_store import save_job, load_job, delete_job

        save_job("test99", {"status": "completed", "progress": 100, "started_at": 1})
        loaded = load_job("test99")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "completed")
        delete_job("test99")


if __name__ == "__main__":
    unittest.main()