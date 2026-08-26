#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

start_marker = "def load_configured_collector_tickers() -> set[str]:"
end_marker = "\n\nTRADING_CONFIG = load_trading_config()"

start = text.find(start_marker)
end = text.find(end_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "ERROR: load_configured_collector_tickers function could not be located; no changes written."
    )

new_function = '''def load_configured_collector_tickers() -> set[str]:
    # Last Data needs the configured market-data universe inside the dashboard
    # container. The dashboard does not necessarily have raspberry/config.yaml
    # mounted, but the generated ticker files under /app/config are available.
    project_root = Path(__file__).resolve().parents[1]

    ticker_file_names = [
        "tickers.json",
        "crypto_tickers.json",
        "international_tickers.json",
    ]

    result = set()

    for file_name in ticker_file_names:
        candidate = Path("/app/config") / file_name
        if not candidate.exists():
            candidate = project_root / "config" / file_name
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
'''

text = text[:start] + new_function + text[end:]
path.write_text(text, encoding="utf-8")

print("SUCCESS: Last Data now loads current ticker membership directly from config ticker files.")
