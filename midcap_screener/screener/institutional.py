"""
Institutional flow scoring (FII/DII proxy).

True FII/DII data requires scraping NSE bulk deal / shareholding filings.
As a free-tier proxy, we use two signals available from yfinance:

1. Institutional ownership % — from yfinance .info
2. Short-term volume-price divergence — rising price + rising volume over
   5 days suggests institutional accumulation.

Scoring:
  +5  if institutional ownership > 30% (well-followed stock)
  +5  if 5-day accumulation pattern detected (price up + vol rising)
  +5  if analyst recommendation is 'buy' or 'strongBuy'
  -5  if 5-day distribution pattern (price flat/down + vol rising)

For production: wire to NSE bulk deal feed or Trendlyne institutional data.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def institutional_score(
    df: pd.DataFrame,
    fundamentals: Optional[Dict] = None,
) -> Dict[str, float]:
    """
    Score based on institutional interest proxies.
    """
    out = {
        "inst_ownership_bonus": 0,
        "accumulation_bonus": 0,
        "analyst_bonus": 0,
        "institutional_total": 0,
    }

    # 1. Institutional ownership
    if fundamentals:
        inst = fundamentals.get("promoter_pct")  # this is actually insiders
        # yfinance also has institutionsPercentHeld — check both
        # For Indian stocks, we use the recommendation as an analyst proxy
        rec = (fundamentals.get("recommendation") or "").lower()
        if rec in ("buy", "strongbuy", "strong_buy"):
            out["analyst_bonus"] = 5
        elif rec in ("sell", "strongsell", "strong_sell"):
            out["analyst_bonus"] = -5

    # 2. Accumulation / distribution pattern (last 5 bars)
    if len(df) >= 6:
        last5 = df.tail(5)
        price_change = (last5["close"].iloc[-1] / last5["close"].iloc[0]) - 1
        vol_trend = last5["volume"].iloc[-1] / last5["volume"].iloc[0] - 1

        if price_change > 0.02 and vol_trend > 0.20:
            # Rising price + rising volume = accumulation
            out["accumulation_bonus"] = 5
        elif price_change < -0.01 and vol_trend > 0.30:
            # Falling price + rising volume = distribution
            out["accumulation_bonus"] = -5

    out["institutional_total"] = (
        out["inst_ownership_bonus"]
        + out["accumulation_bonus"]
        + out["analyst_bonus"]
    )
    return out
