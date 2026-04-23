"""
Telegram alert bot.

Setup:
  1. Create a bot via @BotFather on Telegram, get the token.
  2. Create a channel/group, add the bot as admin.
  3. Get the chat_id (send a message, then check https://api.telegram.org/bot<TOKEN>/getUpdates)
  4. Set env vars:
       export TELEGRAM_BOT_TOKEN="your-token"
       export TELEGRAM_CHAT_ID="your-chat-id"

Usage:
  Called automatically after each screener run.
  Can also be run manually:  python -m webapp.telegram_bot

Message format:
  🟢 A-GRADE SIGNAL
  POLYCAB.NS  Score: 95
  ₹8,185  →  Target: ₹8,750  |  SL: ₹7,900
  R:R 2.0  |  RSI 48  |  Pharma #2
  [uptrend + pullback + quality(25) + hot sector]
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

TIER_EMOJI = {"A": "🟢", "B": "🟡", "WATCH": "👀"}


def send_telegram(message: str, token: str = "", chat_id: str = "") -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.warning("Telegram credentials not set. Skipping alert.")
        return False

    try:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


def format_signal_alert(signal: dict, regime_bullish: bool = True) -> str:
    """Format a single signal as a Telegram message."""
    tier = signal.get("tier", "?")
    emoji = TIER_EMOJI.get(tier, "❓")
    ticker = signal.get("ticker", "?")
    score = signal.get("score", 0)
    close = signal.get("close", 0)
    target = signal.get("target", 0)
    stop = signal.get("stop", 0)
    rr = signal.get("rr", 0)
    rsi = signal.get("rsi14", "?")
    sector = signal.get("sector", "?")
    rationale = signal.get("rationale", "—")

    pct_target = ((target / close) - 1) * 100 if close else 0
    pct_stop = ((close / stop) - 1) * 100 if stop else 0

    lines = [
        f"{emoji} <b>{tier}-GRADE SIGNAL</b>",
        f"<b>{ticker}</b>  Score: {score}/160",
        "",
        f"💰 CMP: ₹{close:,.2f}",
        f"🎯 Target: ₹{target:,.2f} (+{pct_target:.1f}%)",
        f"🛑 Stop: ₹{stop:,.2f} (-{pct_stop:.1f}%)",
        f"📊 R:R {rr}  |  RSI {rsi}  |  {sector}",
        "",
        f"<i>{rationale}</i>",
    ]

    if not regime_bullish:
        lines.insert(0, "⚠️ <b>REGIME BEARISH — stand down</b>\n")

    return "\n".join(lines)


def send_daily_summary(signals: list[dict], regime_bullish: bool = True) -> bool:
    """Send the daily summary to Telegram."""
    a_grade = [s for s in signals if s.get("tier") == "A"]
    b_grade = [s for s in signals if s.get("tier") == "B"]

    if not a_grade and not b_grade:
        msg = "📊 <b>Midcap Screener — No actionable signals today</b>\n\n"
        msg += f"Regime: {'🟢 Bullish' if regime_bullish else '🔴 Bearish'}\n"
        msg += f"Watchlist stocks: {len([s for s in signals if s.get('tier') == 'WATCH'])}"
        return send_telegram(msg)

    parts = [f"📊 <b>Midcap Screener Daily Report</b>\n"]
    parts.append(f"Regime: {'🟢 Bullish' if regime_bullish else '🔴 Bearish'}")
    parts.append(f"A-grade: {len(a_grade)} | B-grade: {len(b_grade)}\n")

    for s in (a_grade + b_grade)[:5]:
        parts.append(format_signal_alert(s, regime_bullish))
        parts.append("")

    return send_telegram("\n".join(parts))


if __name__ == "__main__":
    # Test with a sample signal
    test_signal = {
        "ticker": "POLYCAB.NS", "tier": "A", "score": 95,
        "close": 8185, "target": 8750, "stop": 7900,
        "rr": 2.0, "rsi14": 48.5, "sector": "Industrials",
        "rationale": "uptrend + pullback + quality(25) + hot sector",
    }
    msg = format_signal_alert(test_signal)
    print("Sample message:\n")
    print(msg)
    print("\n(Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars to send for real)")
