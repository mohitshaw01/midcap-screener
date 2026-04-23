"""
Start the web dashboard with auto-scheduled screener runs.

Usage:
    python run_server.py              # starts on port 5000
    python run_server.py --port 8080  # custom port
    python run_server.py --no-schedule  # disable auto-run (manual only)

The screener runs automatically at 16:30 IST on weekdays.
You can also trigger it manually: python -m webapp.scheduler
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from webapp.app import app, init_db


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("server")

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--no-schedule", action="store_true", help="Disable auto-scheduled screener runs")
    args = p.parse_args()

    init_db()
    log.info("Database initialised")

    if not args.no_schedule:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
            scheduler.add_job(
                _run_screener_job,
                CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
                id="daily_screener",
                name="Daily Midcap Screener",
                replace_existing=True,
            )
            scheduler.start()
            log.info("Scheduler started — screener will run at 16:30 IST on weekdays")
        except ImportError:
            log.warning("APScheduler not installed. Auto-scheduling disabled.")
            log.warning("Install with: pip install apscheduler")
            log.warning("Run manually: python -m webapp.scheduler")
    else:
        log.info("Auto-scheduling disabled. Run manually: python -m webapp.scheduler")

    log.info("Starting dashboard on http://localhost:%d", args.port)
    app.run(host="0.0.0.0", port=args.port, debug=False)


def _run_screener_job():
    """Wrapper for the scheduled job."""
    log = logging.getLogger("scheduler_job")
    try:
        from webapp.scheduler import run_screener
        from webapp.telegram_bot import send_daily_summary
        result = run_screener()
        log.info("Screener run complete: %s", result)

        # Send Telegram alert
        if result.get("a_grade", 0) > 0 or result.get("b_grade", 0) > 0:
            from webapp.app import get_db
            conn = get_db()
            signals = conn.execute(
                "SELECT * FROM signals WHERE run_date = ? AND tier IN ('A','B') ORDER BY score DESC",
                (result["date"],)
            ).fetchall()
            conn.close()
            send_daily_summary(
                [dict(s) for s in signals],
                result.get("regime_bullish", True),
            )
    except Exception as e:
        log.error("Scheduled screener run failed: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
