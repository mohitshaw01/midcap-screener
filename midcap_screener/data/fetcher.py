"""
Data layer: fetch the Nifty Midcap 150 constituent list and OHLCV history.

Uses `niftystocks` for the live constituent list (refreshes monthly) and
`yfinance` for daily bars. Both are free; for production, swap in Kite/Upstox/Dhan.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# yfinance symbol for the Nifty Midcap 150 index. Fall-through list is tried in order.
INDEX_CANDIDATES: List[str] = ["^CNXMDCP150", "NIFTYMIDCAP150.NS", "^CRSMID"]


def get_universe() -> List[str]:
    """
    Return current Nifty Midcap 150 tickers with the '.NS' suffix for yfinance.

    Pulls live via `niftystocks`. Refreshing monthly is sufficient because
    the index rebalances semi-annually.
    """
    try:
        from niftystocks import ns
        tickers = ns.get_nifty_midcap150_with_ns()
        if tickers:
            return list(tickers)
    except ImportError:
        logger.error("niftystocks not installed. Run: pip install niftystocks")
        raise
    except Exception as e:
        logger.warning("niftystocks fetch failed: %s", e)
    raise RuntimeError("Could not fetch Nifty Midcap 150 universe.")


def fetch_ohlcv(
    tickers: List[str],
    period: str = "1y",
    interval: str = "1d",
    batch_size: int = 30,
) -> Dict[str, pd.DataFrame]:
    """
    Download OHLCV for each ticker. Returns dict[ticker -> DataFrame] with
    lowercase columns: ['open', 'high', 'low', 'close', 'volume'].

    Batches to avoid yfinance rate limits and malformed multi-ticker responses.
    """
    import yfinance as yf  # imported here so tests can run without network

    out: Dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        logger.info("Fetching batch %d (%d tickers)", i // batch_size + 1, len(batch))
        try:
            raw = yf.download(
                batch,
                period=period,
                interval=interval,
                progress=False,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
            )
        except Exception as e:
            logger.error("Batch download failed: %s", e)
            continue

        # yfinance returns a multi-indexed frame when >1 ticker, flat when 1.
        if len(batch) == 1:
            df = _normalize_columns(raw)
            if not df.empty:
                out[batch[0]] = df
            continue

        for t in batch:
            try:
                df = raw[t].dropna(how="all")
                df = _normalize_columns(df)
                if not df.empty:
                    out[t] = df
            except (KeyError, AttributeError):
                logger.debug("No data returned for %s", t)
    return out


def fetch_index(period: str = "1y") -> pd.DataFrame:
    """Fetch the Nifty Midcap 150 index itself (for the regime filter)."""
    import yfinance as yf

    for symbol in INDEX_CANDIDATES:
        try:
            raw = yf.download(symbol, period=period, progress=False, auto_adjust=True)
            df = _normalize_columns(raw)
            if not df.empty:
                logger.info("Using index symbol: %s", symbol)
                return df
        except Exception:
            continue
    raise RuntimeError("Could not fetch Nifty Midcap 150 index from any candidate.")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Force lowercase single-level columns. Handles yfinance's MultiIndex output."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # If we sliced by ticker, the leftover level is OHLCV names.
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].dropna(how="any")
    return df
