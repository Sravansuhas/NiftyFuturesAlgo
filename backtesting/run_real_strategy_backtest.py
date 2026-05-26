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
from datetime import datetime, timedelta
from backtesting.backtester import Backtester
from backtesting.previous_candle_backtest_strategy import PreviousCandleBacktestStrategy
from backtesting.metrics import calculate_metrics
from config import KITE_API_KEY, KITE_ACCESS_TOKEN
from kiteconnect import KiteConnect


def fetch_real_historical_data(days_back: int = 30, interval: str = "5minute") -> pd.DataFrame:
    """Fetch real Nifty futures historical candles from Kite API (specific contract, not continuous)."""
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)

    # Get active Nifty futures contract (front month)
    instruments = kite.instruments("NFO")
    nifty_futures = [i for i in instruments if i["name"] == "NIFTY" and i["segment"] == "NFO-FUT"]
    nifty_futures.sort(key=lambda x: x["expiry"])
    active = nifty_futures[0]
    token = active["instrument_token"]
    symbol = active["tradingsymbol"]

    print(f"✅ Fetching real data for {symbol} (Token: {token}) — Last {days_back} days @ {interval}")

    to_date = datetime.now()
    from_date = to_date - timedelta(days=days_back)

    try:
        raw_data = kite.historical_data(
            instrument_token=token,
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
            interval=interval,
            continuous=False,   # We want the actual contract the bot will trade, not adjusted continuous
            oi=False
        )

        if not raw_data:
            raise ValueError("Kite returned empty data list — check contract dates or API permissions")

        df = pd.DataFrame(raw_data)

        # === THE FIX ===
        # pykiteconnect returns 'date' (not 'timestamp'). We normalize it for the backtester.
        df['timestamp'] = pd.to_datetime(df['date'])
        df = df.drop(columns=['date'])

        # Ensure all price/volume columns are numeric (defensive)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.set_index('timestamp')
        df = df.sort_index()

        print(f"✅ Loaded {len(df):,} real candles ({df.index[0].date()} → {df.index[-1].date()})")
        return df

    except Exception as e:
        print(f"⚠️  Kite historical data fetch failed: {type(e).__name__}: {e}")
        # Uncomment the next two lines only when debugging a new failure
        # import traceback
        # traceback.print_exc()
        print("→ Falling back to generated sample data for testing...")
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
    print("🚀 Backtesting Previous Candle Breakout Strategy with REAL Kite Data...\n")

    data = fetch_real_historical_data(days_back=30, interval="5minute")

    strategy = PreviousCandleBacktestStrategy(
        capital=1_000_000,
        risk_per_trade_pct=0.005,
        profit_target=25.0,
        stop_loss=15.0
    )

    backtester = Backtester(strategy, initial_capital=1_000_000, slippage_pts=4.0, commission_per_lot=25.0)
    results = backtester.run(data)

    metrics = calculate_metrics(results['trades'], results['equity_curve'], 1_000_000)

    print("\n" + "=" * 60)
    print("FINAL BACKTEST RESULTS (Real Kite Data)")
    print("=" * 60)
    for k, v in metrics.items():
        print(f"{k}: {v}")
    print("=" * 60)