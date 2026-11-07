"""The actual "learn from history" loop: look back at graded predictions
(1 week / 1 month / 2 months / all-time), compute REAL accuracy (not the
training-time walk-forward self-report), compare it against what the live
model claims, and tell a human when the live signal isn't holding up.

Why this exists: train.py's weekly retrain only promotes a challenger model
if it beats the *incumbent's own historical walk-forward numbers*. If the
incumbent's walk-forward was itself inflated (e.g. it was validated on a
historical stretch that happened to suit it, like a one-sided bull run), that
inflated bar can never be beaten again — the model goes stale and nothing
"learns" from new data even though retrain.sh keeps running every Sunday.
This script closes that gap by grading against reality instead.

Run:  python3 review_accuracy.py [--send]
  --send   also push the summary to Telegram (needs TELEGRAM_BOT_TOKEN /
           TELEGRAM_ALLOWED_CHAT_ID in .env, same as weekly_report.py)

Intended schedule: monthly, via deploy/model-review.sh (see that file).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

import app

HISTORY_PATH = app.DATA_DIR / "accuracy_history.json"
WINDOWS = [("7d", 7), ("30d", 30), ("60d", 60), ("90d", 90), ("all", None)]
MIN_SAMPLE = 20  # below this, a window's accuracy is noise — don't act on it


def _rows_since(conn: sqlite3.Connection, days: int | None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if days is None:
        where, params = "status = 'evaluated'", ()
    else:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        where, params = "status = 'evaluated' AND evaluated_at >= ?", (cutoff,)
    return conn.execute(
        f"""SELECT direction_correct, expected_return_pct, actual_change_pct, confidence, action, horizon_days
            FROM predictions WHERE {where}""",
        params,
    ).fetchall()


def _window_stats(rows: list[sqlite3.Row]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    correct = sum(1 for r in rows if r["direction_correct"])
    sign_rows = [r for r in rows if r["expected_return_pct"] is not None and r["actual_change_pct"] is not None]
    sign_match = (
        sum(1 for r in sign_rows if (r["expected_return_pct"] > 0) == (r["actual_change_pct"] > 0))
        if sign_rows else 0
    )
    high_conf = [r for r in rows if r["confidence"] == "high"]
    high_correct = sum(1 for r in high_conf if r["direction_correct"])
    return {
        "n": n,
        "band_accuracy_pct": round(100 * correct / n, 1),
        "raw_sign_match_pct": round(100 * sign_match / len(sign_rows), 1) if sign_rows else None,
        "high_confidence_n": len(high_conf),
        "high_confidence_accuracy_pct": round(100 * high_correct / len(high_conf), 1) if high_conf else None,
    }


def gather() -> dict[str, Any]:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        windows = {label: _window_stats(_rows_since(conn, days)) for label, days in WINDOWS}

    model = {}
    try:
        m = json.loads(app.MODEL_PATH.read_text())
        model = {
            "model": m.get("model"),
            "trained_at": m.get("trained_at"),
            "claimed_walkforward_hit_rate_pct": (m.get("walkforward") or {}).get("hit_rate_pct"),
        }
    except Exception:
        pass

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "windows": windows,
        "champion": model,
    }


def _trend_note(history: list[dict[str, Any]], current: dict[str, Any]) -> str:
    """Compare this run's 30d accuracy against the last recorded run's, so
    'learning over time' is visible rather than just a snapshot."""
    cur_30 = current["windows"].get("30d", {})
    if cur_30.get("n", 0) < MIN_SAMPLE:
        return f"Only {cur_30.get('n', 0)} graded calls in the last 30 days — too few to call a trend yet."
    if not history:
        return "First recorded checkpoint — nothing to compare against yet."
    prev = history[-1]
    prev_30 = prev.get("windows", {}).get("30d", {})
    if prev_30.get("n", 0) < MIN_SAMPLE or prev_30.get("band_accuracy_pct") is None:
        return "Previous checkpoint didn't have enough data to compare against."
    delta = cur_30["band_accuracy_pct"] - prev_30["band_accuracy_pct"]
    direction = "improved" if delta > 1 else "worsened" if delta < -1 else "held steady"
    return (
        f"30-day accuracy {direction}: {prev_30['band_accuracy_pct']}% "
        f"({prev.get('generated_at', '')[:10]}) -> {current['windows']['30d']['band_accuracy_pct']}% "
        f"(now), {delta:+.1f}pt."
    )


def _today_market_note() -> str:
    """Today's market condition, so the monthly check-in answers both halves
    of 'how accurate were we, and what's the market doing now' in one message."""
    bits = []
    try:
        import macro
        regime = macro.current()
        bits.append(
            f"Regime: {regime['regime']} (risk score {regime['risk_on']:+.2f}), "
            f"VIX {regime['vix_level']}, S&P vs 200d {regime['spx_vs_200d_pct']:+.1f}%."
        )
    except Exception:
        pass
    try:
        import world_news
        summary = world_news.accuracy_summary()
        rows = world_news._read_log()
        if rows:
            latest = rows[-1]
            bits.append(f"World/political news today: {latest['label']} (score {latest['score']:+.2f}).")
            if latest.get("political_mention"):
                bits.append(f"Flagged: \"{latest['political_mention']['headline']}\"")
        if summary.get("n", 0) >= 10:
            bits.append(f"World-news-vs-SPY track record: {summary['accuracy_pct']}% ({summary['n']} graded days).")
    except Exception:
        pass
    return "\n".join(bits)


