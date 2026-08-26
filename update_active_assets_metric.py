#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''        newest_market_data = active_df["timestamp"].max()
        sources = active_df["system"].nunique()
        tracked_assets = len(active_tickers)

        cols = st.columns(4)
        newest_local = newest_market_data.tz_convert(LOCAL_TIMEZONE)
        cols[0].metric("Newest data", newest_local.strftime("%H:%M %Z"))
        cols[1].metric("Sources", sources)
        cols[2].metric("Tracked assets", tracked_assets)
        cols[3].metric("Open alerts", open_alerts)
'''

new = '''        newest_market_data = active_df["timestamp"].max()
        sources = active_df["system"].nunique()
        active_assets = len(currently_collected_tickers)

        cols = st.columns(4)
        newest_local = newest_market_data.tz_convert(LOCAL_TIMEZONE)
        cols[0].metric("Newest data", newest_local.strftime("%H:%M %Z"))
        cols[1].metric("Sources", sources)
        cols[2].metric("Active assets", active_assets)
        cols[3].metric("Open alerts", open_alerts)
'''

if old not in text:
    raise SystemExit(
        "ERROR: Last Data metric block not found; no changes written."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Last Data metric renamed to Active assets "
    "and now counts only active/actionable tickers."
)
