"""
Scheduled screener runner.

Runs the full v2 pipeline and stores results into the webapp's SQLite DB.
Can be invoked by:
  1. APScheduler (auto at 16:30 IST on weekdays)
  2. Manual:  python -m webapp.scheduler
  3. From Flask:  POST /api/run-screener (not exposed publicly)
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import yaml

log = logging.getLogger("scheduler")

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_screener(config_path: str = "config.yaml", db_path: str = "screener.db") -> dict:
    """
    Execute the full v2 screener pipeline and store results in the DB.
    Returns a summary dict.
    """
    import os
    os.environ["SCREENER_DB"] = db_path

    from midcap_screener.data.fetcher import fetch_index, fetch_ohlcv, get_universe
    from midcap_screener.indicators.compute import add_indicators, sma
    from midcap_screener.screener.filters import liquidity_ok, regime_bullish
    from midcap_screener.screener.fundamentals import fetch_fundamentals_batch, quality_gate
    from midcap_screener.screener.sector_rotation import assign_sectors, rank_sectors
    from midcap_screener.screener.score_v2 import compute_enhanced_score, format_score_breakdown
    from midcap_screener.risk.sizing import compute_stop_target, position_size
    from webapp.app import store_signals, store_regime, store_sector_rankings

    import pandas as pd

    cfg = yaml.safe_load(Path(ROOT / config_path).read_text())
    today = date.today().isoformat()

    log.info("Starting screener run for %s", today)

    # 1. Data
    tickers = get_universe()
    data = fetch_ohlcv(tickers, period="1y")
    index_df = fetch_index(period="1y")
    regime_ok = regime_bullish(index_df)

    # Store regime
    idx_close = float(index_df["close"].iloc[-1])
    idx_sma50 = float(sma(index_df["close"], 50).iloc[-1])
    store_regime(today, regime_ok, idx_close, idx_sma50)
    log.info("Regime: bullish=%s, index=%.2f, sma50=%.2f", regime_ok, idx_close, idx_sma50)

    # 2. Fundamentals
    fundamentals = fetch_fundamentals_batch(list(data.keys()))

    # 3. Sectors
    enriched = {t: add_indicators(df) for t, df in data.items()}
    sector_map = assign_sectors(list(data.keys()), fundamentals)
    sector_ranking = rank_sectors(sector_map, data)
    store_sector_rankings(today, sector_ranking)
    log.info("Sectors: %s", [(s, f"{r:.1%}") for s, r in sector_ranking[:5]])

    # 4. Score
    signals = []
    for ticker, df in enriched.items():
        if not liquidity_ok(df, cfg["thresholds"]["min_liquidity_cr"]):
            continue
        if len(df) < 200:
            continue
        fund = fundamentals.get(ticker)
        passes, _ = quality_gate(fund)
        if not passes:
            continue

        score = compute_enhanced_score(
            df=df, index_df=index_df, fundamentals=fund,
            sector_map=sector_map, sector_ranking=sector_ranking, ticker=ticker,
        )
        if score["score"] == 0:
            continue

        row = df.iloc[-1]
        entry = float(row["close"])
        atr_val = float(row["atr14"])
        if atr_val <= 0:
            continue

        stop, target = compute_stop_target(
            entry, atr_val,
            cfg["sizing"]["atr_stop_mult"], cfg["sizing"]["atr_target_mult"],
        )
        qty = position_size(
            cfg["capital"], cfg["risk_per_trade"], entry, stop,
            cfg["sizing"]["max_position_pct"],
        )

        signals.append({
            "ticker": ticker,
            "tier": score["tier"],
            "score": score["score"],
            "close": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "rr": round((target - entry) / max(entry - stop, 1e-9), 2),
            "rsi14": round(float(row["rsi14"]), 1) if pd.notna(row["rsi14"]) else None,
            "weekly_rsi": score.get("weekly_rsi"),
            "bb_rank": round(float(row["bb_width_rank_60"]), 2) if pd.notna(row["bb_width_rank_60"]) else None,
            "sector": score.get("sector", ""),
            "sector_rank": score.get("sector_rank", -1),
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
            "atr14": round(atr_val, 2),
            "qty": qty,
            "capital_at_risk": round(qty * (entry - stop), 0),
            "entry_low": round(entry * 0.995, 2),
            "entry_high": round(entry * 1.002, 2),
        })

    signals.sort(key=lambda x: -x["score"])
    store_signals(today, signals)

    a = sum(1 for s in signals if s["tier"] == "A")
    b = sum(1 for s in signals if s["tier"] == "B")
    w = sum(1 for s in signals if s["tier"] == "WATCH")

    summary = {
        "date": today,
        "regime_bullish": regime_ok,
        "total_signals": len(signals),
        "a_grade": a,
        "b_grade": b,
        "watchlist": w,
        "top_pick": signals[0] if signals else None,
    }
    log.info("Done: A=%d B=%d Watch=%d Total=%d", a, b, w, len(signals))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    result = run_screener()
    print(f"\nResult: {result}")