def format_message(report: dict[str, Any], trend_note: str) -> str:
    w = report["windows"]
    champ = report.get("champion", {})
    lines = ["📊 Market Signal — accuracy check-in", ""]
    for label, _ in WINDOWS:
        stats = w.get(label, {})
        if stats.get("n", 0) == 0:
            lines.append(f"{label:>4}: no graded calls yet")
            continue
        flag = " ⚠️ small sample" if stats["n"] < MIN_SAMPLE else ""
        lines.append(
            f"{label:>4}: {stats['n']} calls, band-accuracy {stats['band_accuracy_pct']}%, "
            f"raw direction match {stats.get('raw_sign_match_pct')}%{flag}"
        )
    lines.append("")
    lines.append(trend_note)
    if champ.get("model"):
        cw = w.get("30d", {})
        claim = champ.get("claimed_walkforward_hit_rate_pct")
        live = cw.get("band_accuracy_pct")
        lines.append("")
        lines.append(
            f"Live model: {champ['model']} (trained {str(champ.get('trained_at'))[:10]}). "
            f"Training claimed {claim}% hit rate; last-30d live is {live}%."
        )
        if claim is not None and live is not None and claim - live > 10 and cw.get("n", 0) >= MIN_SAMPLE:
            lines.append(
                "⚠️ Live accuracy is well below what training claimed — the champion's bar "
                "may be inflated from backtest-era luck. Flagging for a fresh look."
            )
    market_note = _today_market_note()
    if market_note:
        lines.append("")
        lines.append("Today's market condition:")
        lines.append(market_note)
    lines.append("")
    lines.append("Next check-in: 1st of next month. Reply here or in Claude Code anytime for the full report.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    app.evaluate_pending_predictions()
    report = gather()

    history: list[dict[str, Any]] = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text())
        except Exception:
            history = []

    trend_note = _trend_note(history, report)
    message = format_message(report, trend_note)
    print(message)

    history.append(report)
    history = history[-24:]  # ~2 years of monthly checkpoints is plenty
    HISTORY_PATH.write_text(json.dumps(history, indent=2))

    if args.send:
        import telegram_bot as tb
        tb.load_env()
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
        if token and chat_id:
            tb.send_message(token, chat_id, message)
            print("\n(sent to Telegram)")
        else:
            print("\n(TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not set — not sent)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
