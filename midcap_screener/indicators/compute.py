"""
Technical indicators. Implemented in pure pandas/numpy so the project has no
hard TA-Lib / pandas_ta dependency. Mathematically this matches the standard
Wilder / J. Welles definitions used by TradingView.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---- primitives ---------------------------------------------------------

def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """
    Wilder's RSI (standard 14-period).
    Edge cases: avg_loss==0 -> RSI=100; avg_gain==0 -> RSI=0.
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    # Convention: pure gains -> 100, pure losses -> 0
    rsi_val = rsi_val.where(avg_loss != 0, 100.0)
    rsi_val = rsi_val.where(avg_gain != 0, 0.0)
    return rsi_val


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger(series: pd.Series, length: int = 20, std: float = 2.0):
    """Returns (upper, mid, lower, width). Width is (upper-lower)/mid."""
    mid = sma(series, length)
    dev = series.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + std * dev
    lower = mid - std * dev
    width = (upper - lower) / mid
    return upper, mid, lower, width


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


# ---- one-call enricher --------------------------------------------------

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all indicators used by the MPVS screener to a copy of `df`.
    Expects lowercase OHLCV columns. Returns the enriched frame.
    """
    df = df.copy()
    c = df["close"]
    df["sma50"] = sma(c, 50)
    df["sma200"] = sma(c, 200)

    df["rsi14"] = rsi(c, 14)

    macd_line, signal_line, hist = macd(c)
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist

    upper, mid, lower, width = bollinger(c, 20, 2.0)
    df["bb_upper"] = upper
    df["bb_mid"] = mid
    df["bb_lower"] = lower
    df["bb_width"] = width
    # Percentile rank of band width over trailing 60 sessions (0 = tightest).
    df["bb_width_rank_60"] = df["bb_width"].rolling(60, min_periods=60).rank(pct=True)

    df["atr14"] = atr(df["high"], df["low"], df["close"], 14)

    df["vol_sma20"] = df["volume"].rolling(20, min_periods=20).mean()
    df["traded_value"] = df["close"] * df["volume"]
    df["traded_value_sma20"] = df["traded_value"].rolling(20, min_periods=20).mean()

    # 10-day % change in 50-DMA as a slope proxy.
    df["sma50_slope_10"] = df["sma50"].pct_change(10)

    return df
