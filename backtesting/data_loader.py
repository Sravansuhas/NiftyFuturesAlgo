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
from typing import Optional, List, Dict, Any, Callable
import time
import pandas as pd
from kiteconnect import KiteConnect
import pytz

from app.config_loader import config
from backtesting.data_health import KITE_INTERVAL_MAX_DAYS

CACHE_DIR = Path("data/historical_cache")

ProgressCallback = Callable[[int, str, Optional[Dict[str, Any]]], None]

# Indian market timezone (matches what historical data in cache uses)
IST = pytz.FixedOffset(330)  # UTC+5:30


def _ensure_ist(dt: datetime) -> datetime:
    """Normalize a datetime to IST-aware. Naive datetimes are assumed to represent IST."""
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def _as_ist_timestamp(dt: datetime) -> pd.Timestamp:
    """Python datetime → tz-aware pandas Timestamp in IST (safe for index comparisons)."""
    return pd.Timestamp(_ensure_ist(dt))


def _normalize_index_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure DataFrame DatetimeIndex is IST-aware so cache/Kite paths compare cleanly."""
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize(IST)
    else:
        out.index = out.index.tz_convert(IST)
    return out


def _annotate_front_month_and_rollover(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tag each bar with the active front-month contract and rollover flag.
    Requires symbol + expiry columns (present after Kite multi-contract fetch).
    """
    if df.empty or "symbol" not in df.columns or "expiry" not in df.columns:
        return df

    if isinstance(df.index, pd.DatetimeIndex):
        combined = df.reset_index().rename(columns={"index": "timestamp"})
        if "timestamp" not in combined.columns:
            combined = combined.rename(columns={combined.columns[0]: "timestamp"})
    else:
        combined = df.copy()
        if "timestamp" not in combined.columns:
            return df

    expiries = combined.groupby("symbol")["expiry"].first().reset_index()
    expiries["expiry"] = pd.to_datetime(expiries["expiry"], utc=False)
    if getattr(expiries["expiry"].dt, "tz", None) is None:
        expiries["expiry"] = expiries["expiry"].dt.tz_localize(IST)
    else:
        expiries["expiry"] = expiries["expiry"].dt.tz_convert(IST)

    def get_front_month(ts):
        ts_ist = pd.Timestamp(ts)
        if ts_ist.tz is None:
            ts_ist = ts_ist.tz_localize(IST)
        else:
            ts_ist = ts_ist.tz_convert(IST)
        future = expiries[expiries["expiry"] >= ts_ist]
        if future.empty:
            return None
        return future.loc[future["expiry"].idxmin(), "symbol"]

    combined["date"] = pd.to_datetime(combined["timestamp"]).dt.date
    daily_front = combined.groupby("date")["timestamp"].first().reset_index()
    daily_front["front_month"] = daily_front["timestamp"].apply(get_front_month)
    combined = combined.merge(daily_front[["date", "front_month"]], on="date", how="left")
    combined["rollover"] = combined["front_month"] != combined["front_month"].shift(1)
    combined.loc[combined.index[0], "rollover"] = False

    combined = combined.set_index("timestamp")
    return _normalize_index_ist(combined)


