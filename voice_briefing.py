"""Short spoken-word market briefing, delivered to Telegram as a voice/audio
message — a 2-3 minute "radio spot" version of the same data the weekly PDF
and world-news scan already produce, for when reading a report isn't what
you want.

Three flavors:
    weekly    - Friday companion to the PDF report (stats, top pick, outlook).
    daily     - quick morning take: today's world-news read + regime + pick.
    breaking  - triggered by world_news.py when the midday scan detects a
                sharp sentiment swing or a market-moving headline; short and
                immediate rather than a full recap.

Uses macOS `say` for text-to-speech (no API key, no internet needed for the
voice itself) and sends the resulting .m4a to Telegram via sendAudio.

Run:  python3 voice_briefing.py weekly --send
      python3 voice_briefing.py daily --send
      python3 voice_briefing.py breaking --send
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import app
import telegram_bot as tb
import weekly_report as wr
import world_news

ROOT = Path(__file__).parent
AUDIO_DIR = ROOT / "data" / "voice_briefings"
VOICE = "Samantha"  # best-quality voice already installed on this Mac


def _ascii(text: str) -> str:
    for a, b in {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"'}.items():
        text = text.replace(a, b)
    return text


def _world_news_take() -> str:
    try:
        rows = world_news._read_log()
    except Exception:
        rows = []
    latest = rows[-1] if rows else None
    if not latest:
        return "No fresh world-news read logged yet today."
    bits = [f"Today's world and political news reads as {latest['label']}."]
    mention = latest.get("political_mention")
    if mention:
        bits.append(f"The headline getting the most attention: {mention['headline']}.")
    return " ".join(bits)


def _regime_take() -> str:
    try:
        import macro
        regime = macro.current()
        tone = "a risk-on" if regime["risk_on"] > 0.1 else "a risk-off" if regime["risk_on"] < -0.1 else "a mixed"
        return (
            f"The overall market backdrop looks like {tone} environment right now, "
            f"with the VIX around {regime['vix_level']:.0f} and the S&P "
            f"{regime['spx_vs_200d_pct']:+.1f}% versus its 200-day average."
        )
    except Exception:
        return ""


def _pick_take() -> str:
    try:
        rec = app.buy_recommendation()
        pick = rec.get("pick")
    except Exception:
        pick = None
    if not pick:
        return "No standout pick right now - nothing is clearing the bar for a real buy call."
    return (
        f"The model's top pick at the moment is {pick['symbol']}, expecting about "
        f"{pick.get('expected_return_pct', 0):+.1f} percent over the next "
        f"{pick.get('horizon_days', 30)} days, at {pick.get('confidence', 'n/a')} confidence."
    )


def build_script(kind: str) -> str:
    today = dt.date.today().strftime("%A, %B %-d")
    if kind == "weekly":
        stats = wr.week_stats()
        pick = app.buy_recommendation().get("pick")
        learned = wr.scorecard_learned()
        political = wr.political_signal_check()
        take = wr.compose_take(stats, pick, learned, political)
        script = (
            f"Hey, this is your Market Signal weekly briefing for {today}. {take} "
            f"{_regime_take()} That's the week. Talk again Friday."
        )
    elif kind == "breaking":
        script = (
            f"Market Signal breaking update, {today}. {_world_news_take()} "
            f"{_regime_take()} {_pick_take()} "
            "Worth a look, but as always, this is one input, not a trade order."
        )
    else:  # daily
        script = (
            f"Good morning, this is your Market Signal daily briefing for {today}. "
            f"{_world_news_take()} {_regime_take()} {_pick_take()} "
            "That's today's read. Have a good one."
        )
    return _ascii(script)


def synthesize(text: str) -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out_path = AUDIO_DIR / f"briefing-{dt.date.today().isoformat()}-{dt.datetime.now().strftime('%H%M%S')}.m4a"
    # Without --data-format, `say` writes raw PCM inside the .m4a container —
    # QuickTime tolerates that but Telegram's player can't decode it and the
    # message arrives silent. Force real AAC encoding instead.
    subprocess.run(
        ["say", "-v", VOICE, "-o", str(out_path), "--data-format=aac", text],
        check=True,
    )
    return out_path


def send(path: Path, caption: str) -> None:
    tb.load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not set — cannot deliver the briefing.")
        return
    tb.send_audio(token, chat_id, path, caption)
    print("Sent to Telegram.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["weekly", "daily", "breaking"])
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    script = build_script(args.kind)
    print(script)
    path = synthesize(script)
    print(f"Wrote {path}")

    if args.send:
        caption = {
            "weekly": "Your Market Signal weekly voice briefing.",
            "daily": "Your Market Signal daily voice briefing.",
            "breaking": "Market Signal breaking news update.",
        }[args.kind]
        send(path, caption)
    return 0


if __name__ == "__main__":
    sys.exit(main())
