"""
Multi-timeframe RSI confirmation.

The key insight: daily RSI in pullback zone (40-55) is necessary but not
sufficient. If the WEEKLY RSI is also 50-65, the stock is in a healthy
uptrend on the higher timeframe and just taking a breather on the daily.
That's where the big 10-20 day moves come from.

Scoring:
  +10 if weekly RSI is in the 50-65 sweet spot (strong but not overbought)
  +5  if weekly RSI is in the 40-50 zone (pullback on weekly too — can work
       but lower conviction)
  -10 if weekly RSI > 75 (overbought on weekly — the daily pullback might
       be the start of a real correction, not just a pause)
  -10 if weekly RSI < 35 (weekly downtrend — daily bounce is likely a trap)
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

from midcap_screener.indicators.compute import rsi


def compute_weekly_rsi(df: pd.DataFrame, period: int = 14) -> float | None:
    """
    Resample daily OHLCV to weekly bars, then compute RSI on the weekly close.
    Returns the latest weekly RSI value, or None if insufficient data.
    """
    if df.empty or len(df) < 70:  # need ~14 weekly bars minimum
        return None

    weekly = df["close"].resample("W-FRI").last().dropna()
    if len(weekly) < period + 1:
        return None

    weekly_rsi = rsi(weekly, period)
    last = weekly_rsi.iloc[-1]
    return float(last) if pd.notna(last) else None


def multi_tf_score(df: pd.DataFrame) -> Dict[str, float]:
    """
    Score based on weekly RSI alignment with the daily pullback thesis.
    """
    out = {"weekly_rsi": None, "multi_tf_bonus": 0}

    w_rsi = compute_weekly_rsi(df)
    if w_rsi is None:
        return out

    out["weekly_rsi"] = round(w_rsi, 1)

    if 50 <= w_rsi <= 65:
        out["multi_tf_bonus"] = 10   # sweet spot
    elif 40 <= w_rsi < 50:
        out["multi_tf_bonus"] = 5    # acceptable
    elif w_rsi > 75:
        out["multi_tf_bonus"] = -10  # overbought on weekly
    elif w_rsi < 35:
        out["multi_tf_bonus"] = -10  # downtrend on weekly

    return out