def _filter_to_front_month(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars from the active front-month contract (avoids mixed-contract series)."""
    if df.empty:
        return df

    work = df
    if "front_month" not in work.columns or "symbol" not in work.columns:
        if "symbol" in work.columns and "expiry" in work.columns:
            work = _annotate_front_month_and_rollover(work)
        else:
            return df

    before = len(work)
    mask = work["front_month"].notna() & (work["symbol"] == work["front_month"])
    filtered = work[mask]
    if len(filtered) < before:
        print(f"[DATA] Front-month filter: {before:,} -> {len(filtered):,} bars")
    return filtered


def _cache_symbol_to_underlying(symbol: str) -> str:
    s = (symbol or "").upper()
    if s.startswith("BANKNIFTY"):
        return "BANKNIFTY"
    if s.startswith("SENSEX"):
        return "SENSEX"
    if "NIFTY" in s and "BANK" not in s:
        return "NIFTY"
    return ""


def _load_overlapping_cached_data(
    from_date: datetime,
    to_date: datetime,
    interval: str = "5minute",
    underlying: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    Smart local cache loader.
    Scans data/historical_cache for any parquet files that overlap with the requested range
    and merges them. This avoids hitting Kite when data is already locally available.
    """
    if not CACHE_DIR.exists():
        return None

    from_ts = _as_ist_timestamp(from_date)
    to_ts = _as_ist_timestamp(to_date)
    underlying_key = underlying.upper() if underlying else None

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
            file_symbol = parts[0]
            if underlying_key and _cache_symbol_to_underlying(file_symbol) != underlying_key:
                continue
            file_from = _as_ist_timestamp(datetime.strptime(parts[-3], "%Y-%m-%d"))
            file_to = _as_ist_timestamp(datetime.strptime(parts[-2], "%Y-%m-%d"))

            if file_to >= from_ts and file_from <= to_ts:
                df = _normalize_index_ist(pd.read_parquet(f))
                mask = (df.index >= from_ts) & (df.index <= to_ts)
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
    combined = _filter_to_front_month(combined)
    print(f"[CACHE] Loaded {len(combined)} rows from local cache (no Kite call needed)")
    return combined


SUPPORTED_INDEX_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})

_INDEX_FUTURES_CONFIG = {
    "NIFTY": ("NFO", "NIFTY", "NFO-FUT"),
    "BANKNIFTY": ("NFO", "BANKNIFTY", "NFO-FUT"),
    "SENSEX": ("BFO", "SENSEX", "BFO-FUT"),
}


