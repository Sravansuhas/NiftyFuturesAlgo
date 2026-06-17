"""
Thread-safe tick-to-OHLCV candle aggregator with optional OI tracking.

Buckets align to IST (Asia/Kolkata) boundaries for 1m / 5m / 15m intervals.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple, Union

from .market_calendar import IST

logger = logging.getLogger(__name__)

SUPPORTED_INTERVALS = {"1m": 1, "5m": 5, "15m": 15}
CandleDict = Dict[str, Union[int, float, str, datetime, None]]


def _to_ist(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def _bucket_start(ts: datetime, interval_minutes: int) -> datetime:
    ts_ist = _to_ist(ts)
    minute = (ts_ist.minute // interval_minutes) * interval_minutes
    return ts_ist.replace(minute=minute, second=0, microsecond=0)


def _empty_candle(
    token: int,
    interval: str,
    bucket: datetime,
    ltp: float,
    volume: int = 0,
    oi: Optional[int] = None,
) -> CandleDict:
    return {
        "instrument_token": token,
        "interval": interval,
        "open_time": bucket,
        "open": ltp,
        "high": ltp,
        "low": ltp,
        "close": ltp,
        "volume": volume,
        "oi_open": oi,
        "oi_close": oi,
        "oi_delta": 0 if oi is not None else None,
    }


class CandleBuilder:
    """Aggregates ticks into IST-aligned OHLCV candles with optional OI delta."""

    def __init__(
        self,
        intervals: Optional[List[str]] = None,
        max_candles_per_token: int = 200,
    ):
        if intervals is None:
            intervals = ["1m", "5m", "15m"]
        unknown = [i for i in intervals if i not in SUPPORTED_INTERVALS]
        if unknown:
            raise ValueError(f"Unsupported intervals: {unknown}")
        self.intervals = list(intervals)
        self.max_candles_per_token = max(1, int(max_candles_per_token))
        self._lock = threading.Lock()
        self._completed: Dict[int, Dict[str, Deque[CandleDict]]] = {}
        self._forming: Dict[int, Dict[str, CandleDict]] = {}
        self._last_cumulative_volume: Dict[int, int] = {}

    def on_tick(
        self,
        instrument_token: int,
        ltp: float,
        timestamp: datetime,
        volume: Optional[int] = None,
        oi: Optional[int] = None,
    ) -> Tuple[Dict[str, CandleDict], Dict[str, CandleDict]]:
        """
        Ingest one tick. Returns (completed_by_interval, forming_by_interval).
        completed_by_interval only contains intervals that rolled on this tick.
        """
        token = int(instrument_token)
        price = float(ltp)
        ts = _to_ist(timestamp)
        tick_volume = 0

        with self._lock:
            if volume is not None:
                cumulative = int(volume)
                prev = self._last_cumulative_volume.get(token)
                if prev is not None and cumulative >= prev:
                    tick_volume = cumulative - prev
                self._last_cumulative_volume[token] = cumulative

            completed: Dict[str, CandleDict] = {}
            forming: Dict[str, CandleDict] = {}

            for interval in self.intervals:
                minutes = SUPPORTED_INTERVALS[interval]
                bucket = _bucket_start(ts, minutes)
                current = self._forming.setdefault(token, {}).get(interval)

                if current is None:
                    current = _empty_candle(token, interval, bucket, price, tick_volume, oi)
                    self._forming[token][interval] = current
                    forming[interval] = dict(current)
                    continue

                current_bucket = current["open_time"]
                if bucket > current_bucket:
                    finished = dict(current)
                    if finished.get("oi_open") is not None and finished.get("oi_close") is not None:
                        finished["oi_delta"] = int(finished["oi_close"]) - int(finished["oi_open"])
                    self._append_completed(token, interval, finished)
                    completed[interval] = finished

                    current = _empty_candle(token, interval, bucket, price, tick_volume, oi)
                    self._forming[token][interval] = current
                    forming[interval] = dict(current)
                    continue

                current["high"] = max(float(current["high"]), price)
                current["low"] = min(float(current["low"]), price)
                current["close"] = price
                current["volume"] = int(current["volume"]) + tick_volume
                if oi is not None:
                    if current.get("oi_open") is None:
                        current["oi_open"] = oi
                    current["oi_close"] = oi
                    current["oi_delta"] = int(oi) - int(current["oi_open"])
                forming[interval] = dict(current)

            return completed, forming

    def get_candles(
        self,
        token: int,
        interval: str,
        n: int = 50,
    ) -> List[CandleDict]:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")
        token = int(token)
        n = max(1, int(n))
        with self._lock:
            store = self._completed.get(token, {}).get(interval)
            if not store:
                return []
            items = list(store)
            return [dict(c) for c in items[-n:]]

    def get_latest_candle(
        self,
        token: int,
        interval: str,
        *,
        include_forming: bool = True,
    ) -> Optional[CandleDict]:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")
        token = int(token)
        with self._lock:
            if include_forming:
                forming = self._forming.get(token, {}).get(interval)
                if forming:
                    return dict(forming)
            store = self._completed.get(token, {}).get(interval)
            if store:
                return dict(store[-1])
        return None

    def get_oi_delta(self, token: int, interval: str = "5m") -> Optional[int]:
        latest = self.get_latest_candle(token, interval, include_forming=True)
        if not latest:
            return None
        delta = latest.get("oi_delta")
        return int(delta) if delta is not None else None

    def _append_completed(self, token: int, interval: str, candle: CandleDict) -> None:
        per_token = self._completed.setdefault(token, {})
        store = per_token.get(interval)
        if store is None:
            store = deque(maxlen=self.max_candles_per_token)
            per_token[interval] = store
        store.append(candle)


def get_previous_candle_for_symbol(
    token: int,
    builder: Optional["CandleBuilder"],
    interval: str = "5m",
) -> Optional[CandleDict]:
    """
    Return the prior completed OHLCV bar for breakout logic (not the forming bar).

    Used by the live strategy to prefer WebSocket-built candles over REST historical
    seeding when enough completed bars exist for the instrument token.
    """
    if builder is None:
        return None
    if interval not in SUPPORTED_INTERVALS:
        return None

    token = int(token)
    completed = builder.get_candles(token, interval, n=1)
    if not completed:
        return None

    prev = completed[-1]
    return {
        "instrument_token": token,
        "interval": interval,
        "open_time": prev["open_time"],
        "open": float(prev["open"]),
        "high": float(prev["high"]),
        "low": float(prev["low"]),
        "close": float(prev["close"]),
        "volume": int(prev.get("volume", 0) or 0),
        "source": "ws_candle",
    }