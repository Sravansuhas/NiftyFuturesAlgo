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
    if sum(losses) == 0:
        profit_factor = float('inf') if wins else 0.0
    else:
        profit_factor = sum(wins) / abs(sum(losses))

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

    # Try to compute gross vs net if the backtester stored gross_pnl
    gross_pnls = [t.get('gross_pnl', t['pnl']) for t in completed_trades]
    total_gross = sum(gross_pnls)
    total_costs = sum(t.get('total_costs', 0) for t in completed_trades)

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "inf",
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "final_equity": round(final_equity, 2),
        "total_gross_pnl": round(total_gross, 2),
        "total_costs_deducted": round(total_costs, 2),
        "net_pnl_after_costs": round(total_gross - total_costs, 2),
    }


def monte_carlo_simulation(trades: List[Dict], n_sims: int = 1000, initial_capital: float = 1_000_000, use_gpu: bool = True) -> Dict:
    """
    Monte Carlo using bootstrap resampling of trade PnLs.
    Tries to use GPU (via PyTorch/CuPy) if available for speed.
    """
    if not trades:
        return {"message": "No trades for Monte Carlo"}

    completed = [t for t in trades if 'pnl' in t]
    n = len(completed)
    if n < 1:
        return {"message": "No trades for Monte Carlo", "n_trades": 0, "gpu_used": False, "low_sample": True}

    low_sample = n < 5
    pnls_np = np.array([t['pnl'] for t in completed], dtype=np.float32)
    n_trades = len(pnls_np)

    # Try GPU acceleration (PyTorch preferred as it's commonly installed)
    gpu_used = False
    gpu_device = None
    try:
        if use_gpu:
            import torch
            if torch.cuda.is_available():
                device = torch.device("cuda")
                gpu_device = torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else "CUDA"
                pnls = torch.from_numpy(pnls_np).to(device)
                final_returns = []
                max_drawdowns = []

                for _ in range(n_sims):
                    idx = torch.randint(0, n_trades, (n_trades,), device=device)
                    sampled = pnls[idx]
                    equity = torch.tensor([initial_capital], device=device, dtype=torch.float32)
                    peak = equity.clone()
                    max_dd = torch.tensor([0.0], device=device)

                    for pnl in sampled:
                        equity += pnl
                        peak = torch.maximum(peak, equity)
                        dd = ((peak - equity) / peak * 100).item()
                        if dd > max_dd.item():
                            max_dd = torch.tensor([dd], device=device)

                    final_return = ((equity - initial_capital) / initial_capital * 100).item()
                    final_returns.append(final_return)
                    max_drawdowns.append(max_dd.item())

                gpu_used = True
    except Exception as e:
        # Silent fallback; user will see gpu_used=False in the report
        pass

    if not gpu_used:
        # CPU fallback
        final_returns = []
        max_drawdowns = []
        for _ in range(n_sims):
            sampled_pnls = np.random.choice(pnls_np, size=n_trades, replace=True)
            equity = initial_capital
            peak = initial_capital
            max_dd = 0.0
            for pnl in sampled_pnls:
                equity += pnl
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            final_return = (equity - initial_capital) / initial_capital * 100
            final_returns.append(final_return)
            max_drawdowns.append(max_dd)

    mc_result = {
        "n_simulations": n_sims,
        "n_trades_used": n,
        "gpu_used": gpu_used,
        "gpu_device": gpu_device if gpu_used else None,
        "low_sample_warning": low_sample,
        "final_return_mean": round(np.mean(final_returns), 2),
        "final_return_median": round(np.median(final_returns), 2),
        "final_return_5th_percentile": round(np.percentile(final_returns, 5), 2),
        "final_return_95th_percentile": round(np.percentile(final_returns, 95), 2),
        "max_dd_mean": round(np.mean(max_drawdowns), 2),
        "max_dd_95th_percentile": round(np.percentile(max_drawdowns, 95), 2),
    }
    if low_sample:
        mc_result["message"] = f"Low sample ({n} trades) — MC is exploratory only, not statistically robust."
    return mc_result