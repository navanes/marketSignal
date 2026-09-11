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
from universe import UNIVERSE

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "data" / ".telegram_bot_state.json"
LOCK_PATH = ROOT / ".telegram_bot.lock"

API_BASE = "https://api.telegram.org/bot{token}/{method}"

HELP_TEXT = (
    "Ask me about any stock, ETF, index, or crypto — just type a ticker or name, "
    "like AAPL, tesla, or bitcoin. Full sentences work too, like "
    "\"should I buy bitcoin?\" or \"what's hot right now?\"\n\n"
    "Or tap /menu to pick one from the watch list — and if it's not there, "
    "just type your own.\n\n"
    "Commands:\n"
    "/menu — browse the watch list by category\n"
    "/pick — today's top-ranked market across the whole watch list\n"
    "/track — how often the model has actually been right so far\n"
    "/help — this message\n\n"
    "Research only, not financial advice — this is one input, not a green light."
)

BUCKET_LABELS = {
    "tech": "📱 Tech",
    "semis": "🔌 Chips",
    "autos": "🚗 Autos",
    "software": "💻 Software",
    "financials": "🏦 Financials",
    "healthcare": "🩺 Healthcare",
    "consumer": "🛒 Consumer",
    "energy": "🛢️ Energy",
    "industrials": "🏗️ Industrials",
    "etf": "📊 Indexes",
    "crypto": "₿ Crypto",
}
BUCKET_ORDER = list(dict.fromkeys(row[2] for row in UNIVERSE))
SYMBOLS_BY_BUCKET: dict[str, list[tuple[str, str]]] = {}
for _symbol, _label, _bucket in UNIVERSE:
    SYMBOLS_BY_BUCKET.setdefault(_bucket, []).append((_symbol, _label))


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


def send_message(token: str, chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    params: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    try:
        call_api(token, "sendMessage", params)
    except Exception as exc:
        print(f"send_message failed: {exc}")


def answer_callback(token: str, callback_query_id: str, text: str = "") -> None:
    try:
        call_api(token, "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})
    except Exception as exc:
        print(f"answer_callback failed: {exc}")


def categories_keyboard() -> dict[str, Any]:
    buttons = [
        [{"text": BUCKET_LABELS.get(bucket, bucket.title()), "callback_data": f"cat:{bucket}"}]
        for bucket in BUCKET_ORDER
    ]
    return {"inline_keyboard": buttons}


def symbols_keyboard(bucket: str) -> dict[str, Any]:
    entries = SYMBOLS_BY_BUCKET.get(bucket, [])
    rows = [
        [{"text": f"{label} ({symbol})", "callback_data": f"sym:{symbol}"}]
        for symbol, label in entries
    ]
    rows.append([{"text": "⬅️ Categories", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


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
        return rec.get("note") or "No pick available yet — the watch list hasn't graded enough calls."

    checks = pick.get("checks", 0) or 0
    correct = round(checks * (pick.get("accuracy_pct") or 0) / 100)
    chance_pct, chance_basis = estimate_chance_right(pick["symbol"], checks, correct)

    lines = [
        f"Out of everything I watch, {pick['symbol']} ({pick.get('name', pick['symbol'])}) looks hottest right now.",
        f"My take: BUY. Model sees it moving about {fmt_pct(pick.get('expected_return_pct'))} over "
        f"{pick.get('horizon_days', 30)} days, confidence {pick.get('confidence', 'n/a')}.",
        f"Chance this call is right: about {chance_pct}% (risk of being wrong: about {100 - chance_pct}%), "
        f"from {chance_basis}.",
    ]
    runner = rec.get("runner_up")
    if runner:
        lines.append(f"Runner-up: {runner['symbol']} ({fmt_pct(runner.get('expected_return_pct'))} expected).")
    lines.append("Not a promise, just the model's best read — treat this as one opinion, not a sure thing.")
    return "\n".join(lines)


_RECOMMEND_INTENT = re.compile(
    r"\b(hot|hottest|recommend|suggest|best|top pick|what should i (buy|invest|get)|"
    r"which (one|market|stock|coin|crypto)|what('?s| is) good|any (tips|ideas|advice))\b",
    re.IGNORECASE,
)


def looks_like_recommend_request(text: str) -> bool:
    return bool(_RECOMMEND_INTENT.search(text))


_EXTRA_ALIASES = {"bitcoin": "BTC-USD", "btc": "BTC-USD", "ethereum": "ETH-USD", "eth": "ETH-USD"}
_NAME_LOOKUP = [(symbol, label) for symbol, label, _bucket in UNIVERSE]


def extract_known_symbol(text: str) -> str | None:
    """Pull a watch-list ticker/name out of a full sentence, e.g. 'should I
    buy tesla right now?' -> 'TSLA'. Checked before falling back to the
    recommend-intent check or treating the whole message as a raw ticker."""
    low = text.lower()
    for symbol, label in _NAME_LOOKUP:
        if re.search(rf"\b{re.escape(label.lower())}\b", low) or re.search(rf"\b{re.escape(symbol.lower())}\b", low):
            return symbol
    for alias, symbol in _EXTRA_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return symbol
    return None


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
        elif low == "/menu":
            send_message(token, chat_id, "Pick a category (or just type any ticker/name instead):", categories_keyboard())
        elif low == "/pick":
            send_message(token, chat_id, format_pick_reply())
        elif low == "/track":
            send_message(token, chat_id, format_track_reply())
        else:
            query = re.sub(r"^/ask\s+", "", query, flags=re.IGNORECASE)
            query = re.sub(r"^/+", "", query)  # e.g. "/ETH" typed as if it were a command
            known = extract_known_symbol(query)
            if known:
                send_message(token, chat_id, format_research_reply(known))
            elif looks_like_recommend_request(query):
                send_message(token, chat_id, format_pick_reply())
            else:
                send_message(token, chat_id, format_research_reply(query))
    except ValueError as exc:
        send_message(token, chat_id, f"Couldn't read that: {exc}")
    except urllib.error.URLError:
        send_message(token, chat_id, "Couldn't reach the market data source, try again in a bit.")
    except Exception as exc:
        print(f"handle_text error: {exc}")
        send_message(token, chat_id, "Something went wrong looking that up.")


def handle_callback(token: str, chat_id: int, callback_id: str, data: str) -> None:
    answer_callback(token, callback_id)
    try:
        if data == "menu":
            send_message(token, chat_id, "Pick a category (or just type any ticker/name instead):", categories_keyboard())
        elif data.startswith("cat:"):
            bucket = data.removeprefix("cat:")
            label = BUCKET_LABELS.get(bucket, bucket.title())
            send_message(token, chat_id, f"{label} — pick one:", symbols_keyboard(bucket))
        elif data.startswith("sym:"):
            symbol = data.removeprefix("sym:")
            send_message(token, chat_id, format_research_reply(symbol))
    except Exception as exc:
        print(f"handle_callback error: {exc}")
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

            callback = update.get("callback_query")
            if callback:
                chat_id = callback["message"]["chat"]["id"]
                if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
                    print(f"Ignoring callback from unauthorized chat {chat_id}")
                    answer_callback(token, callback["id"])
                    continue
                handle_callback(token, chat_id, callback["id"], callback.get("data", ""))
                continue

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
