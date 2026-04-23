"""
Hard filters applied BEFORE scoring:
 - liquidity (20-day avg traded value >= ₹25 cr)
 - regime   (Nifty Midcap 150 itself above its 50-DMA)
 - earnings blackout (stub — see note below)
"""
from __future__ import annotations

import logging

import pandas as pd

from midcap_screener.indicators.compute import sma

logger = logging.getLogger(__name__)

CRORE = 1e7  # 1 crore = 10,000,000 INR


def liquidity_ok(df: pd.DataFrame, min_value_cr: float = 25.0) -> bool:
    """Latest 20-day average traded value must clear the floor."""
    if df.empty or "traded_value_sma20" not in df.columns:
        return False
    tv = df["traded_value_sma20"].iloc[-1]
    if pd.isna(tv):
        return False
    return bool(tv >= min_value_cr * CRORE)


def regime_bullish(index_df: pd.DataFrame) -> bool:
    """True when index close is above its 50-DMA on the latest bar."""
    if index_df.empty or len(index_df) < 50:
        return False
    ma50 = sma(index_df["close"], 50).iloc[-1]
    last = index_df["close"].iloc[-1]
    return bool(pd.notna(ma50) and last > ma50)


def earnings_blackout(ticker: str, days: int = 10) -> bool:
    """
    Return True if the ticker has earnings within `days` trading sessions.

    NOTE: Reliable free earnings calendars for Indian equities don't exist.
    `yfinance.Ticker(t).calendar` is inconsistent. For production, wire this
    to screener.in, Trendlyne, or your broker's feed. Default to False so the
    screener doesn't silently drop everything, and cross-check manually in
    the daily runbook (step 3).
    """
    return False
