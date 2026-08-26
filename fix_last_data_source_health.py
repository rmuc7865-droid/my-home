#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# Replace the current flat configured-ticker loader with a ticker -> source mapping.
start_marker = "def load_configured_collector_tickers() -> set[str]:"
end_marker = "\n\nTRADING_CONFIG = load_trading_config()"

start = text.find(start_marker)
end = text.find(end_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "ERROR: configured ticker loader could not be located; no changes written."
    )

new_function = '''def load_configured_ticker_sources() -> dict[str, set[str]]:
    # Map configured ticker symbols to their collector source/system.
    project_root = Path(__file__).resolve().parents[1]

    ticker_files = {
        "tickers.json": "polygon",
        "crypto_tickers.json": "crypto",
        "international_tickers.json": "international",
    }

    result: dict[str, set[str]] = {}

    for file_name, source_name in ticker_files.items():
        candidate = Path("/app/config") / file_name
        if not candidate.exists():
            candidate = project_root / "config" / file_name
        if not candidate.exists() or not candidate.is_file():
            continue

        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(payload, dict):
            rows = payload.get("tickers") or payload.get("symbols") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        for row in rows:
            if isinstance(row, str):
                ticker = row
            elif isinstance(row, dict):
                ticker = (
                    row.get("ticker")
                    or row.get("Ticker")
                    or row.get("zero_ticker")
                    or row.get("symbol")
                    or row.get("Symbol")
                    or ""
                )
            else:
                ticker = ""

            ticker = str(ticker).strip().upper()
            if ticker:
                result.setdefault(ticker, set()).add(source_name)

    return result
'''

text = text[:start] + new_function + text[end:]

old_global = '''TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
CONFIGURED_COLLECTOR_TICKERS = load_configured_collector_tickers()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
'''

new_global = '''TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
CONFIGURED_TICKER_SOURCES = load_configured_ticker_sources()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
'''

if old_global not in text:
    raise SystemExit(
        "ERROR: configured ticker global block not found; no changes written."
    )
text = text.replace(old_global, new_global, 1)

old_logic = '''        # Last Data keeps historical rows, but actionable state must be based
        # on collector configuration rather than an arbitrary freshness limit.
        # This prevents a temporary missed/delayed bar from making a configured
        # ticker look historical.
        currently_collected_tickers = {
            str(ticker)
            for ticker in latest_received.index
            if str(ticker).strip().upper() in CONFIGURED_COLLECTOR_TICKERS
        }

        active_tickers = latest_received.index
        active_df = market_df.copy()
'''

new_logic = '''        # Last Data keeps historical rows visible. Actionable state is based
        # on SOURCE health, not individual bar freshness: a temporarily delayed
        # ticker stays current while its collector source is alive, but all
        # tickers belonging only to a disabled/stale source become historical.
        source_latest_received = (
            market_df.groupby("system")["received_at"].max()
            if "system" in market_df.columns
            else pd.Series(dtype="datetime64[ns, UTC]")
        )
        source_health_cutoff = newest_received - pd.Timedelta(minutes=30)
        healthy_sources = {
            str(source).strip().lower()
            for source, received_at in source_latest_received.items()
            if pd.notna(received_at) and received_at >= source_health_cutoff
        }

        currently_collected_tickers = set()
        for ticker in latest_received.index:
            ticker_key = str(ticker).strip().upper()
            configured_sources = CONFIGURED_TICKER_SOURCES.get(ticker_key, set())
            if configured_sources & healthy_sources:
                currently_collected_tickers.add(str(ticker))

        active_tickers = latest_received.index
        active_df = market_df.copy()
'''

if old_logic not in text:
    raise SystemExit(
        "ERROR: Last Data configured-membership logic not found; no changes written."
    )
text = text.replace(old_logic, new_logic, 1)

old_caption = '''            "which is the operating-day boundary for this view. Historical tickers that are no "
            "longer part of an enabled collector's configured ticker universe remain visible, "
            "but their actionable Phase/wait/price fields are shown as unavailable."
'''

new_caption = '''            "which is the operating-day boundary for this view. Historical tickers, including "
            "tickers whose configured collector source is currently inactive, remain visible, "
            "but their actionable Phase/wait/price fields are shown as unavailable."
'''

if old_caption in text:
    text = text.replace(old_caption, new_caption, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: Last Data now uses source-level collector health for current/historical state.")
