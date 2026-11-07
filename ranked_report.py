"""Fresh, on-demand ranked research PDFs. No probability-of-profit claims."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import sqlite3
import uuid
from pathlib import Path

import app
from universe import UNIVERSE

HORIZONS = [30, 90, 180, 365]
CONFIDENCE = {"low": 0, "low-medium": 1, "medium": 2, "medium-high": 3, "high": 4}


def collect(crypto_only=False):
    from telegram_bot import _signal_bucket
    universe = [r for r in UNIVERSE if not crypto_only or r[2] == "crypto"]
    buckets = {"BUY": [], "HOLD": [], "SELL": []}
    excluded = []
    today = dt.datetime.now(dt.timezone.utc).date()
    with sqlite3.connect(app.PREDICTIONS_DB) as conn:
        history = {(s, h, d): (n, c or 0) for s, h, d, n, c in conn.execute(
            "SELECT symbol,horizon_days,predicted_direction,COUNT(*),SUM(direction_correct) "
            "FROM predictions WHERE status='evaluated' GROUP BY symbol,horizon_days,predicted_direction"
        )}
    def scan(entry):
        symbol, name, bucket = entry
        try:
            data = app.multi_horizon_signals(symbol, "1y", HORIZONS)
            date = dt.date.fromisoformat(data.get("price_date") or "")
            if not 0 <= (today - date).days <= (2 if bucket == "crypto" else 4):
                raise ValueError("stale price history")
            price = data.get("price")
            if not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
                raise ValueError("missing price")
            scenarios = data["horizons"]
            if not all(isinstance(r.get("expected_return_pct"), (float, int)) and math.isfinite(r["expected_return_pct"]) for r in scenarios):
                raise ValueError("missing forecast")
            anchor = next(r for r in scenarios if r["horizon_days"] == 30)
            action = _signal_bucket(anchor["action"], anchor["direction"])
            n, c = history.get((symbol, 30, anchor["direction"]), (0, 0))
            # Shrink sparse historical evidence toward 50%; this is a rank input,
            # never displayed as a calibrated probability of future success.
            evidence = (c + 10) / (n + 20)
            return action, dict(symbol=symbol, name=name, category=bucket, price=price,
                price_date=str(date), confidence=anchor["confidence"], samples=n,
                accuracy=round(100*c/n, 1) if n else None, evidence=evidence,
                scenarios=scenarios, move=anchor["expected_return_pct"])
        except Exception as exc:
            return "EXCLUDED", {"symbol": symbol, "reason": str(exc)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for action, row in pool.map(scan, universe):
            if action == "EXCLUDED":
                excluded.append(row)
            else:
                buckets[action].append(row)
    for action, rows in buckets.items():
        rows.sort(key=lambda r: (r["evidence"], CONFIDENCE.get(r["confidence"], -1),
            r["move"] if action == "BUY" else -r["move"] if action == "SELL" else -abs(r["move"])), reverse=True)
    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": "Crypto" if crypto_only else "Stocks, ETFs, crypto, gold and silver futures",
            "buckets": buckets, "excluded": excluded, "scanned": len(universe)}


def build_report(crypto_only=False, data=None):
    from fpdf import FPDF, FontFace
    from fpdf.enums import XPos, YPos
    data = collect(crypto_only) if data is None else data
    directory = Path(__file__).parent / "data" / "ranked_reports"
    directory.mkdir(exist_ok=True)
    path = directory / f"top10-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.pdf"
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(False)
    pdf.add_page()
    def line(text, size=10, bold=False, height=6, color=(80, 88, 100)):
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.cell(0, height, text.encode("latin-1", "replace").decode("latin-1"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    line("MARKET SIGNAL", 23, True, 12, (24, 35, 50))
    line(f"{data['generated_at'][:10]}  |  {data['scanned']} assets scanned  |  Top 10 per signal", 10, height=7)
    pdf.ln(3)
    total = sum(min(10, len(rows)) for rows in data["buckets"].values())
    row_height = 7 if total <= 20 else 5
    colors = {"BUY": (24, 119, 81), "HOLD": (94, 104, 120), "SELL": (174, 57, 57)}
    widths = [43, 31, 28, 28, 28, 28]
    def table(rows):
        pdf.set_font("Helvetica", size=11 if total <= 20 else 10)
        pdf.set_text_color(30, 40, 53)
        with pdf.table(col_widths=widths, line_height=row_height, width=186,
                       text_align="LEFT", borders_layout="HORIZONTAL_LINES",
                       headings_style=FontFace(emphasis="BOLD", color=(85, 94, 108))) as t:
            for cells in [["Asset", "Confidence", "1 month", "3 months", "6 months", "12 months"]] + rows:
                tr = t.row()
                for cell in cells:
                    tr.cell(cell)
    for action, rows in data["buckets"].items():
        line(action, 13, True, 8, colors[action])
        if rows:
            table([[f"{i}. {r['symbol']}", (r['confidence'] or "Unknown").title()] +
                   [f"{v['expected_return_pct']:+.1f}%" for v in r['scenarios']]
                   for i, r in enumerate(rows[:10], 1)])
        else:
            line("No qualifying signals.", 11, height=7)
        pdf.ln(4)
    metals = [(action, r) for action, rows in data["buckets"].items() for r in rows if r['category'] == 'metals']
    if metals:
        line("GOLD & SILVER", 13, True, 8, (24, 35, 50))
        table([[f"{'Gold' if r['symbol'] == 'GC=F' else 'Silver'} / {action}", (r['confidence'] or 'Unknown').title()] +
               [f"{v['expected_return_pct']:+.1f}%" for v in r['scenarios']] for action, r in metals])
    pdf.ln(5)
    line("Returns are model estimates. Confidence is a model label, not a success rate.", 9, height=5)
    line("HOLD includes mixed signals. Gold and silver use futures. Research only.", 9, height=5)
    if data['excluded']:
        line(f"Unavailable / stale: {len(data['excluded'])} assets excluded.", 9, height=5)
    pdf.output(str(path))
    path.with_suffix('.json').write_text(json.dumps(data, indent=2))
    return path, f"Top 10 BUY / HOLD / SELL | {data['scope']} | {len(data['excluded'])} excluded. Model research, not guaranteed returns."


def _direction(value):
    return {"up": "UP", "down": "DOWN", "sideways": "FLAT"}.get(value, "?")


if __name__ == "__main__":
    print(build_report()[0])
