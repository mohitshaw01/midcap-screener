"""
Performance metrics. All from the equity curve + trade list produced by engine.py.

Gates defined in the strategy doc — a backtest must meet ALL of these before
going live:
    win_rate >= 0.45
    avg_win / avg_loss >= 1.8
    expectancy > 0 after costs
    max_drawdown >= -0.15  (i.e. <= 15% DD)
    sharpe >= 1.0
    n_trades >= 80
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    n_trades: int
    cagr: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    avg_win: float
    avg_loss: float
    avg_win_over_avg_loss: float
    expectancy: float
    total_return: float
    passes_gates: bool
    gate_detail: dict


def _cagr(equity: pd.Series) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    ratio = equity.iloc[-1] / equity.iloc[0]
    if ratio <= 0:
        return -1.0
    return float(ratio ** (1 / years) - 1)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def _sharpe(equity: pd.Series, periods_per_year: int = 252, rf: float = 0.065) -> float:
    """Annualised Sharpe using daily returns; RF ~ 10Y G-Sec proxy."""
    if equity.empty or len(equity) < 2:
        return 0.0
    rets = equity.pct_change().dropna()
    if rets.empty or rets.std() == 0:
        return 0.0
    excess_daily = rets.mean() - rf / periods_per_year
    return float(excess_daily / rets.std() * np.sqrt(periods_per_year))


def compute_metrics(trades: Iterable, equity: pd.Series, initial_capital: float) -> Metrics:
    trades = list(trades)
    n = len(trades)
    if n == 0:
        return Metrics(
            n_trades=0, cagr=0.0, max_drawdown=0.0, sharpe=0.0,
            win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            avg_win_over_avg_loss=0.0, expectancy=0.0, total_return=0.0,
            passes_gates=False,
            gate_detail={"reason": "no trades"},
        )

    rets = np.array([t.return_pct for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    win_rate = len(wins) / n
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    ratio = (avg_win / abs(avg_loss)) if (len(wins) and len(losses) and avg_loss != 0) \
        else (float("inf") if len(wins) and not len(losses) else 0.0)
    expectancy = float(rets.mean())

    total_return = (equity.iloc[-1] / initial_capital - 1) if len(equity) else 0.0
    cagr = _cagr(equity)
    mdd = _max_drawdown(equity)
    sr = _sharpe(equity)

    gates = {
        "win_rate>=0.45": win_rate >= 0.45,
        "avg_win/avg_loss>=1.8": ratio >= 1.8,
        "expectancy>0": expectancy > 0,
        "max_dd>=-0.15": mdd >= -0.15,
        "sharpe>=1.0": sr >= 1.0,
        "n_trades>=80": n >= 80,
    }

    return Metrics(
        n_trades=n,
        cagr=cagr,
        max_drawdown=mdd,
        sharpe=sr,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_win_over_avg_loss=float(ratio),
        expectancy=expectancy,
        total_return=float(total_return),
        passes_gates=all(gates.values()),
        gate_detail=gates,
    )


def format_metrics(m: Metrics) -> str:
    lines = [
        f"Trades         : {m.n_trades}",
        f"Total return   : {m.total_return*100:7.2f}%",
        f"CAGR           : {m.cagr*100:7.2f}%",
        f"Max drawdown   : {m.max_drawdown*100:7.2f}%",
        f"Sharpe         : {m.sharpe:7.2f}",
        f"Win rate       : {m.win_rate*100:7.2f}%",
        f"Avg win        : {m.avg_win*100:7.2f}%",
        f"Avg loss       : {m.avg_loss*100:7.2f}%",
        f"Win/Loss ratio : {m.avg_win_over_avg_loss:7.2f}",
        f"Expectancy     : {m.expectancy*100:7.2f}%",
        "",
        "Gate check:",
    ]
    for k, v in m.gate_detail.items():
        lines.append(f"  [{'PASS' if v else 'FAIL'}] {k}")
    lines.append(f"\nOverall: {'PASS' if m.passes_gates else 'FAIL'}")
    return "\n".join(lines)
