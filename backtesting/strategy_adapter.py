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
        self.prev_high = 0.0
        self.prev_low = 0.0

    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        """
        Use the improved live strategy for signal logic where possible, but fall back to
        safe local previous-candle tracking for backtest (no private method dependency).
        The dedicated PreviousCandleBacktestStrategy is preferred for clean backtests.
        """
        current_price = float(bar['close'])
        current_high = float(bar.get('high', current_price))
        current_low = float(bar.get('low', current_price))

        # Maintain our own simple previous state (live_strategy may have its own)
        if self.prev_high == 0 or self.prev_low == 0:
            self.prev_high = current_high
            self.prev_low = current_low
            return None

        if (not self.live_strategy.has_entered_today and
            self.prev_high > 0 and
            current_price > self.prev_high + 4):

            self.entry_price = current_price
            self.position = 75
            self.live_strategy.has_entered_today = True
            # Roll
            self.prev_high = current_high
            self.prev_low = current_low
            return {'signal': 'BUY', 'price': current_price, 'quantity': 75}

        if (not self.live_strategy.has_entered_today and
            self.prev_low > 0 and
            current_price < self.prev_low - 4):

            self.entry_price = current_price
            self.position = -75
            self.live_strategy.has_entered_today = True
            self.prev_high = current_high
            self.prev_low = current_low
            return {'signal': 'SELL', 'price': current_price, 'quantity': 75}

        # Roll even on no signal
        self.prev_high = current_high
        self.prev_low = current_low
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
