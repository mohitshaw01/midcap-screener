"""
Enhanced scoring v2 — MPVS + Fundamentals + Sector + Flows + Multi-TF.

Total possible points: 160 (was 100)

Original MPVS (100 pts):
  Trend (25), Pullback (20), Momentum (20), Squeeze (15), Volume (10), RS (10)

New additions (60 pts):
  Quality fundamentals (30) — ROE, low debt, earnings growth, margins
  Sector rotation    (10) — hot sector bonus
  Multi-timeframe    (10) — weekly RSI confirmation
  Institutional      (10) — accumulation pattern + analyst sentiment

Penalties (deducted):
  RSI > 70 overbought     (-20)
  RSI < 30 falling knife  (-15)
  BB expansion             (-10)
  Weekly RSI overbought   (-10)
  Weekly RSI downtrend    (-10)
  Distribution pattern     (-5)
  Weak sector              (-5)

Signal tiers (based on final score out of 160):
  A-grade:   score >= 90  — full position, high conviction
  B-grade:   score 65-89  — half position, good setup developing
  Watchlist: score 45-64  — set price alerts, not actionable yet
  Skip:      score < 45   — no edge
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

from midcap_screener.screener.score import compute_score as compute_technical_score
from midcap_screener.screener.fundamentals import quality_score
from midcap_screener.screener.sector_rotation import sector_score
from midcap_screener.screener.multi_timeframe import multi_tf_score
from midcap_screener.screener.institutional import institutional_score

logger = logging.getLogger(__name__)


def compute_enhanced_score(
    df: pd.DataFrame,
    index_df: Optional[pd.DataFrame] = None,
    fundamentals: Optional[Dict] = None,
    sector_map: Optional[Dict[str, str]] = None,
    sector_ranking: Optional[List[Tuple[str, float]]] = None,
    ticker: str = "",
) -> Dict:
    """
    Compute the full enhanced score combining all signal layers.
    Returns a comprehensive breakdown dict.
    """
    # 1. Technical score (original MPVS)
    tech = compute_technical_score(df, index_df)

    # 2. Fundamental quality bonus
    qual = quality_score(fundamentals)

    # 3. Sector rotation bonus
    sect = {"sector": "Unknown", "sector_bonus": 0, "sector_rank": -1}
    if sector_map and sector_ranking:
        sect = sector_score(ticker, sector_map, sector_ranking)

    # 4. Multi-timeframe RSI
    mtf = multi_tf_score(df)

    # 5. Institutional / flow signals
    inst = institutional_score(df, fundamentals)

    # --- Combine ---
    raw_total = (
        tech.get("trend", 0)
        + tech.get("pullback", 0)
        + tech.get("momentum", 0)
        + tech.get("squeeze", 0)
        + tech.get("volume", 0)
        + tech.get("relative_strength", 0)
        + qual.get("quality_total", 0)
        + max(0, sect.get("sector_bonus", 0))  # only positive bonuses in raw
        + max(0, mtf.get("multi_tf_bonus", 0))
        + max(0, inst.get("institutional_total", 0))
    )

    total_penalty = (
        tech.get("penalty", 0)
        + abs(min(0, sect.get("sector_bonus", 0)))   # negative sector = penalty
        + abs(min(0, mtf.get("multi_tf_bonus", 0)))   # negative mtf = penalty
        + abs(min(0, inst.get("institutional_total", 0)))
    )

    final_score = max(0, raw_total - total_penalty)

    # Determine signal tier
    if final_score >= 90:
        tier = "A"
    elif final_score >= 65:
        tier = "B"
    elif final_score >= 45:
        tier = "WATCH"
    else:
        tier = "SKIP"

    return {
        # Technical breakdown
        "trend": tech.get("trend", 0),
        "pullback": tech.get("pullback", 0),
        "momentum": tech.get("momentum", 0),
        "squeeze": tech.get("squeeze", 0),
        "volume": tech.get("volume", 0),
        "relative_strength": tech.get("relative_strength", 0),
        "tech_penalty": tech.get("penalty", 0),

        # Fundamental breakdown
        "roe_bonus": qual.get("roe_bonus", 0),
        "low_debt_bonus": qual.get("low_debt_bonus", 0),
        "earnings_growth_bonus": qual.get("earnings_growth_bonus", 0),
        "margin_bonus": qual.get("margin_bonus", 0),
        "quality_total": qual.get("quality_total", 0),

        # Sector
        "sector": sect.get("sector", "Unknown"),
        "sector_rank": sect.get("sector_rank", -1),
        "sector_bonus": sect.get("sector_bonus", 0),

        # Multi-timeframe
        "weekly_rsi": mtf.get("weekly_rsi"),
        "multi_tf_bonus": mtf.get("multi_tf_bonus", 0),

        # Institutional
        "accumulation_bonus": inst.get("accumulation_bonus", 0),
        "analyst_bonus": inst.get("analyst_bonus", 0),
        "institutional_total": inst.get("institutional_total", 0),

        # Totals
        "raw_total": raw_total,
        "total_penalty": total_penalty,
        "score": final_score,
        "tier": tier,
    }


def format_score_breakdown(s: Dict) -> str:
    """One-line human-readable summary of why a stock scored what it did."""
    parts = []
    if s.get("trend", 0) >= 15:
        parts.append("uptrend")
    if s.get("trend", 0) >= 25:
        parts.append("rising MA")
    if s.get("pullback", 0) >= 10:
        parts.append("pullback")
    if s.get("pullback", 0) >= 20:
        parts.append("RSI rising")
    if s.get("momentum", 0) >= 10:
        parts.append("MACD flip")
    if s.get("squeeze", 0) >= 15:
        parts.append("BB squeeze")
    if s.get("volume", 0) >= 10:
        parts.append("vol surge")
    if s.get("relative_strength", 0) >= 10:
        parts.append("RS>idx")
    if s.get("quality_total", 0) >= 10:
        parts.append(f"quality({s['quality_total']})")
    if s.get("sector_bonus", 0) > 0:
        parts.append(f"hot sector")
    if s.get("multi_tf_bonus", 0) > 0:
        parts.append(f"wkly RSI ok")
    if s.get("institutional_total", 0) > 0:
        parts.append("accumulation")
    if s.get("tech_penalty", 0) > 0:
        parts.append(f"penalty(-{s['tech_penalty']})")
    return " + ".join(parts) if parts else "—"
