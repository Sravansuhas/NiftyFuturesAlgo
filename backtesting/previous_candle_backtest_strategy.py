"""
backtesting/previous_candle_backtest_strategy.py
Previous candle breakout strategy with deterministic risk management.
"""

from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from backtesting.backtester import BaseBacktestStrategy


class PreviousCandleBacktestStrategy(BaseBacktestStrategy):
    """
    Previous candle breakout strategy for NIFTY futures.

    Risk improvements:
    - Position size is based on stop distance and rupee risk per trade.
    - Risk is reduced after consecutive losing trades.
    - Signals are based on actual previous OHLCV bars, not wall-clock time.
    """

    def __init__(self, capital: float = 1_000_000,
                 risk_per_trade_pct: float = 0.005,
                 profit_target: float = 25.0,
                 stop_loss: float = 15.0,
                 min_breakout_points: float = 8.0,
                 min_candle_range: float = 12.0,
                 lot_size: int = 75,
                 max_lots: int = 4,
                 use_time_filters: bool = False):
        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.min_breakout_points = min_breakout_points
        self.min_candle_range = min_candle_range
        self.lot_size = lot_size
        self.max_lots = max_lots
        self.use_time_filters = use_time_filters

        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_volume = 0
        self.entry_price = 0.0
        self.has_entered_today = False
        self.current_trade_date = None

        self.consecutive_losses = 0
        self.current_risk_multiplier = 1.0

    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        self._reset_daily_state_if_needed(bar)

        current_price = float(bar["close"])
        current_volume = int(bar.get("volume", 0))

        if self.use_time_filters and self._is_edge_case(bar):
            self._store_previous_bar(bar)
            return None

        if self.has_entered_today or self.prev_high <= 0 or self.prev_low <= 0:
            self._store_previous_bar(bar)
            return None

        previous_range = self.prev_high - self.prev_low
        if previous_range < self.min_candle_range:
            self._store_previous_bar(bar)
            return None

        volume_confirmed = current_volume > self.prev_volume * 1.15
        long_breakout = current_price > self.prev_high + self.min_breakout_points
        short_breakout = current_price < self.prev_low - self.min_breakout_points

        if long_breakout and volume_confirmed:
            quantity = self._calculate_quantity(current_price - self.prev_low)
            self.entry_price = current_price
            self.has_entered_today = True
            self._store_previous_bar(bar)
            return {"signal": "BUY", "price": current_price, "quantity": quantity}

        if short_breakout and volume_confirmed:
            quantity = self._calculate_quantity(self.prev_high - current_price)
            self.entry_price = current_price
            self.has_entered_today = True
            self._store_previous_bar(bar)
            return {"signal": "SELL", "price": current_price, "quantity": quantity}

        self._store_previous_bar(bar)
        return None

    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = float(bar["close"])
        pnl_points = current_price - entry_price if position > 0 else entry_price - current_price

        if pnl_points >= self.profit_target or pnl_points <= -self.stop_loss:
            if pnl_points < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self.current_risk_multiplier = 1.0

            if self.consecutive_losses >= 2:
                self.current_risk_multiplier = 0.5

            self.entry_price = 0.0
            return True

        return False

    def _calculate_quantity(self, stop_distance_points: float) -> int:
        if stop_distance_points <= 0:
            return self.lot_size

        effective_risk = self.risk_per_trade_pct * self.current_risk_multiplier
        risk_amount = self.capital * effective_risk
        lot_risk = stop_distance_points * self.lot_size
        lots = max(1, int(risk_amount / lot_risk))
        lots = min(lots, self.max_lots)
        return lots * self.lot_size

    def _store_previous_bar(self, bar: pd.Series):
        self.prev_high = float(bar["high"])
        self.prev_low = float(bar["low"])
        self.prev_volume = int(bar.get("volume", 0))

    def _reset_daily_state_if_needed(self, bar: pd.Series):
        bar_time = getattr(bar, "name", None)
        if not isinstance(bar_time, pd.Timestamp):
            return

        trade_date = bar_time.date()
        if trade_date != self.current_trade_date:
            self.current_trade_date = trade_date
            self.has_entered_today = False

    def _is_edge_case(self, bar: pd.Series) -> bool:
        bar_time = getattr(bar, "name", None)
        if isinstance(bar_time, pd.Timestamp):
            now = bar_time.to_pydatetime()
        else:
            now = datetime.now()

        if now.hour == 9 and now.minute < 30:
            return True
        if now.weekday() == 3 and now.day >= 22:
            return True
        return False
