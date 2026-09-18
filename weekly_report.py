"""Weekly PDF report, delivered to Telegram every Friday night.

Pulls the last 7 days of graded predictions plus the current top pick,
writes a short narrative in the same plain, analytical voice the
Telegram bot uses for advice, and renders it to a one-page PDF that
gets sent as a Telegram document.

Run:  python3 weekly_report.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

import app
import telegram_bot as tb

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "data" / "weekly_reports"


def week_stats() -> dict[str, Any]:
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        overall = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions WHERE status='evaluated'"
        ).fetchone()
        week = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c, AVG(target_error_pct) err FROM predictions "
            "WHERE status='evaluated' AND evaluated_at >= datetime('now', '-7 days')"
        ).fetchone()
        logged_this_week = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        best = conn.execute(
            "SELECT symbol, predicted_direction, target_error_pct, direction_correct FROM predictions "
            "WHERE status='evaluated' AND evaluated_at >= datetime('now', '-7 days') "
            "ORDER BY target_error_pct ASC LIMIT 3"
        ).fetchall()
        worst = conn.execute(
            "SELECT symbol, predicted_direction, target_error_pct, direction_correct FROM predictions "
            "WHERE status='evaluated' AND evaluated_at >= datetime('now', '-7 days') "
            "ORDER BY target_error_pct DESC LIMIT 3"
        ).fetchall()

    return {
        "overall_n": overall["n"] or 0,
        "overall_pct": round((overall["c"] or 0) / overall["n"] * 100) if overall["n"] else None,
        "week_n": week["n"] or 0,
        "week_pct": round((week["c"] or 0) / week["n"] * 100) if week["n"] else None,
        "week_err": week["err"],
        "logged_this_week": logged_this_week,
        "best": [dict(r) for r in best],
        "worst": [dict(r) for r in worst],
    }


def scorecard_learned() -> dict[str, Any] | None:
    try:
        return json.loads((app.DATA_DIR / "scorecard.json").read_text()).get("learned")
    except Exception:
        return None


def recent_daily_notes(days: int = 7) -> list[dict[str, Any]]:
    path = app.DATA_DIR / "daily_reports.json"
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return sorted([e for e in entries if e.get("date", "") >= cutoff], key=lambda e: e["date"])


MIN_POLITICAL_SAMPLE = 20  # don't draw a conclusion from a handful of graded calls


def political_signal_check() -> dict[str, Any] | None:
    """Do stocks with a Trump/administration news mention actually do better
    than everything else? Compares graded direction-accuracy for
    political_mention=1 vs 0. Returns None until there's enough graded
    history on the mention side to say anything meaningful — an untested
    hunch shouldn't get treated as a finding."""
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        mentioned = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c, AVG(target_error_pct) err "
            "FROM predictions WHERE status='evaluated' AND political_mention = 1"
        ).fetchone()
        baseline = conn.execute(
            "SELECT COUNT(*) n, SUM(direction_correct) c FROM predictions "
            "WHERE status='evaluated' AND (political_mention = 0 OR political_mention IS NULL)"
        ).fetchone()
    if not mentioned["n"] or mentioned["n"] < MIN_POLITICAL_SAMPLE or not baseline["n"]:
        return {
            "ready": False,
            "n": mentioned["n"] or 0,
            "needed": MIN_POLITICAL_SAMPLE,
        }
    mentioned_acc = (mentioned["c"] or 0) / mentioned["n"] * 100
    baseline_acc = (baseline["c"] or 0) / baseline["n"] * 100
    return {
        "ready": True,
        "n": mentioned["n"],
        "accuracy_pct": mentioned_acc,
        "baseline_n": baseline["n"],
        "baseline_accuracy_pct": baseline_acc,
        "diff_pct": mentioned_acc - baseline_acc,
    }


def compose_take(
    stats: dict[str, Any],
    pick: dict[str, Any] | None,
    learned: dict[str, Any] | None,
    political: dict[str, Any] | None = None,
) -> str:
    parts = []
    if stats["week_n"]:
        cmp_word = (
            "in line with"
            if stats["overall_pct"] and abs(stats["week_pct"] - stats["overall_pct"]) <= 5
            else ("better than" if stats["week_pct"] > (stats["overall_pct"] or 0) else "worse than")
        )
        parts.append(
            f"This week the model graded {stats['week_n']} calls and got {stats['week_pct']}% right — "
            f"{cmp_word} its {stats['overall_pct']}% lifetime track record."
        )
    else:
        parts.append("No calls came due to be graded this week, so there's no fresh accuracy read yet.")

    if stats["best"]:
        b = stats["best"][0]
        parts.append(f"Best call: {b['symbol']} ({b['predicted_direction']}, missed target by only {b['target_error_pct']:.1f}%).")
    if stats["worst"]:
        w = stats["worst"][0]
        parts.append(f"Furthest off: {w['symbol']} ({w['predicted_direction']}, missed by {w['target_error_pct']:.1f}%).")

    if pick:
        parts.append(
            f"Right now the model's top pick is {pick['symbol']} ({pick.get('name', pick['symbol'])}), "
            f"expecting about {pick.get('expected_return_pct', 0):+.1f}% over {pick.get('horizon_days', 30)} days "
            f"at {pick.get('confidence', 'n/a')} confidence."
        )

    if learned and learned.get("walkforward"):
        wf = learned["walkforward"]
        parts.append(
            f"The live model ({learned.get('model', 'learned')}) is still ahead of the naive baseline in "
            f"walk-forward testing — {wf.get('hit_rate_pct', 0):.0f}% hit rate — so no reason to roll it back."
        )

    if political:
        if political["ready"]:
            lean = "did better" if political["diff_pct"] > 0 else "did worse" if political["diff_pct"] < 0 else "performed about the same"
            parts.append(
                f"On the Trump/government-news idea: stocks with a mention {lean} than everything else — "
                f"{political['accuracy_pct']:.0f}% accuracy on {political['n']} graded calls vs. "
                f"{political['baseline_accuracy_pct']:.0f}% baseline ({political['baseline_n']} calls)."
            )
        else:
            parts.append(
                f"Still gathering evidence on the Trump/government-news idea — only {political['n']} graded "
                f"calls with a mention so far, need {political['needed']} before it's worth a real read."
            )

    parts.append(
        "None of this is financial advice — it's one input, checked against reality every day, not a sure thing."
    )
    return " ".join(parts)


