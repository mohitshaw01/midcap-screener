"""
ATR-based risk management.

Per the strategy:
  stop   = entry - 1.5 * ATR14
  target = entry + 3.0 * ATR14   (=> ~2:1 R:R, mapped to the 5-15% / 10-20 day envelope)
  qty    = floor( (capital * risk_pct) / (entry - stop) )
  cap    = no single position > 20% of capital
"""
from __future__ import annotations

from typing import Tuple


def compute_stop_target(
    entry: float,
    atr_value: float,
    stop_mult: float = 1.5,
    target_mult: float = 3.0,
) -> Tuple[float, float]:
    """Return (stop, target) given the latest ATR value."""
    stop = entry - stop_mult * atr_value
    target = entry + target_mult * atr_value
    return stop, target


def position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_position_pct: float = 0.20,
) -> int:
    """
    Integer share count.

    - Risk leg: (capital * risk_pct) / (entry - stop)
    - Concentration leg: at most `max_position_pct` of capital per name
    - Returns 0 if the stop is at or above entry (invalid trade)
    """
    if entry <= stop or entry <= 0:
        return 0
    per_share_risk = entry - stop
    risk_amount = capital * risk_pct
    qty_risk = int(risk_amount // per_share_risk)
    qty_cap = int((capital * max_position_pct) // entry)
    return max(0, min(qty_risk, qty_cap))


def trailing_stop(
    entry: float,
    current_price: float,
    atr_value: float,
    activate_gain: float = 0.04,
    trail_mult: float = 1.0,
) -> float | None:
    """
    Once unrealised gain >= 4%, trail by 1 * ATR below the current price.
    Returns the trailing-stop level, or None if not yet activated.
    """
    if entry <= 0:
        return None
    gain = (current_price / entry) - 1.0
    if gain < activate_gain:
        return None
    return current_price - trail_mult * atr_value
