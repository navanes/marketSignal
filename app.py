from __future__ import annotations

import datetime as dt
import email.utils
import json
import os
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
REPORTS_PATH = DATA_DIR / "reports.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))

PERIODS = {
    "1mo": {"fetch_range": "1mo", "months": 1, "label": "1 month"},
    "2mo": {"fetch_range": "3mo", "months": 2, "label": "2 months"},
    "3mo": {"fetch_range": "3mo", "months": 3, "label": "3 months"},
    "6mo": {"fetch_range": "6mo", "months": 6, "label": "6 months"},
    "1y": {"fetch_range": "1y", "months": 12, "label": "1 year"},
    "2y": {"fetch_range": "2y", "months": 24, "label": "2 years"},
    "5y": {"fetch_range": "5y", "months": 60, "label": "5 years"},
    "max": {"fetch_range": "max", "months": None, "label": "max history"},
}


def fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MarketSignal/0.1 (+local research assistant)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MarketSignal/0.1 (+local research assistant)",
            "Accept": "application/rss+xml,text/xml,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_symbol(query: str) -> str:
    raw = query.strip()
    if not raw:
        raise ValueError("Enter a stock ticker, ETF, index, sector, or market name.")
    symbol = raw.upper().replace("$", "")
    aliases = {
        "S&P 500": "^GSPC",
        "SP500": "^GSPC",
        "SNP500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW": "^DJI",
        "GOLD": "GC=F",
        "OIL": "CL=F",
        "BITCOIN": "BTC-USD",
        "BTC": "BTC-USD",
    }
    return aliases.get(symbol, symbol)


def period_config(period: str) -> dict[str, Any]:
    return PERIODS.get(period, PERIODS["6mo"])


def trim_series_to_months(series: list[dict[str, Any]], months: int | None) -> list[dict[str, Any]]:
    if months is None or not series:
        return series
    latest = dt.date.fromisoformat(series[-1]["date"])
    cutoff = latest - dt.timedelta(days=months * 31)
    trimmed = [row for row in series if dt.date.fromisoformat(row["date"]) >= cutoff]
    return trimmed or series


def yahoo_chart(symbol: str, period: str = "6mo") -> dict[str, Any]:
    config = period_config(period)
    encoded = urllib.parse.quote(symbol)
    interval = "1wk" if period == "max" else "1d"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?range={config['fetch_range']}&interval={interval}&includePrePost=false&events=div%2Csplits"
    )
    data = fetch_json(url)
    result = data.get("chart", {}).get("result") or []
    if not result:
        error = data.get("chart", {}).get("error", {})
        message = error.get("description") or f"No market data found for {symbol}."
        raise ValueError(message)
    return result[0]


def price_series(chart: dict[str, Any]) -> list[dict[str, Any]]:
    meta = chart.get("meta", {})
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = chart.get("timestamp") or []
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if not isinstance(close, (int, float)):
            continue
        volume = volumes[index] if index < len(volumes) else None
        rows.append(
            {
                "date": dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date().isoformat(),
                "close": close,
                "volume": volume if isinstance(volume, (int, float)) else None,
            }
        )
    if rows and meta.get("regularMarketPrice"):
        rows[-1]["close"] = meta["regularMarketPrice"]
    return rows


