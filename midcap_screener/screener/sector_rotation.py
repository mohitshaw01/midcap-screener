"""
Sector momentum rotation scoring.

Logic:
  1. Group all Midcap 150 stocks by their sector (via yfinance .info).
  2. Compute each sector's average 1-month return.
  3. Rank sectors from strongest to weakest.
  4. Stocks in top 3 sectors get +10 bonus, bottom 3 get -5 penalty.

Why: In trending markets, the strongest sectors continue outperforming for
weeks. A mediocre pullback in a hot sector beats a perfect setup in a dying
sector. This is the single highest-value addition to the screener.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Fallback sector mapping for common Midcap 150 stocks when yfinance fails.
# Maps NSE ticker (without .NS) to sector.
SECTOR_FALLBACK = {
    "BHEL": "Industrials", "POLYCAB": "Industrials", "HEROMOTOCO": "Auto",
    "MARICO": "FMCG", "LUPIN": "Pharma", "PERSISTENT": "IT",
    "INDIANB": "Banking", "BSE": "Financial Services", "LT": "Industrials",
    "SHRIRAMFIN": "Financial Services", "ASHOKLEY": "Auto",
    "LICHSGFIN": "Financial Services", "JSWENERGY": "Energy",
    "INDUSINDBK": "Banking", "MANKIND": "Pharma", "BHARATFORG": "Auto",
    "IDFCFIRSTB": "Banking", "ICICIPRULI": "Insurance",
    "GODREJCP": "FMCG", "TRENT": "Retail", "SBICARD": "Financial Services",
    "FEDERALBNK": "Banking", "CANBK": "Banking",
}


def assign_sectors(
    tickers: List[str],
    fundamentals: Optional[Dict[str, Dict]] = None,
) -> Dict[str, str]:
    """
    Return dict[ticker -> sector]. Uses fundamentals data if available,
    falls back to the hardcoded map, and defaults to 'Unknown'.
    """
    out = {}
    for t in tickers:
        # Try fundamentals first
        if fundamentals and t in fundamentals:
            sector = fundamentals[t].get("sector", "")
            if sector:
                out[t] = sector
                continue
        # Fallback map (strip .NS suffix for lookup)
        clean = t.replace(".NS", "")
        if clean in SECTOR_FALLBACK:
            out[t] = SECTOR_FALLBACK[clean]
        else:
            out[t] = "Unknown"
    return out


def rank_sectors(
    sector_map: Dict[str, str],
    stock_data: Dict[str, pd.DataFrame],
    lookback: int = 21,
) -> List[Tuple[str, float]]:
    """
    Rank sectors by average `lookback`-day return (default 1 month = 21 bars).
    Returns list of (sector, avg_return) sorted best to worst.
    """
    sector_returns: Dict[str, List[float]] = defaultdict(list)

    for ticker, sector in sector_map.items():
        if ticker not in stock_data:
            continue
        df = stock_data[ticker]
        if len(df) < lookback + 1:
            continue
        ret = df["close"].iloc[-1] / df["close"].iloc[-(lookback + 1)] - 1
        sector_returns[sector].append(ret)

    # Average return per sector
    sector_avg = []
    for sector, rets in sector_returns.items():
        if len(rets) >= 3:  # need at least 3 stocks to be meaningful
            avg = sum(rets) / len(rets)
            sector_avg.append((sector, avg))

    sector_avg.sort(key=lambda x: -x[1])
    return sector_avg


def sector_score(
    ticker: str,
    sector_map: Dict[str, str],
    sector_ranking: List[Tuple[str, float]],
    top_n: int = 3,
    bottom_n: int = 3,
) -> Dict[str, float]:
    """
    Score a stock based on its sector's rank.
    +10 for top sectors, -5 for bottom sectors, 0 otherwise.
    """
    out = {"sector": sector_map.get(ticker, "Unknown"), "sector_bonus": 0}

    if not sector_ranking:
        return out

    sectors_ranked = [s[0] for s in sector_ranking]
    stock_sector = sector_map.get(ticker, "Unknown")

    if stock_sector in sectors_ranked[:top_n]:
        out["sector_bonus"] = 10
    elif stock_sector in sectors_ranked[-bottom_n:]:
        out["sector_bonus"] = -5

    # Also store the sector rank for display
    if stock_sector in sectors_ranked:
        out["sector_rank"] = sectors_ranked.index(stock_sector) + 1
    else:
        out["sector_rank"] = -1

    return out
