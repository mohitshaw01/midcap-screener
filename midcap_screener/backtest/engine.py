"""
Event-driven backtest engine.

Each trading day:
  1. Check exits on every open position (stop / target / time-stop / trailing).
  2. If the regime is bearish (index below its 50-DMA) skip new entries.
  3. Score every stock with >= 200 bars of history and >= ₹25 cr liquidity.
  4. Rank by score, take top (max_positions - open) slots above `min_score`.

Assumptions / simplifications:
  - Entries execute at the close of the signal day (same-bar close fill).
    Conservative alternative: next-day open. Swap in `_next_open_price` if needed.
  - Stops/targets check against the bar's high/low. If both hit on the same bar,
    we pessimistically assume the stop fires first (standard backtest convention).
  - Costs are modelled as a flat `cost_per_side` fraction of notional (default 0.2%).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional

import pandas as pd

from midcap_screener.indicators.compute import sma
from midcap_screener.risk.sizing import (
    compute_stop_target,
    position_size,
    trailing_stop,
)
from midcap_screener.screener.score import compute_score

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    ticker: str
    entry_date: pd.Timestamp
    entry: float
    stop: float
    target: float
    qty: int
    atr_at_entry: float
    exit_date: Optional[pd.Timestamp] = None
    exit: Optional[float] = None
    reason: Optional[str] = None  # stop | target | time_stop | trailing | eod

    @property
    def pnl(self) -> float:
        if self.exit is None:
            return 0.0
        return (self.exit - self.entry) * self.qty

    @property
    def return_pct(self) -> float:
        if self.exit is None:
            return 0.0
        return (self.exit / self.entry) - 1.0


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    total_costs: float = 0.0
    final_equity: float = 0.0
    initial_capital: float = 0.0


def backtest(
    stock_data: Dict[str, pd.DataFrame],  # each frame MUST have indicators already added
    index_df: pd.DataFrame,
    start: str,
    end: str,
    capital: float = 500_000.0,
    risk_pct: float = 0.02,
    max_positions: int = 5,
    min_score: float = 70,
    min_liquidity_cr: float = 25.0,
    time_stop_bars: int = 20,
    cost_per_side: float = 0.002,
    verbose: bool = False,
) -> BacktestResult:
    # Build the union of all trading dates in the requested window.
    all_dates = sorted(set().union(*(df.index for df in stock_data.values())))
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    all_dates = [d for d in all_dates if start_ts <= d <= end_ts]
    if not all_dates:
        return BacktestResult(initial_capital=capital)

    index_ma50 = sma(index_df["close"], 50)
    equity = capital
    open_trades: List[Trade] = []
    closed_trades: List[Trade] = []
    equity_points: List[tuple] = []
    total_costs = 0.0

    for today in all_dates:
        # -- EXITS first -------------------------------------------------
        still_open: List[Trade] = []
        for t in open_trades:
            df = stock_data[t.ticker]
            if today not in df.index:
                still_open.append(t)
                continue
            bar = df.loc[today]
            exited = False

            # Stop assumed to fire before target on same-bar collision (conservative)
            if bar["low"] <= t.stop:
                t.exit, t.exit_date, t.reason = t.stop, today, "stop"
                exited = True
            elif bar["high"] >= t.target:
                t.exit, t.exit_date, t.reason = t.target, today, "target"
                exited = True
            else:
                # Trailing stop check
                ts = trailing_stop(t.entry, bar["close"], t.atr_at_entry)
                if ts is not None and ts > t.stop:
                    t.stop = ts  # tighten
                bars_held = len(df.loc[t.entry_date:today])
                if bars_held >= time_stop_bars:
                    t.exit, t.exit_date, t.reason = bar["close"], today, "time_stop"
                    exited = True

            if exited:
                cost = (t.entry + (t.exit or 0)) * t.qty * cost_per_side
                total_costs += cost
                equity += t.pnl - cost
                closed_trades.append(t)
            else:
                still_open.append(t)
        open_trades = still_open

        # -- Regime gate -------------------------------------------------
        if today not in index_ma50.index or pd.isna(index_ma50.loc[today]):
            equity_points.append((today, equity + _mtm(open_trades, stock_data, today)))
            continue
        if index_df["close"].loc[today] < index_ma50.loc[today]:
            equity_points.append((today, equity + _mtm(open_trades, stock_data, today)))
            continue

        # -- ENTRIES -----------------------------------------------------
        slots = max_positions - len(open_trades)
        if slots > 0:
            open_tickers = {t.ticker for t in open_trades}
            scored: List[tuple] = []
            for ticker, df in stock_data.items():
                if ticker in open_tickers or today not in df.index:
                    continue
                pos = df.index.get_loc(today)
                if isinstance(pos, slice) or pos < 200:
                    continue
                window = df.iloc[: pos + 1]
                tv = window["traded_value_sma20"].iloc[-1]
                if pd.isna(tv) or tv < min_liquidity_cr * 1e7:
                    continue
                index_window = index_df.loc[:today]
                s = compute_score(window, index_window)
                if s["total"] >= min_score:
                    scored.append((ticker, s["total"], window))

            scored.sort(key=lambda x: -x[1])
            for ticker, score, window in scored[:slots]:
                row = window.iloc[-1]
                atr_val = row["atr14"]
                if pd.isna(atr_val) or atr_val <= 0:
                    continue
                entry = row["close"]
                stop, target = compute_stop_target(entry, atr_val)
                qty = position_size(equity, risk_pct, entry, stop)
                if qty <= 0:
                    continue
                open_trades.append(
                    Trade(
                        ticker=ticker,
                        entry_date=today,
                        entry=entry,
                        stop=stop,
                        target=target,
                        qty=qty,
                        atr_at_entry=atr_val,
                    )
                )
                if verbose:
                    logger.info(
                        "ENTRY %s @ %.2f  stop %.2f  target %.2f  qty %d  score %d",
                        ticker, entry, stop, target, qty, int(score),
                    )

        equity_points.append((today, equity + _mtm(open_trades, stock_data, today)))

    # -- Close any leftovers at last price ---------------------------------
    for t in open_trades:
        last = stock_data[t.ticker]
        if len(last) > 0:
            t.exit = float(last["close"].iloc[-1])
            t.exit_date = last.index[-1]
            t.reason = "eod"
            cost = (t.entry + t.exit) * t.qty * cost_per_side
            total_costs += cost
            equity += t.pnl - cost
            closed_trades.append(t)

    ec = pd.Series({d: v for d, v in equity_points})
    return BacktestResult(
        trades=closed_trades,
        equity_curve=ec,
        total_costs=total_costs,
        final_equity=equity,
        initial_capital=capital,
    )


def _mtm(open_trades: List[Trade], data: Dict[str, pd.DataFrame], today: pd.Timestamp) -> float:
    """Unrealised mark-to-market on open trades."""
    total = 0.0
    for t in open_trades:
        df = data[t.ticker]
        if today in df.index:
            total += (df.loc[today, "close"] - t.entry) * t.qty
    return total


# --------------------------------------------------------------------- walk-forward

def walk_forward(
    stock_data: Dict[str, pd.DataFrame],
    index_df: pd.DataFrame,
    train_years: int = 2,
    test_months: int = 6,
    **bt_kwargs,
) -> List[BacktestResult]:
    """
    Rolling walk-forward: run the backtest on rolling OOS windows.
    This project's parameters are fixed (not fit on the data), so the training
    fold is mainly for calibration/inspection — the reported metrics should come
    from the concatenated OOS folds.
    """
    all_dates = sorted(set().union(*(df.index for df in stock_data.values())))
    if not all_dates:
        return []
    start = all_dates[0] + pd.DateOffset(years=train_years)
    end_cap = all_dates[-1]
    folds: List[BacktestResult] = []
    cursor = start
    while cursor < end_cap:
        fold_end = min(cursor + pd.DateOffset(months=test_months), end_cap)
        res = backtest(
            stock_data=stock_data,
            index_df=index_df,
            start=str(cursor.date()),
            end=str(fold_end.date()),
            **bt_kwargs,
        )
        folds.append(res)
        cursor = fold_end + timedelta(days=1)
    return folds
