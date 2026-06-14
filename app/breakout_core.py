"""
Shared previous-candle breakout logic for live and backtest engines.

Keeps entry filters and multi-condition exits aligned between:
- app/strategy.py (live/paper)
- backtesting/previous_candle_backtest_strategy.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class BreakoutEntryConfig:
    breakout_atr_mult: float = 0.85
    min_prev_candle_range_atr: float = 0.55
    volume_mult: float = 1.15
    use_trend_filter: bool = True


@dataclass
class ExitConfig:
    """Exit thresholds — ATR-based (backtest) or fixed points (live paper defaults)."""
    profit_target_pts: float
    stop_loss_pts: float
    trail_atr_mult: float = 1.5
    trail_activation_stop_mult: float = 1.2
    max_hold_seconds: int = 90 * 60
    time_exit_profit_fraction: float = 0.4
    atr_floor: float = 8.0
    breakeven_activation_mult: float = 0.80
    chop_profit_defense: bool = False
    profit_lock_retrace_pct: float = 0.40
    chop_min_hold_seconds: int = 30 * 60


@dataclass
class ExitState:
    best_price: float = 0.0
    entry_time: Any = None


@dataclass
class RegimeAdjustments:
    volatility: str = "normal"
    trend: str = "flat"
    htf_bias: str = "neutral"


def compute_breakout_distance(atr: float, breakout_atr_mult: float) -> float:
    return max(0.0, atr * breakout_atr_mult)


def adjust_breakout_buffer(base_buffer: float, regime: Optional[RegimeAdjustments] = None) -> float:
    """Apply live-style regime multipliers to breakout buffer."""
    if regime is None:
        return base_buffer
    buf = base_buffer
    if regime.volatility == "high":
        buf *= 1.30
    elif regime.volatility == "low":
        buf *= 0.85
    if regime.trend == "ranging":
        buf *= 1.15
    if regime.htf_bias == "bullish":
        buf *= 1.35
    elif regime.htf_bias == "bearish":
        buf *= 1.35
    return buf


def passes_range_volatility_filter(
    prev_range: float, atr: float, min_prev_candle_range_atr: float
) -> bool:
    if atr <= 0:
        return False
    return (prev_range / atr) >= min_prev_candle_range_atr


def passes_volume_filter(current_volume: int, prev_volume: int, volume_mult: float) -> bool:
    if prev_volume <= 0:
        return True
    return current_volume > (prev_volume * volume_mult)


def evaluate_breakout_signals(
    price: float,
    prev_high: float,
    prev_low: float,
    breakout_distance: float,
    ema: float = 0.0,
    use_trend_filter: bool = True,
) -> Tuple[bool, bool]:
    long_signal = price > (prev_high + breakout_distance)
    short_signal = price < (prev_low - breakout_distance)
    if use_trend_filter and ema > 0:
        long_signal = long_signal and price > ema
        short_signal = short_signal and price < ema
    return long_signal, short_signal


def compute_atr_exit_levels(
    atr: float,
    profit_target_atr_mult: float,
    stop_loss_atr_mult: float,
    atr_floor: float = 8.0,
) -> Tuple[float, float]:
    safe_atr = max(atr, atr_floor) if atr >= 1 else max(atr, 1.0)
    if atr < 1:
        target = profit_target_atr_mult * 20
        stop = stop_loss_atr_mult * 15
    else:
        target = safe_atr * profit_target_atr_mult
        stop = safe_atr * stop_loss_atr_mult
    return target, stop


def build_exit_config_from_atr(
    atr: float,
    profit_target_atr_mult: float,
    stop_loss_atr_mult: float,
    max_hold_minutes: int = 90,
) -> ExitConfig:
    target, stop = compute_atr_exit_levels(atr, profit_target_atr_mult, stop_loss_atr_mult)
    return ExitConfig(
        profit_target_pts=target,
        stop_loss_pts=stop,
        max_hold_seconds=max_hold_minutes * 60,
    )


def build_exit_config_from_fixed(
    profit_target: float,
    stop_loss: float,
    atr: float = 8.0,
    max_hold_minutes: int = 90,
) -> ExitConfig:
    return ExitConfig(
        profit_target_pts=profit_target,
        stop_loss_pts=stop_loss,
        max_hold_seconds=max_hold_minutes * 60,
        atr_floor=max(atr, 8.0),
    )


def compute_stop_price(entry_price: float, is_long: bool, cfg: ExitConfig) -> float:
    """Absolute stop price for a position (points-based cfg)."""
    if is_long:
        return entry_price - cfg.stop_loss_pts
    return entry_price + cfg.stop_loss_pts


def compute_target_price(entry_price: float, is_long: bool, cfg: ExitConfig) -> float:
    """Absolute profit-target price for a position (points-based cfg)."""
    if is_long:
        return entry_price + cfg.profit_target_pts
    return entry_price - cfg.profit_target_pts


def intrabar_exit_trigger(
    bar_high: float,
    bar_low: float,
    entry_price: float,
    is_long: bool,
    cfg: ExitConfig,
    state: ExitState,
) -> Tuple[bool, float, Optional[str], ExitState]:
    """
    Conservative intrabar exit simulation (stop-first).

    For LONG: if low <= stop -> exit at stop; elif high >= target -> exit at target;
    then trailing using bar extremes (high updates best, low checks trail).

    For SHORT the mirror applies (stop checked on high first).
    """
    if entry_price <= 0 or bar_high <= 0 or bar_low <= 0:
        return False, 0.0, None, state

    stop_price = compute_stop_price(entry_price, is_long, cfg)
    target_price = compute_target_price(entry_price, is_long, cfg)
    safe_atr = cfg.atr_floor

    if is_long:
        if bar_low <= stop_price:
            return True, stop_price, "stop_loss", state
        if bar_high >= target_price:
            return True, target_price, "profit_target", state

        favorable = bar_high - entry_price
        if favorable > cfg.stop_loss_pts * cfg.trail_activation_stop_mult:
            best = state.best_price if state.best_price else entry_price
            best = max(best, bar_high)
            trail_stop = best - (cfg.trail_atr_mult * safe_atr)
            if bar_low <= trail_stop:
                return (
                    True,
                    trail_stop,
                    "trailing_stop",
                    ExitState(best_price=best, entry_time=state.entry_time),
                )
            state = ExitState(best_price=best, entry_time=state.entry_time)
    else:
        if bar_high >= stop_price:
            return True, stop_price, "stop_loss", state
        if bar_low <= target_price:
            return True, target_price, "profit_target", state

        favorable = entry_price - bar_low
        if favorable > cfg.stop_loss_pts * cfg.trail_activation_stop_mult:
            best = state.best_price if state.best_price else entry_price
            best = min(best, bar_low)
            trail_stop = best + (cfg.trail_atr_mult * safe_atr)
            if bar_high >= trail_stop:
                return (
                    True,
                    trail_stop,
                    "trailing_stop",
                    ExitState(best_price=best, entry_time=state.entry_time),
                )
            state = ExitState(best_price=best, entry_time=state.entry_time)

    return False, 0.0, None, state


def _seconds_between(bar_time: Any, entry_time: Any) -> Optional[float]:
    if bar_time is None or entry_time is None:
        return None
    try:
        bt = bar_time.timestamp() if hasattr(bar_time, "timestamp") else None
        et = entry_time.timestamp() if hasattr(entry_time, "timestamp") else None
        if bt is not None and et is not None:
            return bt - et
    except Exception:
        pass
    return None


def should_exit_position(
    current_price: float,
    entry_price: float,
    is_long: bool,
    atr: float,
    cfg: ExitConfig,
    state: ExitState,
    bar_time: Any = None,
    regime: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, ExitState, Optional[str]]:
    """
    Multi-condition exit aligned with live strategy:
    1) Hard profit target  2) Hard stop  3) Breakeven lock  4) Profit lock retrace
    5) Chop profit defense  6) ATR trail  7) Time stop
    """
    if entry_price <= 0 or current_price <= 0:
        return False, state, None

    pnl = (current_price - entry_price) if is_long else (entry_price - current_price)
    safe_atr = max(atr, cfg.atr_floor)
    regime = regime or {}
    trend = regime.get("trend", "ranging")

    best = state.best_price if state.best_price else entry_price
    if is_long:
        best = max(best, current_price)
    else:
        best = min(best, current_price) if best else current_price
    state = ExitState(best_price=best, entry_time=state.entry_time)
    peak_pnl = (best - entry_price) if is_long else (entry_price - best)

    if pnl >= cfg.profit_target_pts:
        return True, state, "profit_target"

    effective_stop = cfg.stop_loss_pts
    if peak_pnl >= cfg.stop_loss_pts * cfg.breakeven_activation_mult:
        effective_stop = 0.0

    if pnl <= -effective_stop:
        reason = "breakeven_stop" if effective_stop == 0.0 else "stop_loss"
        return True, state, reason
    if (
        peak_pnl >= cfg.profit_target_pts * 0.55
        and pnl > 0
        and peak_pnl > 0
        and pnl <= peak_pnl * (1.0 - cfg.profit_lock_retrace_pct)
    ):
        return True, state, "profit_lock"

    elapsed = _seconds_between(bar_time, state.entry_time)
    chop_defense = cfg.chop_profit_defense or trend in ("ranging", "flat")
    if chop_defense and pnl > 0 and elapsed is not None:
        if elapsed >= cfg.chop_min_hold_seconds:
            if pnl < cfg.profit_target_pts * cfg.time_exit_profit_fraction:
                return True, state, "chop_profit_defense"

    if pnl > cfg.stop_loss_pts * cfg.trail_activation_stop_mult:
        if is_long:
            trail_stop = best - (cfg.trail_atr_mult * safe_atr)
            if current_price < trail_stop:
                return True, state, "trailing_stop"
        else:
            trail_stop = best + (cfg.trail_atr_mult * safe_atr)
            if current_price > trail_stop:
                return True, state, "trailing_stop"

    if elapsed is not None and elapsed > cfg.max_hold_seconds:
        if pnl < cfg.profit_target_pts * cfg.time_exit_profit_fraction:
            return True, state, "time_exit"

    return False, state, None