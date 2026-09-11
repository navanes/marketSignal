"""Append notes to today's entry in the dashboard's daily report log.

Usage:
    python3 log_day.py "Checked the model, it's doing fine." "Added Oracle to the watch list."

Notes for the same date accumulate (running the script twice today adds to
today's list instead of overwriting it). Keep each note short and plain —
it's shown on the dashboard as a simple, kid-level report.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

LOG_PATH = Path(__file__).parent / "data" / "daily_reports.json"


def main() -> None:
    notes = [n.strip() for n in sys.argv[1:] if n.strip()]
    if not notes:
        print("Give at least one note to log, e.g.:")
        print('  python3 log_day.py "Checked the model, it looked healthy."')
        raise SystemExit(1)

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
    print(f"Logged {len(notes)} note(s) for {today}.")


if __name__ == "__main__":
    main()
