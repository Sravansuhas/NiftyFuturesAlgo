"""
backtesting/backtester.py
Clean, modular futures backtesting engine.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

import pandas as pd


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
        {'signal': 'BUY' or 'SELL' or None, 'price': float, 'quantity': int}
        """
        pass

    @abstractmethod
    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        """Return True if we should exit the current position."""
        pass


class Backtester:
    def __init__(self, strategy: BaseBacktestStrategy, initial_capital: float = 1_000_000, default_quantity: int = 75):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.default_quantity = default_quantity
        self.cash = initial_capital
        self.position = 0
        self.entry_price = 0.0
        self.entry_time = None
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []

    def _mark_to_market_equity(self, current_price: float) -> float:
        unrealized_pnl = 0.0
        if self.position != 0:
            unrealized_pnl = (current_price - self.entry_price) * self.position
        return self.cash + unrealized_pnl

    def run(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        Run backtest on historical DataFrame.
        DataFrame must have columns: ['open', 'high', 'low', 'close', 'volume']
        with DateTimeIndex.
        """
        required_columns = {"open", "high", "low", "close", "volume"}
        missing_columns = required_columns - set(data.columns)
        if missing_columns:
            raise ValueError(f"Backtest data missing columns: {sorted(missing_columns)}")
        if data.empty:
            raise ValueError("Backtest data is empty")

        print(f"\nStarting backtest on {len(data)} bars...")

        for idx, bar in data.iterrows():
            current_price = float(bar["close"])

            if self.position != 0 and self.strategy.on_exit(bar, self.position, self.entry_price):
                pnl = (current_price - self.entry_price) * self.position
                self.cash += pnl
                self.trades.append({
                    "entry_time": self.entry_time,
                    "entry_price": self.entry_price,
                    "exit_time": idx,
                    "exit_price": current_price,
                    "pnl": pnl,
                    "quantity": abs(self.position),
                    "direction": "BUY" if self.position > 0 else "SELL",
                })
                self.position = 0
                self.entry_price = 0.0
                self.entry_time = None

            if self.position == 0:
                signal = self.strategy.on_bar(bar)
                if signal and signal.get("signal") in {"BUY", "SELL"}:
                    direction = 1 if signal["signal"] == "BUY" else -1
                    quantity = int(signal.get("quantity", self.default_quantity))
                    if quantity <= 0:
                        raise ValueError(f"Invalid signal quantity: {quantity}")
                    self.position = direction * quantity
                    self.entry_price = float(signal.get("price", current_price))
                    self.entry_time = idx

            self.equity_curve.append(self._mark_to_market_equity(current_price))

        final_equity = self._mark_to_market_equity(float(data["close"].iloc[-1]))
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100

        print("\nBacktest completed")
        print(f"Final Equity : Rs {final_equity:,.2f}")
        print(f"Total Return : {total_return:.2f}%")
        print(f"Total Trades : {len(self.trades)}")

        return {
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "trades": self.trades,
            "equity_curve": self.equity_curve,
        }
