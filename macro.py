"""Market-wide regime state, aligned by date.

Everything here comes from the same Yahoo chart API the price code already
uses, so it's fully historical — these features can go straight into the model
and be walk-forward validated (unlike fundamentals/news, which are live-only).

State per date:
  vix_level        annualised-vol proxy, ~10 (calm) .. 40+ (panic)
  vix_trend_5d     change in VIX over 5 sessions (rising = fear building)
  yield_trend_10d  change in the 10y Treasury yield over 10 sessions
  spx_vs_200d_pct  S&P 500 vs its 200-day average (> 0 = uptrend)
  risk_on          -1 (risk-off) .. +1 (risk-on), blended from the above
  regime           "risk_on" | "neutral" | "risk_off"
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import app

_CACHE: dict[str, Any] = {"at": 0.0, "by_date": None}
_TTL = 60 * 60 * 6


def _series(symbol: str, rng: str = "2y") -> dict[str, float]:
    try:
        rows = app.price_series(app.yahoo_chart(symbol, rng))
    except Exception:
        return {}
    return {r["date"]: r["close"] for r in rows if isinstance(r.get("close"), (int, float))}


def _sma(seq: list[float], n: int) -> float | None:
    return sum(seq[-n:]) / n if len(seq) >= n else None


def build(range_: str = "2y") -> dict[str, dict[str, Any]]:
    """date -> regime dict. Cached; pass range_='5y' for a backtest build."""
    vix = _series("^VIX", range_)
    tnx = _series("^TNX", range_)
    spx = _series("^GSPC", range_)
    if not spx:
        return {}

    spx_dates = sorted(spx)
    spx_closes: list[float] = []
    by_date: dict[str, dict[str, Any]] = {}
    vix_hist: list[float] = []
    tnx_hist: list[float] = []

    for d in spx_dates:
        spx_closes.append(spx[d])
        if d in vix:
            vix_hist.append(vix[d])
        if d in tnx:
            tnx_hist.append(tnx[d])

        vix_level = vix_hist[-1] if vix_hist else 18.0
        vix_trend = (vix_hist[-1] - vix_hist[-6]) if len(vix_hist) >= 6 else 0.0
        yield_trend = (tnx_hist[-1] - tnx_hist[-11]) if len(tnx_hist) >= 11 else 0.0
        sma200 = _sma(spx_closes, 200)
        spx_vs_200 = ((spx_closes[-1] - sma200) / sma200 * 100.0) if sma200 else 0.0

        # Blend into one risk score. Calm VIX + SPX above its 200d + falling
        # yields = risk-on; the opposite = risk-off.
        s = 0.0
        s += max(-1.0, min(1.0, (20.0 - vix_level) / 12.0)) * 0.4
        s += max(-1.0, min(1.0, -vix_trend / 6.0)) * 0.2
        s += max(-1.0, min(1.0, spx_vs_200 / 8.0)) * 0.3
        s += max(-1.0, min(1.0, -yield_trend / 0.5)) * 0.1
        risk_on = round(max(-1.0, min(1.0, s)), 3)
        regime = "risk_on" if risk_on > 0.25 else "risk_off" if risk_on < -0.25 else "neutral"

        by_date[d] = {
            "vix_level": round(vix_level, 2),
            "vix_trend_5d": round(vix_trend, 2),
            "yield_trend_10d": round(yield_trend, 3),
            "spx_vs_200d_pct": round(spx_vs_200, 2),
            "risk_on": risk_on,
            "regime": regime,
        }
    return by_date


def current() -> dict[str, Any]:
    now = dt.datetime.now().timestamp()
    if _CACHE["by_date"] is None or now - _CACHE["at"] > _TTL:
        _CACHE["by_date"] = build("2y")
        _CACHE["at"] = now
    bd = _CACHE["by_date"] or {}
    if not bd:
        return {"vix_level": 18.0, "vix_trend_5d": 0.0, "yield_trend_10d": 0.0,
                "spx_vs_200d_pct": 0.0, "risk_on": 0.0, "regime": "neutral"}
    return bd[max(bd)]


def as_of(by_date: dict[str, dict[str, Any]], date_str: str) -> dict[str, Any]:
    """Regime on or just before date_str (for the backtest's walk-forward)."""
    keys = [d for d in by_date if d <= date_str]
    if not keys:
        return {"vix_level": 18.0, "vix_trend_5d": 0.0, "yield_trend_10d": 0.0,
                "spx_vs_200d_pct": 0.0, "risk_on": 0.0, "regime": "neutral"}
    return by_date[max(keys)]
