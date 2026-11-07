"""Daily macro/political news pass — separate from the per-symbol headline
check in app.news_tilt(). That one only sees headlines that come up in a
"{SYMBOL} stock market" search, so a presidential speech, a tariff
announcement, or a Fed statement rarely surfaces there unless it happens to
name a specific ticker. This module searches broad, market-wide topics and
a short list of people whose public statements reliably move markets, scores
the day's tone the same way app.news_sentiment() scores per-symbol headlines,
and — critically — logs the call so it can be GRADED later against what the
market (SPY) actually did, closing the same learn-from-history loop as
review_accuracy.py.

Run:  python3 world_news.py scan     # fetch today's world news, score it, log it
      python3 world_news.py grade    # grade past day's calls against SPY's actual move
      python3 world_news.py report   # print a short human summary
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import app

LOG_PATH = app.DATA_DIR / "world_news_log.jsonl"

# Broad, market-moving topics — not tied to any one symbol.
WORLD_QUERIES = [
    "Federal Reserve interest rates",
    "stock market today",
    "tariffs trade war",
    "inflation report",
    "recession economy",
    "oil prices OPEC",
    "geopolitical conflict markets",
]

# People whose public remarks (speeches, interviews, posts on X/Twitter) are
# reliably market-moving. Keep this short and specific — the point is
# signal, not a general news feed.
WATCHED_FIGURES = [
    "President Trump",
    "Jerome Powell Fed Chair",
    "Treasury Secretary",
    "ECB president",
]

GRADE_HORIZON_DAYS = 1  # grade same-day news against next trading day's SPY move


def _raw_google_news(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Like app.google_news(), but without appending ' stock market' to the
    query — we want the headline as-is, since these queries are already
    market-framed or are a named person, not a ticker."""
    search = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={search}&hl=en-US&gl=US&ceid=US:en"
    xml_text = app.fetch_text(url)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title") or ""
        source = item.findtext("source") or ""
        published = item.findtext("pubDate") or ""
        published_iso = ""
        if published:
            import email.utils
            try:
                published_iso = email.utils.parsedate_to_datetime(published).isoformat()
            except (TypeError, ValueError):
                published_iso = published
        items.append({"title": title, "source": source, "published": published_iso, "query": query})
    return items


def gather_world_news() -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    for query in WORLD_QUERIES + WATCHED_FIGURES:
        try:
            articles.extend(_raw_google_news(query))
        except Exception as exc:
            print(f"  {query}: fetch failed ({exc})")
    return articles


def scan() -> dict[str, Any]:
    """Fetch today's world/political news, score it, and log the call so
    grade() can check it against reality later."""
    app.DATA_DIR.mkdir(exist_ok=True)
    articles = gather_world_news()
    sentiment = app.news_sentiment(articles)
    political = app.detect_political_mention(articles, hours=24)

    try:
        spy = app.yahoo_chart("SPY", "5d")
        spy_price = app.price_series(spy)[-1]["close"]
    except Exception:
        spy_price = None

    record = {
        "date": dt.date.today().isoformat(),
        "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_articles": len(articles),
        "score": sentiment["score"],
        "label": sentiment["label"],
        "events": sentiment["events"],
        "political_mention": political,
        "top_headlines": [a["title"] for a in articles[:8]],
        "spy_price_at_call": spy_price,
        "graded": False,
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _read_log() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    rows = []
    for line in LOG_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def grade() -> dict[str, Any]:
    """For every logged day old enough to have a next trading day's SPY close,
    record whether a 'positive' world-news day was actually followed by SPY
    going up (and vice versa for 'negative'). This is the macro-news
    equivalent of predictions.direction_correct."""
    rows = _read_log()
    if not rows:
        return {"graded": 0}
    try:
        spy_series = app.price_series(app.yahoo_chart("SPY", "3mo"))
    except Exception:
        return {"graded": 0, "error": "could not fetch SPY"}
    closes = {r["date"]: r["close"] for r in spy_series}
    dates_sorted = sorted(closes)

    graded = 0
    for row in rows:
        if row.get("graded") or row.get("label") == "neutral":
            continue
        call_date = row["date"]
        future_dates = [d for d in dates_sorted if d > call_date]
        if len(future_dates) < GRADE_HORIZON_DAYS:
            continue
        target_date = future_dates[GRADE_HORIZON_DAYS - 1]
        start_price = row.get("spy_price_at_call") or closes.get(call_date)
        end_price = closes.get(target_date)
        if not start_price or not end_price:
            continue
        actual_move_pct = (end_price - start_price) / start_price * 100
        predicted_up = row["label"] == "positive"
        correct = (actual_move_pct > 0) == predicted_up
        row["graded"] = True
        row["actual_move_pct"] = round(actual_move_pct, 3)
        row["correct"] = correct
        graded += 1

    if graded:
        with LOG_PATH.open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    return {"graded": graded, "total_logged": len(rows)}


def accuracy_summary() -> dict[str, Any]:
    rows = [r for r in _read_log() if r.get("graded")]
    n = len(rows)
    if n == 0:
        return {"n": 0}
    correct = sum(1 for r in rows if r.get("correct"))
    return {"n": n, "accuracy_pct": round(100 * correct / n, 1)}


def report() -> str:
    summary = accuracy_summary()
    rows = _read_log()
    latest = rows[-1] if rows else None
    lines = ["🌎 World/political news read — Market Signal"]
    if latest:
        lines.append(f"Today ({latest['date']}): {latest['label']} (score {latest['score']:+.2f}), "
                      f"{latest['n_articles']} headlines scanned.")
        if latest.get("political_mention"):
            lines.append(f"Flagged: \"{latest['political_mention']['headline']}\"")
    if summary["n"] >= 10:
        lines.append(f"Track record: {summary['accuracy_pct']}% right on next-day SPY direction "
                      f"({summary['n']} graded calls).")
    else:
        lines.append(f"Track record: only {summary['n']} graded calls so far — too early to trust.")
    return "\n".join(lines)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        record = scan()
        print(json.dumps(record, indent=2)[:2000])
    elif cmd == "grade":
        print(grade())
    elif cmd == "report":
        print(report())
    else:
        print("usage: world_news.py {scan|grade|report}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