def get_index_futures_instruments(
    kite: KiteConnect,
    underlying: str = "NIFTY",
    from_date: datetime = None,
    to_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Return live index FUT contracts (NIFTY / BANKNIFTY / SENSEX) from Kite.

    Kite only lists *live* contracts (expired tokens are not returned).
    See: https://kite.trade/docs/connect/v3/historical/#continuous-data
    """
    key = underlying.upper()
    if key not in _INDEX_FUTURES_CONFIG:
        raise ValueError(f"Unsupported underlying: {underlying}")

    segment, name, segment_filter = _INDEX_FUTURES_CONFIG[key]
    instruments = kite.instruments(segment)
    index_fut = [
        inst for inst in instruments
        if inst.get("name") == name and inst.get("segment") == segment_filter
    ]
    if not index_fut:
        return pd.DataFrame()

    df = pd.DataFrame(index_fut)
    from_ts = _as_ist_timestamp(from_date)
    to_ts = _as_ist_timestamp(to_date or datetime.now())

    df["expiry"] = pd.to_datetime(df["expiry"], utc=False)
    if getattr(df["expiry"].dt, "tz", None) is not None:
        df["expiry"] = df["expiry"].dt.tz_convert(IST)
    else:
        df["expiry"] = df["expiry"].dt.tz_localize(IST)

    # Keep contracts that had not expired before the range starts.
    # (JUN/JUL/AUG etc. can each supply a slice of a multi-month window.)
    df = df[df["expiry"] >= from_ts.normalize()]
    # Drop far-future listings that cannot have started trading yet
    df = df[df["expiry"] <= to_ts + pd.Timedelta(days=120)]

    return df.sort_values("expiry")


def get_nifty_futures_instruments(
    kite: KiteConnect,
    from_date: datetime,
    to_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Backward-compatible NIFTY-only wrapper."""
    return get_index_futures_instruments(kite, "NIFTY", from_date, to_date)


def _contract_fetch_window(
    expiry: pd.Timestamp,
    from_date: datetime,
    to_date: datetime,
) -> Optional[tuple]:
    """
    Clip Kite historical fetch to when this live contract could actually trade.
    Nifty monthly futures typically list ~2–3 months before expiry.
    """
    from_ts = _as_ist_timestamp(from_date)
    to_ts = _as_ist_timestamp(to_date)
    exp = pd.Timestamp(expiry)
    if exp.tz is None:
        exp = exp.tz_localize(IST)
    else:
        exp = exp.tz_convert(IST)

    listing_est = exp - pd.Timedelta(days=95)
    win_from = max(from_ts, listing_est)
    try:
        from app.market_calendar import get_nse_fo_market_close

        close_t = get_nse_fo_market_close(exp.date())
        session_end = exp.normalize() + pd.Timedelta(
            hours=close_t.hour, minutes=close_t.minute
        )
    except Exception:
        session_end = exp + pd.Timedelta(hours=15, minutes=30)
    win_to = min(to_ts, session_end)
    if win_from >= win_to:
        return None
    return win_from.to_pydatetime(), win_to.to_pydatetime()


def _kite_datetime_str(dt: datetime, session_end: bool = False) -> str:
    """Format for Kite historical API (IST session bounds per official docs)."""
    d = _ensure_ist(dt)
    if session_end:
        try:
            from app.market_calendar import get_nse_fo_market_close

            close_t = get_nse_fo_market_close(d.date())
            return d.strftime(f"%Y-%m-%d {close_t.hour:02d}:{close_t.minute:02d}:00")
        except Exception:
            return d.strftime("%Y-%m-%d 15:30:00")
    return d.strftime("%Y-%m-%d 09:15:00")


def _chunk_date_range(from_date: datetime, to_date: datetime, interval: str) -> List[tuple]:
    """Split a date range into Kite-safe chunks (per-interval max days)."""
    max_days = KITE_INTERVAL_MAX_DAYS.get(interval, 60)
    chunks = []
    cursor = from_date
    while cursor < to_date:
        chunk_end = min(cursor + timedelta(days=max_days - 1), to_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(seconds=1)
    return chunks


def _fetch_contract_bars(
    kite: KiteConnect,
    token: int,
    symbol: str,
    from_date: datetime,
    to_date: datetime,
    interval: str,
) -> pd.DataFrame:
    """Fetch one contract across chunked windows (respects Kite per-request limits)."""
    chunks = _chunk_date_range(from_date, to_date, interval)
    parts = []
    for i, (c_from, c_to) in enumerate(chunks):
        raw = kite.historical_data(
            instrument_token=token,
            from_date=_kite_datetime_str(c_from, session_end=False),
            to_date=_kite_datetime_str(c_to, session_end=True),
            interval=interval,
            continuous=False,
            oi=False,
        )
        if raw:
            parts.append(pd.DataFrame(raw))
        if i < len(chunks) - 1:
            from app.kite_rate_limit import historical_limiter

            historical_limiter.wait()
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["date"], utc=False)
    if getattr(df["timestamp"].dt, "tz", None) is not None:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST)
    df = df.drop(columns=["date"])
    df["symbol"] = symbol
    return df


