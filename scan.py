"""Nightly universe scan.

Runs the research model once per (symbol, horizon) for the whole universe and
logs each call as a prediction, so the track record compounds automatically
without anyone typing tickers. Also grades any predictions that have come due.

Run:  python3 scan.py            # scan the whole universe
      python3 scan.py --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time

import app
from universe import SYMBOLS, SCAN_HORIZONS


def already_logged_today(symbol: str, horizon_days: int) -> bool:
    app.init_prediction_db()
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        row = conn.execute(
            "SELECT 1 FROM predictions WHERE symbol = ? AND horizon_days = ? "
            "AND date(created_at) = date('now') LIMIT 1",
            (symbol, horizon_days),
        ).fetchone()
    return row is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="")
    parser.add_argument("--force", action="store_true", help="log even if already logged today")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or SYMBOLS
    started = time.time()
    logged = skipped = failed = 0

    for i, symbol in enumerate(symbols, 1):
        for horizon in SCAN_HORIZONS:
            if not args.force and already_logged_today(symbol, horizon):
                skipped += 1
                continue
            try:
                data = app.research(symbol, "6mo", horizon)
                pred = data.get("prediction")
                if pred:
                    logged += 1
                    fc = data.get("forecast_model", {})
                    print(f"[{i}/{len(symbols)}] {symbol} {horizon}d -> {fc.get('direction')} "
                          f"{fc.get('expected_return_pct')}% ({fc.get('confidence')})")
                else:
                    failed += 1
                    print(f"[{i}/{len(symbols)}] {symbol} {horizon}d -> no prediction saved")
            except Exception as exc:
                failed += 1
                print(f"[{i}/{len(symbols)}] {symbol} {horizon}d -> error {exc}")
            time.sleep(1.0)  # be polite to the data sources

    graded_before = _evaluated_count()
    app.evaluate_pending_predictions()
    newly_graded = _evaluated_count() - graded_before

    mins = (time.time() - started) / 60
    print(
        f"\n{dt.datetime.now().isoformat(timespec='seconds')}  "
        f"logged {logged}, skipped {skipped} (already today), failed {failed}, "
        f"graded {newly_graded} due predictions  [{mins:.1f} min]"
    )
    return 0


def _evaluated_count() -> int:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        return conn.execute("SELECT COUNT(*) FROM predictions WHERE status = 'evaluated'").fetchone()[0]


if __name__ == "__main__":
    sys.exit(main())
