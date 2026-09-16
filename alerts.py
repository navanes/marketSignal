"""Proactive "this looks like a real buy" pings to Telegram.

Runs at the end of the nightly scan (see scan.py). Looks at that night's
freshly logged 30-day calls and pushes an unsolicited message for anything
that clears a real bar — a genuine Buy/Strong Buy action at high or
medium-high confidence, not just "Watch" or a mixed signal. A per-symbol
cooldown stops the same name from re-alerting every single night while it
stays in a buy zone.

Run:  python3 alerts.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import app
import telegram_bot as tb

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "data" / "alert_state.json"

# Only these actions count as a real "buy now" call — Watch/Selective Buy and
# anything Neutral/Hold/Avoid don't qualify, no matter how big the expected
# move looks.
QUALIFYING_ACTIONS = {"Strong Buy / Accumulate", "Buy / Accumulate"}
QUALIFYING_CONFIDENCE = {"high", "medium-high"}
MIN_CHANCE_PCT = 55  # blended live+backtest accuracy floor, same math the bot uses
COOLDOWN_DAYS = 5  # don't re-alert the same symbol within this many days


def _load_state() -> dict[str, str]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _tonights_candidates() -> list[dict[str, Any]]:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, name, action, confidence, predicted_direction, "
            "expected_return_pct, start_price FROM predictions "
            "WHERE date(created_at) = date('now') AND horizon_days = 30"
        ).fetchall()
    return [dict(r) for r in rows]


def check_and_send() -> int:
    """Send an alert for each qualifying symbol not on cooldown. Returns how
    many alerts were sent."""
    tb.load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("alerts: TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not set — skipping.")
        return 0

    state = _load_state()
    today = dt.date.today()
    sent = 0

    for row in _tonights_candidates():
        symbol = row["symbol"]
        if row["action"] not in QUALIFYING_ACTIONS:
            continue
        if row["confidence"] not in QUALIFYING_CONFIDENCE:
            continue

        last_alerted = state.get(symbol)
        if last_alerted:
            days_since = (today - dt.date.fromisoformat(last_alerted)).days
            if days_since < COOLDOWN_DAYS:
                continue

        with sqlite3.connect(app.PREDICTIONS_DB) as conn:
            conn.row_factory = sqlite3.Row
            hist = conn.execute(
                "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions "
                "WHERE status='evaluated' AND symbol = ?",
                (symbol,),
            ).fetchone()
        chance_pct, chance_basis = tb.estimate_chance_right(symbol, hist["n"] or 0, hist["c"] or 0)
        if chance_pct < MIN_CHANCE_PCT:
            continue

        price_phrase = f", currently around {row['start_price']}" if row["start_price"] else ""
        message = (
            f"Hey — this looks like a moment for {symbol} ({row['name']}). 📈\n\n"
            f"It's a {row['action'].split(' / ')[0].lower()} call, {row['confidence']} confidence: "
            f"model sees it moving about {tb.fmt_pct(row['expected_return_pct'])} over the next 30 days"
            f"{price_phrase}.\n\n"
            f"Chance this call is right: about {chance_pct}%, going off {chance_basis}.\n\n"
            f"Not a promise — just flagging it because it cleared a real bar tonight, not a hunch."
        )
        tb.send_message(token, chat_id, message)
        state[symbol] = today.isoformat()
        sent += 1

    _save_state(state)
    return sent


if __name__ == "__main__":
    n = check_and_send()
    print(f"alerts: sent {n} buy alert(s).")
