"""
Options chain archive — daily parquet snapshots for index chains.

Layout: data/options_chain/{YYYY-MM-DD}/{INDEX}.parquet

Scaffold for historical chain storage; live fetch remains in options_chain.py.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from .market_calendar import IST, now_ist

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = PROJECT_ROOT / "data" / "options_chain"

SUPPORTED_INDICES = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})


def _normalize_index(index: str) -> str:
    key = (index or "").strip().upper()
    if key not in SUPPORTED_INDICES:
        raise ValueError(f"Unsupported index: {index!r} — expected one of {sorted(SUPPORTED_INDICES)}")
    return key


def _snapshot_date(snapshot_at: Optional[datetime] = None) -> str:
    when = snapshot_at or now_ist()
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST).strftime("%Y-%m-%d")


def _archive_path(index: str, day: str) -> Path:
    return ARCHIVE_ROOT / day / f"{index}.parquet"


def _chain_to_dataframe(chain_data: Union[pd.DataFrame, List[Dict[str, Any]]]) -> pd.DataFrame:
    if isinstance(chain_data, pd.DataFrame):
        df = chain_data.copy()
    else:
        df = pd.DataFrame(list(chain_data or []))
    if df.empty:
        return df
    return df


def save_chain_snapshot(
    index: str,
    chain_data: Union[pd.DataFrame, List[Dict[str, Any]]],
    *,
    snapshot_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Persist one index chain snapshot under data/options_chain/{date}/{index}.parquet.

    Adds archive columns: snapshot_at, index.
    """
    key = _normalize_index(index)
    day = _snapshot_date(snapshot_at)
    when = snapshot_at or now_ist()
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    when = when.astimezone(IST)

    df = _chain_to_dataframe(chain_data)
    df["snapshot_at"] = when.isoformat()
    df["index"] = key
    if metadata:
        for col, val in metadata.items():
            df[f"meta_{col}"] = val

    path = _archive_path(key, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, compression="snappy", index=False)
    except Exception as exc:
        logger.warning("Parquet write failed (%s); falling back to CSV", exc)
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        return csv_path
    return path


def list_snapshots(
    index: str,
    days: int = 30,
    *,
    as_of: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    List archive metadata for an index over the last `days` calendar days.
    """
    key = _normalize_index(index)
    end = as_of or now_ist().date()
    start = end - timedelta(days=max(0, int(days) - 1))
    results: List[Dict[str, Any]] = []

    if not ARCHIVE_ROOT.exists():
        return results

    for day_dir in sorted(ARCHIVE_ROOT.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day < start or day > end:
            continue

        parquet_path = day_dir / f"{key}.parquet"
        csv_path = day_dir / f"{key}.csv"
        path = parquet_path if parquet_path.exists() else csv_path
        if not path.exists():
            continue

        meta: Dict[str, Any] = {
            "date": day.isoformat(),
            "index": key,
            "path": str(path),
            "format": path.suffix.lstrip("."),
            "row_count": None,
            "snapshot_at": None,
        }
        try:
            if path.suffix == ".parquet":
                df = pd.read_parquet(path)
            else:
                df = pd.read_csv(path)
            meta["row_count"] = int(len(df))
            if "snapshot_at" in df.columns and len(df):
                meta["snapshot_at"] = str(df["snapshot_at"].iloc[-1])
        except Exception as exc:
            meta["read_error"] = str(exc)
        results.append(meta)

    results.sort(key=lambda r: r["date"], reverse=True)
    return results


def load_snapshot(index: str, day: Union[str, date]) -> Optional[pd.DataFrame]:
    """Load a single archived chain snapshot if present."""
    key = _normalize_index(index)
    day_str = day.isoformat() if isinstance(day, date) else str(day)
    path = _archive_path(key, day_str)
    if not path.exists():
        alt = path.with_suffix(".csv")
        if not alt.exists():
            return None
        path = alt
    try:
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)
    except Exception as exc:
        logger.debug("Archive read failed for %s: %s", path, exc)
        return None