"""
backtesting/data_loader.py

Efficient and robust data loader for real Nifty futures historical data via Kite Connect.

Handles:
- Fetching multi-month data across contract expiries
- Proper front-month selection
- Basic cleaning and regime labeling ready
- Caching support (Parquet)

This is designed for serious walk-forward and regime validation.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import pandas as pd
from kiteconnect import KiteConnect
import pytz

from app.config_loader import config

CACHE_DIR = Path("data/historical_cache")

# Indian market timezone (matches what historical data in cache uses)
IST = pytz.FixedOffset(330)  # UTC+5:30


def _ensure_ist(dt: datetime) -> datetime:
    """Normalize a datetime to IST-aware. Naive datetimes are assumed to represent IST."""
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def _load_overlapping_cached_data(from_date: datetime, to_date: datetime, interval: str = "5minute") -> Optional[pd.DataFrame]:
    """
    Smart local cache loader.
    Scans data/historical_cache for any parquet files that overlap with the requested range
    and merges them. This avoids hitting Kite when data is already locally available.
    """
    if not CACHE_DIR.exists():
        return None

    # Normalize all boundaries to IST-aware to match cached parquet indexes
    from_date = _ensure_ist(from_date)
    to_date = _ensure_ist(to_date)

    cache_files = list(CACHE_DIR.glob(f"*_{interval}.parquet"))
    if not cache_files:
        return None

    relevant_dfs = []
    for f in cache_files:
        try:
            # Filename format: SYMBOL_YYYY-MM-DD_YYYY-MM-DD_5minute.parquet
            parts = f.stem.split("_")
            if len(parts) < 4:
                continue
            file_from = _ensure_ist(datetime.strptime(parts[-3], "%Y-%m-%d"))
            file_to = _ensure_ist(datetime.strptime(parts[-2], "%Y-%m-%d"))

            # Check for any overlap (now tz-safe)
            if file_to >= from_date and file_from <= to_date:
                df = pd.read_parquet(f)
                # Filter to requested range (tz-aware vs tz-aware comparison)
                mask = (df.index >= from_date) & (df.index <= to_date)
                df = df[mask]
                if not df.empty:
                    relevant_dfs.append(df)
                    print(f"[CACHE] Using local data from {f.name} for overlap")
        except Exception as e:
            print(f"[CACHE] Skipped invalid cache file {f.name}: {e}")
            continue

    if not relevant_dfs:
        return None

    combined = pd.concat(relevant_dfs).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    print(f"[CACHE] Loaded {len(combined)} rows from local cache (no Kite call needed)")
    return combined


def get_nifty_futures_instruments(kite: KiteConnect, date: datetime) -> pd.DataFrame:
    """Get all Nifty futures instruments and filter for those active around a date."""
    instruments = kite.instruments("NFO")
    nifty_fut = [
        inst for inst in instruments
        if inst["name"] == "NIFTY" and inst["segment"] == "NFO-FUT"
    ]
    df = pd.DataFrame(nifty_fut)
    df["expiry"] = pd.to_datetime(df["expiry"])
    # Filter contracts that were trading around the given date
    df = df[(df["expiry"] >= date - timedelta(days=45)) & (df["expiry"] <= date + timedelta(days=45))]
    return df.sort_values("expiry")


def fetch_real_nifty_futures_data(
    kite: KiteConnect,
    from_date: datetime,
    to_date: datetime,
    interval: str = "5minute",
    use_cache: bool = True,
    cache_dir: str = "data/historical",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch real 5-minute (or other) Nifty futures data across multiple months/contracts.

    Local cache is ALWAYS preferred for speed and to respect Kite rate limits.
    - If overlapping data exists in data/historical_cache/ it is used automatically (no checkbox needed).
    - Set force_refresh=True to bypass cache and hit Kite (explicit "I want fresh data" case).
    """
    # Normalize dates to IST-aware so cache lookups and saved files stay consistent
    from_date = _ensure_ist(from_date)
    to_date = _ensure_ist(to_date)

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / f"nifty_futures_{from_date.date()}_{to_date.date()}_{interval}.parquet"

    if force_refresh:
        print(f"[DATA] ⚠️  FORCE REFRESH from Kite requested — ignoring all local caches for {from_date.date()}..{to_date.date()}")
    else:
        # === Smart Local-First Caching (major efficiency improvement) ===
        # 1. Try to satisfy the request purely from existing local cache files
        if use_cache:
            local_data = _load_overlapping_cached_data(from_date, to_date, interval)
            if local_data is not None and len(local_data) > 1000:
                # If we have good coverage from cache, return it directly
                # (future enhancement: delta fetch only missing periods)
                if local_data.index.min() <= from_date and local_data.index.max() >= to_date:
                    print("[DATA] ✅ Fully satisfied from local cache — no Kite call made. (auto-preferred best overlap)")
                    return local_data

    # 2. Fall back to old exact-match cache for the specific request (skip on force)
    if use_cache and not force_refresh and cache_file.exists():
        print(f"[DATA] Loading exact cached data from {cache_file}")
        return pd.read_parquet(cache_file)

    print(f"[DATA] Fetching real Nifty futures data from {from_date.date()} to {to_date.date()}{' (FORCE_REFRESH, bypassing cache)' if force_refresh else ''}...")

    # Get instruments for the start of the period
    instruments_df = get_nifty_futures_instruments(kite, from_date)

    all_data = []

    for _, row in instruments_df.iterrows():
        token = row["instrument_token"]
        symbol = row["tradingsymbol"]
        expiry = row["expiry"]

        # Only fetch data while this contract was the front month or relevant
        try:
            raw = kite.historical_data(
                instrument_token=token,
                from_date=from_date.strftime("%Y-%m-%d"),
                to_date=to_date.strftime("%Y-%m-%d"),
                interval=interval,
                continuous=False,
                oi=False
            )
            if not raw:
                continue

            df = pd.DataFrame(raw)
            df["timestamp"] = pd.to_datetime(df["date"])
            df = df.drop(columns=["date"])
            df["symbol"] = symbol
            df["expiry"] = expiry
            all_data.append(df)

        except Exception as e:
            print(f"[DATA] Warning: Could not fetch {symbol} → {e}")

    if not all_data:
        raise ValueError(
            "No data fetched from Kite for the requested date range. "
            "Common causes: (1) Access token expired or invalid, (2) Date range too wide or in the future, "
            "(3) No overlapping Nifty futures contracts for those dates. "
            "Try a shorter recent window (e.g. last 3-4 months) with valid credentials."
        )

    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values("timestamp").set_index("timestamp")

    # === Professional Front-Month + Rollover Logic (efficient version) ===
    # For each unique date, determine the front-month (nearest future expiry)
    combined = combined.reset_index()
    expiries = combined.groupby('symbol')['expiry'].first().reset_index()
    expiries['expiry'] = pd.to_datetime(expiries['expiry'])

    def get_front_month(ts):
        future = expiries[expiries['expiry'] >= ts]
        if future.empty:
            return None
        return future.loc[future['expiry'].idxmin(), 'symbol']

    combined['date'] = combined['timestamp'].dt.date
    daily_front = combined.groupby('date')['timestamp'].first().reset_index()
    daily_front['front_month'] = daily_front['timestamp'].apply(get_front_month)

    combined = combined.merge(daily_front[['date', 'front_month']], on='date', how='left')

    # Detect rollover (front month change)
    combined['rollover'] = combined['front_month'] != combined['front_month'].shift(1)
    combined.loc[0, 'rollover'] = False

    # Basic cleaning
    combined = combined.drop_duplicates(subset=['timestamp'])
    combined = combined[(combined["high"] >= combined["low"]) & 
                        (combined["volume"] > 0)]

    # Re-set index
    combined = combined.set_index('timestamp')

    # Ensure the saved index is consistently IST-aware (prevents future comparison errors)
    if combined.index.tz is None:
        combined.index = combined.index.tz_localize(IST)
    else:
        combined.index = combined.index.tz_convert(IST)

    if use_cache:
        combined.to_parquet(cache_file)
        print(f"[DATA] Saved to cache: {cache_file}")

    print(f"[DATA] Fetched {len(combined):,} bars across {len(instruments_df)} contracts.")
    print(f"[DATA] Detected {combined['rollover'].sum()} potential rollover points.")
    return combined


