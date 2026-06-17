"""
Paper Trading Configuration for Aegis

This file contains sensible, production-oriented (paper) parameters
for live testing. These are intentionally more active than the strict
backtest "BALANCED" version, because:

- We want to observe real behavior and signal frequency.
- Paper trading allows us to monitor and learn.
- We still keep strong risk and session discipline.

Use these when running in PAPER_MODE.
"""

from dataclasses import dataclass
from datetime import time


@dataclass
class PaperTradingParams:
    """
    God-level paper trading parameters.
    Designed after seeing both the 869-trade blowup and the 2-trade desert.
    """

    # === Session Discipline ===
    session_start: time = time(9, 45)
    session_end: time = time(15, 10)

    # === Breakout Quality ===
    # You can use either fixed points OR ATR-based (recommended for robustness)
    use_atr_breakout: bool = True
    breakout_buffer_points: float = 10.0          # Fallback when use_atr_breakout=False
    breakout_atr_mult: float = 0.75               # 0.65 - 0.95 is a good range

    # Minimum volatility to even consider trading (prevents dead market gambling)
    min_atr_points: float = 6.0

    # Volume filter
    volume_confirmation: bool = True
    volume_mult: float = 1.08

    # === Exits ===
    profit_target: float = 22.0
    stop_loss: float = 12.0
    use_atr_exits: bool = True
    profit_target_atr_mult: float = 2.0
    stop_loss_atr_mult: float = 1.1

    # === Risk & Frequency Control ===
    risk_per_trade_pct: float = 0.0035
    max_trades_per_day: int = 3

    # Cooldown after any trade (in minutes) — powerful anti-overtrading tool
    cooldown_minutes_after_trade: int = 12

    # === Safety ===
    avoid_expiry_day: bool = True
    expiry_day_cutoff_hour: int = 12

    use_trend_filter: bool = False


# Recommended preset for most paper trading sessions
DEFAULT_PAPER_PARAMS = PaperTradingParams()


# More aggressive paper trading preset (use with caution)
AGGRESSIVE_PAPER_PARAMS = PaperTradingParams(
    session_start=time(9, 40),
    session_end=time(15, 15),
    breakout_buffer_points=8.0,
    volume_mult=1.05,
    max_trades_per_day=4,
    profit_target=20.0,
    stop_loss=11.0,
    use_trend_filter=False,
)
