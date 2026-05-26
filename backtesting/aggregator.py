"""
backtesting/aggregator.py
Multi-strategy aggregator with 4/5 voting and confidence scoring.
"""

from typing import List, Dict, Any
import pandas as pd
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy


class MultiStrategyAggregator:
    def __init__(self, capital: float = 1_000_000):
        self.strategies = [
            PreviousCandleBacktestStrategy(capital=capital, profit_target=25.0, stop_loss=15.0)
        ]  # Add more strategies here in future

    def on_bar(self, bar: pd.Series) -> Dict[str, Any]:
        signals = []
        for strategy in self.strategies:
            signal = strategy.on_bar(bar)
            if signal:
                signals.append(signal)

        buy_count = sum(1 for s in signals if s['signal'] == 'BUY')
        sell_count = sum(1 for s in signals if s['signal'] == 'SELL')

        if buy_count >= 4:
            return {'signal': 'BUY', 'confidence': 80}
        if sell_count >= 4:
            return {'signal': 'SELL', 'confidence': 80}
        return {'signal': None, 'confidence': 0}