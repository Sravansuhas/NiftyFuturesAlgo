"""
Shared regime detection — live/backtest parity with app/strategy.py thresholds.
"""

from __future__ import annotations

import pandas as pd


def detect_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return DataFrame indexed like *df* with columns: volatility, trend.

    Thresholds aligned with PreviousCandleBreakoutStrategy live logic.
    """
    if df.empty:
        return pd.DataFrame(columns=["volatility", "trend"])

    atr = (df["high"] - df["low"]).rolling(14, min_periods=1).mean()
    long_atr = atr.rolling(50, min_periods=1).mean()
    ratio = atr / long_atr.replace(0, pd.NA)
    vol = pd.Series("normal", index=df.index, dtype="object")
    vol[ratio < 0.65] = "low"
    vol[ratio > 1.45] = "high"

    slow_ma = df["close"].rolling(30, min_periods=1).mean()
    trend = pd.Series("ranging", index=df.index, dtype="object")
    trend[df["close"] > slow_ma * 1.002] = "uptrend"
    trend[df["close"] < slow_ma * 0.998] = "downtrend"

    return pd.DataFrame({"volatility": vol, "trend": trend}, index=df.index)


def detect_regime_simple(df: pd.DataFrame, window: int = 50) -> pd.Series:
    """
    Back-compat vol regime Series with _trend sidecar for WFO.

    Delegates to detect_regimes(); maps uptrend/downtrend/ranging → up/down/flat.
    """
    regimes = detect_regimes(df)
    vol = regimes["volatility"]
    trend_map = {"uptrend": "up", "downtrend": "down", "ranging": "flat"}
    trend = regimes["trend"].map(trend_map).fillna("flat")
    vol._trend = trend  # type: ignore[attr-defined]
    return vol