def _ascii(text: str) -> str:
    """Core PDF fonts (Helvetica) only support latin-1 — swap the smart
    punctuation the rest of the app uses for plain ASCII equivalents."""
    for unicode_char, ascii_char in {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...",
    }.items():
        text = text.replace(unicode_char, ascii_char)
    return text.encode("latin-1", "replace").decode("latin-1")


def build_pdf(stats: dict[str, Any], pick: dict[str, Any] | None, take: str, notes: list[dict[str, Any]]) -> Path:
    today = dt.date.today()
    week_start = today - dt.timedelta(days=7)

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    _orig_cell, _orig_multi_cell = pdf.cell, pdf.multi_cell

    def _cell(w, h=0, text="", **kw):
        kw.setdefault("new_x", XPos.LMARGIN)
        kw.setdefault("new_y", YPos.NEXT)
        return _orig_cell(w, h, _ascii(text), **kw)

    def _multi_cell(w, h, text, **kw):
        # A single-line multi_cell otherwise leaves the cursor at the text's
        # end-x instead of wrapping to the margin — force it every time.
        kw.setdefault("new_x", XPos.LMARGIN)
        kw.setdefault("new_y", YPos.NEXT)
        return _orig_multi_cell(w, h, _ascii(text), **kw)

    pdf.cell, pdf.multi_cell = _cell, _multi_cell

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "MarketSignal — Weekly Report")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 8, f"{week_start.isoformat()} to {today.isoformat()}")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "This week's take")
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, take)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "The numbers")
    pdf.set_font("Helvetica", "", 11)
    week_line = f"Calls graded this week: {stats['week_n']}"
    if stats["week_n"]:
        week_line += f" ({stats['week_pct']}% right, avg miss {stats['week_err']:.1f}%)"
    lifetime_line = (
        f"Lifetime: {stats['overall_n']} graded, {stats['overall_pct']}% right"
        if stats["overall_n"]
        else "Lifetime: no graded calls yet"
    )
    pdf.multi_cell(
        0, 6,
        f"Calls logged this week: {stats['logged_this_week']}\n{week_line}\n{lifetime_line}",
    )
    pdf.ln(3)

    if stats["best"] or stats["worst"]:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Notable calls this week")
        pdf.set_font("Helvetica", "", 11)
        for label, rows in (("Best", stats["best"]), ("Off the mark", stats["worst"])):
            for r in rows:
                mark = "right" if r["direction_correct"] else "wrong"
                pdf.multi_cell(0, 6, f"{label}: {r['symbol']} — called {r['predicted_direction']}, {mark}, missed by {r['target_error_pct']:.1f}%")
        pdf.ln(3)

    if notes:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Daily notes this week")
        pdf.set_font("Helvetica", "", 10)
        for entry in notes:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, entry["date"])
            pdf.set_font("Helvetica", "", 10)
            for note in entry.get("notes", []):
                pdf.multi_cell(0, 5, f"  - {note}")
        pdf.ln(2)

    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, "Research only, not financial advice. Generated automatically from graded prediction history.")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"marketsignal-weekly-{today.isoformat()}.pdf"
    pdf.output(str(out_path))
    return out_path


def send_document(token: str, chat_id: str, path: Path, caption: str) -> None:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )

    add_field("chat_id", chat_id)
    add_field("caption", caption)
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n".encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    import urllib.request

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"sendDocument failed: {result}")


def main() -> None:
    tb.load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not set — cannot deliver the report.")
        return

    stats = week_stats()
    rec = app.buy_recommendation()
    pick = rec.get("pick")
    learned = scorecard_learned()
    notes = recent_daily_notes()
    political = political_signal_check()
    take = compose_take(stats, pick, learned, political)

    pdf_path = build_pdf(stats, pick, take, notes)
    print(f"Wrote {pdf_path}")

    send_document(token, chat_id, pdf_path, "Your MarketSignal weekly report is here.")
    print("Sent to Telegram.")


if __name__ == "__main__":
    main()
