"""Walk-forward backtest of MarketSignal's current model.

For every symbol in the universe it replays history one step at a time: at each
past date it feeds the model ONLY the data available up to that point, records
the up/down/sideways call, then looks ahead the horizon to grade it. No news is
available historically, so this measures the technical/price model on its own.

Outputs:
  data/backtest.sqlite3   one row per graded walk-forward prediction
  data/scorecard.json     the calibration summary the web app reads

Run:  python3 backtest.py [--years 5] [--step 5] [--symbols AAPL,MSFT]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import statistics
import sys
import time
import urllib.error

import app
from universe import SYMBOLS, SCAN_HORIZONS

BACKTEST_DB = app.DATA_DIR / "backtest.sqlite3"
SCORECARD_PATH = app.DATA_DIR / "scorecard.json"

# Rough map from the model's word-confidence to a probability, so we can score
# calibration (Brier). 0.25 would be a coin flip.
CONF_PROB = {"low": 0.50, "low-medium": 0.55, "medium": 0.62, "medium-high": 0.70, "high": 0.78}
WARMUP_BARS = 300  # need SMA50 + RSI + MACD + a few hundred bars for the analog model


def _table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS runs")
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, as_of_date TEXT, horizon_days INTEGER,
            predicted_direction TEXT, actual_direction TEXT, direction_correct INTEGER,
            expected_return_pct REAL, actual_change_pct REAL, target_error_pct REAL,
            confidence TEXT, band_pct REAL, score REAL,
            start_price REAL, future_price REAL,
            regime TEXT, rsi14 REAL, vol_annualized_pct REAL
        )
        """
    )


