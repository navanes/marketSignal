# MarketSignal

A local stock and market research assistant. Enter a ticker or market name, and the app gathers live market data, recent news, and produces a structured investment research report.

## Run

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

## Optional AI Summaries

The app works without an API key using a deterministic scoring model. To enable AI-written narrative analysis, set:

```bash
export OPENAI_API_KEY="your_key_here"
```

## Notes

This is a research assistant, not financial advice. It is designed to organize evidence, risks, and scenarios so you can make better decisions.

## Free Data Roadmap

The current version uses free endpoints:

- Yahoo Finance chart data for price history and moving averages
- Google News RSS for recent headlines
- Local JSON history in `data/reports.json`

Good next free or free-tier additions:

- SEC EDGAR company filings
- FRED macro data for rates, inflation, unemployment, and recession indicators
- Alpha Vantage free tier for fundamentals and technical indicators
- Financial Modeling Prep free tier for company profiles and ratios
- Finnhub free tier for company news, earnings, and basic financial metrics
- Nasdaq earnings calendar pages or RSS where available

Paid sources like Bloomberg, Refinitiv, FactSet, or Dow Jones can be valuable later, but they are expensive and best added after the report format, watchlists, scoring model, and database are proven.

## Chart Analysis Philosophy

Trendlines, channels, support/resistance, Fibonacci levels, and chart patterns are scenario tools. They do not truly predict the market. MarketSignal treats them as conditional evidence:

- If price holds support, upside probability may improve.
- If price breaks support, the setup weakens or invalidates.
- If price reaches resistance, confirmation matters before chasing.
- News and technicals should agree before confidence rises.
