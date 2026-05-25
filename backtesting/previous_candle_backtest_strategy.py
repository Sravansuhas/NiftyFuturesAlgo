"""
backtesting/previous_candle_backtest_strategy.py
Previous Candle Breakout Strategy with Improved Risk Management.
"""

import time
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
from backtesting.backtester import BaseBacktestStrategy


class PreviousCandleBacktestStrategy(BaseBacktestStrategy):
    """
    Previous Candle Breakout Strategy with Risk Management.

    Risk Improvements:
    - Default risk per trade = 0.5%
    - Reduce risk after 2 consecutive losing trades
    - Reset risk after a winning trade
    """

    def __init__(self, capital: float = 1_000_000,
                 risk_per_trade_pct: float = 0.005,   # Reduced to 0.5%
                 profit_target: float = 25.0,
                 stop_loss: float = 15.0,
                 min_breakout_points: float = 8.0,
                 min_candle_range: float = 12.0,
                 use_time_filters: bool = False):

        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.min_breakout_points = min_breakout_points
        self.min_candle_range = min_candle_range
        self.use_time_filters = use_time_filters

        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_volume = 0
        self.position = 0
        self.entry_price = 0.0
        self.has_entered_today = False
        self._last_candle_update = 0

        # Risk management variables
        self.consecutive_losses = 0
        self.current_risk_multiplier = 1.0   # Starts at 100% risk

    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        current_price = bar['close']
        current_volume = bar.get('volume', 100000)

        # Update previous candle
        current_time = time.time()
        if self.prev_high == 0 or (current_time - self._last_candle_update > 60):
            candle_range = self.prev_high - self.prev_low if self.prev_high > 0 else 999
            if candle_range >= self.min_candle_range:
                self.prev_high = current_price + 10
                self.prev_low = current_price - 10
                self.prev_volume = current_volume
                self._last_candle_update = current_time

        # Edge case filter
        if self.use_time_filters and self._is_edge_case():
            return None

        # Long Breakout
        if (not self.has_entered_today and self.prev_high > 0 and
                current_price > self.prev_high + self.min_breakout_points and
                current_volume > self.prev_volume * 1.15):

            effective_risk = self.risk_per_trade_pct * self.current_risk_multiplier
            risk_amount = self.capital * effective_risk
            stop_distance = current_price - self.prev_low
            if stop_distance <= 0:
                return None

            lots = max(1, int(risk_amount / stop_distance))
            self.entry_price = current_price
            self.position = lots
            self.has_entered_today = True
            return {'signal': 'BUY', 'price': current_price}

        # Short Breakout
        if (not self.has_entered_today and self.prev_low > 0 and
                current_price < self.prev_low - self.min_breakout_points and
                current_volume > self.prev_volume * 1.15):

            effective_risk = self.risk_per_trade_pct * self.current_risk_multiplier
            risk_amount = self.capital * effective_risk
            stop_distance = self.prev_high - current_price
            if stop_distance <= 0:
                return None

            lots = max(1, int(risk_amount / stop_distance))
            self.entry_price = current_price
            self.position = -lots
            self.has_entered_today = True
            return {'signal': 'SELL', 'price': current_price}

        return None

    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = bar['close']
        pnl = (current_price - entry_price) * position

        if pnl >= self.profit_target or pnl <= -self.stop_loss:
            # Update losing streak
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self.current_risk_multiplier = 1.0   # Reset risk after win

            # Reduce risk after 2 consecutive losses
            if self.consecutive_losses >= 2:
                self.current_risk_multiplier = 0.5

            self.position = 0
            self.entry_price = 0.0
            self.has_entered_today = False
            return True

        return False

    def _is_edge_case(self) -> bool:
        now = datetime.now()
        if now.hour == 9 and now.minute < 30:
            return True
        if now.weekday() == 3 and now.day >= 22:
            return True
        return False