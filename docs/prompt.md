# Nifty Midcap 150 Swing-Trade Screener — Refined Prompt & Implementation Report

This is the specification document that drove the `midcap_screener` build.
Preserved here for reference — if you ever want to re-derive a scoring
rubric, a backtest gate, or a risk parameter, start here.

---

## Part 1 — The Refined Prompt

> **Role:** Act as a quantitative developer and swing-trading systems designer
> with experience in Indian equity markets.
>
> **Objective:** Help me design and build an end-to-end Python-based screener
> that filters stocks from the **Nifty Midcap 150** universe (strictly no
> small-caps, no large-caps) to shortlist **5–10 candidates per week** with the
> highest probability of delivering **+5% to +15% returns within a 10–20
> trading-day holding window**.
>
> **Strategy constraints:**
> 1. **Universe:** Only the current Nifty Midcap 150 constituents. Pull the
>    live list (do not hard-code) and refresh monthly.
> 2. **Liquidity floor:** 20-day average traded value ≥ ₹25 crore.
> 3. **Primary edge — momentum + pullback:** Favour stocks in confirmed
>    uptrends (price above 50-DMA and 200-DMA, 50-DMA sloping up) that are
>    currently in a shallow 2–5 day pullback (RSI-14 between 40–60).
> 4. **Confirmation stack:** Require alignment across four independent signals
>    — trend (MAs), momentum (RSI + MACD histogram turning up), volatility
>    contraction (Bollinger Band width near 20-day low) and volume (breakout-day
>    volume ≥ 1.5× 20-day average).
> 5. **Volatility-aware sizing:** Use ATR(14) to set stop-loss (1.5× ATR below
>    entry) and target (3× ATR above entry) so the ~2:1 reward-to-risk maps to
>    the +5–15% / 10–20 day envelope.
> 6. **Event filter:** Exclude stocks with earnings in the next 10 trading days
>    and stocks in F&O ban or with recent corporate actions.
> 7. **Regime filter:** Only take long signals when Nifty Midcap 150 itself is
>    above its 50-DMA.

---

## Part 2 — Implementation Notes

### Why this universe and horizon

The Nifty Midcap 150 covers companies ranked 101–250 by market cap from the
Nifty 500, rebalanced semi-annually. Small-caps are excluded by construction;
large-caps typically don't move 5–15% in two weeks without news, so midcaps
are the sweet spot for this return/time profile.

A 10–20 trading-day holding window with a 5–15% target translates to
roughly 0.25%–0.75% expected daily drift — attainable in trending midcaps.

### Strategy: "Momentum + Pullback + Volatility Squeeze" (MPVS)

Combines three effects that are each individually known to work:
1. **Momentum / trend-following** — stocks above rising 50-DMA and 200-DMA
   continue outperforming on average.
2. **Short-term mean reversion** — after a 2–5 day pullback in an uptrend,
   forward 10–20 day returns are historically higher than random entries.
3. **Volatility squeeze breakouts** — when Bollinger Band width compresses to
   a multi-week low, the next directional move is statistically larger than
   average.

### The four-layer architecture

**Data → Features → Scoring → Ranking/Output** — see README.md for the
implemented file layout.

### Scoring rubric (0-100)

| Component | Max | Condition |
|-----------|-----|-----------|
| Trend | 25 | +15 if close > SMA50 > SMA200; +10 if SMA50 slope positive |
| Pullback | 20 | +20 if RSI ∈ [40,55] AND rising 2 sessions |
| Momentum | 20 | +10 hist flip + 10 cross-up in last 5 |
| Squeeze | 15 | bottom 20% of last 60 days |
| Volume | 10 | today ≥ 1.5× 20d avg |
| Relative strength | 10 | 3-mo return beats index by > 5% |

Shortlist threshold: **≥ 70**.

### Backtest gates

Before going live, the backtest must show:
- Win rate ≥ 45%
- Average win / average loss ≥ 1.8
- Expectancy > 0 after costs
- Max drawdown ≤ 15%
- Sharpe ≥ 1.0
- ≥ 80 trades in the backtest

### Honest limitations

- This is a framework that raises the *probability*; it's not a guarantee.
  Expect 45–55% win rate in good regimes.
- Realistic expectancy after costs: 1.5–3% per trade, ~15–30% annualised
  with 10–15% drawdowns. Anyone promising more is selling something.
- Underperforms in: sideways/choppy markets, sharp sector rotations, first
  2–3 days of macro shocks.
- yfinance data has occasional stale/wrong bars — budget for a paid source
  once the prototype proves itself.
- Plan for first 20–30 live trades to underperform backtest by 20–30% as
  you calibrate execution. If the gap doesn't close by trade 30, it was
  overfit — go back to the scoring layer.
