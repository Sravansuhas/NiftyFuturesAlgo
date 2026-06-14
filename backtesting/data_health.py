"""
backtesting/data_health.py

Validates local market data under data/ (parquet caches, JSON/JSONL runtime files).
Used by the dashboard to show health status and trigger repairs via Kite download.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

IST_OFFSET = timedelta(hours=5, minutes=30)

DATA_ROOT = Path("data")
HISTORICAL_DIRS = [DATA_ROOT / "historical_cache", DATA_ROOT / "historical"]

# Kite Connect v3 — official intervals (see kite.trade/docs/connect/v3/historical/)
KITE_HISTORICAL_INTERVALS = [
    "minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"
]

# Practical per-request lookback limits (community + Zerodha forum guidance, 2025–2026).
# Chunk fetches stay under these to avoid API errors.
KITE_INTERVAL_MAX_DAYS = {
    "minute": 60,
    "3minute": 60,
    "5minute": 100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day": 2000,
}

STALE_DAYS_THRESHOLD = 5  # cache older than this is flagged stale (not corrupt)


def _validate_json_file(path: Path) -> Dict[str, Any]:
    """Validate single JSON or JSONL (append-only log) files."""
    item: Dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "format": "unknown",
    }
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            item.update({"status": "empty", "issues": ["File is empty"]})
            return item
        try:
            json.loads(text)
            item.update({"status": "ok", "format": "json", "records": 1})
            return item
        except json.JSONDecodeError:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            for ln in lines:
                json.loads(ln)
            item.update({"status": "ok", "format": "jsonl", "records": len(lines)})
            return item
    except Exception as e:
        item.update({"status": "corrupt", "issues": [str(e)]})
        return item


def _validate_parquet(path: Path) -> Dict[str, Any]:
    """Deep validation of a parquet OHLCV file."""
    item: Dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "format": "parquet",
    }
    issues: List[str] = []

    try:
        df = pd.read_parquet(path)
    except Exception as e:
        item.update({"status": "corrupt", "issues": [f"Cannot read parquet: {e}"]})
        return item

    if df.empty:
        item.update({"status": "corrupt", "issues": ["Parquet file has zero rows"]})
        return item

    required = {"open", "high", "low", "close", "volume"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        issues.append(f"Missing columns: {sorted(missing_cols)}")

    rows = len(df)
    nulls = int(df.isnull().sum().sum())
    if nulls:
        issues.append(f"{nulls} null values in OHLCV columns")

    bad_hl = 0
    zero_vol = 0
    if "high" in df.columns and "low" in df.columns:
        bad_hl = int((df["high"] < df["low"]).sum())
        if bad_hl:
            issues.append(f"{bad_hl} bars where high < low")
    if "volume" in df.columns:
        zero_vol = int((df["volume"] <= 0).sum())
        if zero_vol > rows * 0.05:
            issues.append(f"{zero_vol} bars with zero volume (>5% of rows)")

    actual_from = str(pd.to_datetime(df.index.min()).date())
    actual_to = str(pd.to_datetime(df.index.max()).date())
    days_old = (datetime.now().date() - pd.to_datetime(df.index.max()).date()).days

    # Parse filename dates when present: SYMBOL_YYYY-MM-DD_YYYY-MM-DD_5minute
    file_from = file_to = None
    parts = path.stem.split("_")
    if len(parts) >= 4:
        try:
            file_from = parts[-3]
            file_to = parts[-2]
        except Exception:
            pass

    status = "ok"
    if issues:
        status = "corrupt"
    elif days_old > STALE_DAYS_THRESHOLD:
        status = "stale"
        issues.append(f"Latest bar is {days_old} days old (threshold {STALE_DAYS_THRESHOLD}d)")

    item.update({
        "status": status,
        "issues": issues,
        "rows": rows,
        "actual_from": actual_from,
        "actual_to": actual_to,
        "file_from": file_from,
        "file_to": file_to,
        "days_old": days_old,
        "interval": parts[-1] if parts else "unknown",
        "symbol": parts[0] if parts else path.stem,
        "bad_high_low": bad_hl,
        "zero_volume": zero_vol,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    })
    return item


def scan_data_health(stale_days: int = STALE_DAYS_THRESHOLD) -> Dict[str, Any]:
    """
    Full health report for data/ folder.
    Returns overall status: healthy | stale | corrupt | missing
    """
    global STALE_DAYS_THRESHOLD
    STALE_DAYS_THRESHOLD = stale_days

    parquet_files: List[Dict[str, Any]] = []
    json_files: List[Dict[str, Any]] = []

    for folder in HISTORICAL_DIRS:
        if not folder.exists():
            continue
        for f in sorted(folder.glob("*.parquet")):
            parquet_files.append(_validate_parquet(f))

    if DATA_ROOT.exists():
        for pattern in ("*.json", "*.jsonl"):
            for f in sorted(DATA_ROOT.glob(pattern)):
                if f.name.startswith("."):
                    continue
                json_files.append(_validate_json_file(f))
        kb = DATA_ROOT / "knowledge_base"
        if kb.exists():
            for f in sorted(kb.glob("*.json")):
                json_files.append(_validate_json_file(f))

    corrupt = [p for p in parquet_files if p.get("status") == "corrupt"]
    stale = [p for p in parquet_files if p.get("status") == "stale"]
    ok = [p for p in parquet_files if p.get("status") == "ok"]
    corrupt_json = [j for j in json_files if j.get("status") == "corrupt"]

    latest_to: Optional[str] = None
    latest_days_old: Optional[int] = None
    if parquet_files:
        dated = [(p.get("actual_to"), p.get("days_old", 999)) for p in parquet_files if p.get("actual_to")]
        if dated:
            latest_to = max(d[0] for d in dated)
            latest_days_old = min(d[1] for d in dated if d[0] == latest_to)

    if not parquet_files:
        overall = "missing"
    elif corrupt or corrupt_json:
        overall = "corrupt"
    elif latest_days_old is not None and latest_days_old <= stale_days:
        overall = "healthy"
    elif ok:
        overall = "healthy"
    elif stale:
        overall = "stale"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parquet_count": len(parquet_files),
        "parquet_ok": len(ok),
        "parquet_stale": len(stale),
        "parquet_corrupt": len(corrupt),
        "json_files": json_files,
        "parquet_files": parquet_files,
        "latest_data_date": latest_to,
        "stale_days_threshold": stale_days,
        "cache_dirs": [str(d) for d in HISTORICAL_DIRS],
        "docker_note": (
            "Docker Postgres/Redis (docker-compose.yml) are optional infra volumes — "
            "market OHLCV data lives in data/historical_cache/*.parquet on disk."
        ),
        "kite_api": {
            "intervals": KITE_HISTORICAL_INTERVALS,
            "interval_max_days_per_request": KITE_INTERVAL_MAX_DAYS,
            "project_default_interval": "5minute",
            "rate_limit_note": "~3 historical requests/second — downloads are chunked automatically",
            "continuous_futures": "Use continuous=1 for stitched futures across expiries (day bars)",
            "docs_url": "https://kite.trade/docs/connect/v3/historical/",
        },
        "recommendation": _recommendation(overall, stale, corrupt, latest_to),
    }


def _recommendation(overall: str, stale: list, corrupt: list, latest_to: Optional[str]) -> str:
    if overall == "missing":
        return "No historical parquet found. Use 'Download from Kite' in the Algo Lab → Presets & Data tab."
    if overall == "corrupt":
        n = len(corrupt)
        return f"{n} corrupt file(s) detected. Click 'Repair / Re-download' to fetch fresh data from Kite."
    if overall == "stale":
        return (
            f"Data is readable but outdated (latest: {latest_to}). "
            "Download fresh data to extend coverage through today."
        )
    msg = "Local cache looks healthy. Backtests can use 'Load from Local Cache' for instant runs."
    if stale and latest_to:
        msg += f" ({len(stale)} older file(s) on disk are superseded — latest bars through {latest_to}.)"
    return msg