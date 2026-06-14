"""
backtesting/backtester.py
Clean, modular futures backtesting engine.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from app.breakout_core import (
    ExitState,
    build_exit_config_from_atr,
    build_exit_config_from_fixed,
    intrabar_exit_trigger,
)
from backtesting.costs import TransactionCostModel, default_cost_model
from backtesting.metrics import calculate_metrics


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
        self._pending_entry: Optional[Dict[str, Any]] = None

    def _uses_intrabar_exits(self) -> bool:
        if getattr(self.strategy, "use_intrabar_exits", False):
            return True
        params = getattr(self.strategy, "params", None)
        if params is not None and getattr(params, "use_live_exit_stack", False):
            return True
        return False

    def _is_high_uncertainty_bar(self, bar: pd.Series, bar_time: Any) -> bool:
        """Indian F&O stress bars: rollover, expiry, pre-event windows."""
        if bool(bar.get("rollover", False)):
            return True
        if bool(bar.get("is_gap_open", False)):
            return True
        try:
            from app.market_calendar import is_expiry_day, is_within_pre_event_block_window

            if hasattr(bar_time, "date") and is_expiry_day(bar_time.date()):
                return True
            if is_within_pre_event_block_window(bar_time):
                return True
        except Exception:
            pass
        return False

    def _entry_on_next_bar_enabled(self) -> bool:
        if getattr(self.strategy, "entry_on_next_bar", False):
            return True
        params = getattr(self.strategy, "params", None)
        if params is not None and getattr(params, "entry_on_next_bar", False):
            # Strategy defers internally when it tracks pending signals itself.
            return not hasattr(self.strategy, "_pending_signal")
        return False

    def _check_intrabar_exit(
        self, bar_high: float, bar_low: float, bar_time: Any
    ) -> tuple:
        is_long = self.position > 0
        cfg, state = self._resolve_exit_context(bar_time)
        if cfg is None:
            return False, 0.0, None, ExitState()

        triggered, price, reason, new_state = intrabar_exit_trigger(
            bar_high,
            bar_low,
            self.entry_price,
            is_long,
            cfg,
            state,
        )
        return triggered, price, reason, new_state

    def _resolve_exit_context(self, bar_time: Any):
        """Build ExitConfig + ExitState from strategy duck-typing."""
        strategy = self.strategy
        state = getattr(strategy, "_exit_state", ExitState())
        if state.entry_time is None:
            entry_time = getattr(strategy, "_entry_bar_time", None) or self.entry_time
            state = ExitState(best_price=state.best_price or self.entry_price, entry_time=entry_time)
        if not state.best_price:
            state = ExitState(best_price=self.entry_price, entry_time=state.entry_time)

        if hasattr(strategy, "get_exit_config"):
            cfg = strategy.get_exit_config(self.entry_price, self.position > 0)
            if cfg is not None:
                atr = float(getattr(strategy, "current_atr", 0.0) or 0.0)
                if atr > 0:
                    cfg.atr_floor = max(atr, cfg.atr_floor)
                return cfg, state

        exit_cfg = getattr(strategy, "exit_config", None)
        if exit_cfg is not None:
            return exit_cfg, state

        params = getattr(strategy, "params", None)
        atr = float(getattr(strategy, "current_atr", 0.0) or 0.0)
        if params is not None and hasattr(params, "profit_target_atr_mult"):
            cfg = build_exit_config_from_atr(
                atr,
                params.profit_target_atr_mult,
                params.stop_loss_atr_mult,
                max_hold_minutes=getattr(params, "max_hold_minutes", 90),
            )
            cfg.trail_atr_mult = getattr(params, "trail_atr_mult", cfg.trail_atr_mult)
            if atr > 0:
                cfg.atr_floor = max(atr, cfg.atr_floor)
            return cfg, state

        profit_target = getattr(strategy, "profit_target", None)
        stop_loss = getattr(strategy, "stop_loss", None)
        if profit_target is not None and stop_loss is not None:
            cfg = build_exit_config_from_fixed(profit_target, stop_loss, atr=atr or 8.0)
            return cfg, state

        return None, state

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

        last_trading_date: Optional[date] = None

        for idx, bar in data.iterrows():
            bar_date = self._bar_trading_date(idx)
            if bar_date is not None and bar_date != last_trading_date:
                if last_trading_date is not None and hasattr(self.strategy, "reset_daily"):
                    self.strategy.reset_daily()
                last_trading_date = bar_date

            current_price = float(bar["close"])
            bar_open = float(bar["open"])
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])

            # === Deferred next-bar entry (engine-level) ===
            if self._pending_entry is not None and self.position == 0:
                pending = self._pending_entry
                direction = 1 if pending["signal"] == "BUY" else -1
                quantity = int(pending.get("quantity", self.default_quantity))
                if quantity <= 0:
                    raise ValueError(f"Invalid pending entry quantity: {quantity}")
                self.position = direction * quantity
                self.entry_price = bar_open
                self.entry_time = idx
                self._pending_entry = None
                if hasattr(self.strategy, "_exit_state"):
                    self.strategy._exit_state = ExitState(
                        best_price=bar_open, entry_time=idx
                    )

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

            # Normal exit logic (intrabar first, then close-based on_exit)
            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None
            if self.position != 0:
                if self._uses_intrabar_exits():
                    triggered, intrabar_price, reason, new_state = self._check_intrabar_exit(
                        bar_high, bar_low, idx
                    )
                    if triggered:
                        exit_price = intrabar_price
                        exit_reason = reason
                        if hasattr(self.strategy, "_exit_state"):
                            self.strategy._exit_state = new_state

                if exit_price is None and self.strategy.on_exit(
                    bar, self.position, self.entry_price
                ):
                    exit_price = current_price
                    exit_reason = "close_exit"

            if exit_price is not None:
                gross_pnl = (exit_price - self.entry_price) * self.position

                is_high_uncertainty = self._is_high_uncertainty_bar(bar, idx)
                net_pnl = self.cost_model.apply_to_pnl(
                    gross_pnl=gross_pnl,
                    quantity=self.position,
                    entry_price=self.entry_price,
                    exit_price=exit_price,
                    slippage_points=self.slippage_pts,
                    is_high_uncertainty=is_high_uncertainty,
                    bar_time=idx,
                )

                self.cash += net_pnl
                total_cost = gross_pnl - net_pnl
                trade_record: Dict[str, Any] = {
                    "entry_time": self.entry_time,
                    "entry_price": self.entry_price,
                    "exit_time": idx,
                    "exit_price": exit_price,
                    "pnl": net_pnl,
                    "gross_pnl": gross_pnl,
                    "quantity": abs(self.position),
                    "direction": "BUY" if self.position > 0 else "SELL",
                    "slippage_pts": self.slippage_pts,
                    "total_costs": round(total_cost, 2),
                    "cost_model": repr(self.cost_model),
                }
                if exit_reason:
                    trade_record["exit_reason"] = exit_reason
                self.trades.append(trade_record)
                self.position = 0
                self.entry_price = 0.0
                self.entry_time = None

            if self.position == 0 and self._pending_entry is None:
                signal = self.strategy.on_bar(bar)
                if signal and signal.get("signal") in {"BUY", "SELL"}:
                    direction = 1 if signal["signal"] == "BUY" else -1
                    quantity = int(signal.get("quantity", self.default_quantity))
                    if quantity <= 0:
                        raise ValueError(f"Invalid signal quantity: {quantity}")

                    if self._entry_on_next_bar_enabled() and signal.get("execution_bar") != "next":
                        self._pending_entry = signal
                    else:
                        fill_price = (
                            bar_open
                            if signal.get("execution_bar") == "next"
                            else current_price
                        )
                        self.position = direction * quantity
                        self.entry_price = fill_price
                        self.entry_time = idx
                        if hasattr(self.strategy, "_exit_state"):
                            self.strategy._exit_state = ExitState(
                                best_price=fill_price, entry_time=idx
                            )

            self.equity_curve.append(self._mark_to_market_equity(current_price))

        final_equity = self._mark_to_market_equity(float(data["close"].iloc[-1]))
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100

        if self.verbose:
            print("\nBacktest completed")
            print(f"Final Equity : Rs {final_equity:,.2f}")
            print(f"Total Return : {total_return:.2f}%")
            print(f"Total Trades : {len(self.trades)}")

        result: Dict[str, Any] = {
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "trades": self.trades,
            "equity_curve": self.equity_curve,
        }

        metrics = calculate_metrics(self.trades, self.equity_curve, self.initial_capital)
        if isinstance(metrics, dict) and "message" not in metrics:
            for key, value in metrics.items():
                result[key] = self._json_safe_metric(value)

        return result

    @staticmethod
    def _bar_trading_date(ts: Any) -> Optional[date]:
        """Extract calendar date from a bar timestamp (IST-aware safe)."""
        if ts is None:
            return None
        if hasattr(ts, "date"):
            try:
                return ts.date()
            except Exception:
                pass
        return None

    @staticmethod
    def _json_safe_metric(value: Any) -> Union[int, float, str]:
        """Normalize metrics for JSON APIs (avoid inf breaking serializers)."""
        if value == "inf" or value == float("inf"):
            return 99.99
        if isinstance(value, (int, float)):
            return value
        return value

    def _simulate_rollover_cost(self, current_price: float, quantity: int) -> float:
        """Simple but realistic Nifty futures rollover cost simulation."""
        lots = max(1, quantity // self.cost_model.config.lot_size)
        # Typical Nifty roll cost: 0.5 - 2 points spread + commissions
        roll_spread_points = 1.0
        roll_commission = self.cost_model.config.brokerage_per_order * 2 * lots
        return (roll_spread_points * self.cost_model.config.lot_size * lots) + roll_commission
