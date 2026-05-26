"""
backtesting/run_real_strategy_backtest.py
Runs the Previous Candle Breakout Strategy on REAL historical Nifty futures data from Kite API.
Fixed version — handles pykiteconnect 'date' key correctly + robust error handling.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from backtesting.backtester import Backtester
from backtesting.previous_candle_backtest_strategy import (
    PreviousCandleBacktestStrategy, StrategyParams
)
from backtesting.metrics import calculate_metrics
from backtesting.costs import TransactionCostModel, CostConfig
from backtesting.data_cache import fetch_with_cache
from config import KITE_API_KEY, KITE_ACCESS_TOKEN
from kiteconnect import KiteConnect


def fetch_real_historical_data(days_back: int = 30, interval: str = "5minute", use_cache: bool = True) -> pd.DataFrame:
    """
    Fetch real Nifty futures historical candles (front-month contract, not continuous).
    Now uses local caching by default — huge improvement for iteration speed and rate limits.
    """
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)

    instruments = kite.instruments("NFO")
    nifty_futures = [i for i in instruments if i["name"] == "NIFTY" and i["segment"] == "NFO-FUT"]
    nifty_futures.sort(key=lambda x: x["expiry"])
    active = nifty_futures[0]
    token = active["instrument_token"]
    symbol = active["tradingsymbol"]

    to_date = datetime.now()
    from_date = to_date - timedelta(days=days_back)
    from_str = from_date.strftime("%Y-%m-%d")
    to_str = to_date.strftime("%Y-%m-%d")

    print(f"✅ Preparing data for {symbol} — {days_back} days @ {interval} (cache={'on' if use_cache else 'off'})")

    try:
        if use_cache:
            df = fetch_with_cache(
                kite=kite,
                instrument_token=token,
                from_date=from_str,
                to_date=to_str,
                interval=interval,
                symbol=symbol,
                continuous=False,
                oi=False,
            )
        else:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_str,
                to_date=to_str,
                interval=interval,
                continuous=False,
                oi=False,
            )
            df = pd.DataFrame(raw)
            if "date" in df.columns:
                df["timestamp"] = pd.to_datetime(df["date"])
                df = df.drop(columns=["date"])
            df = df.set_index("timestamp").sort_index()

        # Defensive numeric conversion
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        print(f"✅ Loaded {len(df):,} candles ({df.index[0].date()} → {df.index[-1].date()})")
        return df

    except Exception as e:
        print(f"⚠️ Kite fetch failed: {type(e).__name__}: {e}")
        print("→ Falling back to generated sample data...")
        return generate_sample_data()


def generate_sample_data(n_bars: int = 1500) -> pd.DataFrame:
    np.random.seed(42)
    prices = [24000.0]
    for _ in range(n_bars - 1):
        prices.append(prices[-1] + np.random.normal(0, 7))
    return pd.DataFrame({
        'open': prices,
        'high': [p + abs(np.random.normal(0, 4)) for p in prices],
        'low': [p - abs(np.random.normal(0, 4)) for p in prices],
        'close': prices,
        'volume': np.random.randint(80000, 200000, n_bars)
    }, index=pd.date_range('2026-05-20', periods=n_bars, freq='5min'))


if __name__ == "__main__":
    print("=" * 72)
    print("HARDENED Previous Candle Breakout Backtest — Post 90-day Disaster Version")
    print("=" * 72)
    print("Goal: Find the sweet spot between the previous gambling (869 trades)")
    print("      and the ultra-conservative version that only took 2 trades.\n")

    data = fetch_real_historical_data(days_back=90, interval="5minute", use_cache=True)

    # ======================================================================
    # PRESET CONFIGURATIONS
    # ======================================================================
    # BALANCED aims for reasonable trade frequency while keeping strong filters.

    params = StrategyParams(
        session_start=time(9, 50),
        session_end=time(15, 5),
        breakout_atr_mult=0.78,
        min_prev_candle_range_atr=0.50,
        volume_mult=1.12,
        profit_target_atr_mult=2.15,
        stop_loss_atr_mult=1.05,
        risk_per_trade_pct=0.0035,
        max_trades_per_day=2,
        use_trend_filter=True,
        ema_period=20,
        avoid_expiry_day=True,
        atr_period=14,
        lot_size=75,
    )
    preset_name = "BALANCED"

    # --- MORE ACTIVE (uncomment this block and comment the one above if needed) ---
    # params = StrategyParams(
    #     session_start=time(9, 45),
    #     session_end=time(15, 10),
    #     breakout_atr_mult=0.68,
    #     min_prev_candle_range_atr=0.42,
    #     volume_mult=1.08,
    #     profit_target_atr_mult=2.0,
    #     stop_loss_atr_mult=1.0,
    #     risk_per_trade_pct=0.0038,
    #     max_trades_per_day=3,
    #     use_trend_filter=False,
    #     avoid_expiry_day=True,
    # )
    # preset_name = "MORE_ACTIVE"

    print(f"Using preset: {preset_name}\n")

    strategy = PreviousCandleBacktestStrategy(capital=1_000_000, params=params)

    # Realistic Zerodha costs (always on)
    cost_model = TransactionCostModel(CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=55.0,
        default_slippage_points=4.0,          # slightly conservative
        lot_size=75,
        high_uncertainty_multiplier=1.85,
    ))

    backtester = Backtester(strategy, initial_capital=1_000_000, cost_model=cost_model)
    results = backtester.run(data)

    metrics = calculate_metrics(results['trades'], results['equity_curve'], 1_000_000)

    print("\n" + "=" * 72)
    print(f"FINAL BACKTEST RESULTS — {preset_name} CONFIG (90 days real Nifty data)")
    print("=" * 72)
    for k, v in metrics.items():
        print(f"{k}: {v}")
    print("=" * 72)

    print("\nTrade frequency diagnostic:")
    trades = metrics.get('total_trades', 0)
    print(f"  Total trades: {trades} over ~63 trading days → {trades / 63:.2f} per day on average")
    print("\nTarget range for this style: ~0.6 – 2.0 trades per day on average.")
    print("Too low → under-trading (missed edge). Too high → noise trading.")
    print("=" * 72)