def fetch_real_index_futures_data(
    kite: KiteConnect,
    from_date: datetime,
    to_date: datetime,
    underlying: str = "NIFTY",
    interval: str = "5minute",
    use_cache: bool = True,
    cache_dir: str = "data/historical",
    force_refresh: bool = False,
    cache_only: bool = False,
    min_cache_rows: int = 500,
    progress_callback: Optional[ProgressCallback] = None,
) -> pd.DataFrame:
    """
    Fetch real 5-minute (or other) index futures data across multiple months/contracts.

    Supports NIFTY (NFO), BANKNIFTY (NFO), SENSEX (BFO).

    Local cache is ALWAYS preferred for speed and to respect Kite rate limits.
    """
    underlying_key = underlying.upper()
    if underlying_key not in SUPPORTED_INDEX_UNDERLYINGS:
        raise ValueError(f"Unsupported underlying: {underlying}")
    from_date = _ensure_ist(from_date)
    to_date = _ensure_ist(to_date)
    from_ts = _as_ist_timestamp(from_date)
    to_ts = _as_ist_timestamp(to_date)

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / f"{underlying_key.lower()}_futures_{from_date.date()}_{to_date.date()}_{interval}.parquet"

    def _progress(pct: int, stage: str, extra: Optional[Dict[str, Any]] = None):
        if progress_callback:
            progress_callback(pct, stage, extra)

    _progress(5, "preparing", {"from": str(from_date.date()), "to": str(to_date.date())})

    if force_refresh:
        print(f"[DATA] ⚠️  FORCE REFRESH from Kite requested — ignoring all local caches for {from_date.date()}..{to_date.date()}")
    else:
        # === Smart Local-First Caching (major efficiency improvement) ===
        if use_cache:
            _progress(15, "scanning_local_cache")
            local_data = _load_overlapping_cached_data(
                from_date, to_date, interval, underlying=underlying_key
            )
            if local_data is not None and len(local_data) >= min_cache_rows:
                local_data = _normalize_index_ist(_filter_to_front_month(local_data))
                if local_data.index.min() <= from_ts and local_data.index.max() >= to_ts:
                    print("[DATA] ✅ Fully satisfied from local cache — no Kite call made. (auto-preferred best overlap)")
                    _progress(100, "done", {"source": "local_cache", "rows": len(local_data)})
                    return local_data
                if cache_only and len(local_data) >= min_cache_rows:
                    clipped = local_data[(local_data.index >= from_ts) & (local_data.index <= to_ts)]
                    result = clipped if len(clipped) >= min_cache_rows else local_data
                    print(
                        f"[DATA] ✅ Cache-only mode — using {len(result)} bars "
                        f"({result.index.min().date()} → {result.index.max().date()})"
                    )
                    _progress(100, "done", {"source": "local_cache_partial", "rows": len(result)})
                    return result

    if use_cache and not force_refresh and cache_file.exists():
        print(f"[DATA] Loading exact cached data from {cache_file}")
        df = _normalize_index_ist(_filter_to_front_month(pd.read_parquet(cache_file)))
        _progress(100, "done", {"source": "exact_cache", "rows": len(df)})
        return df

    if cache_only:
        raise ValueError(
            f"No cached {underlying_key} futures data with at least {min_cache_rows} bars. "
            "Refresh Kite token and re-fetch, or run a dashboard backtest to populate historical_cache."
        )

    print(f"[DATA] Fetching real {underlying_key} futures data from {from_date.date()} to {to_date.date()}{' (FORCE_REFRESH, bypassing cache)' if force_refresh else ''}...")
    _progress(20, "loading_instruments")

    instruments_df = get_index_futures_instruments(kite, underlying_key, from_date, to_date)
    contracts = list(instruments_df.iterrows())
    total_contracts = len(contracts)
    all_data = []
    fetch_errors: List[str] = []

    if total_contracts == 0:
        raise ValueError(
            f"No live {underlying_key} futures contracts found in Kite instruments master for the requested range. "
            "Kite only exposes tokens for live contracts — expired months cannot be re-fetched. "
            f"Range: {from_date.date()} to {to_date.date()}. "
            "Try a shorter recent window or use local cache for older months."
        )

    print(f"[DATA] Found {total_contracts} live {underlying_key} FUT contract(s) for range {from_date.date()}..{to_date.date()}")

    for idx, (_, row) in enumerate(contracts):
        token = row["instrument_token"]
        symbol = row["tradingsymbol"]
        expiry = row["expiry"]
        pct = 25 + int((idx / total_contracts) * 55)
        _progress(pct, "fetching_kite", {
            "contract": symbol,
            "contract_index": idx + 1,
            "contract_total": total_contracts,
        })

        window = _contract_fetch_window(expiry, from_date, to_date)
        if window is None:
            print(f"[DATA] Skipping {symbol} — no overlap with requested range")
            continue
        win_from, win_to = window

        try:
            df = _fetch_contract_bars(kite, token, symbol, win_from, win_to, interval)
            if df.empty:
                print(f"[DATA] No bars returned for {symbol} ({win_from.date()}..{win_to.date()})")
                continue
            df["expiry"] = expiry
            all_data.append(df)
            print(f"[DATA] Fetched {len(df)} bars for {symbol} ({win_from.date()}..{win_to.date()})")
        except Exception as e:
            msg = f"{symbol}: {e}"
            fetch_errors.append(msg)
            print(f"[DATA] Warning: Could not fetch {msg}")

    if not all_data:
        hint = (
            "No candle data returned from Kite. "
            "Common causes: (1) Access token expired — re-login via Settings, "
            "(2) Range predates all live contracts (Kite cannot fetch expired contract tokens), "
            "(3) 5minute requests exceed ~100 days per call (we chunk automatically). "
        )
        if fetch_errors:
            hint += f" API errors: {'; '.join(fetch_errors[:3])}"
        raise ValueError(hint)

    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values("timestamp").set_index("timestamp")
    combined = _annotate_front_month_and_rollover(combined)

    # Basic cleaning
    combined = combined.reset_index()
    combined = combined.drop_duplicates(subset=["timestamp"])
    combined = combined[(combined["high"] >= combined["low"]) & (combined["volume"] > 0)]
    combined = combined.set_index("timestamp")

    combined = _normalize_index_ist(_filter_to_front_month(combined))

    _progress(88, "saving_cache")
    if use_cache:
        combined.to_parquet(cache_file)
        print(f"[DATA] Saved to cache: {cache_file}")
        # Also write per-symbol cache for historical_cache overlap loader
        try:
            sym_cache = CACHE_DIR / f"{combined['symbol'].iloc[-1]}_{from_date.date()}_{to_date.date()}_{interval}.parquet"
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            combined.to_parquet(sym_cache)
            print(f"[DATA] Saved overlap cache: {sym_cache}")
        except Exception:
            pass

    print(f"[DATA] Fetched {len(combined):,} bars across {len(instruments_df)} contracts.")
    print(f"[DATA] Detected {combined['rollover'].sum()} potential rollover points.")
    _progress(100, "done", {"source": "kite", "rows": len(combined)})
    return combined


