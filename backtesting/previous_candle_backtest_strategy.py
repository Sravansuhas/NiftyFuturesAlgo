"""
backtesting/previous_candle_backtest_strategy.py

HARDENED Previous Candle Breakout Strategy for Nifty Futures.

This version was rebuilt after a 90-day real-data disaster (-182% return, PF 0.43)
exposed that the original loose parameters only worked in a short favorable regime.

Core Trader Philosophy Applied:
- Nifty 5-min breakouts are mostly noise outside of clear trending windows.
- Opening (9:15-10:00) and closing (15:00-15:30) periods are toxic for this style.
- You must demand meaningful volatility before risking capital.
- Fixed rupee targets are dangerous across volatility regimes → use ATR.
- Never let a negative expectancy system compound.
- Expiry days are special and dangerous.

All parameters are exposed so we can properly test robustness.
"""

from dataclasses import dataclass
from datetime import time, datetime
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from backtesting.backtester import BaseBacktestStrategy

# Use the central, accurate NSE calendar for consistency between live and backtest
try:
    from app.market_calendar import is_market_open, is_expiry_day as calendar_is_expiry_day, is_safe_trading_window
except ImportError:
    # Fallback for standalone backtest runs
    def is_market_open(at=None): return True
    def calendar_is_expiry_day(d=None): return False
    def is_safe_trading_window(at=None): return True


@dataclass
class StrategyParams:
    """
    All tunable parameters for the hardened breakout strategy.
    Defaults are intentionally conservative after seeing the 90-day blowup.
    """
    # === Session Discipline (Critical) ===
    # Only take signals inside this window. Nifty opening and closing noise is brutal.
    session_start: time = time(10, 0)      # 10:00 IST
    session_end: time = time(15, 0)        # 15:00 IST

    # === Breakout Quality Filters ===
    # Breakout must be at least this many ATR(14) of recent price action
    breakout_atr_mult: float = 0.85        # ~0.85–1.2 is typical good range after testing

    # Previous candle must itself have had decent range (avoid dead markets)
    min_prev_candle_range_atr: float = 0.55

    # Volume confirmation multiplier on previous candle
    volume_mult: float = 1.15

    # === ATR-based Exits (Adaptive to Volatility) ===
    # These replace the dangerous fixed 25pt / 15pt targets
    profit_target_atr_mult: float = 2.0
    stop_loss_atr_mult: float = 1.1

    # === Risk & Frequency Control ===
    risk_per_trade_pct: float = 0.0035     # 0.35% — lowered after seeing compounding damage
    max_trades_per_day: int = 2            # Extremely important for this style on Nifty

    # === Regime / Trend Awareness (Lightweight) ===
    # Require price to be on the correct side of a simple 20-period EMA for direction
    use_trend_filter: bool = True
    ema_period: int = 20

    # === Expiry Handling ===
    # On the actual expiry date of the contract, be extremely cautious
    avoid_expiry_day: bool = True
    expiry_day_risk_mult: float = 0.0      # 0.0 = no new trades on expiry day

    # ATR period
    atr_period: int = 14

    # Safety
    lot_size: int = 75

    # === Research Mode (ONLY for backtesting / WFA exploration) ===
    # When True, relaxes the most conservative filters so parameter search can actually produce variation.
    # This must NEVER be enabled in live or paper trading.
    research_mode: bool = False

    # === Realism: Execute signal on the next bar instead of the signal bar ===
    # This is a major improvement for breakout strategies (avoids look-ahead on the breakout bar itself).
    entry_on_next_bar: bool = False


