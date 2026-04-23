"""
Daily screener entry point. Run after NSE close (~4:15 PM IST):

    python run_daily.py

Outputs screen_YYYY-MM-DD.csv and .html to reports_out/. Console prints the
regime flag and top 10 ranked candidates.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

from midcap_screener.data.fetcher import fetch_index, fetch_ohlcv, get_universe
from midcap_screener.data.cache import ParquetCache
from midcap_screener.indicators.compute import add_indicators
from midcap_screener.reports.daily import build_report
from midcap_screener.risk.sizing import compute_stop_target, position_size
from midcap_screener.screener.filters import liquidity_ok, regime_bullish
from midcap_screener.screener.score import compute_score

log = logging.getLogger("run_daily")


def main(config_path: str = "config.yaml") -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    cfg = yaml.safe_load(Path(config_path).read_text())
    cache = ParquetCache(cfg["paths"]["cache_dir"])

    log.info("Fetching Nifty Midcap 150 universe...")
    tickers = get_universe()
    log.info("Universe: %d tickers", len(tickers))

    log.info("Fetching OHLCV (1y)...")
    data = fetch_ohlcv(tickers, period="1y")
    log.info("Downloaded data for %d / %d tickers", len(data), len(tickers))
    cache.put_many(data)
    cache.stamp_today()

    log.info("Fetching index for regime check...")
    index_df = fetch_index(period="1y")
    regime_ok = regime_bullish(index_df)
    log.info("Regime bullish = %s", regime_ok)

    ranked = []
    for ticker, raw in data.items():
        df = add_indicators(raw)
        if not liquidity_ok(df, cfg["thresholds"]["min_liquidity_cr"]):
            continue
        score = compute_score(df, index_df)
        if score["total"] == 0:
            continue  # no signal at all — skip to avoid clutter
        row = df.iloc[-1]
        entry = float(row["close"])
        atr_val = float(row["atr14"])
        if not (atr_val > 0):
            continue
        stop, target = compute_stop_target(
            entry, atr_val,
            cfg["sizing"]["atr_stop_mult"], cfg["sizing"]["atr_target_mult"],
        )
        qty = position_size(
            cfg["capital"], cfg["risk_per_trade"], entry, stop,
            cfg["sizing"]["max_position_pct"],
        )
        ranked.append({
            "ticker": ticker,
            "score": int(score["total"]),
            "close": round(entry, 2),
            "entry_low": round(entry * 0.995, 2),
            "entry_high": round(entry * 1.002, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "rr": round((target - entry) / max(entry - stop, 1e-9), 2),
            "atr14": round(atr_val, 2),
            "qty": qty,
            "capital_at_risk": round(qty * (entry - stop), 0),
            "rsi14": round(float(row["rsi14"]), 1) if pd.notna(row["rsi14"]) else None,
            "bb_rank": round(float(row["bb_width_rank_60"]), 2) if pd.notna(row["bb_width_rank_60"]) else None,
            "rationale": _rationale(score),
            "penalty": int(score.get("penalty", 0)),
        })

    # Build shortlist: top 10 by score. Mark those above threshold as actionable.
    ranked.sort(key=lambda x: -x["score"])
    min_score = cfg["thresholds"]["min_score"]
    for r in ranked:
        r["signal"] = "BUY" if r["score"] >= min_score else "WATCH"
    shortlist = ranked[:10]

    # Always show the top 10 by score — even if none clear the threshold.
    # Helps the user see what the system is seeing and what's close.
    all_ranked = sorted(ranked, key=lambda x: -x["score"])
    if all_ranked:
        log.info("--- Top 10 by score (threshold = %s) ---", cfg["thresholds"]["min_score"])
        for r in all_ranked[:10]:
            penalty_note = f"  penalty={r.get('penalty', 0)}" if r.get("penalty", 0) else ""
            log.info("  %-18s score=%3d  close=%8.2f  RSI=%-5s  BB-rank=%-5s%s  [%s]",
                     r["ticker"], r["score"], r["close"],
                     r.get("rsi14", "?"), r.get("bb_rank", "?"),
                     penalty_note, r["rationale"])

    csv_path, html_path = build_report(
        shortlist, regime_ok, cfg["paths"]["reports_dir"],
    )
    log.info("Report: %s", csv_path)
    log.info("HTML:   %s", html_path)

    if not regime_ok:
        log.warning("REGIME BEARISH — do NOT take new longs today.")
    if not shortlist:
        log.info("No candidates cleared the %s-point threshold.",
                 cfg["thresholds"]["min_score"])
    else:
        log.info("Top candidates:")
        for r in shortlist:
            log.info("  %-14s  score=%3d  close=%-9s  stop=%-9s  target=%-9s  qty=%4d  [%s]",
                     r["ticker"], r["score"], r["close"], r["stop"], r["target"], r["qty"],
                     r["rationale"])


def _rationale(score) -> str:
    parts = []
    if score.get("trend", 0) >= 15: parts.append("uptrend")
    if score.get("trend", 0) >= 25: parts.append("rising 50MA")
    if score.get("pullback", 0) >= 10: parts.append("in pullback")
    if score.get("pullback", 0) >= 20: parts.append("RSI rising")
    if score.get("momentum", 0) >= 10: parts.append("MACD flip")
    if score.get("squeeze", 0) >= 15: parts.append("BB squeeze")
    if score.get("volume", 0) >= 10: parts.append("vol surge")
    if score.get("relative_strength", 0) >= 10: parts.append("RS > index")
    return " + ".join(parts) if parts else "—"


if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    main(cfg_path)
