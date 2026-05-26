"""
backtesting/data_cache.py

Simple but effective local caching for historical Nifty futures data.

Pain point this solves:
- Every backtest run hitting Kite historical API repeatedly kills your rate limit quota
  and makes iteration painfully slow.
- 90-180 day multi-expiry studies become feasible when you cache the raw 5min bars.

Usage:
    from backtesting.data_cache import fetch_with_cache

    df = fetch_with_cache(
        kite=kite,
        instrument_token=token,
        from_date=...,
        to_date=...,
        interval="5minute",
        symbol="NIFTY26MAYFUT"   # used for filename
    )
"""

import os
from pathlib import Path
from typing import Optional
import pandas as pd
from datetime import datetime


CACHE_DIR = Path("data/historical_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str, from_date: str, to_date: str, interval: str) -> Path:
    safe_symbol = symbol.replace(" ", "_")
    return CACHE_DIR / f"{safe_symbol}_{from_date}_{to_date}_{interval}.parquet"


def fetch_with_cache(
    kite,
    instrument_token: int,
    from_date: str,
    to_date: str,
    interval: str = "5minute",
    symbol: str = "UNKNOWN",
    continuous: bool = False,
    oi: bool = False,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch historical data, using local Parquet cache when available.
    Falls back to CSV if pyarrow is not installed.
    """
    cache_file = _cache_path(symbol, from_date, to_date, interval)

    if cache_file.exists() and not force_refresh:
        try:
            df = pd.read_parquet(cache_file)
            print(f"[CACHE] Loaded {len(df)} rows from {cache_file.name}")
            return df
        except Exception as e:
            print(f"[CACHE] Failed to read parquet ({e}), will re-fetch.")

    print(f"[KITE] Fetching {symbol} {interval} from {from_date} to {to_date} (no/expired cache)...")

    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
        continuous=continuous,
        oi=oi,
    )

    if not raw:
        raise ValueError("Kite returned empty historical data")

    df = pd.DataFrame(raw)

    # Normalize timestamp column (pykiteconnect returns 'date')
    if "date" in df.columns:
        df["timestamp"] = pd.to_datetime(df["date"])
        df = df.drop(columns=["date"])
    elif "timestamp" not in df.columns:
        # fallback
        df["timestamp"] = pd.to_datetime(df.iloc[:, 0])

    df = df.set_index("timestamp").sort_index()

    # Try to cache as Parquet (fast + compressed). Fall back to CSV.
    try:
        df.to_parquet(cache_file, compression="snappy")
        print(f"[CACHE] Saved {len(df)} rows → {cache_file.name}")
    except Exception:
        csv_file = cache_file.with_suffix(".csv")
        df.to_csv(csv_file)
        print(f"[CACHE] Saved as CSV (parquet engine unavailable) → {csv_file.name}")

    return df
