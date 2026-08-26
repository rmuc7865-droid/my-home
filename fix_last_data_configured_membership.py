#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

anchor = """def load_market_regions() -> dict[str, str]:
    path = Path("/app/config/instruments.json")
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / "config" / "instruments.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result = {}
    for row in rows if isinstance(rows, list) else []:
        ticker = str(row.get("Ticker") or row.get("ticker") or "").strip().upper()
        region = str(row.get("MarketRegion") or "").strip().upper()
        isin = str(row.get("ISIN") or "").strip().upper()
        if not region:
            if isin.startswith("US"):
                region = "US"
            elif isin.startswith("DE"):
                region = "DE"
        if ticker and region:
            result[ticker] = region
    return result
"""

addition = anchor + """

def load_configured_collector_tickers() -> set[str]:
    # Return tickers belonging to enabled configured market collectors.
    project_root = Path(__file__).resolve().parents[1]
    config_path = Path("/app/raspberry/config.yaml")
    if not config_path.exists():
        config_path = project_root / "raspberry" / "config.yaml"

    try:
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()

    ticker_files = []

    def visit(value):
        if isinstance(value, dict):
            ticker_file = value.get("ticker_file")
            enabled = value.get("enabled", True)
            if ticker_file and bool(enabled):
                ticker_files.append(str(ticker_file))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(config_data)

    result = set()

    for configured_path in ticker_files:
        candidate = Path(configured_path)
        if not candidate.exists():
            candidate = project_root / "config" / Path(configured_path).name
        if not candidate.exists():
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
                    or row.get("symbol")
                    or row.get("Symbol")
                    or ""
                )
            else:
                ticker = ""

            ticker = str(ticker).strip().upper()
            if ticker:
                result.add(ticker)

    return result
"""

if anchor not in text:
    raise SystemExit("ERROR: load_market_regions block not found; no changes written.")
text = text.replace(anchor, addition, 1)

old = """TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
"""

new = """TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
CONFIGURED_COLLECTOR_TICKERS = load_configured_collector_tickers()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
"""

if old not in text:
    raise SystemExit("ERROR: trading-config globals block not found; no changes written.")
text = text.replace(old, new, 1)

old = """        newest_received = market_df["received_at"].max()
        latest_received = market_df.groupby("ticker")["received_at"].max()

        # Last Data intentionally keeps historical tickers visible, but we
        # still need to know whether a ticker is part of the current collector
        # feed. A ticker whose newest received_at is more than 30 minutes behind
        # the newest received record is treated as historical/inactive.
        current_receive_cutoff = newest_received - pd.Timedelta(minutes=30)
        currently_collected_tickers = set(
            latest_received[
                latest_received >= current_receive_cutoff
            ].index.astype(str)
        )

        active_tickers = latest_received.index
        active_df = market_df.copy()
"""

new = """        newest_received = market_df["received_at"].max()
        latest_received = market_df.groupby("ticker")["received_at"].max()

        # Last Data keeps historical rows, but actionable state must be based
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
"""

if old not in text:
    raise SystemExit("ERROR: temporary 30-minute Last Data heuristic not found; no changes written.")
text = text.replace(old, new, 1)

old = """            "which is the operating-day boundary for this view. Historical tickers that are no "
            "longer being received remain visible, but their actionable Phase/wait/price fields "
            "are shown as unavailable."
"""

new = """            "which is the operating-day boundary for this view. Historical tickers that are no "
            "longer part of an enabled collector's configured ticker universe remain visible, "
            "but their actionable Phase/wait/price fields are shown as unavailable."
"""

if old not in text:
    raise SystemExit("ERROR: historical-row caption text not found; no changes written.")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: Last Data current/historical state now uses enabled collector ticker configuration.")
