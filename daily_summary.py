"""Write a plain, kid-simple daily report entry from the nightly scan's results.

Runs automatically at the end of scan.py so the dashboard's daily report
section gets a new entry every day the scan runs — weekends included —
with no manual step required. Purely templated from real numbers, no LLM
call involved.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import app

LOG_PATH = Path(__file__).parent / "data" / "daily_reports.json"


def _append(notes: list[str]) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    entries: list[dict] = []
    if LOG_PATH.exists():
        try:
            entries = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            entries = []
    today = dt.date.today().isoformat()
    entry = next((e for e in entries if e.get("date") == today), None)
    if entry is None:
        entry = {"date": today, "notes": []}
        entries.append(entry)
    entry["notes"].extend(notes)
    entries = sorted(entries, key=lambda e: e.get("date", ""))[-120:]
    LOG_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def write_scan_summary(logged: int, skipped: int, failed: int, newly_graded: int) -> None:
    notes = [f"Checked {logged + skipped} stocks tonight and wrote down {logged} new guesses."]
    if failed:
        notes.append(f"{failed} stocks didn't work — worth a look if that keeps happening.")

    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        total_evaluated, total_correct = conn.execute(
            "SELECT COUNT(*), SUM(direction_correct) FROM predictions WHERE status = 'evaluated'"
        ).fetchone()
        graded_today_correct = graded_today_total = 0
        if newly_graded:
            rows = conn.execute(
                "SELECT direction_correct FROM predictions WHERE status = 'evaluated' "
                "AND date(evaluated_at) = date('now')"
            ).fetchall()
            graded_today_total = len(rows)
            graded_today_correct = sum(1 for r in rows if r["direction_correct"])

    if graded_today_total:
        notes.append(
            f"{graded_today_correct} of {graded_today_total} older guesses that came due today turned out right."
        )
    if total_evaluated:
        pct = round(total_correct / total_evaluated * 100)
        notes.append(f"So far, out of {total_evaluated} guesses checked, {pct}% were right.")

    _append(notes)


if __name__ == "__main__":
    write_scan_summary(0, 0, 0, 0)
