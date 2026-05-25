"""
backtesting/backtester.py
Clean, modular backtesting engine.
"""

from typing import List, Dict, Any
import pandas as pd
from abc import ABC, abstractmethod


class BaseBacktestStrategy(ABC):
    """
    Abstract class that any backtestable strategy must implement.
    This keeps the backtester decoupled from the live strategy.
    """

    @abstractmethod
    def on_bar(self, bar: pd.Series) -> Dict[str, Any]:
        """
        Called on every new bar (candle).
        Should return a dict with signal info, e.g.:
        {'signal': 'BUY' or 'SELL' or None, 'price': float}
        """
        pass

    @abstractmethod
    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        """Return True if we should exit the current position."""
        pass


class Backtester:
    def __init__(self, strategy: BaseBacktestStrategy, initial_capital: float = 1_000_000):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position = 0
        self.entry_price = 0.0
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []

    def run(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        Run backtest on historical DataFrame.
        DataFrame must have columns: ['open', 'high', 'low', 'close', 'volume']
        with DateTimeIndex.
        """
        print(f"\n🚀 Starting Backtest on {len(data)} bars...")

        for idx, bar in data.iterrows():
            # Update equity
            current_price = bar['close']
            equity = self.cash + (self.position * current_price)
            self.equity_curve.append(equity)

            # Check for exit first
            if self.position != 0:
                if self.strategy.on_exit(bar, self.position, self.entry_price):
                    pnl = (current_price - self.entry_price) * self.position
                    self.cash += pnl
                    self.trades.append({
                        'exit_time': idx,
                        'exit_price': current_price,
                        'pnl': pnl,
                        'position': self.position
                    })
                    self.position = 0
                    self.entry_price = 0.0

            # Check for new entry
            if self.position == 0:
                signal = self.strategy.on_bar(bar)
                if signal and signal.get('signal') in ['BUY', 'SELL']:
                    direction = 1 if signal['signal'] == 'BUY' else -1
                    self.position = direction * 75  # 1 lot for now
                    self.entry_price = current_price
                    self.trades.append({
                        'entry_time': idx,
                        'entry_price': current_price,
                        'direction': signal['signal']
                    })

        # Final equity
        final_equity = self.cash + (self.position * data['close'].iloc[-1])
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100

        print(f"\n✅ Backtest Completed")
        print(f"Final Equity : ₹{final_equity:,.2f}")
        print(f"Total Return : {total_return:.2f}%")
        print(f"Total Trades : {len([t for t in self.trades if 'exit_time' in t])}")

        return {
            'final_equity': final_equity,
            'total_return_pct': total_return,
            'trades': self.trades,
            'equity_curve': self.equity_curve
        }