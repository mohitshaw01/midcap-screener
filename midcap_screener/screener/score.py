"""
The 0-100 composite scoring rubric (MPVS strategy).

Rubric (from the strategy doc):
  Trend (25) — 15 if close > SMA50 > SMA200, 10 if SMA50 rising over last 10 days
  Pullback (20) — 20 if RSI(14) in 40..55 AND rising 2 sessions; 10 if just in band
  Momentum (20) — 10 for MACD-hist flip in last 3 days, 10 for MACD cross-up in last 5
  Squeeze  (15) — 15 if BB-width is in the bottom 20th percentile of last 60 days
  Volume   (10) — 10 if today's volume >= 1.5x 20-day average
  Rel. Str (10) — 10 if stock's 3-mo return beats index by >5%
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def compute_score(
    df: pd.DataFrame, index_df: Optional[pd.DataFrame] = None
) -> Dict[str, float]:
    """
    Score the *last* row of `df`. All indicators must already be computed.
    Returns a breakdown dict with keys trend / pullback / momentum / squeeze
    / volume / relative_strength / total.
    """
    if len(df) < 200:
        return _zero_score(reason="insufficient_history")

    row = df.iloc[-1]
    out: Dict[str, float] = {}

    # ---- Trend (25) -----------------------------------------------------
    trend = 0
    if (
        pd.notna(row["sma50"])
        and pd.notna(row["sma200"])
        and row["close"] > row["sma50"] > row["sma200"]
    ):
        trend += 15
    if pd.notna(row["sma50_slope_10"]) and row["sma50_slope_10"] > 0:
        trend += 10
    out["trend"] = trend

    # ---- Pullback quality (20) -----------------------------------------
    pullback = 0
    r = row["rsi14"]
    if pd.notna(r) and 40 <= r <= 55:
        pullback = 10  # baseline for "in the pullback zone"
        last3 = df["rsi14"].tail(3).dropna().values
        if len(last3) == 3 and last3[0] < last3[1] < last3[2]:
            pullback = 20  # RSI rising for the last 2 sessions — cleaner entry
    out["pullback"] = pullback

    # ---- Penalties (deducted from total at the end) --------------------
    # These prevent stocks that are extended / overbought from ranking high
    # even when other signals fire. The strategy is "buy the pullback", not
    # "chase the breakout".
    penalty = 0

    # Overbought: RSI > 70 means the stock is stretched. Buying here is chasing.
    if pd.notna(r) and r > 70:
        penalty += 20

    # Oversold: RSI < 30 in a midcap is usually a falling knife, not a pullback.
    if pd.notna(r) and r < 30:
        penalty += 15

    # Band expansion: BB-width in the top 20% of last 60 days means volatility
    # is EXPANDING, not compressing. The opposite of a squeeze setup.
    if pd.notna(row["bb_width_rank_60"]) and row["bb_width_rank_60"] >= 0.80:
        penalty += 10

    out["penalty"] = penalty

    # ---- Momentum confirmation (20) ------------------------------------
    momentum = 0
    hist = df["macd_hist"].dropna()
    if len(hist) >= 3:
        last3h = hist.tail(3).values
        # flipped to positive in the last 3 sessions
        if last3h[-1] > 0 and (last3h[0] <= 0 or last3h[1] <= 0):
            momentum += 10

    last5 = df.tail(5)
    if len(last5) >= 2 and last5[["macd", "macd_signal"]].notna().all().all():
        # MACD line crosses above signal line anywhere in last 5 sessions
        above = last5["macd"] > last5["macd_signal"]
        crossed = (above & ~above.shift(1, fill_value=False)).any()
        if bool(crossed):
            momentum += 10
    out["momentum"] = momentum

    # ---- Volatility squeeze (15) ---------------------------------------
    squeeze = 0
    if pd.notna(row["bb_width_rank_60"]) and row["bb_width_rank_60"] <= 0.20:
        squeeze = 15
    out["squeeze"] = squeeze

    # ---- Volume surge (10) ---------------------------------------------
    volume = 0
    if (
        pd.notna(row["vol_sma20"])
        and row["vol_sma20"] > 0
        and row["volume"] >= 1.5 * row["vol_sma20"]
    ):
        volume = 10
    out["volume"] = volume

    # ---- Relative strength vs index (10) -------------------------------
    rs = 0
    if index_df is not None and len(index_df) >= 63 and len(df) >= 63:
        stock_ret = df["close"].iloc[-1] / df["close"].iloc[-63] - 1
        idx_ret = index_df["close"].iloc[-1] / index_df["close"].iloc[-63] - 1
        if stock_ret - idx_ret > 0.05:
            rs = 10
    out["relative_strength"] = rs

    raw_total = (out["trend"] + out["pullback"] + out["momentum"]
                 + out["squeeze"] + out["volume"] + out["relative_strength"])
    out["total"] = max(0, raw_total - out["penalty"])
    return out


def _zero_score(reason: str) -> Dict[str, float]:
    return {
        "trend": 0,
        "pullback": 0,
        "momentum": 0,
        "squeeze": 0,
        "volume": 0,
        "relative_strength": 0,
        "penalty": 0,
        "total": 0,
        "reason": reason,  # type: ignore[dict-item]
    }
