"""
Enhanced daily screener v2.

Improvements over v1:
  1. Fundamental quality gate (excludes junk before scoring)
  2. Sector momentum rotation (hot sector bonus)
  3. Multi-timeframe RSI confirmation
  4. Institutional accumulation detection
  5. Tiered signals: A-grade / B-grade / Watchlist
  6. Comprehensive output with full score breakdown

Run:
    python run_daily_v2.py
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
from midcap_screener.screener.fundamentals import (
    fetch_fundamentals_batch, quality_gate,
)
from midcap_screener.screener.sector_rotation import assign_sectors, rank_sectors
from midcap_screener.screener.score_v2 import compute_enhanced_score, format_score_breakdown

log = logging.getLogger("run_daily_v2")


def main(config_path: str = "config.yaml") -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    cfg = yaml.safe_load(Path(config_path).read_text())
    cache = ParquetCache(cfg["paths"]["cache_dir"])

    # ---- Step 1: Universe & data ----------------------------------------
    log.info("=" * 70)
    log.info("MIDCAP 150 ENHANCED SCREENER v2")
    log.info("=" * 70)

    log.info("[1/6] Fetching universe...")
    tickers = get_universe()
    log.info("Universe: %d tickers", len(tickers))

    log.info("[2/6] Fetching OHLCV (1y)...")
    data = fetch_ohlcv(tickers, period="1y")
    log.info("Downloaded: %d / %d tickers", len(data), len(tickers))
    cache.put_many(data)

    log.info("[3/6] Fetching index...")
    index_df = fetch_index(period="1y")
    regime_ok = regime_bullish(index_df)
    log.info("Regime bullish = %s", regime_ok)

    # ---- Step 2: Fundamentals -------------------------------------------
    log.info("[4/6] Fetching fundamentals (this takes 2-3 minutes)...")
    fundamentals = fetch_fundamentals_batch(list(data.keys()))
    log.info("Fundamentals loaded: %d tickers", len(fundamentals))

    # ---- Step 3: Sector rotation ----------------------------------------
    log.info("[5/6] Computing sector rotation...")
    sector_map = assign_sectors(list(data.keys()), fundamentals)
    # Need indicator-enriched data for sector ranking
    enriched_data = {}
    for t, raw in data.items():
        enriched_data[t] = add_indicators(raw)
    sector_ranking = rank_sectors(sector_map, data)  # uses raw close data
    log.info("Sector ranking:")
    for i, (sector, ret) in enumerate(sector_ranking[:10]):
        emoji = "🔥" if i < 3 else "  "
        log.info("  %s #%d  %-25s  %+.1f%%", emoji, i + 1, sector, ret * 100)

    # ---- Step 4: Score everything ---------------------------------------
    log.info("[6/6] Scoring all stocks...")
    ranked = []
    excluded = {"low_liquidity": 0, "quality_gate": 0, "no_history": 0}

    for ticker, df in enriched_data.items():
        # Hard filter: liquidity
        if not liquidity_ok(df, cfg["thresholds"]["min_liquidity_cr"]):
            excluded["low_liquidity"] += 1
            continue

        # Hard filter: history
        if len(df) < 200:
            excluded["no_history"] += 1
            continue

        # Hard filter: fundamental quality gate
        fund_info = fundamentals.get(ticker)
        passes, reason = quality_gate(fund_info)
        if not passes:
            excluded["quality_gate"] += 1
            log.debug("Excluded %s: %s", ticker, reason)
            continue

        # Compute enhanced score
        score = compute_enhanced_score(
            df=df,
            index_df=index_df,
            fundamentals=fund_info,
            sector_map=sector_map,
            sector_ranking=sector_ranking,
            ticker=ticker,
        )

        if score["score"] == 0:
            continue

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
            "tier": score["tier"],
            "score": score["score"],
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
            "weekly_rsi": score.get("weekly_rsi"),
            "bb_rank": round(float(row["bb_width_rank_60"]), 2) if pd.notna(row["bb_width_rank_60"]) else None,
            "sector": score.get("sector", ""),
            "sector_rank": score.get("sector_rank", -1),
            # Score breakdown
            "trend": score.get("trend", 0),
            "pullback": score.get("pullback", 0),
            "momentum": score.get("momentum", 0),
            "squeeze": score.get("squeeze", 0),
            "vol": score.get("volume", 0),
            "quality": score.get("quality_total", 0),
            "sect_bonus": score.get("sector_bonus", 0),
            "mtf_bonus": score.get("multi_tf_bonus", 0),
            "inst_bonus": score.get("institutional_total", 0),
            "penalty": score.get("total_penalty", 0),
            "rationale": format_score_breakdown(score),
        })

    ranked.sort(key=lambda x: -x["score"])

    # ---- Output ---------------------------------------------------------
    log.info("")
    log.info("Excluded: %s", excluded)
    log.info("")

    if not regime_ok:
        log.warning("⚠️  REGIME BEARISH — Midcap 150 below 50-DMA. NO NEW LONGS.")
        log.warning("")

    # Print by tier
    a_grade = [r for r in ranked if r["tier"] == "A"]
    b_grade = [r for r in ranked if r["tier"] == "B"]
    watch = [r for r in ranked if r["tier"] == "WATCH"]

    def _print_tier(label: str, stocks: list, color: str = ""):
        if not stocks:
            log.info("  %s: (none today)", label)
            return
        log.info("  %s (%d stocks):", label, len(stocks))
        log.info("  %-16s Scr  Close     SL        Target    R:R   RSI   wRSI  Sect%-14s [Rationale]", "", "")
        log.info("  " + "-" * 115)
        for r in stocks[:10]:
            log.info(
                "  %-16s %3d  %8.2f  %8.2f  %8.2f  %4.1f  %5.1f  %-5s %-18s [%s]",
                r["ticker"], r["score"], r["close"], r["stop"], r["target"],
                r["rr"],
                r.get("rsi14") or 0,
                str(r.get("weekly_rsi") or "?"),
                r.get("sector", "")[:18],
                r["rationale"][:50],
            )
        log.info("")

    log.info("=" * 70)
    log.info("SIGNAL TIERS")
    log.info("=" * 70)
    _print_tier("🟢 A-GRADE (full position, high conviction)", a_grade)
    _print_tier("🟡 B-GRADE (half position, developing)", b_grade)
    _print_tier("👀 WATCHLIST (set alerts, not actionable yet)", watch[:10])

    # Build report (include A + B grade, plus top 5 watch)
    report_stocks = a_grade + b_grade + watch[:5]
    csv_path, html_path = build_report(
        report_stocks, regime_ok, cfg["paths"]["reports_dir"],
    )
    log.info("Report: %s", csv_path)
    log.info("HTML:   %s", html_path)
    log.info("")

    # Summary stats
    log.info("--- Summary ---")
    log.info("A-grade: %d  |  B-grade: %d  |  Watchlist: %d  |  Total scored: %d",
             len(a_grade), len(b_grade), len(watch), len(ranked))
    if a_grade:
        log.info("")
        log.info("🎯 TOP PICK: %s @ ₹%.2f  |  SL: ₹%.2f  |  Target: ₹%.2f  |  Score: %d",
                 a_grade[0]["ticker"], a_grade[0]["close"],
                 a_grade[0]["stop"], a_grade[0]["target"], a_grade[0]["score"])


if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    main(cfg_path)
