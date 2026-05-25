"""
backtesting/metrics.py
Clean module to calculate strategy performance metrics.
"""

from typing import List, Dict
import numpy as np


def calculate_metrics(trades: List[Dict], equity_curve: List[float], initial_capital: float) -> Dict:
    """
    Calculate key performance metrics from backtest results.
    """
    if not trades:
        return {"message": "No trades executed"}

    # Filter only completed trades (with exit)
    completed_trades = [t for t in trades if 'exit_time' in t and 'pnl' in t]

    if not completed_trades:
        return {"message": "No completed trades"}

    pnls = [t['pnl'] for t in completed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_trades = len(completed_trades)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    profit_factor = sum(wins) / abs(sum(losses)) if sum(losses) != 0 else float('inf')

    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = ((final_equity - initial_capital) / initial_capital) * 100

    # Max Drawdown
    peak = initial_capital
    max_dd = 0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 2),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "final_equity": round(final_equity, 2)
    }