def simple_moving_average(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
        else:
            result.append(statistics.fmean(values[index + 1 - window : index + 1]))
    return result


def quote_summary(symbol: str, chart: dict[str, Any], series: list[dict[str, Any]], period: str) -> dict[str, Any]:
    meta = chart.get("meta", {})
    closes = [row["close"] for row in series]
    volumes = [row["volume"] for row in series if isinstance(row.get("volume"), (int, float))]
    current = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    previous = meta.get("chartPreviousClose") or (closes[0] if closes else None)
    change_pct = ((current - previous) / previous * 100) if current and previous else None
    sma20 = statistics.fmean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = statistics.fmean(closes[-50:]) if len(closes) >= 50 else None
    high_6m = max(closes) if closes else None
    low_6m = min(closes) if closes else None
    latest_date = series[-1]["date"] if series else None
    avg_volume = statistics.fmean(volumes[-30:]) if volumes else None

    return {
        "symbol": meta.get("symbol") or symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "currency": meta.get("currency"),
        "price": current,
        "previous_close": previous,
        "change_pct_6m": change_pct,
        "sma20": sma20,
        "sma50": sma50,
        "high_6m": high_6m,
        "low_6m": low_6m,
        "avg_volume_30d": avg_volume,
        "latest_date": latest_date,
        "range": period,
        "range_label": period_config(period)["label"],
    }


def exponential_moving_average(values: list[float], window: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2 / (window + 1)
    result: list[float | None] = []
    ema: float | None = None
    for index, value in enumerate(values):
        if index + 1 < window:
            result.append(None)
            continue
        if ema is None:
            ema = statistics.fmean(values[index + 1 - window : index + 1])
        else:
            ema = value * alpha + ema * (1 - alpha)
        result.append(ema)
    return result


def relative_strength_index(values: list[float], window: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= window:
        return result
    gains = []
    losses = []
    for index in range(1, window + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = statistics.fmean(gains)
    avg_loss = statistics.fmean(losses)
    result[window] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for index in range(window + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
        result[index] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return result


def macd_values(values: list[float]) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema12 = exponential_moving_average(values, 12)
    ema26 = exponential_moving_average(values, 26)
    macd_line: list[float | None] = []
    compact_macd: list[float] = []
    compact_indexes: list[int] = []
    for index, (fast, slow) in enumerate(zip(ema12, ema26)):
        value = fast - slow if fast is not None and slow is not None else None
        macd_line.append(value)
        if value is not None:
            compact_macd.append(value)
            compact_indexes.append(index)
    compact_signal = exponential_moving_average(compact_macd, 9)
    signal_line: list[float | None] = [None] * len(values)
    for compact_index, original_index in enumerate(compact_indexes):
        signal_line[original_index] = compact_signal[compact_index]
    histogram = [
        (macd - signal) if macd is not None and signal is not None else None
        for macd, signal in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def support_resistance(series: list[dict[str, Any]], window: int = 80) -> dict[str, Any]:
    recent = series[-window:] if len(series) > window else series
    if not recent:
        return {"support": None, "resistance": None, "support_date": None, "resistance_date": None}
    support_row = min(recent, key=lambda row: row["close"])
    resistance_row = max(recent, key=lambda row: row["close"])
    return {
        "support": support_row["close"],
        "support_date": support_row["date"],
        "resistance": resistance_row["close"],
        "resistance_date": resistance_row["date"],
    }


def trend_state(price: float | None, sma20: float | None, sma50: float | None, macd_hist: float | None) -> str:
    if price is None or sma20 is None or sma50 is None:
        return "insufficient"
    if price > sma20 > sma50 and (macd_hist or 0) >= 0:
        return "bullish"
    if price < sma20 < sma50 and (macd_hist or 0) <= 0:
        return "bearish"
    return "mixed"


def pattern_state(price: float | None, support: float | None, resistance: float | None, volatility: float | None) -> str:
    if price is None or support is None or resistance is None or resistance <= support:
        return "insufficient"
    range_width = resistance - support
    support_distance = (price - support) / range_width
    resistance_distance = (resistance - price) / range_width
    if resistance_distance <= 0.12:
        return "near resistance / breakout watch"
    if support_distance <= 0.12:
        return "near support / rebound watch"
    if volatility is not None and volatility < 18:
        return "low-volatility consolidation"
    return "range trading"


def linear_regression(values: list[float]) -> tuple[float, float]:
    x_values = list(range(len(values)))
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(values)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if not denominator:
        return 0.0, y_mean
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values)) / denominator
    intercept = y_mean - slope * x_mean
    return slope, intercept


def chart_analysis_summary(chart: list[dict[str, Any]], technical: dict[str, Any]) -> dict[str, Any]:
    if len(chart) < 8:
        return {
            "trendline": None,
            "channel": None,
            "fibonacci": [],
            "patterns": [],
            "scenario_report": "Not enough price history to draw reliable chart-analysis scenarios.",
        }

    lookback_count = min(90, len(chart))
    lookback = chart[-lookback_count:]
    closes = [row["close"] for row in lookback]
    slope, intercept = linear_regression(closes)
    fitted = [intercept + slope * index for index in range(len(closes))]
    residuals = [close - fit for close, fit in zip(closes, fitted)]
    channel_offset = statistics.stdev(residuals) if len(residuals) > 1 else max(closes) * 0.02
    first_index = len(chart) - lookback_count
    last_index = len(chart) - 1
    trendline = {
        "start_index": first_index,
        "start": fitted[0],
        "end_index": last_index,
        "end": fitted[-1],
        "slope": slope,
        "direction": "uptrend" if slope > 0 else "downtrend" if slope < 0 else "sideways",
    }
    channel = {
        "upper_start": fitted[0] + channel_offset,
        "upper_end": fitted[-1] + channel_offset,
        "lower_start": fitted[0] - channel_offset,
        "lower_end": fitted[-1] - channel_offset,
        "start_index": first_index,
        "end_index": last_index,
    }

    support = technical.get("support")
    resistance = technical.get("resistance")
    fibonacci = []
    if isinstance(support, (int, float)) and isinstance(resistance, (int, float)) and resistance > support:
        span = resistance - support
        for ratio in (0.236, 0.382, 0.5, 0.618, 0.786):
            fibonacci.append(
                {
                    "ratio": ratio,
                    "label": f"{ratio * 100:.1f}%",
                    "value": resistance - span * ratio,
                }
            )

    patterns = []
    pattern = technical.get("pattern")
    if pattern and pattern != "insufficient":
        patterns.append({"name": pattern, "bias": "conditional"})
    latest = chart[-1]
    price = latest["close"]
    if fibonacci:
        nearest_fib = min(fibonacci, key=lambda level: abs(level["value"] - price))
        patterns.append({"name": f"nearest Fibonacci level {nearest_fib['label']}", "bias": "watch"})
    if trendline["direction"] == "uptrend" and price >= channel["lower_end"]:
        patterns.append({"name": "rising channel support intact", "bias": "bullish"})
    elif trendline["direction"] == "downtrend" and price <= channel["upper_end"]:
        patterns.append({"name": "falling channel resistance intact", "bias": "bearish"})

    probability = technical.get("probability") or {}
    up_probability = probability.get("up_pct")
    down_probability = probability.get("down_pct")
    news_clause = "news tone is not decisive"
    if up_probability is not None and down_probability is not None:
        if up_probability > down_probability:
            news_clause = f"similar setups favor upside {up_probability:.0f}% to {down_probability:.0f}%"
        elif down_probability > up_probability:
            news_clause = f"similar setups favor downside {down_probability:.0f}% to {up_probability:.0f}%"
        else:
            news_clause = "similar setups are evenly split"
    support_text = fmt_money(support) if isinstance(support, (int, float)) else "support"
    resistance_text = fmt_money(resistance) if isinstance(resistance, (int, float)) else "resistance"
    scenario_report = (
        f"Conditional view: price is in a {trendline['direction']} structure. "
        f"If it holds above {support_text}, the setup can keep working toward {resistance_text}. "
        f"A break below {support_text} weakens the setup. Historical context says {news_clause}."
    )
    return {
        "trendline": trendline,
        "channel": channel,
        "fibonacci": fibonacci,
        "patterns": patterns[:5],
        "scenario_report": scenario_report,
    }


def probability_model(chart: list[dict[str, Any]], lookahead: int = 10) -> dict[str, Any]:
    if len(chart) < 45:
        return {
            "up_pct": None,
            "down_pct": None,
            "sideways_pct": None,
            "sample_size": 0,
            "lookahead_sessions": lookahead,
            "method": "Needs at least 45 price points.",
        }
    latest = chart[-1]
    latest_rsi = latest.get("rsi14")
    latest_macd = latest.get("macd_histogram")
    latest_close = latest.get("close")
    latest_sma20 = latest.get("sma20")
    latest_sma50 = latest.get("sma50")
    latest_trend = trend_state(latest_close, latest_sma20, latest_sma50, latest_macd)
    matches = []
    for index in range(35, len(chart) - lookahead):
        row = chart[index]
        row_trend = trend_state(row.get("close"), row.get("sma20"), row.get("sma50"), row.get("macd_histogram"))
        rsi_match = latest_rsi is None or row.get("rsi14") is None or abs(row["rsi14"] - latest_rsi) <= 8
        macd_match = (
            latest_macd is None
            or row.get("macd_histogram") is None
            or (row["macd_histogram"] >= 0) == (latest_macd >= 0)
        )
        if row_trend == latest_trend and rsi_match and macd_match:
            future_return = (chart[index + lookahead]["close"] - row["close"]) / row["close"] * 100
            matches.append(future_return)
    if len(matches) < 8:
        for index in range(35, len(chart) - lookahead):
            row = chart[index]
            row_trend = trend_state(row.get("close"), row.get("sma20"), row.get("sma50"), row.get("macd_histogram"))
            if row_trend == latest_trend:
                future_return = (chart[index + lookahead]["close"] - row["close"]) / row["close"] * 100
                matches.append(future_return)
    up = len([value for value in matches if value > 1])
    down = len([value for value in matches if value < -1])
    sideways = max(0, len(matches) - up - down)
    total = len(matches)
    return {
        "up_pct": up / total * 100 if total else None,
        "down_pct": down / total * 100 if total else None,
        "sideways_pct": sideways / total * 100 if total else None,
        "sample_size": total,
        "avg_forward_return_pct": statistics.fmean(matches) if matches else None,
        "lookahead_sessions": lookahead,
        "method": "Historical 10-session outcomes after similar trend, RSI, and MACD conditions.",
    }


def analytics_summary(series: list[dict[str, Any]], period: str = "6mo") -> dict[str, Any]:
    closes = [row["close"] for row in series]
    if len(closes) < 2:
        return {
            "chart": [],
            "forecast": [],
            "stats": {},
            "monthly_peaks": [],
            "flow": [],
            "has_price_history": False,
        }

    sma20 = simple_moving_average(closes, 20)
    sma50 = simple_moving_average(closes, 50)
    rsi14 = relative_strength_index(closes, 14)
    macd_line, macd_signal, macd_histogram = macd_values(closes)
    chart = [
        {
            "date": row["date"],
            "close": row["close"],
            "volume": row.get("volume"),
            "sma20": sma20[index],
            "sma50": sma50[index],
            "rsi14": rsi14[index],
            "macd": macd_line[index],
            "macd_signal": macd_signal[index],
            "macd_histogram": macd_histogram[index],
        }
        for index, row in enumerate(series)
    ]

    returns = [
        (closes[index] - closes[index - 1]) / closes[index - 1]
        for index in range(1, len(closes))
        if closes[index - 1]
    ]
    periods_per_year = 52 if period == "max" else 252
    volatility = statistics.stdev(returns) * (periods_per_year**0.5) * 100 if len(returns) > 1 else None
    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak:
            max_drawdown = min(max_drawdown, (close - peak) / peak * 100)

    lookback = closes[-60:] if len(closes) >= 60 else closes
    x_values = list(range(len(lookback)))
    x_mean = statistics.fmean(x_values)
    y_mean = statistics.fmean(lookback)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    slope = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, lookback)) / denominator
        if denominator
        else 0
    )
    last_date = dt.date.fromisoformat(series[-1]["date"])
    forecast = []
    forecast_step_days = 7 if period == "max" else 1
    for step in range(1, 11):
        forecast.append(
            {
                "date": (last_date + dt.timedelta(days=step * forecast_step_days)).isoformat(),
                "close": max(0, closes[-1] + slope * step),
                "method": "linear_10_period_projection" if period == "max" else "linear_10_session_projection",
            }
        )
    recent_return_stdev = statistics.stdev(returns[-60:]) if len(returns) > 2 else 0
    scenario_paths = {"base": [], "bullish": [], "bearish": []}
    for step, point in enumerate(forecast, start=1):
        uncertainty = closes[-1] * recent_return_stdev * (step**0.5)
        scenario_paths["base"].append({"date": point["date"], "close": point["close"]})
        scenario_paths["bullish"].append({"date": point["date"], "close": point["close"] + uncertainty})
        scenario_paths["bearish"].append({"date": point["date"], "close": max(0, point["close"] - uncertainty)})

    monthly: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(chart):
        month = row["date"][:7]
        current = monthly.get(month)
        if current is None or row["close"] > current["close"]:
            monthly[month] = {
                "date": row["date"],
                "month": month,
                "index": index,
                "close": row["close"],
            }

    volumes = [row.get("volume") for row in series if isinstance(row.get("volume"), (int, float))]
    recent_volume = statistics.fmean(volumes[-10:]) if len(volumes) >= 10 else None
    baseline_volume = statistics.fmean(volumes[-30:]) if len(volumes) >= 30 else None
    volume_change = (
        (recent_volume - baseline_volume) / baseline_volume * 100
        if recent_volume is not None and baseline_volume
        else None
    )
    latest = chart[-1]
    levels = support_resistance(series)
    probability = probability_model(chart)
    trend = trend_state(latest.get("close"), latest.get("sma20"), latest.get("sma50"), latest.get("macd_histogram"))
    pattern = pattern_state(latest.get("close"), levels["support"], levels["resistance"], volatility)

    technical = {
        "trend": trend,
        "pattern": pattern,
        "rsi14": latest.get("rsi14"),
        "rsi_state": "overbought"
        if (latest.get("rsi14") or 0) >= 70
        else "oversold"
        if latest.get("rsi14") is not None and latest["rsi14"] <= 30
        else "neutral",
        "macd": latest.get("macd"),
        "macd_signal": latest.get("macd_signal"),
        "macd_histogram": latest.get("macd_histogram"),
        "macd_state": "insufficient"
        if latest.get("macd_histogram") is None
        else "bullish"
        if latest["macd_histogram"] > 0
        else "bearish",
        "support": levels["support"],
        "support_date": levels["support_date"],
        "resistance": levels["resistance"],
        "resistance_date": levels["resistance_date"],
        "volume_change_pct": volume_change,
        "volume_state": "insufficient"
        if volume_change is None
        else "rising"
        if volume_change is not None and volume_change > 10
        else "falling"
        if volume_change is not None and volume_change < -10
        else "normal",
        "probability": probability,
    }

    return {
        "chart": chart,
        "forecast": forecast,
        "scenario_paths": scenario_paths,
        "monthly_peaks": list(monthly.values()),
        "technical": technical,
        "chart_analysis": chart_analysis_summary(chart, technical),
        "stats": {
            "days": len(closes),
            "volatility_annualized_pct": volatility,
            "max_drawdown_pct": max_drawdown,
            "avg_daily_return_pct": statistics.fmean(returns) * 100 if returns else None,
            "projection_change_pct": ((forecast[-1]["close"] - closes[-1]) / closes[-1] * 100)
            if forecast and closes[-1]
            else None,
        },
        "flow": [],
        "has_price_history": True,
    }


def decision_flow(quote: dict[str, Any], sentiment: dict[str, Any], signal: dict[str, Any], analytics: dict[str, Any]) -> list[dict[str, str]]:
    stats = analytics.get("stats", {})
    flow = [
        {
            "label": "Price Trend",
            "value": "Known" if quote.get("price") else "Unavailable",
            "state": "good" if signal["score"] > 0 else "warn" if signal["score"] < 0 else "neutral",
        },
        {
            "label": "News Tone",
            "value": sentiment["label"].title(),
            "state": "good" if sentiment["label"] == "positive" else "bad" if sentiment["label"] == "negative" else "neutral",
        },
        {
            "label": "Risk",
            "value": fmt_pct(stats.get("volatility_annualized_pct")),
            "state": "warn" if (stats.get("volatility_annualized_pct") or 0) > 35 else "neutral",
        },
        {
            "label": "Output",
            "value": signal["action"],
            "state": "good" if signal["score"] >= 1 else "bad" if signal["score"] <= -1 else "neutral",
        },
    ]
    analytics["flow"] = flow
    return flow


def save_report_snapshot(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    existing: list[dict[str, Any]] = []
    if REPORTS_PATH.exists():
        try:
            existing = json.loads(REPORTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    existing.append(
        {
            "generated_at": data["generated_at"],
            "query": data["query"],
            "period": data.get("period"),
            "symbol": data["quote"]["symbol"],
            "price": data["quote"].get("price"),
            "action": data["signal"]["action"],
            "score": data["signal"]["score"],
            "sentiment": data["sentiment"]["label"],
        }
    )
    REPORTS_PATH.write_text(json.dumps(existing[-200:], indent=2), encoding="utf-8")


def google_news(query: str, limit: int = 10) -> list[dict[str, str]]:
    search = urllib.parse.quote(f"{query} stock market")
    url = f"https://news.google.com/rss/search?q={search}&hl=en-US&gl=US&ceid=US:en"
    xml_text = fetch_text(url)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        source = item.findtext("source") or ""
        published = item.findtext("pubDate") or ""
        published_iso = ""
        if published:
            try:
                published_iso = email.utils.parsedate_to_datetime(published).isoformat()
            except (TypeError, ValueError):
                published_iso = published
        title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
        items.append(
            {
                "title": title,
                "source": source,
                "url": link,
                "published": published_iso,
            }
        )
    return items


POSITIVE_TERMS = {
    "beat",
    "beats",
    "upgrade",
    "raises",
    "growth",
    "profit",
    "profits",
    "surge",
    "rally",
    "bullish",
    "record",
    "strong",
    "outperform",
    "buy",
    "expands",
    "partnership",
}
NEGATIVE_TERMS = {
    "miss",
    "misses",
    "downgrade",
    "cuts",
    "lawsuit",
    "probe",
    "risk",
    "risks",
    "fall",
    "falls",
    "drop",
    "drops",
    "bearish",
    "weak",
    "underperform",
    "sell",
    "layoff",
    "warning",
    "loss",
}


def news_sentiment(news: list[dict[str, str]]) -> dict[str, Any]:
    scored = []
    total = 0
    for article in news:
        words = set(re.findall(r"[a-z]+", article["title"].lower()))
        pos = len(words & POSITIVE_TERMS)
        neg = len(words & NEGATIVE_TERMS)
        score = pos - neg
        total += score
        scored.append({**article, "score": score})
    label = "neutral"
    if total >= 2:
        label = "positive"
    elif total <= -2:
        label = "negative"
    return {"score": total, "label": label, "articles": scored}


def fmt_money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "n/a"
    prefix = "$" if currency in (None, "USD") else f"{currency} "
    return f"{prefix}{value:,.2f}"


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def fmt_number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def build_signal(quote: dict[str, Any], sentiment: dict[str, Any], analytics: dict[str, Any] | None = None) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    stats = (analytics or {}).get("stats", {})
    technical = (analytics or {}).get("technical", {})
    price = quote.get("price")
    sma20 = quote.get("sma20")
    sma50 = quote.get("sma50")
    high = quote.get("high_6m")
    low = quote.get("low_6m")
    change = quote.get("change_pct_6m")

    if price and sma20:
        if price > sma20:
            score += 1
            reasons.append("Price is above the 20-day average, showing short-term support.")
        else:
            score -= 1
            reasons.append("Price is below the 20-day average, showing short-term pressure.")
    if sma20 and sma50:
        if sma20 > sma50:
            score += 1
            reasons.append("The 20-day average is above the 50-day average, a constructive momentum signal.")
        else:
            score -= 1
            reasons.append("The 20-day average is below the 50-day average, a caution signal.")
    if price and high and low and high != low:
        position = (price - low) / (high - low)
        if position > 0.82:
            score -= 1
            reasons.append("Price is near the top of the selected range, so chase risk is higher.")
        elif position < 0.28:
            score += 1
            reasons.append("Price is near the lower part of the selected range, which may offer better entry asymmetry.")
    if change is not None:
        if change > 20:
            score -= 1
            reasons.append("The selected-range move is already strong, so expectations may be demanding.")
        elif change < -20:
            score -= 1
            reasons.append("The selected-range trend is materially negative, so confirm stabilization before buying.")
    if sentiment["label"] == "positive":
        score += 1
        reasons.append("Recent headlines skew positive.")
    elif sentiment["label"] == "negative":
        score -= 1
        reasons.append("Recent headlines skew negative.")
    projection = stats.get("projection_change_pct")
    if projection is not None:
        if projection > 1.5:
            score += 1
            reasons.append("The short projection points higher from the current price.")
        elif projection < -1.5:
            score -= 1
            reasons.append("The short projection points lower from the current price.")
    rsi = technical.get("rsi14")
    if rsi is not None:
        if rsi < 35:
            score += 1
            reasons.append("RSI is near oversold territory, which can improve rebound odds.")
        elif rsi > 70:
            score -= 1
            reasons.append("RSI is overbought, so pullback risk is elevated.")
    macd_histogram = technical.get("macd_histogram")
    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 1
            reasons.append("MACD momentum is positive.")
        elif macd_histogram < 0:
            score -= 1
            reasons.append("MACD momentum is negative.")
    probability = technical.get("probability") or {}
    up_probability = probability.get("up_pct")
    down_probability = probability.get("down_pct")
    if up_probability is not None and down_probability is not None:
        if up_probability - down_probability >= 20:
            score += 1
            reasons.append("Similar historical setups had a stronger upside outcome rate.")
        elif down_probability - up_probability >= 20:
            score -= 1
            reasons.append("Similar historical setups had a stronger downside outcome rate.")
    pattern = technical.get("pattern")
    if pattern == "near support / rebound watch":
        score += 1
        reasons.append("Price is close to support, creating a rebound-watch setup.")
    elif pattern == "near resistance / breakout watch":
        score -= 1
        reasons.append("Price is close to resistance, so confirmation is needed before chasing.")

    volatility = stats.get("volatility_annualized_pct")

    if score >= 4:
        action = "Strong Buy / Accumulate"
        stance = "constructive"
        confidence = "high"
    elif score >= 3:
        action = "Buy / Accumulate"
        stance = "constructive"
        confidence = "medium-high"
    elif score >= 1:
        action = "Watch / Selective Buy"
        stance = "balanced positive"
        confidence = "medium" if score == 2 else "low-medium"
    elif score <= -4:
        action = "Strong Avoid / Reduce"
        stance = "defensive"
        confidence = "high"
    elif score <= -3:
        action = "Avoid / Consider Reducing"
        stance = "defensive"
        confidence = "medium-high"
    elif score <= -1:
        action = "Hold / Wait"
        stance = "cautious"
        confidence = "medium" if score == -2 else "low-medium"
    else:
        action = "Neutral / Need More Evidence"
        stance = "mixed"
        confidence = "low"

    if volatility and volatility > 45 and confidence == "high":
        confidence = "medium-high"
        reasons.append("High volatility reduces confidence even when the directional signal is strong.")

    return {
        "score": score,
        "action": action,
        "stance": stance,
        "confidence": confidence,
        "reasons": reasons[:8],
    }


def deterministic_report(
    query: str,
    quote: dict[str, Any],
    sentiment: dict[str, Any],
    signal: dict[str, Any],
    analytics: dict[str, Any],
) -> str:
    currency = quote.get("currency")
    technical = analytics.get("technical", {})
    chart_analysis = analytics.get("chart_analysis", {})
    probability = technical.get("probability") or {}
    positive = [a for a in sentiment["articles"] if a["score"] > 0][:3]
    negative = [a for a in sentiment["articles"] if a["score"] < 0][:3]
    neutral = [a for a in sentiment["articles"] if a["score"] == 0][:3]

    bullets = "\n".join(f"- {reason}" for reason in signal["reasons"])
    positive_text = "\n".join(f"- {a['title']} ({a['source']})" for a in positive) or "- No clearly positive headline cluster detected."
    negative_text = "\n".join(f"- {a['title']} ({a['source']})" for a in negative) or "- No clearly negative headline cluster detected."
    neutral_text = "\n".join(f"- {a['title']} ({a['source']})" for a in neutral) or "- Not enough neutral background headlines captured."

    return f"""Investment Research Report: {quote['name']} ({quote['symbol']})

Recommendation
{signal['action']} with {signal['confidence']} confidence. The current evidence is {signal['stance']}, not definitive. Treat this as a research view, not a guaranteed prediction.

Market Snapshot
- Latest price: {fmt_money(quote.get('price'), currency)}
- Selected-range change: {fmt_pct(quote.get('change_pct_6m'))}
- 20-day average: {fmt_money(quote.get('sma20'), currency)}
- 50-day average: {fmt_money(quote.get('sma50'), currency)}
- Selected-range price band: {fmt_money(quote.get('low_6m'), currency)} to {fmt_money(quote.get('high_6m'), currency)}
- Latest market date: {quote.get('latest_date') or 'n/a'}
- Active chart range: {quote.get('range_label') or quote.get('range') or 'n/a'}

Key Signals
{bullets or "- No strong technical or headline signal was detected."}

Technical Analysis
- Trend: {technical.get('trend', 'n/a')}
- Pattern: {technical.get('pattern', 'n/a')}
- RSI 14: {fmt_pct(technical.get('rsi14')).replace('%', '')} ({technical.get('rsi_state', 'n/a')})
- MACD histogram: {fmt_number(technical.get('macd_histogram'))} ({technical.get('macd_state', 'n/a')})
- Support: {fmt_money(technical.get('support'), currency)} from {technical.get('support_date') or 'n/a'}
- Resistance: {fmt_money(technical.get('resistance'), currency)} from {technical.get('resistance_date') or 'n/a'}
- Volume trend: {technical.get('volume_state', 'n/a')} ({fmt_pct(technical.get('volume_change_pct'))} versus recent baseline)

Probability Model
- Up probability: {fmt_pct(probability.get('up_pct'))}
- Sideways probability: {fmt_pct(probability.get('sideways_pct'))}
- Down probability: {fmt_pct(probability.get('down_pct'))}
- Sample size: {probability.get('sample_size', 0)} historical matches
- Method: {probability.get('method', 'n/a')}

Chart Scenario
{chart_analysis.get('scenario_report', 'No chart scenario available.')}

News Tone
Recent headline sentiment is {sentiment['label']} with a raw score of {sentiment['score']}.

Bullish Evidence
{positive_text}

Bearish Evidence
{negative_text}

Context To Read
{neutral_text}

Decision Framework
- Buy only if your time horizon matches the risk. Short-term trades need tighter stop discipline than long-term accumulation.
- Wait for a better entry if price is extended, news is euphoric, or earnings are close.
- Reduce or avoid if negative headlines align with weak momentum and deteriorating fundamentals.
- Re-check this report after earnings, major macro data, analyst revisions, or unusual price volume."""


def news_only_quote(query: str, symbol: str, reason: str, period: str = "6mo") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": query,
        "exchange": "News-only research",
        "currency": "USD",
        "price": None,
        "previous_close": None,
        "change_pct_6m": None,
        "sma20": None,
        "sma50": None,
        "high_6m": None,
        "low_6m": None,
        "avg_volume_30d": None,
        "latest_date": None,
        "range": period,
        "range_label": period_config(period)["label"],
        "data_note": reason,
    }


def ai_report(
    query: str,
    quote: dict[str, Any],
    sentiment: dict[str, Any],
    signal: dict[str, Any],
    analytics: dict[str, Any],
) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful investment research assistant. Do not claim certainty. "
                    "Give evidence, risks, scenarios, and a clear but qualified stance. "
                    "Never present this as financial advice."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "market_snapshot": quote,
                        "headline_sentiment": sentiment,
                        "model_signal": signal,
                        "technical_analysis": analytics.get("technical", {}),
                    },
                    indent=2,
                ),
            },
        ],
        "temperature": 0.25,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def research(query: str, period: str = "6mo") -> dict[str, Any]:
    symbol = normalize_symbol(query)
    selected_period = period if period in PERIODS else "6mo"
    series: list[dict[str, Any]] = []
    try:
        chart = yahoo_chart(symbol, selected_period)
        series = trim_series_to_months(price_series(chart), period_config(selected_period)["months"])
        quote = quote_summary(symbol, chart, series, selected_period)
    except (ValueError, urllib.error.URLError) as exc:
        quote = news_only_quote(query, symbol, str(exc), selected_period)
    news = google_news(query or symbol)
    sentiment = news_sentiment(news)
    analytics = analytics_summary(series, selected_period)
    signal = build_signal(quote, sentiment, analytics)
    decision_flow(quote, sentiment, signal, analytics)
    report = ai_report(query, quote, sentiment, signal, analytics) or deterministic_report(
        query, quote, sentiment, signal, analytics
    )
    data = {
        "query": query,
        "period": selected_period,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "quote": quote,
        "sentiment": sentiment,
        "signal": signal,
        "analytics": analytics,
        "report": report,
        "sources": [
            {"name": "Yahoo Finance chart API", "url": "https://finance.yahoo.com/"},
            {"name": "Google News RSS", "url": "https://news.google.com/"},
        ],
    }
    save_report_snapshot(data)
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = STATIC_DIR / "index.html" if parsed.path == "/" else None
        if path is None or not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/static/"):
            rel = parsed.path.removeprefix("/static/")
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
                self.send_error(403)
                return
            self.serve_file(target)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/research":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            query = str(payload.get("query", "")).strip()
            period = str(payload.get("period", "6mo")).strip()
            data = research(query, period)
            self.send_json(200, data)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except urllib.error.URLError as exc:
            self.send_json(502, {"error": f"Could not reach a market/news data source: {exc}"})
        except Exception as exc:
            self.send_json(500, {"error": f"Research failed: {exc}"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MarketSignal running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