class PreviousCandleBacktestStrategy(BaseBacktestStrategy):
    """
    Robust version of the Previous Candle Breakout strategy.

    Key improvements over the version that blew up:
    - ATR-adaptive breakout threshold and stops
    - Strict session filter (avoids 9:15-10:00 and 15:00-15:30)
    - Minimum volatility requirement
    - Simple trend filter
    - Hard cap on trades per day
    - Expiry day awareness
    - Much lower default risk
    """

    def __init__(self, capital: float = 1_000_000, params: Optional[StrategyParams] = None, **kwargs):
        """
        Supports two calling conventions (important for walk-forward / optimization frameworks):

        1. PreviousCandleBacktestStrategy(params=StrategyParams(...))
        2. PreviousCandleBacktestStrategy(risk_per_trade_pct=0.0035, breakout_atr_mult=0.85, ...)

        The second form is heavily used by parameter search tools like the walk-forward runner.
        """
        self.capital = capital

        if params is not None:
            self.params = params
        elif kwargs:
            # Filter + small alias map for legacy / mixed calling styles (aggregator, old scripts, etc.)
            alias_map = {
                "profit_target": "profit_target_atr_mult",
                "stop_loss": "stop_loss_atr_mult",
                "risk_pct": "risk_per_trade_pct",
            }
            normalized = {}
            valid_fields = set(StrategyParams.__dataclass_fields__.keys())
            for k, v in kwargs.items():
                k = alias_map.get(k, k)
                if k in valid_fields:
                    normalized[k] = v
            self.params = StrategyParams(**normalized) if normalized else StrategyParams()

        # === Apply Research Mode relaxations (only affects backtesting) ===
        if self.params.research_mode:
            # Significantly relax for exploration so we can actually get statistical power
            self.params.max_trades_per_day = max(self.params.max_trades_per_day, 10)
            self.params.session_start = time(9, 15)
            self.params.session_end = time(15, 30)
            self.params.min_prev_candle_range_atr = min(self.params.min_prev_candle_range_atr, 0.28)
            self.params.breakout_atr_mult = min(self.params.breakout_atr_mult, 0.50)
            self.params.avoid_expiry_day = False
            # Allow more noise for parameter exploration
            self.params.volume_mult = max(1.0, self.params.volume_mult - 0.08)
        else:
            self.params = StrategyParams()

        # State
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_range = 0.0
        self.prev_volume = 0

        self.position = 0
        self.entry_price = 0.0
        self.has_entered_today = False
        self.trades_today = 0
        self.current_atr = 0.0

        # Rolling ATR calculation state
        self._atr_values = []
        self._ema_values = []
        self._price_history = []

        self.consecutive_losses = 0
        self.current_risk_multiplier = 1.0

        # For next-bar entry realism
        self._pending_signal = None

    # ------------------------------------------------------------------
    # ATR & EMA Helpers (computed on the fly during backtest)
    # ------------------------------------------------------------------
    def _update_atr_and_ema(self, high: float, low: float, close: float):
        """Maintain a simple rolling ATR(14) and EMA(20)."""
        tr = max(high - low, abs(high - close), abs(low - close))  # approximate true range
        self._atr_values.append(tr)
        if len(self._atr_values) > self.params.atr_period:
            self._atr_values.pop(0)

        if len(self._atr_values) >= self.params.atr_period:
            self.current_atr = float(np.mean(self._atr_values[-self.params.atr_period:]))
        else:
            self.current_atr = tr  # warm-up period

        # Simple EMA for trend filter
        self._price_history.append(close)
        if len(self._price_history) > self.params.ema_period:
            self._price_history.pop(0)

        if len(self._price_history) == self.params.ema_period:
            # Exponential moving average
            ema = self._price_history[-1]
            k = 2 / (self.params.ema_period + 1)
            for p in self._price_history[-self.params.ema_period:]:
                ema = p * k + ema * (1 - k)
            self._ema_values.append(ema)
            if len(self._ema_values) > 5:
                self._ema_values.pop(0)

    def _get_ema(self) -> float:
        return self._ema_values[-1] if self._ema_values else 0.0

    def _is_in_session(self, bar_time) -> bool:
        if self.params.research_mode:
            # In research mode we use a very wide window for exploration
            if not hasattr(bar_time, "hour"):
                return True
            bar_t = time(bar_time.hour, bar_time.minute)
            return time(9, 15) <= bar_t <= time(15, 30)

        if not hasattr(bar_time, "hour"):
            return True

        # Prefer the central accurate calendar when available
        try:
            # Convert bar timestamp to IST-aware datetime if possible
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is None:
                bar_dt = bar_time.replace(tzinfo=None)  # assume already IST
            else:
                bar_dt = bar_time
            return is_safe_trading_window(bar_dt)
        except Exception:
            # Fallback to configured session
            bar_t = time(bar_time.hour, bar_time.minute)
            return self.params.session_start <= bar_t <= self.params.session_end

    def _is_expiry_day(self, bar_time) -> bool:
        if not self.params.avoid_expiry_day or not hasattr(bar_time, "date"):
            return False

        d = bar_time.date()

        # Best effort: use central calendar if it has accurate data
        try:
            if calendar_is_expiry_day(d):
                return True
        except Exception:
            pass

        # Fallback heuristic (last Thursday of month)
        if d.weekday() != 3:
            return False
        next_month = d.replace(day=28) + pd.Timedelta(days=4)
        last_day = next_month - pd.Timedelta(days=next_month.day)
        last_thu = last_day - pd.Timedelta(days=(last_day.weekday() - 3) % 7)
        return d >= last_thu - pd.Timedelta(days=1) and d <= last_thu

    # ------------------------------------------------------------------
    # Main Strategy Logic
    # ------------------------------------------------------------------
    def on_bar(self, bar: pd.Series) -> Optional[Dict[str, Any]]:
        current_price = float(bar['close'])
        current_high = float(bar.get('high', current_price))
        current_low = float(bar.get('low', current_price))
        current_volume = int(bar.get('volume', 100000))

        bar_time = bar.name

        # === Handle pending next-bar entry (realism improvement) ===
        if self.params.entry_on_next_bar and self._pending_signal is not None:
            pending = self._pending_signal
            # Execute the pending entry this bar (can add extra slippage here if desired)
            self.entry_price = current_price
            self.position = pending['quantity'] if pending['signal'] == 'BUY' else -pending['quantity']
            self.has_entered_today = True
            self.trades_today += 1

            executed_signal = {
                **pending,
                'price': current_price,  # actual execution price (next bar)
                'execution_bar': 'next'
            }
            self._pending_signal = None
            self._roll_previous(current_high, current_low, current_volume)
            return executed_signal

        # Update indicators first
        self._update_atr_and_ema(current_high, current_low, current_price)

        # Initialize previous candle on first valid bar
        if self.prev_high == 0.0 or self.prev_low == 0.0:
            self.prev_high = current_high
            self.prev_low = current_low
            self.prev_range = current_high - current_low
            self.prev_volume = current_volume
            return None

        # === HARD FILTERS (always applied) ===
        rejection_reason = None

        if not self._is_in_session(bar_time):
            rejection_reason = "outside_session"
        elif self._is_expiry_day(bar_time) and self.params.avoid_expiry_day:
            rejection_reason = "expiry_day"
        elif self.trades_today >= self.params.max_trades_per_day:
            rejection_reason = "max_daily_trades"
        elif self.current_atr < 1.0:
            rejection_reason = "insufficient_atr_data"
        else:
            # Extra safety using central calendar when not in research mode
            if not self.params.research_mode:
                try:
                    if not is_market_open(bar_time):
                        rejection_reason = "holiday_or_closed"
                except Exception:
                    pass

        if rejection_reason:
            self._roll_previous(current_high, current_low, current_volume)
            # Optional: uncomment for heavy debugging
            # print(f"[{bar_time}] Signal rejected: {rejection_reason}")
            return None

        # === VOLATILITY QUALITY FILTER ===
        prev_range_atr = self.prev_range / self.current_atr if self.current_atr > 0 else 0
        if prev_range_atr < self.params.min_prev_candle_range_atr:
            self._roll_previous(current_high, current_low, current_volume)
            return None

        # === VOLUME FILTER ===
        vol_ok = current_volume > (self.prev_volume * self.params.volume_mult)
        if not vol_ok:
            self._roll_previous(current_high, current_low, current_volume)
            return None

        # === BREAKOUT CONDITION (ATR-based) ===
        breakout_distance = self.current_atr * self.params.breakout_atr_mult

        long_signal = current_price > (self.prev_high + breakout_distance)
        short_signal = current_price < (self.prev_low - breakout_distance)

        # === LIGHTWEIGHT TREND FILTER ===
        if self.params.use_trend_filter and len(self._ema_values) > 0:
            ema = self._get_ema()
            if ema > 0:
                long_signal = long_signal and (current_price > ema)
                short_signal = short_signal and (current_price < ema)

        signal = None

        if not self.has_entered_today:
            raw_signal = None
            if long_signal:
                risk_amount = self.capital * self.params.risk_per_trade_pct * self.current_risk_multiplier
                stop_distance = max(1.0, self.current_atr * self.params.stop_loss_atr_mult)
                lots = max(1, int(risk_amount / stop_distance))
                lots = min(lots, 3)
                qty = lots * self.params.lot_size

                raw_signal = {
                    'signal': 'BUY',
                    'price': current_price,
                    'quantity': qty,
                    'atr': round(self.current_atr, 2)
                }

            elif short_signal:
                risk_amount = self.capital * self.params.risk_per_trade_pct * self.current_risk_multiplier
                stop_distance = max(1.0, self.current_atr * self.params.stop_loss_atr_mult)
                lots = max(1, int(risk_amount / stop_distance))
                lots = min(lots, 3)
                qty = lots * self.params.lot_size

                raw_signal = {
                    'signal': 'SELL',
                    'price': current_price,
                    'quantity': qty,
                    'atr': round(self.current_atr, 2)
                }

            if raw_signal:
                if self.params.entry_on_next_bar:
                    # Store for execution on next bar (more realistic)
                    self._pending_signal = raw_signal
                    signal = None
                else:
                    # Immediate execution (current behavior)
                    self.entry_price = current_price
                    self.position = raw_signal['quantity'] if raw_signal['signal'] == 'BUY' else -raw_signal['quantity']
                    self.has_entered_today = True
                    self.trades_today += 1
                    signal = raw_signal

        self._roll_previous(current_high, current_low, current_volume)
        return signal

    def _roll_previous(self, high: float, low: float, volume: int):
        self.prev_high = high
        self.prev_low = low
        self.prev_range = high - low
        self.prev_volume = volume

    # ------------------------------------------------------------------
    # Exit Logic (ATR-based)
    # ------------------------------------------------------------------
    def on_exit(self, bar: pd.Series, position: int, entry_price: float) -> bool:
        current_price = float(bar['close'])

        if self.current_atr < 1:
            # Fallback to fixed during warm-up
            target = self.params.profit_target_atr_mult * 20
            sl = self.params.stop_loss_atr_mult * 15
        else:
            target = self.current_atr * self.params.profit_target_atr_mult
            sl = self.current_atr * self.params.stop_loss_atr_mult

        pnl = (current_price - entry_price) * position

        if pnl >= target or pnl <= -sl:
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self.current_risk_multiplier = 1.0

            if self.consecutive_losses >= 2:
                self.current_risk_multiplier = 0.6  # de-risk after streak

            self.position = 0
            self.entry_price = 0.0
            self.has_entered_today = False
            return True

        return False

    def reset_daily(self):
        """Call this at the start of each new trading day in the runner."""
        self.has_entered_today = False
        self.trades_today = 0
