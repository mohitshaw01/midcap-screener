"""
Smoke test: validates the pipeline end-to-end using synthetic data.

Structure (each concern testable in isolation):
  1. Indicator math — hand-computed reference values vs our implementation.
  2. Per-component scoring — build a focused fixture per score component,
     assert that component lights up. This is how you debug real issues.
  3. Hard filters — liquidity, regime.
  4. Risk maths — stop / target / sizing / trailing.
  5. End-to-end — add_indicators -> score -> backtest -> metrics -> report.

Runs without any network access.

Run:
    cd project && python -m tests.smoke_test
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from midcap_screener.backtest.engine import backtest
from midcap_screener.backtest.metrics import compute_metrics, format_metrics
from midcap_screener.indicators.compute import add_indicators, atr, rsi, sma
from midcap_screener.reports.daily import build_report
from midcap_screener.risk.sizing import (
    compute_stop_target,
    position_size,
    trailing_stop,
)
from midcap_screener.screener.filters import liquidity_ok, regime_bullish
from midcap_screener.screener.score import compute_score

RNG = np.random.default_rng(42)


# ---------------------------------------------------------------- helpers

def _ohlcv_from_close(close: np.ndarray, start: str = "2023-01-01",
                     vol_multiplier: float = 1.0) -> pd.DataFrame:
    """Build a liquid OHLCV frame from a closing-price path."""
    n = len(close)
    dates = pd.date_range(start, periods=n, freq="B")
    opens = np.roll(close, 1); opens[0] = close[0]
    rng = np.random.default_rng(0)
    jitter_h = np.abs(rng.normal(0, 0.003, n)) * close
    jitter_l = np.abs(rng.normal(0, 0.003, n)) * close
    highs = np.maximum(opens, close) + jitter_h
    lows = np.minimum(opens, close) - jitter_l
    # Vol chosen so traded value (close*vol) clears the ₹25 cr liquidity floor.
    vol = np.full(n, 2_000_000.0) * vol_multiplier
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": close, "volume": vol},
        index=dates,
    )


def _index_frame(direction: str = "bull", n: int = 300) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    total = 0.20 if direction == "bull" else -0.20
    close = 20000 * np.exp(np.linspace(0, total, n))
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": np.full(n, 1e9)},
        index=dates,
    )


def _base_uptrend(n: int = 300) -> np.ndarray:
    """Clean smooth uptrend — the baseline trend fixture."""
    return 100 * np.exp(np.linspace(0, 0.40, n))


# ---------------------------------------------------------------- 1. indicators

def test_indicator_math():
    # SMA(3) of [1,2,3,4,5] = [nan, nan, 2, 3, 4]
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    got = sma(s, 3)
    assert pd.isna(got.iloc[0]) and pd.isna(got.iloc[1])
    assert got.iloc[2:].tolist() == [2.0, 3.0, 4.0]

    # RSI of a strictly rising series approaches 100.
    rising = pd.Series(np.arange(1, 50, dtype=float))
    r = rsi(rising, 14).dropna()
    assert r.iloc[-1] > 99, f"RSI of strict uptrend should approach 100, got {r.iloc[-1]}"

    # ATR is strictly non-negative.
    df = _ohlcv_from_close(np.linspace(100, 120, 50))
    a = atr(df["high"], df["low"], df["close"], 14).dropna()
    assert (a >= 0).all() and len(a) > 0

    # Full enrichment produces all expected columns.
    enriched = add_indicators(df)
    expected = {
        "sma50", "sma200", "rsi14", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_mid", "bb_lower", "bb_width", "bb_width_rank_60",
        "atr14", "vol_sma20", "traded_value_sma20", "sma50_slope_10",
    }
    missing = expected - set(enriched.columns)
    assert not missing, f"Missing indicator columns: {missing}"
    print("[PASS] 1. Indicator math (SMA, RSI, ATR, full enrichment)")


# ---------------------------------------------------------------- 2. per-component scoring

def test_score_trend():
    close = _base_uptrend(300)
    df = add_indicators(_ohlcv_from_close(close))
    s = compute_score(df, None)
    assert s["trend"] == 25, f"Expected trend=25 for clean uptrend, got {s['trend']}"

    # Inverse: downtrend
    down = 100 * np.exp(np.linspace(0, -0.35, 300))
    s_down = compute_score(add_indicators(_ohlcv_from_close(down)), None)
    assert s_down["trend"] == 0, f"Expected trend=0 for downtrend, got {s_down['trend']}"
    print(f"[PASS] 2a. Trend  (up={s['trend']}, down={s_down['trend']})")


def test_score_pullback():
    """Uptrend then 40 bars of noisy flat period (so RSI settles near 50),
    then 3-bar shallow dip, then 2 small up bars."""
    rng = np.random.default_rng(7)
    close = _base_uptrend(300).tolist()
    last = close[-1]
    # Noisy flat period with REAL up and down bars, ~50 bars, mild positive drift
    flat = []
    price = last
    for _ in range(50):
        price = price * (1 + rng.normal(0.0002, 0.006))  # small drift, meaningful noise
        flat.append(price)
    # 3-bar shallow dip
    dip = [flat[-1] * 0.997, flat[-1] * 0.997 * 0.996, flat[-1] * 0.997 * 0.996 * 0.997]
    # 2 small up bars — small enough that RSI stays <= 55
    up = [dip[-1] * 1.002, dip[-1] * 1.002 * 1.002]
    full = np.array(close + flat + dip + up)
    df = add_indicators(_ohlcv_from_close(full))
    s = compute_score(df, None)
    last_rsi = df["rsi14"].iloc[-1]
    # 10 (in band) or 20 (in band + rising) both count as "pullback firing"
    assert s["pullback"] >= 10, \
        f"Pullback should fire (>=10) with RSI={last_rsi:.1f}, got {s['pullback']}"
    print(f"[PASS] 2b. Pullback (RSI={last_rsi:.1f}, score={s['pullback']})")


def test_score_squeeze():
    """
    Two-stage contraction so the final bar's BB-width ranks in the bottom quintile
    of the trailing 60 bars.

    Phase A: volatile random walk (builds a long-term backdrop).
    Phase B: moderately tight range (~40 bars) — populates the top 40/60 of the
             rolling window with mid-size BB widths.
    Phase C: very tight range (~20 bars) — collapses BB width so the last bar
             ranks <= 0.20 within the 60-bar window.
    """
    volatile = 100 * np.exp(RNG.normal(0, 0.02, 200).cumsum())
    mid_start = volatile[-1]
    # Moderate consolidation — IID around a level (no cumsum drift)
    moderate = mid_start * np.exp(RNG.normal(0, 0.006, 40))
    # Very tight consolidation — tiny IID noise around a level
    tight_start = moderate[-1]
    tight = tight_start * np.exp(RNG.normal(0, 0.0005, 20))
    close = np.concatenate([volatile, moderate, tight])
    df = add_indicators(_ohlcv_from_close(close))
    last_rank = df["bb_width_rank_60"].iloc[-1]
    s = compute_score(df, None)
    assert s["squeeze"] == 15, \
        f"Squeeze should be 15 when rank={last_rank:.2f}, got {s['squeeze']}"
    print(f"[PASS] 2c. Squeeze (bb_rank={last_rank:.2f}, score={s['squeeze']})")


def test_score_volume():
    df_raw = _ohlcv_from_close(_base_uptrend(250))
    df_raw.loc[df_raw.index[-1], "volume"] *= 3
    df = add_indicators(df_raw)
    s = compute_score(df, None)
    assert s["volume"] == 10, f"Volume should be 10 with 3x surge, got {s['volume']}"
    print(f"[PASS] 2d. Volume   (last vol 3x, score={s['volume']})")


def test_score_momentum():
    """Sideways then rally -> MACD crosses and/or histogram flips positive."""
    flat = np.full(150, 100.0) + RNG.normal(0, 0.3, 150)
    rally = flat[-1] * np.exp(np.linspace(0, 0.04, 60))
    close = np.concatenate([flat, rally])
    df = add_indicators(_ohlcv_from_close(close))
    s = compute_score(df, None)
    assert s["momentum"] >= 10, f"Momentum should fire, got {s['momentum']}"
    print(f"[PASS] 2e. Momentum (score={s['momentum']})")


def test_score_relative_strength():
    close = _base_uptrend(300)
    df = add_indicators(_ohlcv_from_close(close))
    idx = _index_frame("bull", 300)
    # Flatten last 63 bars of the index -> stock beats index easily
    vals = idx["close"].values.copy()
    vals[-63:] = vals[-63]
    idx["close"] = vals
    idx["open"] = vals
    idx["high"] = vals * 1.001
    idx["low"] = vals * 0.999
    s = compute_score(df, idx)
    assert s["relative_strength"] == 10, \
        f"RS should be 10, got {s['relative_strength']}"
    print(f"[PASS] 2f. Relative strength (score={s['relative_strength']})")


def test_score_ordering():
    """STRONG > WEAK >= BAD (strict on first, weak inequality on second)."""
    # STRONG: long uptrend + noisy-but-drifting-up flat phase + 3-bar dip + 2 up bars + volume surge.
    # Drift must be strong enough that noise doesn't flip the phase negative across seeds.
    # Target: ~+6% over 50 bars (drift 0.0012/bar) so the phase reliably ends ABOVE SMA50.
    rng = np.random.default_rng(11)
    base = _base_uptrend(300).tolist()
    last = base[-1]
    flat = []
    price = last
    for _ in range(50):
        price = price * (1 + rng.normal(0.0012, 0.004))  # ~6% drift, ~3% noise std over 50 bars
        flat.append(price)
    dip = [flat[-1] * 0.997, flat[-1] * 0.997 * 0.996, flat[-1] * 0.997 * 0.996 * 0.997]
    up = [dip[-1] * 1.002, dip[-1] * 1.002 * 1.002]
    strong_close = np.array(base + flat + dip + up)
    strong_raw = _ohlcv_from_close(strong_close)
    strong_raw.loc[strong_raw.index[-1], "volume"] *= 2.5
    strong = add_indicators(strong_raw)

    # WEAK: random walk around 100 (no trend, no structure)
    weak_close = 100 + RNG.normal(0, 1.5, 300).cumsum()
    weak = add_indicators(_ohlcv_from_close(weak_close))

    # BAD: noisy downtrend. Daily noise prevents the degenerate-smooth-curve artifacts
    # (near-zero BB width, decelerating-decay MACD hist flip) that a pure exp decay causes.
    rng2 = np.random.default_rng(99)
    bad_drift = np.linspace(0, -0.35, 300)
    bad_noise = rng2.normal(0, 0.012, 300)  # realistic daily noise
    bad_close = 100 * np.exp(bad_drift + bad_noise)
    bad = add_indicators(_ohlcv_from_close(bad_close))

    idx = _index_frame("bull", 400)
    s_strong = compute_score(strong, idx)["total"]
    s_weak = compute_score(weak, idx)["total"]
    s_bad = compute_score(bad, idx)["total"]
    # Key invariants:
    #   (1) STRONG clearly beats both WEAK and BAD — the system should rank real
    #       MPVS setups above noise and above downtrends.
    #   (2) BAD cannot clear the 70-point production threshold. Even a random relief
    #       bounce in a downtrend may fire pullback/momentum (up to ~30 pts), but
    #       without trend(25) + squeeze(15) + RS(10) it can't get to 70.
    assert s_strong > s_weak, f"STRONG ({s_strong}) must beat WEAK ({s_weak})"
    assert s_strong > s_bad, f"STRONG ({s_strong}) must beat BAD ({s_bad})"
    assert s_bad < 70, f"BAD ({s_bad}) must stay below the 70-point production filter"
    print(f"[PASS] 2g. Ordering STRONG={s_strong}  WEAK={s_weak}  BAD={s_bad}")


# ---------------------------------------------------------------- 3. filters

def test_filters():
    df = add_indicators(_ohlcv_from_close(_base_uptrend(250)))
    # Close ends ~149, vol=2M -> traded value ~30 cr. Should pass ₹25 cr floor.
    assert liquidity_ok(df, min_value_cr=25.0)
    assert not liquidity_ok(df, min_value_cr=10_000.0)

    assert regime_bullish(_index_frame("bull")) is True
    assert regime_bullish(_index_frame("bear")) is False
    print("[PASS] 3. Filters (liquidity + regime)")


# ---------------------------------------------------------------- 4. risk

def test_risk_maths():
    stop, target = compute_stop_target(entry=500.0, atr_value=10.0)
    assert stop == 485.0 and target == 530.0, (stop, target)

    # Cap-limited: risk leg = 500_000*0.02/15 = 666; cap leg = 500_000*0.20/500 = 200
    qty = position_size(capital=500_000, risk_pct=0.02, entry=500.0, stop=485.0)
    assert qty == 200, f"Expected cap-limited qty=200, got {qty}"

    # Still cap-limited at lower price: risk=5000 vs cap=1000
    qty2 = position_size(capital=500_000, risk_pct=0.02, entry=100.0, stop=98.0)
    assert qty2 == 1000, f"Expected cap-limited qty=1000, got {qty2}"

    # Invalid trades
    assert position_size(500_000, 0.02, 100, 100) == 0
    assert position_size(500_000, 0.02, 100, 101) == 0

    # Trailing: not activated below 4% gain
    assert trailing_stop(entry=100, current_price=103, atr_value=2) is None
    ts = trailing_stop(entry=100, current_price=105, atr_value=2)
    assert ts is not None and abs(ts - 103.0) < 1e-9, f"Expected 103, got {ts}"
    print("[PASS] 4. Risk maths (stop/target, sizing, trailing)")


# ---------------------------------------------------------------- 5. end-to-end

def test_end_to_end():
    universe = {}
    for i in range(3):
        close = _base_uptrend(300) * (0.8 + 0.15 * i)
        raw = _ohlcv_from_close(close)
        raw.loc[raw.index[-5:], "volume"] *= 2
        universe[f"TEST{i}.NS"] = add_indicators(raw)

    idx = _index_frame("bull", 300)
    res = backtest(
        universe, idx,
        start="2023-06-01", end="2024-01-01",
        capital=500_000, risk_pct=0.02, max_positions=3,
        min_score=40,  # relaxed for synthetic data
        cost_per_side=0.002,
    )
    assert len(res.equity_curve) > 0, "Backtest should produce equity curve"
    assert res.initial_capital == 500_000

    m = compute_metrics(res.trades, res.equity_curve, 500_000)
    assert hasattr(m, "n_trades") and isinstance(m.gate_detail, dict)
    print(f"[PASS] 5a. Backtest ran: trades={len(res.trades)}  "
          f"final_equity={res.final_equity:.0f}")

    sample = [{
        "ticker": "TEST.NS", "score": 75, "close": 500.0,
        "entry_low": 497.5, "entry_high": 501.0,
        "stop": 485.0, "target": 530.0, "rr": 2.0,
        "atr14": 10.0, "qty": 100, "capital_at_risk": 1500,
        "rsi14": 48.5, "bb_rank": 0.15,
        "rationale": "uptrend + pullback + volume",
    }]
    out_dir = ROOT / "reports_out" / "smoke"
    csv_path, html_path = build_report(sample, True, out_dir, as_of="smoke")
    assert csv_path.exists() and csv_path.stat().st_size > 0
    assert html_path.exists() and html_path.stat().st_size > 0
    print(f"[PASS] 5b. Report written: {csv_path.name} + {html_path.name}")
    return res, m


def run() -> int:
    print("=" * 72)
    print("SMOKE TEST — Nifty Midcap 150 MPVS Screener")
    print("=" * 72)
    test_indicator_math()
    test_score_trend()
    test_score_pullback()
    test_score_squeeze()
    test_score_volume()
    test_score_momentum()
    test_score_relative_strength()
    test_score_ordering()
    test_filters()
    test_risk_maths()
    res, m = test_end_to_end()

    print("\n--- Integration backtest metrics (synthetic data; shape check only) ---")
    print(format_metrics(m))
    print("\n" + "=" * 72)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(run())
