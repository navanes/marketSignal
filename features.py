"""One feature vector per (price-history window, horizon).

Used identically by train.py (to fit) and app.py (to serve), so the model
never sees a different shape in production than it trained on. Pure stdlib —
the whole project deliberately has no numpy/pandas.
"""

from __future__ import annotations

import statistics
from typing import Any

import app

# Order matters: model.json stores coefficients in this order.
FEATURE_NAMES: list[str] = [
    "ret_5d",
    "ret_21d",
    "ret_63d",
    "price_vs_sma20",
    "price_vs_sma50",
    "sma20_vs_sma50",
    "rsi_centered",
    "macd_hist_norm",
    "range_position",
    "vol_annualized",
    "volume_change",
    "analog_up_minus_down",
    "analog_avg_fwd_return",
    "dist_to_resistance",
    "dist_to_support",
    "horizon_frac",
]

MAX_HORIZON_SESSIONS = 22  # ~30 calendar days; used to normalise the horizon feature


def _pct_change(a: float | None, b: float | None) -> float:
    if not a or not b:
        return 0.0
    return (a - b) / b * 100.0


def extract_features(window: list[dict[str, Any]], horizon_days: int, analytics: dict[str, Any] | None = None) -> dict[str, float] | None:
    """window: chronological [{date, close, volume}, ...] up to and including the
    decision bar. analytics: optional pre-computed app.analytics_summary(window,...)
    to avoid recomputing. Returns None if there isn't enough history."""
    if len(window) < 70:
        return None
    closes = [row["close"] for row in window]
    price = closes[-1]
    if not price:
        return None

    if analytics is None:
        analytics = app.analytics_summary(window, "1y", horizon_days)
    technical = analytics.get("technical") or {}
    stats = analytics.get("stats") or {}
    probability = technical.get("probability") or {}
    chart = analytics.get("chart") or []
    latest = chart[-1] if chart else {}

    sma20 = latest.get("sma20")
    sma50 = latest.get("sma50")
    recent = closes[-126:] if len(closes) >= 126 else closes
    low_n, high_n = min(recent), max(recent)
    range_position = ((price - low_n) / (high_n - low_n) - 0.5) if high_n > low_n else 0.0

    rsi = latest.get("rsi14")
    macd_hist = latest.get("macd_histogram")
    up_p, down_p = probability.get("up_pct"), probability.get("down_pct")
    analog_n = probability.get("sample_size") or 0
    analog_avg = probability.get("avg_forward_return_pct")
    support = technical.get("support")
    resistance = technical.get("resistance")
    sessions = app.horizon_sessions(horizon_days)

    def back(n: int) -> float:
        return closes[-1 - n] if len(closes) > n else closes[0]

    return {
        "ret_5d": _pct_change(price, back(5)),
        "ret_21d": _pct_change(price, back(21)),
        "ret_63d": _pct_change(price, back(63)),
        "price_vs_sma20": _pct_change(price, sma20),
        "price_vs_sma50": _pct_change(price, sma50),
        "sma20_vs_sma50": _pct_change(sma20, sma50),
        "rsi_centered": ((rsi - 50.0) / 50.0) if rsi is not None else 0.0,
        "macd_hist_norm": (macd_hist / price * 100.0) if macd_hist is not None else 0.0,
        "range_position": range_position,
        "vol_annualized": (stats.get("volatility_annualized_pct") or 0.0) / 100.0,
        "volume_change": (technical.get("volume_change_pct") or 0.0) / 100.0,
        "analog_up_minus_down": ((up_p - down_p) / 100.0) if (up_p is not None and down_p is not None and analog_n >= 8) else 0.0,
        "analog_avg_fwd_return": (analog_avg / 100.0) if (analog_avg is not None and analog_n >= 10) else 0.0,
        "dist_to_resistance": _pct_change(resistance, price) if resistance else 0.0,
        "dist_to_support": _pct_change(price, support) if support else 0.0,
        "horizon_frac": sessions / MAX_HORIZON_SESSIONS,
    }


def feature_vector(feats: dict[str, float]) -> list[float]:
    return [feats[name] for name in FEATURE_NAMES]


def realized_daily_vol(window: list[dict[str, Any]], lookback: int = 60) -> float:
    closes = [row["close"] for row in window][-(lookback + 1):]
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]
    return statistics.pstdev(rets) if len(rets) > 2 else 0.0
