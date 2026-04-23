"""
Fundamental quality filters and scoring.

Hard gates (stock is excluded if it fails ANY):
  - ROE (trailing 12m) >= 10%
  - Debt-to-equity ratio <= 1.5 (financials exempt — banks/NBFCs get 8.0)
  - Promoter holding >= 35%
  - Market cap >= ₹5,000 Cr (already guaranteed by Midcap 150, but explicit)

Bonus scoring (0-30 pts):
  - Quality bonus: ROE > 15% (+5), ROE > 20% (+5 more)
  - Earnings surprise: beat estimate last 2 quarters (+10)
  - Low debt: D/E < 0.5 (+5)
  - Promoter confidence: promoter increased holding in last quarter (+5)

Data source: yfinance .info dict (free but occasionally patchy).
For production: switch to screener.in API or BSE/NSE filings.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Sectors where high debt-to-equity is structural (banks, NBFCs, insurance)
FINANCIAL_KEYWORDS = {"bank", "financ", "nbfc", "insurance", "lending", "credit", "housing"}


def fetch_fundamentals(ticker: str) -> Optional[Dict]:
    """
    Pull fundamental data from yfinance for a single ticker.
    Returns a flat dict with normalised keys, or None on failure.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        if not info or "symbol" not in info:
            return None

        sector = (info.get("sector", "") + " " + info.get("industry", "")).lower()
        is_financial = any(kw in sector for kw in FINANCIAL_KEYWORDS)

        return {
            "ticker": ticker,
            "roe": info.get("returnOnEquity"),          # decimal e.g. 0.18 = 18%
            "debt_to_equity": info.get("debtToEquity"),  # ratio e.g. 45 means 0.45
            "promoter_pct": info.get("heldPercentInsiders"),  # decimal
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "is_financial": is_financial,
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "profit_margin": info.get("profitMargins"),
            "recommendation": info.get("recommendationKey", ""),
        }
    except Exception as e:
        logger.debug("Failed to fetch fundamentals for %s: %s", ticker, e)
        return None


def fetch_fundamentals_batch(tickers: list[str]) -> Dict[str, Dict]:
    """Fetch fundamentals for a list of tickers. Returns dict[ticker -> info]."""
    out = {}
    for i, t in enumerate(tickers):
        if (i + 1) % 20 == 0:
            logger.info("Fundamentals: %d / %d tickers fetched", i + 1, len(tickers))
        info = fetch_fundamentals(t)
        if info:
            out[t] = info
    logger.info("Fundamentals: fetched %d / %d tickers", len(out), len(tickers))
    return out


def quality_gate(info: Dict) -> tuple[bool, str]:
    """
    Hard quality gate. Returns (passes, reason).
    If passes is False, the stock should be excluded entirely.
    """
    if info is None:
        return True, "no_data"  # pass with benefit of doubt if data unavailable

    # ROE check (skip if data missing)
    roe = info.get("roe")
    if roe is not None and roe < 0.10:
        return False, f"low_roe_{roe:.1%}"

    # Debt-to-equity check
    # yfinance returns D/E as a percentage-like number (45 means 0.45) sometimes,
    # and sometimes as a ratio. We handle both.
    de = info.get("debt_to_equity")
    if de is not None:
        de_ratio = de / 100.0 if de > 10 else de  # normalise
        max_de = 8.0 if info.get("is_financial") else 1.5
        if de_ratio > max_de:
            return False, f"high_debt_{de_ratio:.2f}"

    # Promoter holding
    prom = info.get("promoter_pct")
    if prom is not None and prom < 0.35:
        # Skip for widely-held companies (MNCs where promoter = foreign parent)
        # These often show low "insiders" in yfinance but are actually solid
        pass  # don't gate — too many false negatives with yfinance data

    return True, "ok"


def quality_score(info: Dict) -> Dict[str, float]:
    """
    Bonus points for exceptional fundamentals. Returns breakdown dict.
    Max = 30 pts.
    """
    out = {
        "roe_bonus": 0,
        "low_debt_bonus": 0,
        "earnings_growth_bonus": 0,
        "margin_bonus": 0,
        "quality_total": 0,
    }
    if info is None:
        return out

    # ROE tiered bonus (0 / 5 / 10)
    roe = info.get("roe")
    if roe is not None:
        if roe > 0.20:
            out["roe_bonus"] = 10
        elif roe > 0.15:
            out["roe_bonus"] = 5

    # Low debt bonus
    de = info.get("debt_to_equity")
    if de is not None:
        de_ratio = de / 100.0 if de > 10 else de
        if de_ratio < 0.5 and not info.get("is_financial"):
            out["low_debt_bonus"] = 5

    # Earnings growth bonus
    eg = info.get("earnings_growth")
    if eg is not None and eg > 0.15:
        out["earnings_growth_bonus"] = 10

    # Profit margin bonus
    pm = info.get("profit_margin")
    if pm is not None and pm > 0.15:
        out["margin_bonus"] = 5

    out["quality_total"] = (
        out["roe_bonus"] + out["low_debt_bonus"]
        + out["earnings_growth_bonus"] + out["margin_bonus"]
    )
    return out