def prepare_walk_forward_data(
    kite: KiteConnect,
    months_back: int = 6,
    interval: str = "5minute"
) -> pd.DataFrame:
    """Convenience wrapper for typical walk-forward usage."""
    to_date = _ensure_ist(datetime.now())
    from_date = _ensure_ist(to_date - timedelta(days=30 * months_back))
    return fetch_real_nifty_futures_data(kite, from_date, to_date, interval=interval)


def list_available_cached_datasets() -> List[Dict[str, Any]]:
    """
    Scan data/historical_cache for all available local parquet datasets.
    Returns rich metadata for the GUI 'Available Cached Datasets' panel.
    Safe: never throws, degrades gracefully if pandas missing or bad files.
    Used by dashboard to show what is already local (no repeated Kite downloads).
    """
    results: List[Dict[str, Any]] = []
    if not CACHE_DIR.exists():
        return results

    cache_files = sorted(CACHE_DIR.glob("*_5minute.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in cache_files:
        try:
            stat = f.stat()
            size_kb = round(stat.st_size / 1024, 1)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

            stem = f.stem
            parts = stem.split("_")
            # Support both naming styles:
            #  NIFTY26MAYFUT_2026-02-25_2026-05-26_5minute
            #  nifty_futures_2026-04-26_2026-05-26_5minute
            symbol = parts[0] if parts else "UNKNOWN"
            file_from = parts[-3] if len(parts) >= 4 else "?"
            file_to = parts[-2] if len(parts) >= 4 else "?"

            rows = None
            actual_from = file_from
            actual_to = file_to
            try:
                df = pd.read_parquet(f)
                if len(df) > 0 and hasattr(df.index, "min"):
                    rows = int(len(df))
                    actual_from = str(pd.to_datetime(df.index.min()).date())
                    actual_to = str(pd.to_datetime(df.index.max()).date())
            except Exception:
                pass  # pandas not required for basic filename info

            results.append({
                "filename": f.name,
                "path": str(f),
                "symbol": symbol,
                "file_from": file_from,
                "file_to": file_to,
                "actual_from": actual_from,
                "actual_to": actual_to,
                "rows": rows if rows is not None else "?",
                "size_kb": size_kb,
                "mtime": mtime,
                "interval": "5minute",
            })
        except Exception as e:
            results.append({
                "filename": f.name,
                "error": str(e),
                "size_kb": round(f.stat().st_size / 1024, 1) if f.exists() else 0,
            })
    return results
