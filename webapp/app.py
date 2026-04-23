"""
Flask API + scheduler for the Midcap Screener dashboard.

Endpoints:
  GET  /api/signals        — today's ranked signals (A/B/Watch)
  GET  /api/signals/history — last 30 days of signals
  GET  /api/sectors        — sector rotation ranking
  GET  /api/regime         — current regime status
  GET  /api/stock/<ticker> — detailed view for one stock
  GET  /api/trades         — trade journal entries
  POST /api/trades         — log a new trade
  GET  /api/performance    — portfolio P&L summary
  GET  /                   — serve the React dashboard

Scheduler:
  Runs the full v2 screener at 16:30 IST every weekday.
  Stores results in a local SQLite DB so the dashboard is instant.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("SCREENER_DB", "screener.db")
app = Flask(__name__, static_folder="static", template_folder="templates")


# ── Database ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        tier TEXT,
        score INTEGER,
        close REAL,
        stop REAL,
        target REAL,
        rr REAL,
        rsi14 REAL,
        weekly_rsi REAL,
        bb_rank REAL,
        sector TEXT,
        sector_rank INTEGER,
        trend INTEGER, pullback INTEGER, momentum INTEGER,
        squeeze INTEGER, vol INTEGER, quality INTEGER,
        sect_bonus INTEGER, mtf_bonus INTEGER, inst_bonus INTEGER,
        penalty INTEGER,
        rationale TEXT,
        atr14 REAL,
        qty INTEGER,
        capital_at_risk REAL,
        entry_low REAL,
        entry_high REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        entry_date TEXT,
        entry_price REAL,
        stop REAL,
        target REAL,
        qty INTEGER,
        exit_date TEXT,
        exit_price REAL,
        exit_reason TEXT,
        pnl REAL,
        return_pct REAL,
        notes TEXT,
        signal_score INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS regime_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        is_bullish INTEGER,
        index_close REAL,
        index_sma50 REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sector_rankings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        sector TEXT,
        rank INTEGER,
        avg_return REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(run_date);
    CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
    CREATE INDEX IF NOT EXISTS idx_regime_date ON regime_log(run_date);
    """)
    conn.commit()
    conn.close()


# ── Store screener results ──────────────────────────────────────────────

