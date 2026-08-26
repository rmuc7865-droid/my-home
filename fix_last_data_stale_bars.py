#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''        currently_collected_tickers = set()
        for ticker in latest_received.index:
            ticker_key = str(ticker).strip().upper()
            configured_sources = CONFIGURED_TICKER_SOURCES.get(ticker_key, set())
            if configured_sources & healthy_sources:
                currently_collected_tickers.add(str(ticker))

        active_tickers = latest_received.index
        active_df = market_df.copy()
'''

new = '''        currently_collected_tickers = set()
        for ticker in latest_received.index:
            ticker_key = str(ticker).strip().upper()
            configured_sources = CONFIGURED_TICKER_SOURCES.get(ticker_key, set())
            if configured_sources & healthy_sources:
                currently_collected_tickers.add(str(ticker))

        # A healthy source alone is not enough: a specific ticker can still
        # have an unreasonably old market bar. Keep normal overnight/weekend
        # stock gaps, but suppress clearly stale multi-day stock records.
        #
        # Crypto trades continuously, so a much tighter freshness limit is
        # appropriate there.
        latest_market_rows = (
            market_df.sort_values(["timestamp", "id"])
            .drop_duplicates(subset=["ticker"], keep="last")
            .set_index("ticker")
        )
        market_reference_time = pd.to_datetime(
            market_df["timestamp"].max(), utc=True
        )

        stale_tickers = set()

        for ticker in currently_collected_tickers:
            if ticker not in latest_market_rows.index:
                stale_tickers.add(ticker)
                continue

            latest_row = latest_market_rows.loc[ticker]
            latest_bar_time = pd.to_datetime(
                latest_row.get("timestamp"), utc=True
            )

            if pd.isna(latest_bar_time):
                stale_tickers.add(ticker)
                continue

            asset_type = str(
                latest_row.get("asset_type") or ""
            ).strip().lower()

            if asset_type == "crypto":
                maximum_bar_age = pd.Timedelta(minutes=60)
            else:
                maximum_bar_age = pd.Timedelta(hours=72)

            if market_reference_time - latest_bar_time > maximum_bar_age:
                stale_tickers.add(ticker)

        currently_collected_tickers -= stale_tickers

        active_tickers = latest_received.index
        active_df = market_df.copy()
'''

if old not in text:
    raise SystemExit(
        "ERROR: source-health membership block not found; no changes written."
    )

text = text.replace(old, new, 1)

old_caption = '''            "which is the operating-day boundary for this view. Historical tickers, including "
            "tickers whose configured collector source is currently inactive, remain visible, "
            "but their actionable Phase/wait/price fields are shown as unavailable."
'''

new_caption = '''            "which is the operating-day boundary for this view. Historical tickers, including "
            "tickers whose configured collector source is inactive or whose latest market bar is "
            "too stale, remain visible, but their actionable Phase/wait/price fields are shown "
            "as unavailable. Crypto bars older than 60 minutes and non-crypto bars older than "
            "72 hours are treated as stale."
'''

if old_caption not in text:
    raise SystemExit(
        "ERROR: Last Data source-health caption not found; no changes written."
    )

text = text.replace(old_caption, new_caption, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Last Data now suppresses stale bars "
    "(crypto >60m, non-crypto >72h)."
)
