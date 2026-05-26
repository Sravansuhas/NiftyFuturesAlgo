"""
backtesting/backtester.py
Clean, modular futures backtesting engine.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd

from backtesting.costs import TransactionCostModel, default_cost_model


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
    def __init__(self, strategy: BaseBacktestStrategy, initial_capital: float = 1_000_000,
                 default_quantity: int = 75,
                 cost_model: Optional[TransactionCostModel] = None,
                 slippage_pts: float = 3.5,
                 verbose: bool = True,
                 cost_multiplier: float = 1.0):
        """
        cost_model: Realistic Zerodha Nifty futures cost + slippage model (recommended).
                  Falls back to a conservative default if not provided.
        slippage_pts: Override default slippage for this run (still used by cost_model).
        cost_multiplier: Scale all costs by this factor (1.0 = normal, 2.0 = double costs, etc.)
                         Useful for sensitivity analysis.
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.default_quantity = default_quantity
        self.slippage_pts = slippage_pts
        self.verbose = verbose
        self.cost_multiplier = cost_multiplier

        base_model = cost_model or default_cost_model
        if cost_multiplier != 1.0:
            self.cost_model = TransactionCostModel.with_multiplier(base_model.config, cost_multiplier)
        else:
            self.cost_model = base_model

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

        if self.verbose:
            print(f"\nStarting backtest on {len(data)} bars...")

        for idx, bar in data.iterrows():
            current_price = float(bar["close"])

            # === Rollover simulation (explicit P&L/cost on contract change) ===
            is_rollover = False
            if hasattr(bar, 'get'):
                is_rollover = bool(bar.get('rollover', False))

            if is_rollover and self.position != 0:
                gross_pnl = (current_price - self.entry_price) * self.position
                roll_cost = self._simulate_rollover_cost(current_price, abs(self.position))
                net_pnl = gross_pnl - roll_cost

                self.cash += net_pnl
                self.trades.append({
                    "entry_time": self.entry_time,
                    "entry_price": self.entry_price,
                    "exit_time": idx,
                    "exit_price": current_price,
                    "pnl": net_pnl,
                    "gross_pnl": gross_pnl,
                    "quantity": abs(self.position),
                    "direction": "BUY" if self.position > 0 else "SELL",
                    "slippage_pts": 0,
                    "total_costs": round(roll_cost, 2),
                    "cost_model": "rollover_simulation",
                    "is_rollover": True,
                })
                # Re-establish position seamlessly on the new contract
                self.entry_price = current_price
                self.entry_time = idx

            # Normal exit logic
            if self.position != 0 and self.strategy.on_exit(bar, self.position, self.entry_price):
                # Gross theoretical P&L before costs
                gross_pnl = (current_price - self.entry_price) * self.position

                # Use the proper realistic cost model (Zerodha Nifty FUT)
                is_high_uncertainty = False
                net_pnl = self.cost_model.apply_to_pnl(
                    gross_pnl=gross_pnl,
                    quantity=self.position,
                    entry_price=self.entry_price,
                    exit_price=current_price,
                    slippage_points=self.slippage_pts,
                    is_high_uncertainty=is_high_uncertainty,
                    bar_time=idx,
                )

                self.cash += net_pnl
                lots = max(1, abs(self.position) // self.cost_model.config.lot_size)
                total_cost = gross_pnl - net_pnl
                self.trades.append({
                    "entry_time": self.entry_time,
                    "entry_price": self.entry_price,
                    "exit_time": idx,
                    "exit_price": current_price,
                    "pnl": net_pnl,
                    "gross_pnl": gross_pnl,
                    "quantity": abs(self.position),
                    "direction": "BUY" if self.position > 0 else "SELL",
                    "slippage_pts": self.slippage_pts,
                    "total_costs": round(total_cost, 2),
                    "cost_model": repr(self.cost_model),
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
                    self.entry_price = current_price
                    self.entry_time = idx

            self.equity_curve.append(self._mark_to_market_equity(current_price))

        final_equity = self._mark_to_market_equity(float(data["close"].iloc[-1]))
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100

        if self.verbose:
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

    def _simulate_rollover_cost(self, current_price: float, quantity: int) -> float:
        """Simple but realistic Nifty futures rollover cost simulation."""
        lots = max(1, quantity // self.cost_model.config.lot_size)
        # Typical Nifty roll cost: 0.5 - 2 points spread + commissions
        roll_spread_points = 1.0
        roll_commission = self.cost_model.config.brokerage_per_order * 2 * lots
        return (roll_spread_points * self.cost_model.config.lot_size * lots) + roll_commission
