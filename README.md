# Nifty Midcap 150 — MPVS Swing Screener

A Python screener that filters the Nifty Midcap 150 universe to shortlist 5–10
swing-trade candidates per week targeting **+5% to +15% returns in a 10–20
trading-day holding window**.

**Strategy:** Momentum + Pullback + Volatility Squeeze (MPVS). See
[`docs/prompt.md`](docs/prompt.md) (the spec that drove this build) for the
full rationale.

---

## Project layout

```
midcap_screener/
├── config.yaml                 # all tunables — capital, risk %, thresholds
├── run_daily.py                # screener entry point (run after 4:15 PM IST)
├── run_backtest.py             # backtest entry point
├── requirements.txt
├── midcap_screener/
│   ├── data/fetcher.py         # niftystocks universe + yfinance OHLCV
│   ├── data/cache.py           # parquet cache
│   ├── indicators/compute.py   # SMA, RSI, MACD, BB, ATR (pure pandas)
│   ├── screener/score.py       # 0–100 composite rubric
│   ├── screener/filters.py     # liquidity, regime, earnings-blackout stub
│   ├── risk/sizing.py          # ATR stops, position sizing, trailing
│   ├── backtest/engine.py      # event-driven + walk-forward
│   ├── backtest/metrics.py     # CAGR, DD, Sharpe, win rate, expectancy
│   └── reports/daily.py        # CSV + HTML output
└── tests/smoke_test.py         # full unit + integration tests
```

---

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Daily screener (after NSE close, ~4:15 PM IST)

```bash
python run_daily.py
```

Outputs `reports_out/screen_YYYY-MM-DD.csv` and `.html`. Console prints the
regime flag and the top 10 ranked candidates. The top of the HTML has a
**red BEARISH banner** when the Nifty Midcap 150 is below its 50-DMA — when
red, you take no new longs.

## Backtest

```bash
python run_backtest.py                       # single window from config.yaml
python run_backtest.py --walk-forward        # rolling 2y train / 6m test
python run_backtest.py --period 5y           # override yfinance history depth
```

Outputs `reports_out/trades.csv` and `reports_out/equity_curve.csv`, plus a
metrics table to stdout with **gate checks** — the strategy must pass all of
these before going live:

| Gate | Threshold |
|------|-----------|
| Win rate | ≥ 45% |
| Avg win / Avg loss | ≥ 1.8 |
| Expectancy | > 0 after costs |
| Max drawdown | ≤ 15% |
| Sharpe | ≥ 1.0 |
| Trade count | ≥ 80 |

---

## The scoring rubric (100 points)

| Component | Max | Condition |
|-----------|-----|-----------|
| Trend | 25 | +15 if close > SMA50 > SMA200; +10 if SMA50 slope positive over 10d |
| Pullback quality | 20 | +20 if RSI(14) ∈ [40,55] **and** rising 2 sessions; +10 if in band only |
| Momentum | 20 | +10 if MACD hist flipped positive in last 3 bars; +10 if MACD > signal in last 5 |
| Volatility squeeze | 15 | +15 if BB-width ranks in bottom 20% of last 60 bars |
| Volume | 10 | +10 if today's volume ≥ 1.5× 20-day avg |
| Relative strength | 10 | +10 if 3-month return beats Nifty Midcap 150 by > 5% |

**Shortlist threshold: ≥ 70 points.**

---

## Risk framework

- Per-trade risk capped at **2% of capital**
- Max **5 concurrent positions**; no single name > 20% of capital
- **Regime gate:** no new longs when Nifty Midcap 150 < its 50-DMA
- **Exits (in priority order):** stop-loss (1.5×ATR), target (3×ATR), time-stop
  at 20 bars, trailing stop (1×ATR) once unrealised gain ≥ 4%
- **Portfolio stop:** pause strategy if the book drops 6% in a month

---

## Testing

```bash
python -m tests.smoke_test
```

Runs 11 tests with synthetic data (no network required):
- Indicator math correctness (SMA, RSI edge cases, ATR non-negativity)
- Per-component score firing (each of the 6 scoring components individually)
- Score ordering (STRONG > WEAK, STRONG > BAD, BAD can't clear 70 threshold)
- Hard filters (liquidity, regime)
- Risk maths (stop/target, cap-limited vs risk-limited sizing, trailing activation)
- End-to-end: backtest run → metrics → CSV+HTML report generation

Expected output ends with `ALL SMOKE TESTS PASSED`.

---

## Daily runbook (real-money use)

1. Run `python run_daily.py` after NSE close.
2. **Check the regime flag.** If red (bearish), stop here.
3. For each ranked name, cross-check manually:
   - Upcoming earnings within 10 trading days (screener.in / moneycontrol) — the
     in-code earnings filter is a stub; you must check this manually.
   - F&O ban list (NSE publishes daily).
   - Any corporate actions (splits, dividends, bonus).
4. Calculate exact position size using the printed stop distance.
5. Place limit orders for next morning, 0.2–0.5% below previous close, GTC.
6. Set stop-loss and target the moment entry fills.
7. **Log every trade** — entry, stop, target, rationale, outcome. Without
   the log you can't improve the system.

---

## Honest limitations

- **This raises probability, it doesn't guarantee returns.** Expect 45–55%
  win rate in good regimes, lower in choppy markets.
- **Realistic expectancy after costs** is 1.5–3% per trade — ~15–30% annualised
  across 40–60 trades/year, with 10–15% drawdowns along the way.
- **Underperforms in** sideways/choppy markets, sharp sector rotations, and
  first 2–3 days of sudden macro shocks.
- **yfinance data** occasionally has stale bars or wrong adjustments for
  Indian stocks. Budget for a paid source (Kite Connect / Upstox / Dhan)
  once the prototype proves itself.
- **Earnings blackout is a stub.** Free earnings calendars for NSE are
  unreliable — cross-check manually per the runbook.
- **Your edge is discipline + small size**, not better signals. Don't skip
  the backtest gates, don't override stops, don't scale after a hot streak.
- Plan for the **first 20–30 live trades to underperform backtest by 20–30%**
  as you calibrate execution. If the gap doesn't close by trade 30, your
  backtest is overfit — go back to the scoring layer.

---

## Suggested build sequence

Already implemented by this project. To productionize:

1. **Week 1:** data + indicators + daily screener — paper trade for 2 weeks.
2. **Week 2:** full walk-forward backtest, verify all six gates pass.
3. **Week 3:** live with 25% of intended capital for 2 months. Scale only if
   live metrics track the backtest.