def backtest_symbol(conn: sqlite3.Connection, symbol: str, step: int) -> int:
    try:
        chart = app.yahoo_chart(symbol, "5y")
        series = app.price_series(chart)
    except (ValueError, urllib.error.URLError) as exc:
        print(f"  {symbol}: skipped ({exc})")
        return 0
    if len(series) < WARMUP_BARS + 40:
        print(f"  {symbol}: skipped (only {len(series)} bars)")
        return 0

    max_sessions = max(app.horizon_sessions(h) for h in SCAN_HORIZONS)
    neutral_sentiment = {"label": "neutral", "score": 0.0}
    rows: list[tuple] = []

    for t in range(WARMUP_BARS, len(series) - max_sessions, step):
        window = series[: t + 1]
        recent = [row["close"] for row in window[-126:]]
        quote = {
            "symbol": symbol,
            "price": window[-1]["close"],
            "low_6m": min(recent),
            "high_6m": max(recent),
        }
        for horizon in SCAN_HORIZONS:
            sessions = app.horizon_sessions(horizon)
            if t + sessions >= len(series):
                continue
            analytics = app.analytics_summary(window, "1y", horizon)
            if not analytics.get("has_price_history"):
                continue
            # Always the hand-weighted blend — this file is the fixed baseline
            # reference. The learned model's honest out-of-sample numbers come
            # from train.py's walk-forward and land in scorecard.json["learned"].
            fc = app.directional_forecast(quote, neutral_sentiment, analytics)
            start = quote["price"]
            future = series[t + sessions]["close"]
            actual_dir = app.prediction_direction(start, future, fc["band_pct"])
            target = fc.get("target_price")
            rows.append((
                symbol,
                window[-1]["date"],
                horizon,
                fc["direction"],
                actual_dir,
                1 if actual_dir == fc["direction"] else 0,
                fc["expected_return_pct"],
                (future - start) / start * 100 if start else None,
                abs(future - target) / start * 100 if (target and start) else None,
                fc["confidence"],
                fc["band_pct"],
                fc["score"],
                start,
                future,
                (analytics.get("technical") or {}).get("trend"),
                (analytics.get("technical") or {}).get("rsi14"),
                (analytics.get("stats") or {}).get("volatility_annualized_pct"),
            ))

    conn.executemany(
        """INSERT INTO runs (
            symbol, as_of_date, horizon_days, predicted_direction, actual_direction, direction_correct,
            expected_return_pct, actual_change_pct, target_error_pct, confidence, band_pct, score,
            start_price, future_price, regime, rsi14, vol_annualized_pct
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    print(f"  {symbol}: {len(rows)} graded predictions")
    return len(rows)


def _group(rows: list[dict], key: str) -> list[dict]:
    buckets: dict = {}
    for row in rows:
        k = row.get(key)
        if k is None:
            continue
        b = buckets.setdefault(k, {"key": k, "n": 0, "correct": 0, "errors": [], "returns": []})
        b["n"] += 1
        b["correct"] += row["direction_correct"]
        if row["target_error_pct"] is not None:
            b["errors"].append(row["target_error_pct"])
        if row["actual_change_pct"] is not None:
            b["returns"].append(row["actual_change_pct"])
    out = []
    for b in buckets.values():
        out.append({
            key: b["key"],
            "n": b["n"],
            "accuracy_pct": round(b["correct"] / b["n"] * 100, 1) if b["n"] else None,
            "avg_error_pct": round(statistics.fmean(b["errors"]), 2) if b["errors"] else None,
            "realized_avg_return_pct": round(statistics.fmean(b["returns"]), 2) if b["returns"] else None,
        })
    return sorted(out, key=lambda x: -x["n"])


def _precision(rows: list[dict], direction: str) -> dict:
    called = [r for r in rows if r["predicted_direction"] == direction]
    if not called:
        return {"n": 0, "precision_pct": None, "avg_realized_return_pct": None}
    hits = sum(1 for r in called if r["actual_direction"] == direction)
    rets = [r["actual_change_pct"] for r in called if r["actual_change_pct"] is not None]
    return {
        "n": len(called),
        "precision_pct": round(hits / len(called) * 100, 1),
        "avg_realized_return_pct": round(statistics.fmean(rets), 2) if rets else None,
    }


def build_scorecard(conn: sqlite3.Connection, years: int) -> dict:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM runs").fetchall()]
    if not rows:
        return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "sample": 0,
                "headline": "No backtest rows — run failed to fetch data."}

    graded = [r for r in rows if r["direction_correct"] is not None]
    n = len(graded)
    acc = sum(r["direction_correct"] for r in graded) / n * 100
    errs = [r["target_error_pct"] for r in graded if r["target_error_pct"] is not None]
    brier_pts = [(CONF_PROB.get(r["confidence"], 0.5) - r["direction_correct"]) ** 2 for r in graded]
    sideways_share = sum(1 for r in graded if r["predicted_direction"] == "sideways") / n * 100

    # Calibration: does a bigger predicted move actually show up?
    calib_buckets = [(-99, -3), (-3, -1), (-1, 1), (1, 3), (3, 6), (6, 99)]
    calibration = []
    for lo, hi in calib_buckets:
        sub = [r for r in graded if r["expected_return_pct"] is not None and lo <= r["expected_return_pct"] < hi]
        if not sub:
            continue
        pred = statistics.fmean(r["expected_return_pct"] for r in sub)
        real = statistics.fmean(r["actual_change_pct"] for r in sub if r["actual_change_pct"] is not None)
        calibration.append({
            "predicted_bucket": f"{lo:+d}..{hi:+d}%" if abs(lo) != 99 and abs(hi) != 99 else (f"< {hi:+d}%" if lo == -99 else f"> {lo:+d}%"),
            "n": len(sub),
            "predicted_avg_pct": round(pred, 2),
            "realized_avg_pct": round(real, 2),
        })

    by_symbol = _group(graded, "symbol")
    ranked_symbols = sorted(
        [s for s in by_symbol if s["n"] >= 20 and s["accuracy_pct"] is not None],
        key=lambda x: x["accuracy_pct"],
        reverse=True,
    )
    headline = (
        f"{n:,} graded predictions over ~{years}y. Direction {acc:.1f}%. "
        f"Up-calls right {_precision(graded, 'up')['precision_pct']}% , down-calls {_precision(graded, 'down')['precision_pct']}%. "
        f"{sideways_share:.0f}% of calls were 'sideways'."
    )

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "years": years,
        "symbols": len({r["symbol"] for r in graded}),
        "sample": n,
        "overall": {
            "direction_accuracy_pct": round(acc, 1),
            "avg_target_error_pct": round(statistics.fmean(errs), 2) if errs else None,
            "brier": round(statistics.fmean(brier_pts), 4),
            "sideways_share_pct": round(sideways_share, 1),
            "up_call": _precision(graded, "up"),
            "down_call": _precision(graded, "down"),
        },
        "by_confidence": _group(graded, "confidence"),
        "by_direction": _group(graded, "predicted_direction"),
        "by_regime": _group(graded, "regime"),
        "by_horizon": _group(graded, "horizon_days"),
        "best_symbols": ranked_symbols[:6],
        "worst_symbols": ranked_symbols[-6:][::-1],
        "calibration": calibration,
        "headline": headline,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--step", type=int, default=5, help="trading-day stride between test points")
    parser.add_argument("--symbols", default="", help="comma list; default = whole universe")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    app.DATA_DIR.mkdir(exist_ok=True)
    started = time.time()
    total = 0
    with sqlite3.connect(BACKTEST_DB) as conn:
        _table(conn)
        for i, symbol in enumerate(symbols, 1):
            print(f"[{i}/{len(symbols)}] {symbol}")
            try:
                total += backtest_symbol(conn, symbol, args.step)
            except Exception as exc:  # keep going; one bad symbol shouldn't kill the run
                print(f"  {symbol}: error {exc}")
            time.sleep(0.8)  # be polite to Yahoo
        scorecard = build_scorecard(conn, args.years)

    SCORECARD_PATH.write_text(json.dumps(scorecard, indent=2))
    mins = (time.time() - started) / 60
    print(f"\nDone: {total:,} graded predictions from {len(symbols)} symbols in {mins:.1f} min")
    print(f"Scorecard -> {SCORECARD_PATH}")
    print("\n" + scorecard.get("headline", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