def store_signals(run_date: str, signals: list[dict]):
    conn = get_db()
    # Clear existing signals for this date
    conn.execute("DELETE FROM signals WHERE run_date = ?", (run_date,))
    for s in signals:
        conn.execute("""
            INSERT INTO signals (run_date, ticker, tier, score, close, stop, target,
                rr, rsi14, weekly_rsi, bb_rank, sector, sector_rank,
                trend, pullback, momentum, squeeze, vol, quality,
                sect_bonus, mtf_bonus, inst_bonus, penalty, rationale,
                atr14, qty, capital_at_risk, entry_low, entry_high)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            run_date, s.get("ticker"), s.get("tier"), s.get("score"),
            s.get("close"), s.get("stop"), s.get("target"), s.get("rr"),
            s.get("rsi14"), s.get("weekly_rsi"), s.get("bb_rank"),
            s.get("sector"), s.get("sector_rank"),
            s.get("trend"), s.get("pullback"), s.get("momentum"),
            s.get("squeeze"), s.get("vol"), s.get("quality"),
            s.get("sect_bonus"), s.get("mtf_bonus"), s.get("inst_bonus"),
            s.get("penalty"), s.get("rationale"),
            s.get("atr14"), s.get("qty"), s.get("capital_at_risk"),
            s.get("entry_low"), s.get("entry_high"),
        ))
    conn.commit()
    conn.close()


def store_regime(run_date: str, is_bullish: bool, index_close: float, index_sma50: float):
    conn = get_db()
    conn.execute("DELETE FROM regime_log WHERE run_date = ?", (run_date,))
    conn.execute(
        "INSERT INTO regime_log (run_date, is_bullish, index_close, index_sma50) VALUES (?,?,?,?)",
        (run_date, int(is_bullish), index_close, index_sma50),
    )
    conn.commit()
    conn.close()


def store_sector_rankings(run_date: str, rankings: list[tuple]):
    conn = get_db()
    conn.execute("DELETE FROM sector_rankings WHERE run_date = ?", (run_date,))
    for i, (sector, ret) in enumerate(rankings):
        conn.execute(
            "INSERT INTO sector_rankings (run_date, sector, rank, avg_return) VALUES (?,?,?,?)",
            (run_date, sector, i + 1, ret),
        )
    conn.commit()
    conn.close()


# ── API Routes ──────────────────────────────────────────────────────────

@app.route("/api/signals")
def api_signals():
    """Today's signals, or specify ?date=YYYY-MM-DD"""
    d = request.args.get("date", date.today().isoformat())
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM signals WHERE run_date = ? ORDER BY score DESC", (d,)
    ).fetchall()
    conn.close()
    if not rows:
        # Try latest available date
        conn = get_db()
        latest = conn.execute(
            "SELECT DISTINCT run_date FROM signals ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        if latest:
            d = latest["run_date"]
            rows = conn.execute(
                "SELECT * FROM signals WHERE run_date = ? ORDER BY score DESC", (d,)
            ).fetchall()
        conn.close()
    return jsonify({"date": d, "signals": [dict(r) for r in rows]})


@app.route("/api/signals/history")
def api_signals_history():
    """Signal counts by date for the last 30 days."""
    conn = get_db()
    rows = conn.execute("""
        SELECT run_date,
               COUNT(*) as total,
               SUM(CASE WHEN tier='A' THEN 1 ELSE 0 END) as a_grade,
               SUM(CASE WHEN tier='B' THEN 1 ELSE 0 END) as b_grade,
               SUM(CASE WHEN tier='WATCH' THEN 1 ELSE 0 END) as watch
        FROM signals
        GROUP BY run_date
        ORDER BY run_date DESC
        LIMIT 30
    """).fetchall()
    conn.close()
    return jsonify({"history": [dict(r) for r in rows]})


@app.route("/api/sectors")
def api_sectors():
    d = request.args.get("date", date.today().isoformat())
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sector_rankings WHERE run_date = ? ORDER BY rank ASC", (d,)
    ).fetchall()
    if not rows:
        latest = conn.execute(
            "SELECT DISTINCT run_date FROM sector_rankings ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        if latest:
            d = latest["run_date"]
            rows = conn.execute(
                "SELECT * FROM sector_rankings WHERE run_date = ? ORDER BY rank ASC", (d,)
            ).fetchall()
    conn.close()
    return jsonify({"date": d, "sectors": [dict(r) for r in rows]})


@app.route("/api/regime")
def api_regime():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM regime_log ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return jsonify(dict(row))
    return jsonify({"is_bullish": None, "message": "No data yet. Run the screener first."})


@app.route("/api/trades", methods=["GET"])
def api_trades_get():
    conn = get_db()
    rows = conn.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT 100").fetchall()
    conn.close()
    # Calculate summary stats
    trades = [dict(r) for r in rows]
    closed = [t for t in trades if t.get("exit_price")]
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    total_pnl = sum(t.get("pnl") or 0 for t in closed)
    return jsonify({
        "trades": trades,
        "summary": {
            "total": len(trades),
            "closed": len(closed),
            "open": len(trades) - len(closed),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": len(wins) / len(closed) if closed else 0,
            "total_pnl": round(total_pnl, 2),
        }
    })


@app.route("/api/trades", methods=["POST"])
def api_trades_post():
    data = request.json
    conn = get_db()
    conn.execute("""
        INSERT INTO trades (ticker, entry_date, entry_price, stop, target, qty,
                           exit_date, exit_price, exit_reason, pnl, return_pct,
                           notes, signal_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("ticker"), data.get("entry_date"), data.get("entry_price"),
        data.get("stop"), data.get("target"), data.get("qty"),
        data.get("exit_date"), data.get("exit_price"), data.get("exit_reason"),
        data.get("pnl"), data.get("return_pct"),
        data.get("notes"), data.get("signal_score"),
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/performance")
def api_performance():
    """Portfolio performance summary."""
    conn = get_db()
    rows = conn.execute("""
        SELECT entry_date, exit_date, ticker, entry_price, exit_price,
               qty, pnl, return_pct, exit_reason
        FROM trades WHERE exit_price IS NOT NULL
        ORDER BY exit_date DESC
    """).fetchall()
    conn.close()
    trades = [dict(r) for r in rows]
    if not trades:
        return jsonify({"trades": [], "metrics": {}})

    pnls = [t["pnl"] or 0 for t in trades]
    returns = [t["return_pct"] or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return jsonify({
        "trades": trades,
        "metrics": {
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(sum(pnls), 2),
            "avg_return": round(sum(returns) / len(returns) * 100, 2) if returns else 0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
        }
    })


# ── Serve frontend ──────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


# ── Init ────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
