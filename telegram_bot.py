"""MarketSignal advice bot.

Ask it about any ticker, crypto, or index in a Telegram DM and it runs the
same research pipeline the web app uses (app.research) and replies with the
model's call — direction, expected move, confidence — plus its real,
measured track record so the number isn't taken as gospel.

Env (in .env, next to this file):
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_ALLOWED_CHAT_ID=...      # only this chat gets replies

Run:
    python3 telegram_bot.py
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import app

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "data" / ".telegram_bot_state.json"
LOCK_PATH = ROOT / ".telegram_bot.lock"

API_BASE = "https://api.telegram.org/bot{token}/{method}"

HELP_TEXT = (
    "Ask me about any stock, ETF, index, or crypto — just type a ticker or name, "
    "like AAPL, tesla, or bitcoin.\n\n"
    "Commands:\n"
    "/pick — today's top-ranked market across the whole watch list\n"
    "/track — how often the model has actually been right so far\n"
    "/help — this message\n\n"
    "Research only, not financial advice — this is one input, not a green light."
)


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def acquire_lock() -> None:
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another instance is already running (lock held). Exiting.")
        raise SystemExit(0)
    globals()["_LOCK_FILE"] = lock_file  # keep it open/held for process lifetime


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"offset": 0}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def call_api(token: str, method: str, params: dict[str, Any] | None = None, timeout: int = 35) -> dict[str, Any]:
    url = API_BASE.format(token=token, method=method)
    data = json.dumps(params or {}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(token: str, chat_id: int | str, text: str) -> None:
    try:
        call_api(token, "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    except Exception as exc:
        print(f"send_message failed: {exc}")


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def live_accuracy_line(symbol: str | None = None) -> str:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        if symbol:
            row = conn.execute(
                "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions "
                "WHERE status='evaluated' AND symbol = ?",
                (symbol,),
            ).fetchone()
        else:
            row = None
        overall = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions WHERE status='evaluated'"
        ).fetchone()

    parts = []
    if overall["n"]:
        pct = round((overall["c"] or 0) / overall["n"] * 100)
        parts.append(f"model's been right {pct}% of the time overall ({overall['n']} graded calls)")
    if row and row["n"]:
        pct = round((row["c"] or 0) / row["n"] * 100)
        parts.append(f"{pct}% on {symbol} specifically ({row['n']} calls)")
    return "Track record: " + "; ".join(parts) + "." if parts else "No track record graded yet."


_VERDICTS = {
    "Strong Buy / Accumulate": ("BUY", "the setup looks strong right now"),
    "Buy / Accumulate": ("BUY", "the setup leans in your favor"),
    "Watch / Selective Buy": ("WAIT", "it's leaning positive but not clearly enough yet"),
    "Strong Avoid / Reduce": ("SELL / AVOID", "the setup looks weak right now"),
    "Avoid / Consider Reducing": ("SELL / AVOID", "the setup leans against it"),
    "Hold / Wait": ("HOLD", "nothing here is clear enough to act on"),
    "Neutral / Need More Evidence": ("WAIT", "it's too mixed to call either way"),
}


def _scorecard_years() -> int | None:
    try:
        return json.loads((app.DATA_DIR / "scorecard.json").read_text()).get("years")
    except Exception:
        return None


def estimate_chance_right(symbol: str, live_n: int, live_correct: int) -> tuple[int, str]:
    """Rough % chance this specific call is right, blending this symbol's
    live graded track record with its 5-year backtest accuracy — the same
    method the dashboard's 'today's pick' uses. Returns (pct, basis note)."""
    backtest_acc: dict[str, float] = {}
    try:
        backtest_acc = json.loads((app.DATA_DIR / "scorecard.json").read_text()).get("by_symbol_all") or {}
    except Exception:
        pass
    bt = backtest_acc.get(symbol)

    if live_n >= 5:
        live_rate = live_correct / live_n
        blended = 0.7 * live_rate + 0.3 * ((bt / 100.0) if bt is not None else live_rate)
        basis = f"blend of {live_n} real checks on {symbol} and its 5-year backtest"
    elif bt is not None:
        blended = bt / 100.0
        basis = f"{symbol}'s 5-year backtest (not enough live checks yet)"
    else:
        blended = 0.5
        basis = "a coin-flip baseline (no history on this one yet)"
    return round(blended * 100), basis


def format_research_reply(query: str) -> str:
    data = app.research(query, "6mo", app.DEFAULT_HORIZON_DAYS)
    quote = data["quote"]
    signal = data["signal"]
    fc = data.get("forecast_model", {})
    symbol = quote.get("symbol", query.upper())
    name = quote.get("name", symbol)
    price = quote.get("price")
    currency = quote.get("currency") or ""

    verdict, casual_reason = _VERDICTS.get(signal.get("action", ""), ("WAIT", "it's not a clear signal"))
    direction = fc.get("direction", "sideways")
    move = fc.get("expected_return_pct")
    move_phrase = (
        f"it might move about {fmt_pct(move)} over the next {app.DEFAULT_HORIZON_DAYS} days"
        if move is not None
        else "it's not expecting a big move either way"
    )

    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions "
            "WHERE status='evaluated' AND symbol = ?",
            (symbol,),
        ).fetchone()

    years = _scorecard_years()
    history_bits = []
    if years:
        history_bits.append(f"{years} years of backtested history")
    if row["n"]:
        pct = round((row["c"] or 0) / row["n"] * 100)
        history_bits.append(f"{row['n']} real checks on {symbol} so far ({pct}% of those were right)")
    history_phrase = " and ".join(history_bits) if history_bits else "not much graded history yet"

    price_phrase = f"{symbol} is at {price} {currency}".strip() + ". " if price else ""

    chance_pct, chance_basis = estimate_chance_right(symbol, row["n"] or 0, row["c"] or 0)

    message = (
        f"{price_phrase}"
        f"My take: {verdict}. Based on {history_phrase}, {casual_reason} — {move_phrase}, "
        f"confidence is {fc.get('confidence', 'n/a')}.\n\n"
        f"Chance this call is right: about {chance_pct}% (risk of being wrong: about {100 - chance_pct}%), "
        f"from {chance_basis}.\n\n"
        f"Not a promise, just the model's best read — treat this as one opinion, not a sure thing."
    )
    return message


def format_pick_reply() -> str:
    rec = app.buy_recommendation()
    pick = rec.get("pick")
    if not pick:
        return rec.get("note") or "No pick available yet."
    lines = [
        f"Top pick right now: {pick['symbol']} — {pick.get('name', pick['symbol'])}",
        f"Model sees {fmt_pct(pick.get('expected_return_pct'))} over {pick.get('horizon_days', 30)}d, "
        f"confidence {pick.get('confidence', 'n/a')}",
    ]
    if pick.get("accuracy_pct") is not None:
        lines.append(f"Hit rate on this symbol so far: {round(pick['accuracy_pct'])}% ({pick.get('checks', 0)} checks)")
    runner = rec.get("runner_up")
    if runner:
        lines.append(f"Runner-up: {runner['symbol']} ({fmt_pct(runner.get('expected_return_pct'))})")
    lines.append(live_accuracy_line())
    lines.append("Research only, not financial advice.")
    return "\n".join(lines)


def format_track_reply() -> str:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c, AVG(target_error_pct) err "
            "FROM predictions WHERE status='evaluated'"
        ).fetchone()
    evaluated = row["n"] or 0
    pending = total - evaluated
    lines = [f"{evaluated} graded calls so far ({pending} still pending), {total} logged in total."]
    if evaluated:
        acc = round((row["c"] or 0) / evaluated * 100)
        lines.append(f"Direction right {acc}% of the time.")
        if row["err"] is not None:
            lines.append(f"Average miss on the target price: {row['err']:.1f}%.")
    else:
        lines.append("Not enough graded calls yet.")
    return "\n".join(lines)


def handle_text(token: str, chat_id: int, text: str) -> None:
    query = text.strip()
    if not query:
        return
    low = query.lower()
    try:
        if low in ("/start", "/help"):
            send_message(token, chat_id, HELP_TEXT)
        elif low == "/pick":
            send_message(token, chat_id, format_pick_reply())
        elif low == "/track":
            send_message(token, chat_id, format_track_reply())
        else:
            query = re.sub(r"^/ask\s+", "", query, flags=re.IGNORECASE)
            query = re.sub(r"^/+", "", query)  # e.g. "/ETH" typed as if it were a command
            send_message(token, chat_id, format_research_reply(query))
    except ValueError as exc:
        send_message(token, chat_id, f"Couldn't read that: {exc}")
    except urllib.error.URLError:
        send_message(token, chat_id, "Couldn't reach the market data source, try again in a bit.")
    except Exception as exc:
        print(f"handle_text error: {exc}")
        send_message(token, chat_id, "Something went wrong looking that up.")


def poll(token: str, allowed_chat_id: str | None) -> None:
    state = load_state()
    print("MarketSignal bot polling...")
    while True:
        try:
            resp = call_api(
                token, "getUpdates", {"offset": state["offset"], "timeout": 30}, timeout=40
            )
        except Exception as exc:
            print(f"getUpdates failed: {exc}")
            time.sleep(5)
            continue

        for update in resp.get("result", []):
            state["offset"] = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message")
            if not message or "text" not in message:
                continue
            chat_id = message["chat"]["id"]
            if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
                print(f"Ignoring message from unauthorized chat {chat_id}")
                continue
            handle_text(token, chat_id, message["text"])

        save_state(state)


def main() -> None:
    load_env()
    acquire_lock()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set in .env — exiting.")
        raise SystemExit(1)
    allowed_chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip() or None
    if not allowed_chat_id:
        print("Warning: TELEGRAM_ALLOWED_CHAT_ID not set — the bot will reply to anyone.")
    poll(token, allowed_chat_id)


if __name__ == "__main__":
    sys.exit(main())
