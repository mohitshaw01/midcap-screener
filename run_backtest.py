"""
Backtest entry point.

    python run_backtest.py                # uses config.yaml backtest.start / .end
    python run_backtest.py --walk-forward

Prints metrics and writes equity_curve.csv + trades.csv to reports_out/.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

from midcap_screener.backtest.engine import backtest, walk_forward
from midcap_screener.backtest.metrics import compute_metrics, format_metrics
from midcap_screener.data.fetcher import fetch_index, fetch_ohlcv, get_universe
from midcap_screener.indicators.compute import add_indicators

log = logging.getLogger("backtest")


def _prepare_data(period: str = "5y"):
    log.info("Fetching universe...")
    tickers = get_universe()
    log.info("Fetching OHLCV (%s)...", period)
    raw = fetch_ohlcv(tickers, period=period)
    log.info("Enriching with indicators...")
    data = {t: add_indicators(df) for t, df in raw.items() if len(df) >= 220}
    log.info("%d tickers with sufficient history", len(data))
    index_df = fetch_index(period=period)
    return data, index_df


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--walk-forward", action="store_true")
    p.add_argument("--period", default="5y", help="yfinance period, e.g. 5y / 3y / max")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    bt_cfg = cfg["backtest"]
    data, index_df = _prepare_data(period=args.period)

    bt_kwargs = dict(
        capital=cfg["capital"],
        risk_pct=cfg["risk_per_trade"],
        max_positions=cfg["max_positions"],
        min_score=cfg["thresholds"]["min_score"],
        min_liquidity_cr=cfg["thresholds"]["min_liquidity_cr"],
        time_stop_bars=cfg["exits"]["time_stop_bars"],
        cost_per_side=bt_cfg["cost_per_side"],
    )

    out_dir = Path(cfg["paths"]["reports_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.walk_forward:
        log.info("Running walk-forward backtest...")
        folds = walk_forward(
            data, index_df,
            train_years=bt_cfg["walk_forward_train_years"],
            test_months=bt_cfg["walk_forward_test_months"],
            **bt_kwargs,
        )
        all_trades = [t for f in folds for t in f.trades]
        # stitch OOS equity curves
        equity_curves = [f.equity_curve for f in folds if len(f.equity_curve)]
        if equity_curves:
            stitched = pd.concat(equity_curves).sort_index()
            stitched = stitched[~stitched.index.duplicated(keep="last")]
        else:
            stitched = pd.Series(dtype=float)
        metrics = compute_metrics(all_trades, stitched, cfg["capital"])
        print("\n=== WALK-FORWARD (concatenated OOS) ===")
    else:
        log.info("Running single backtest %s -> %s", bt_cfg["start"], bt_cfg["end"])
        res = backtest(
            data, index_df,
            start=bt_cfg["start"], end=bt_cfg["end"],
            **bt_kwargs,
        )
        metrics = compute_metrics(res.trades, res.equity_curve, cfg["capital"])
        pd.DataFrame([t.__dict__ for t in res.trades]).to_csv(out_dir / "trades.csv", index=False)
        res.equity_curve.to_csv(out_dir / "equity_curve.csv", header=["equity"])
        print("\n=== BACKTEST ===")

    print(format_metrics(metrics))


if __name__ == "__main__":
    main()
