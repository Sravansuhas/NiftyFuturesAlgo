import time
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
from backtesting.backtester import BaseBacktestStrategy


class PreviousCandleBacktestStrategy(BaseBacktestStrategy):
    def __init__(self, capital: float = 1_000_000, risk_per_trade_pct: float = 0.005, profit_target: float = 25.0, stop_loss: float = 15.0):
        self.capital = capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_volume = 0
        self.position = 0
        self.entry_price = 0.0
        self.has_entered_today = False
        self._last_candle_update = 0
        self.consecutive_losses = 0
        self.current_risk_multiplier = 1.0

    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        """Proper previous-candle logic: the bar just before the current one is 'previous'."""
        current_price = float(bar['close'])
        current_volume = int(bar.get('volume', 100000))
        current_high = float(bar.get('high', current_price))
        current_low = float(bar.get('low', current_price))

        # First bar: initialize prev from this bar's range (conservative, no entry yet)
        if self.prev_high == 0.0 or self.prev_low == 0.0:
            self.prev_high = current_high
            self.prev_low = current_low
            self.prev_volume = current_volume
            self._last_candle_update = time.time()
            return None

        # Check breakout against the *previous* completed candle
        signal = None
        if not self.has_entered_today:
            vol_ok = current_volume > max(1, self.prev_volume) * 1.08
            if current_price > self.prev_high + 4 and vol_ok:
                risk_amount = self.capital * self.risk_per_trade_pct * self.current_risk_multiplier
                stop_distance = max(1.0, current_price - self.prev_low)
                lots = max(1, int(risk_amount / stop_distance))
                lots = min(lots, 4)  # safety
                qty = lots * 75
                self.entry_price = current_price
                self.position = qty
                self.has_entered_today = True
                signal = {'signal': 'BUY', 'price': current_price, 'quantity': qty}
            elif current_price < self.prev_low - 4 and vol_ok:
                risk_amount = self.capital * self.risk_per_trade_pct * self.current_risk_multiplier
                stop_distance = max(1.0, self.prev_high - current_price)
                lots = max(1, int(risk_amount / stop_distance))
                lots = min(lots, 4)
                qty = lots * 75
                self.entry_price = current_price
                self.position = -qty
                self.has_entered_today = True
                signal = {'signal': 'SELL', 'price': current_price, 'quantity': qty}

        # Always roll prev to current bar for next iteration (backtest is sequential)
        self.prev_high = current_high
        self.prev_low = current_low
        self.prev_volume = current_volume
        self._last_candle_update = time.time()

        return signal

    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = bar['close']
        pnl = (current_price - entry_price) * position
        if pnl >= self.profit_target or pnl <= -self.stop_loss:
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self.current_risk_multiplier = 1.0
            if self.consecutive_losses >= 2:
                self.current_risk_multiplier = 0.5
            self.position = 0
            self.entry_price = 0.0
            self.has_entered_today = False
            return True
        return False