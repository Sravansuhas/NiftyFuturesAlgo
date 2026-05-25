"""
backtesting/strategy_adapter.py
Adapter that allows us to use the live PreviousCandleBreakoutStrategy
inside the Backtester without changing the original code.
"""

import pandas as pd
from typing import Optional, Dict, Any
from backtesting.backtester import BaseBacktestStrategy

# Import your live strategy
from app.strategy import PreviousCandleBreakoutStrategy


class LiveStrategyAdapter(BaseBacktestStrategy):
    """
    Wraps PreviousCandleBreakoutStrategy so it works with the Backtester.
    """

    def __init__(self, profit_target: float = 25.0, stop_loss: float = 15.0):
        # We create an instance of your live strategy (without needing a real Kite object)
        self.live_strategy = PreviousCandleBreakoutStrategy(
            kite=None,  # We don't need real Kite connection in backtest
            profit_target=profit_target,
            stop_loss=stop_loss
        )
        self.position = 0
        self.entry_price = 0.0

    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        """
        Convert DataFrame row into something the live strategy can understand,
        then check for entry signals.
        """
        current_price = bar['close']

        # Feed current price into the live strategy's internal logic
        # We manually update previous candle levels for backtesting
        self.live_strategy._update_previous_candle(current_price)

        # Check for long breakout
        if (not self.live_strategy.has_entered_today and
            self.live_strategy.prev_high > 0 and
            current_price > self.live_strategy.prev_high):

            self.entry_price = current_price
            self.position = 75
            self.live_strategy.has_entered_today = True
            return {'signal': 'BUY', 'price': current_price}

        # Check for short breakout
        if (not self.live_strategy.has_entered_today and
            self.live_strategy.prev_low > 0 and
            current_price < self.live_strategy.prev_low):

            self.entry_price = current_price
            self.position = -75
            self.live_strategy.has_entered_today = True
            return {'signal': 'SELL', 'price': current_price}

        return None

    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = bar['close']
        pnl = current_price - entry_price if position > 0 else entry_price - current_price

        if pnl >= self.live_strategy.profit_target or pnl <= -self.live_strategy.stop_loss:
            self.position = 0
            self.entry_price = 0.0
            self.live_strategy.has_entered_today = False
            return True

        return False
