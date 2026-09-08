"""The symbols MarketSignal scans every night and backtests against.

Keep this liquid and diversified — the point is to have enough names to rank
against each other, not to cover the whole market. Edit freely; the nightly
scan and the backtest both read this list.
"""

# (symbol, short label, bucket) — bucket is just for grouping in reports.
UNIVERSE: list[tuple[str, str, str]] = [
    # Mega-cap tech
    ("AAPL", "Apple", "tech"),
    ("MSFT", "Microsoft", "tech"),
    ("GOOGL", "Alphabet", "tech"),
    ("AMZN", "Amazon", "tech"),
    ("META", "Meta", "tech"),
    ("NVDA", "Nvidia", "semis"),
    ("AVGO", "Broadcom", "semis"),
    ("AMD", "AMD", "semis"),
    ("TSLA", "Tesla", "autos"),
    ("NFLX", "Netflix", "tech"),
    ("CRM", "Salesforce", "software"),
    ("ORCL", "Oracle", "software"),
    ("ADBE", "Adobe", "software"),
    # Financials
    ("JPM", "JPMorgan", "financials"),
    ("BAC", "Bank of America", "financials"),
    ("GS", "Goldman Sachs", "financials"),
    ("V", "Visa", "financials"),
    ("MA", "Mastercard", "financials"),
    ("BRK-B", "Berkshire", "financials"),
    # Healthcare
    ("UNH", "UnitedHealth", "healthcare"),
    ("LLY", "Eli Lilly", "healthcare"),
    ("JNJ", "Johnson & Johnson", "healthcare"),
    ("ABBV", "AbbVie", "healthcare"),
    ("MRK", "Merck", "healthcare"),
    # Consumer / industrial / energy
    ("WMT", "Walmart", "consumer"),
    ("COST", "Costco", "consumer"),
    ("HD", "Home Depot", "consumer"),
    ("MCD", "McDonald's", "consumer"),
    ("KO", "Coca-Cola", "consumer"),
    ("PG", "Procter & Gamble", "consumer"),
    ("XOM", "ExxonMobil", "energy"),
    ("CVX", "Chevron", "energy"),
    ("CAT", "Caterpillar", "industrials"),
    ("BA", "Boeing", "industrials"),
    ("GE", "GE Aerospace", "industrials"),
    # Broad-market / style ETFs
    ("SPY", "S&P 500", "etf"),
    ("QQQ", "Nasdaq 100", "etf"),
    ("IWM", "Russell 2000", "etf"),
    ("DIA", "Dow 30", "etf"),
    # Crypto
    ("BTC-USD", "Bitcoin", "crypto"),
    ("ETH-USD", "Ethereum", "crypto"),
]

SYMBOLS: list[str] = [row[0] for row in UNIVERSE]
LABELS: dict[str, str] = {row[0]: row[1] for row in UNIVERSE}
BUCKETS: dict[str, str] = {row[0]: row[2] for row in UNIVERSE}

# Horizons (calendar days) the nightly scan logs a prediction for.
SCAN_HORIZONS: list[int] = [10, 30]
