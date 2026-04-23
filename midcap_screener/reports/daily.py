"""
Daily report generator. Produces a CSV + HTML with the ranked shortlist and
a big regime banner at the top.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Dict

import pandas as pd

HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Midcap Screener — {date}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 24px; background:#f8f9fa; }}
h1 {{ margin-top: 0; }}
.banner {{ padding: 14px 18px; border-radius: 6px; color: white; font-weight: 600; margin-bottom: 16px; }}
.bull {{ background: #27ae60; }}
.bear {{ background: #c0392b; }}
table {{ border-collapse: collapse; width: 100%; background:white; }}
th, td {{ padding: 8px 12px; border-bottom: 1px solid #e1e4e8; text-align: right; font-variant-numeric: tabular-nums; }}
th {{ background: #f1f3f5; text-align: right; }}
td:first-child, th:first-child, td:last-child, th:last-child {{ text-align: left; }}
.small {{ color: #6a737d; font-size: 13px; margin-top: 24px; }}
</style></head><body>
<h1>Nifty Midcap 150 — MPVS Screener</h1>
<p>As of close {date}</p>
<div class="banner {bcls}">Regime: {banner}</div>
{table}
<p class="small">
  This is a ranking aid — always cross-check earnings blackout and F&O ban list before ordering.
  Entry band is ±0.2–0.5% around previous close; use a limit order, good-till-cancelled.
  Stop = 1.5×ATR below entry, target = 3×ATR above. Never risk more than 2% of capital per trade.
</p></body></html>
"""


def build_report(
    ranked: List[Dict],
    regime_bullish: bool,
    out_dir: str | Path,
    as_of: str | None = None,
) -> tuple[Path, Path]:
    """
    Returns (csv_path, html_path). Both written to `out_dir`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of or pd.Timestamp.now().strftime("%Y-%m-%d")

    df = pd.DataFrame(ranked)
    csv_path = out_dir / f"screen_{stamp}.csv"
    df.to_csv(csv_path, index=False)

    html_path = out_dir / f"screen_{stamp}.html"
    banner = "BULLISH — strategy active" if regime_bullish else "BEARISH — stand down, no new longs"
    bcls = "bull" if regime_bullish else "bear"
    table = df.to_html(index=False) if not df.empty else "<p><em>No candidates above score threshold.</em></p>"
    html_path.write_text(HTML_TEMPLATE.format(date=stamp, banner=banner, bcls=bcls, table=table))
    return csv_path, html_path