def fetch_real_nifty_futures_data(
    kite: KiteConnect,
    from_date: datetime,
    to_date: datetime,
    interval: str = "5minute",
    use_cache: bool = True,
    cache_dir: str = "data/historical",
    force_refresh: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> pd.DataFrame:
    """Backward-compatible NIFTY-only wrapper."""
    return fetch_real_index_futures_data(
        kite=kite,
        from_date=from_date,
        to_date=to_date,
        underlying="NIFTY",
        interval=interval,
        use_cache=use_cache,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
        progress_callback=progress_callback,
    )


def prepare_walk_forward_data(
    kite: KiteConnect,
    months_back: int = 6,
    interval: str = "5minute",
    underlying: str = "NIFTY",
) -> pd.DataFrame:
    """Convenience wrapper for typical walk-forward usage."""
    to_date = _ensure_ist(datetime.now())
    from_date = _ensure_ist(to_date - timedelta(days=30 * months_back))
    return fetch_real_index_futures_data(
        kite, from_date, to_date, underlying=underlying, interval=interval
    )


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


def best_cache_date_window_for_underlying(underlying: str) -> tuple[datetime, datetime] | None:
    """
    Union of all cached parquet date ranges for an index (e.g. NIFTY*).
    Prefer this over picking a single high-row contract file — expired-month
    parquets can have more rows but collapse after front-month filtering.
    """
    prefix = underlying.upper()
    datasets = [
        ds for ds in list_available_cached_datasets()
        if (ds.get("symbol") or "").upper().startswith(prefix)
        and ds.get("actual_from") and ds.get("actual_to")
        and ds.get("actual_from") != "?"
    ]
    if not datasets:
        return None
    from_dt = min(datetime.strptime(d["actual_from"], "%Y-%m-%d") for d in datasets)
    to_dt = max(datetime.strptime(d["actual_to"], "%Y-%m-%d") for d in datasets)
    return from_dt, to_dt
