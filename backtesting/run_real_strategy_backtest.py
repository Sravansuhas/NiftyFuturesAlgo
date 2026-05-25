"""
backtesting/run_real_strategy_backtest.py
Backtest with metrics and parameter tuning.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from backtesting.backtester import Backtester
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.metrics import calculate_metrics


def generate_nifty_data(n_bars: int = 1200) -> pd.DataFrame:
    np.random.seed(42)
    prices = [24000.0]
    for _ in range(n_bars - 1):
        change = np.random.normal(0, 7)
        prices.append(prices[-1] + change)

    return pd.DataFrame({
        'open': prices,
        'high': [p + abs(np.random.normal(0, 4)) for p in prices],
        'low': [p - abs(np.random.normal(0, 4)) for p in prices],
        'close': prices,
        'volume': np.random.randint(80000, 200000, n_bars)
    }, index=pd.date_range('2026-05-20', periods=n_bars, freq='5min'))


if __name__ == "__main__":
    print("Backtesting Previous Candle Breakout Strategy with Tuning...\n")

    data = generate_nifty_data(1500)

    param_grid = [
        {"profit_target": 20, "stop_loss": 12},
        {"profit_target": 25, "stop_loss": 15},
        {"profit_target": 30, "stop_loss": 18},
    ]

    best_return = -999
    best_metrics = None

    for params in param_grid:
        print(f"--- PT={params['profit_target']}, SL={params['stop_loss']} ---")

        strategy = PreviousCandleBacktestStrategy(
            profit_target=params['profit_target'],
            stop_loss=params['stop_loss']
        )

        backtester = Backtester(strategy, initial_capital=1_000_000)
        results = backtester.run(data)

        metrics = calculate_metrics(results['trades'], results['equity_curve'], 1_000_000)

        print(f"Return: {metrics.get('total_return_pct', 0)}% | "
              f"Win Rate: {metrics.get('win_rate_pct', 0)}% | "
              f"Max DD: {metrics.get('max_drawdown_pct', 0)}% | "
              f"Trades: {metrics.get('total_trades', 0)}")

        if metrics.get('total_return_pct', 0) > best_return:
            best_return = metrics.get('total_return_pct', 0)
            best_metrics = {**params, **metrics}

    print("\n" + "="*60)
    print("BEST RESULT")
    print("="*60)
    for k, v in best_metrics.items():
        print(f"{k}: {v}")
    print("="*60)
