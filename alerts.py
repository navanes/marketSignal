"""Proactive "this looks like a real buy" pings to Telegram.

Runs at the end of the nightly scan (see scan.py). Checks EVERY tracked
horizon for that night's freshly logged calls — daily through annual — and
pushes an unsolicited message for a symbol the moment any of them clears a
real bar: a genuine Buy/Strong Buy action at high or medium-high confidence,
not just "Watch" or a mixed signal. The message says which specific
timeframe(s) qualified (a stock can look like a short-term trade, a
long-term hold, both, or neither) rather than assuming one fixed horizon. A
per-symbol cooldown stops the same name from re-alerting every single night
while it stays in a buy zone.

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


def _tonights_candidates() -> dict[str, list[dict[str, Any]]]:
    """Every horizon logged tonight, grouped by symbol."""
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, name, action, confidence, predicted_direction, "
            "expected_return_pct, start_price, horizon_days FROM predictions "
            "WHERE date(created_at) = date('now')"
        ).fetchall()
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(dict(row))
    return by_symbol


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

    for symbol, rows in _tonights_candidates().items():
        last_alerted = state.get(symbol)
        if last_alerted and (today - dt.date.fromisoformat(last_alerted)).days < COOLDOWN_DAYS:
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

        qualifying = [
            row for row in rows
            if row["action"] in QUALIFYING_ACTIONS
            and row["confidence"] in QUALIFYING_CONFIDENCE
            # The action label (technicals/momentum) and the forecast
            # (expected_return_pct) come from different parts of the model and
            # can disagree — e.g. a "Buy" action next to a negative expected
            # move. Only alert where they actually agree.
            and (row["expected_return_pct"] or 0) > 0
        ]
        if not qualifying:
            continue
        qualifying.sort(key=lambda r: r["horizon_days"])

        name = rows[0]["name"]
        price = rows[0]["start_price"]
        price_phrase = f" It's currently around {price}." if price else ""

        def _timeframe_line(row: dict[str, Any]) -> str:
            label = app.HORIZON_LABELS.get(row["horizon_days"], f"{row['horizon_days']}d")
            action_word = row["action"].split(" / ")[0]
            move = tb.fmt_pct(row["expected_return_pct"])
            return f"• {label}: {action_word}, {move}, {row['confidence']} confidence"

        timeframe_lines = "\n".join(_timeframe_line(row) for row in qualifying)
        shortest, longest = qualifying[0]["horizon_days"], qualifying[-1]["horizon_days"]
        if shortest == longest:
            span_note = ""
        elif longest <= 30:
            span_note = " Looks like more of a short-term move than a long-term hold."
        elif shortest >= 90:
            span_note = " Looks like more of a long-term hold than a quick trade."
        else:
            span_note = " Clears the bar on both short and long timeframes."

        message = (
            f"Hey — this looks like a moment for {symbol} ({name}). 📈{price_phrase}\n\n"
            f"Clears the bar on:\n{timeframe_lines}\n"
            f"{span_note}\n\n"
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
