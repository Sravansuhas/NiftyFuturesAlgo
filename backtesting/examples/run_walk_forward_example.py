"""
Professional Walk-Forward + Regime Backtest Runner - Production Example

This is the clean, recommended way to validate your current live strategy
parameters across real multi-month, multi-regime Nifty futures data.

HOW TO RUN WITH REAL DATA:
1. Make sure you have valid Kite credentials in your .env
2. Run this script. It will automatically fetch 4-6 months of 5-minute data
   across multiple contract expiries using the new data_loader.
3. It will run proper walk-forward + regime analysis and print a detailed report.

Run:
    PYTHONPATH=. python backtesting/examples/run_walk_forward_example.py
"""

from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kiteconnect import KiteConnect
from dotenv import load_dotenv
import os

from backtesting.data_loader import fetch_real_nifty_futures_data
from backtesting.walk_forward_runner import run_walk_forward
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.costs import TransactionCostModel, CostConfig, NIFTY_LOT_SIZE_2026


def main():
    print("=" * 80)
    print("NiftyFuturesAlgo — Real Multi-Month Walk-Forward + Regime Validation")
    print("=" * 80)

    load_dotenv()

    # ====================== CONFIG ======================
    MONTHS_OF_DATA = 5
    N_FOLDS = 5
    TRAIN_SIZE = 0.60

    # Current live-ish parameters (from your latest PaperTradingParams)
    PARAM_GRID = {
        "risk_per_trade_pct": [0.0032, 0.0035],
        "breakout_atr_mult": [0.75, 0.78],
        "profit_target_atr_mult": [2.0, 2.15],
        "stop_loss_atr_mult": [1.05, 1.1],
    }

    cost_model = TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0,
        lot_size=NIFTY_LOT_SIZE_2026,
    ))
    # ===================================================

    # Connect to Kite
    kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
    kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

    # Fetch real multi-month data
    to_date = datetime.now()
    from_date = to_date - timedelta(days=30 * MONTHS_OF_DATA)

    print(f"\nFetching real data from {from_date.date()} to {to_date.date()} (~{MONTHS_OF_DATA} months)...")
    data = fetch_real_nifty_futures_data(
        kite=kite,
        from_date=from_date,
        to_date=to_date,
        interval="5minute",
        use_cache=True
    )

    print(f"\nRunning Walk-Forward Analysis with {N_FOLDS} folds...\n")

    results = run_walk_forward(
        strategy_class=PreviousCandleBacktestStrategy,
        data=data,
        param_grid=PARAM_GRID,
        n_folds=N_FOLDS,
        train_size=TRAIN_SIZE,
        cost_model=cost_model,
    )

    # ==================== REPORT ====================
    print("\n" + "=" * 80)
    print("WALK-FORWARD + REGIME VALIDATION RESULTS (REAL DATA)")
    print("=" * 80)
    print(f"Data Period            : {data.index[0].date()} → {data.index[-1].date()}")
    print(f"Avg Out-of-Sample Return: {results['avg_return']:.2f}%")
    print(f"Avg Profit Factor        : {results['avg_pf']:.2f}")
    print(f"Folds                    : {len(results['folds'])}")
    print("=" * 80)

    for r in results["folds"]:
        print(f"\nFold {r['fold']}:")
        print(f"  OOS Return     : {r['test_return']:.2f}%")
        print(f"  Profit Factor  : {r['test_pf']:.2f}")
        print(f"  Trades         : {r['trades']}")
        if r.get("regime_performance"):
            print("  Performance by Regime:")
            for regime, perf in r["regime_performance"].items():
                print(f"    {regime:8s} → {perf['trades']:3d} trades | Total PnL: {perf['total_pnl']:,.0f}")


if __name__ == "__main__":
    main()
