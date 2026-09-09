"""Per-stock fundamentals from Yahoo (analyst view, growth, valuation).

Yahoo's quoteSummary needs a cookie + crumb now, so we fetch those once and
cache. Fundamentals move slowly — each symbol's snapshot is cached 12h.

This is a LIVE overlay only: Yahoo gives the current snapshot, not history,
so it can't go into the walk-forward backtest. `fundamental_tilt()` returns a
small, bounded nudge (+/-2%) to sit on top of the trained model, and the app
tracks forward whether that nudge actually helps.
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import urllib.parse
import urllib.request
from typing import Any

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_AUTH: dict[str, Any] = {"opener": None, "crumb": None, "at": 0.0}
_AUTH_TTL = 60 * 30
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 60 * 60 * 12

# Symbols with no meaningful single-name fundamentals.
SKIP_PREFIXES = ("^",)
SKIP = {"SPY", "QQQ", "IWM", "DIA", "BTC-USD", "ETH-USD", "XRP-USD"}


def _auth() -> tuple[Any, str] | tuple[None, None]:
    now = dt.datetime.now().timestamp()
    if _AUTH["opener"] and _AUTH["crumb"] and now - _AUTH["at"] < _AUTH_TTL:
        return _AUTH["opener"], _AUTH["crumb"]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA)]
    try:
        try:
            op.open("https://fc.yahoo.com", timeout=10).read()
        except Exception:
            pass  # 404s but still drops the cookie we need
        crumb = op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode().strip()
        if not crumb or "<" in crumb:
            return None, None
        _AUTH.update(opener=op, crumb=crumb, at=now)
        return op, crumb
    except Exception:
        return None, None


def _raw(v: Any) -> Any:
    return v.get("raw") if isinstance(v, dict) else v


def fundamentals(symbol: str) -> dict[str, Any] | None:
    symbol = symbol.upper()
    if symbol in SKIP or symbol.startswith(SKIP_PREFIXES):
        return None
    now = dt.datetime.now().timestamp()
    hit = _CACHE.get(symbol)
    if hit and now - hit["_at"] < _CACHE_TTL:
        return hit["data"]

    op, crumb = _auth()
    if not op:
        return None
    modules = "financialData,defaultKeyStatistics,summaryDetail,earningsTrend"
    url = (
        f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
        f"?modules={modules}&crumb={urllib.parse.quote(crumb)}"
    )
    try:
        payload = json.loads(op.open(url, timeout=12).read().decode())
        r = payload["quoteSummary"]["result"][0]
    except Exception:
        return None

    fin = r.get("financialData", {}) or {}
    key = r.get("defaultKeyStatistics", {}) or {}
    summ = r.get("summaryDetail", {}) or {}

    price = _raw(fin.get("currentPrice"))
    target = _raw(fin.get("targetMeanPrice"))
    upside = ((target - price) / price * 100.0) if (price and target) else None

    # Net analyst EPS-estimate revisions for the current + next quarter.
    up = dn = 0
    for tr in (r.get("earningsTrend", {}) or {}).get("trend", []) or []:
        if tr.get("period") in ("0q", "+1q"):
            rev = tr.get("epsRevisions", {}) or {}
            up += _raw(rev.get("upLast30days")) or 0
            dn += _raw(rev.get("downLast30days")) or 0

    data = {
        "symbol": symbol,
        "price": price,
        "target_mean": target,
        "target_upside_pct": round(upside, 2) if upside is not None else None,
        "recommendation_mean": _raw(fin.get("recommendationMean")),   # 1 strong buy .. 5 sell
        "num_analysts": _raw(fin.get("numberOfAnalystOpinions")),
        "forward_pe": _raw(summ.get("forwardPE")),
        "peg": _raw(key.get("pegRatio")),
        "revenue_growth": _raw(fin.get("revenueGrowth")),            # yoy fraction
        "earnings_growth": _raw(fin.get("earningsGrowth")),
        "profit_margin": _raw(fin.get("profitMargins")),
        "eps_revisions_net_30d": up - dn,
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
    }
    _CACHE[symbol] = {"_at": now, "data": data}
    return data


def fundamental_tilt(f: dict[str, Any] | None) -> dict[str, Any]:
    """A small bounded nudge (percentage points of expected return) from the
    fundamental picture, with a plain-language reason."""
    if not f:
        return {"tilt_pct": 0.0, "reason": ""}
    tilt = 0.0
    bits: list[str] = []

    up = f.get("target_upside_pct")
    if isinstance(up, (int, float)):
        c = max(-1.2, min(1.2, up / 20.0))
        tilt += c
        if abs(c) >= 0.3:
            bits.append(f"{up:+.0f}% to analyst target")

    rec = f.get("recommendation_mean")
    if isinstance(rec, (int, float)):
        c = max(-0.6, min(0.6, (3.0 - rec) / 1.5))
        tilt += c
        if abs(c) >= 0.2:
            bits.append("analysts lean " + ("buy" if rec < 2.6 else "sell" if rec > 3.4 else "hold"))

    rev = f.get("eps_revisions_net_30d")
    if isinstance(rev, (int, float)) and rev:
        c = max(-0.6, min(0.6, rev / 6.0))
        tilt += c
        bits.append(f"{'up' if rev > 0 else 'down'}-revisions {rev:+d}")

    eg = f.get("earnings_growth")
    if isinstance(eg, (int, float)):
        c = max(-0.4, min(0.4, eg))
        tilt += c
        if abs(c) >= 0.15:
            bits.append(f"earnings {eg*100:+.0f}% y/y")

    tilt = round(max(-2.0, min(2.0, tilt)), 2)
    return {"tilt_pct": tilt, "reason": "; ".join(bits[:3])}
