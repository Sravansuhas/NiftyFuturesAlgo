"""
backtesting/example_backtest.py
Simple example to test the Backtester.
This version includes a reliable import fix.
"""

import sys
import os

# Add project root to Python path so imports work reliably
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from backtesting.backtester import Backtester, BaseBacktestStrategy
from backtesting.costs import TransactionCostModel, CostConfig


class SimpleBreakoutStrategy(BaseBacktestStrategy):
    """Very simple strategy for testing the backtester."""

    def __init__(self, profit_target: float = 25.0, stop_loss: float = 15.0):
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.entry_price = 0.0
        self.position = 0

    def on_bar(self, bar: pd.Series) -> dict:
        current_price = bar['close']

        if self.prev_high == 0:
            self.prev_high = current_price + 20
            self.prev_low = current_price - 20
            return None

        if current_price > self.prev_high and self.position == 0:
            self.entry_price = current_price
            self.position = 1
            return {'signal': 'BUY', 'price': current_price}

        if current_price < self.prev_low and self.position == 0:
            self.entry_price = current_price
            self.position = -1
            return {'signal': 'SELL', 'price': current_price}

        return None

    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = bar['close']
        pnl = current_price - entry_price if position > 0 else entry_price - current_price

        if pnl >= self.profit_target or pnl <= -self.stop_loss:
            self.position = 0
            self.entry_price = 0.0
            return True
        return False


def generate_sample_data(n_bars: int = 600) -> pd.DataFrame:
    np.random.seed(42)
    prices = [24000]
    for _ in range(n_bars - 1):
        change = np.random.normal(0, 8)
        prices.append(prices[-1] + change)

    data = pd.DataFrame({
        'open': prices,
        'high': [p + abs(np.random.normal(0, 5)) for p in prices],
        'low': [p - abs(np.random.normal(0, 5)) for p in prices],
        'close': prices,
        'volume': np.random.randint(80000, 200000, n_bars)
    }, index=pd.date_range(start='2026-05-01', periods=n_bars, freq='5min'))

    return data


if __name__ == "__main__":
    print("Running Example Backtest...")

    data = generate_sample_data(700)
    strategy = SimpleBreakoutStrategy(profit_target=25.0, stop_loss=15.0)

    # Realistic Zerodha Nifty futures costs (this is the key improvement)
    cost_model = TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=45.0,
        default_slippage_points=4.0,   # slightly conservative for generated data
        lot_size=75,
    ))
    backtester = Backtester(strategy, initial_capital=1_000_000, cost_model=cost_model)
    results = backtester.run(data)

    print("\nBacktest Summary:")
    print(f"Final Equity : Rs {results['final_equity']:,.2f}")
    print(f"Total Return : {results['total_return_pct']:.2f}%")
    print(f"Total Trades : {len([t for t in results['trades'] if 'exit_time' in t])}")
