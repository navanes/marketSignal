"""Track every BUY/SELL call the /signals command has actually made, so we
can check back once it grades and learn from the ones that were wrong.

Separate from the raw predictions table (which logs EVERY nightly forecast
regardless of whether anyone acted on it) — this only logs the specific
calls /signals surfaced as an actionable BUY or SELL, so accuracy and
post-mortems are scoped to what a person would have actually seen and could
have acted on.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

import app

MIN_REVIEW_SAMPLE = 8  # don't generalize a pattern from a handful of misses


def init_signal_calls_db() -> None:
    app.DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                called_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                bucket TEXT NOT NULL,
                prediction_id INTEGER NOT NULL,
                chance_pct_at_call INTEGER,
                timeframe_span TEXT,
                UNIQUE(symbol, bucket, prediction_id)
            )
            """
        )


def record_signal_calls(buckets: dict[str, list[dict[str, Any]]]) -> int:
    """Log every BUY/SELL item from a gather_signals() result. Safe to call
    on every /signals request — duplicates (same symbol/bucket/prediction)
    are silently skipped rather than re-logged."""
    init_signal_calls_db()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    logged = 0
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        for bucket in ("BUY", "SELL"):
            for item in buckets.get(bucket, []):
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO signal_calls "
                    "(called_at, symbol, bucket, prediction_id, chance_pct_at_call, timeframe_span) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (now, item["symbol"], bucket, item["prediction_id"], item["chance_pct"], item.get("timeframe")),
                )
                if cursor.rowcount:
                    logged += 1
    return logged


def review_signal_calls() -> dict[str, Any]:
    """Join logged signal calls against their now-graded predictions.
    Returns per-bucket accuracy and a short diagnostic for each wrong call,
    pulled from the technicals stored at the moment the call was made."""
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT sc.symbol, sc.bucket, sc.chance_pct_at_call, sc.timeframe_span, sc.called_at,
                   p.direction_correct, p.expected_return_pct, p.actual_change_pct, p.target_error_pct,
                   p.trend, p.rsi14, p.macd_histogram, p.sentiment, p.confidence, p.due_date
            FROM signal_calls sc
            JOIN predictions p ON p.id = sc.prediction_id
            WHERE p.status = 'evaluated'
            ORDER BY sc.called_at DESC
            """
        ).fetchall()

    summary: dict[str, Any] = {"BUY": {"n": 0, "correct": 0}, "SELL": {"n": 0, "correct": 0}}
    wrong: list[dict[str, Any]] = []
    for row in rows:
        bucket = row["bucket"]
        summary[bucket]["n"] += 1
        if row["direction_correct"]:
            summary[bucket]["correct"] += 1
        else:
            wrong.append(dict(row))

    for bucket_stats in summary.values():
        bucket_stats["accuracy_pct"] = (
            round(bucket_stats["correct"] / bucket_stats["n"] * 100) if bucket_stats["n"] else None
        )

    return {"summary": summary, "wrong": wrong, "total_graded": len(rows)}


def format_wrong_call(row: dict[str, Any]) -> str:
    bits = [
        (
            f"{row['symbol']} {row['bucket']} call (due {row['due_date']}) — "
            f"expected {row['expected_return_pct']:+.1f}%, actual {row['actual_change_pct']:+.1f}%."
            if row["expected_return_pct"] is not None and row["actual_change_pct"] is not None
            else f"{row['symbol']} {row['bucket']} call (due {row['due_date']}) missed."
        )
    ]
    context = []
    if row["trend"]:
        context.append(f"trend was {row['trend']}")
    if row["rsi14"] is not None:
        context.append(f"RSI {row['rsi14']:.0f}")
    if row["sentiment"]:
        context.append(f"news sentiment {row['sentiment']}")
    if row["chance_pct_at_call"] is not None:
        context.append(f"we called it {row['chance_pct_at_call']}% confident at the time")
    if context:
        bits.append("At the time: " + ", ".join(context) + ".")
    return " ".join(bits)
