#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# 1) Healthy / Market: ignore crypto system.
old = '''    result["collector_bad"] = latest.loc[
        latest["CollectorStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["market_bad"] = latest.loc[
        latest["MarketStatus"] != "OK", "system"
    ].astype(str).tolist()
'''

new = '''    # Crypto is intentionally excluded from the dashboard Healthy/Market
    # indicators. Those badges represent the systems relevant to ZERO
    # stock trading decisions.
    status_latest = latest[
        latest["system"].astype(str).str.strip().str.lower() != "crypto"
    ].copy()

    result["collector_bad"] = status_latest.loc[
        status_latest["CollectorStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["market_bad"] = status_latest.loc[
        status_latest["MarketStatus"] != "OK", "system"
    ].astype(str).tolist()
'''

if old not in text:
    raise SystemExit("ERROR: Healthy/Market collector-status block not found; no changes written.")
text = text.replace(old, new, 1)

# 2) WaitToTrade / WaitToOpening from Newest data, keep Phase from LastCollect.
old = '''                    region = market_region_for_ticker(ticker, latest_source.get("asset_type"))
                    phase_values[ticker] = market_phase_info(
                        latest_source["timestamp"], region
                    )
                    duration_values[ticker] = movement_durations(
'''

new = '''                    region = market_region_for_ticker(ticker, latest_source.get("asset_type"))

                    # Phase describes the ticker's latest available market bar,
                    # while the wait columns use the dashboard's newest market time.
                    bar_phase_info = market_phase_info(
                        latest_source["timestamp"], region
                    )
                    current_wait_info = market_phase_info(
                        newest_market_data, region
                    )
                    phase_values[ticker] = (
                        bar_phase_info[0],
                        current_wait_info[1],
                        current_wait_info[2],
                    )

                    duration_values[ticker] = movement_durations(
'''

if old not in text:
    raise SystemExit("ERROR: Last Data market-phase block not found; no changes written.")
text = text.replace(old, new, 1)

# 3) Remove Sources metric.
old = '''        newest_market_data = active_df["timestamp"].max()
        sources = active_df["system"].nunique()
        active_assets = len(currently_collected_tickers)

        cols = st.columns(4)
        newest_local = newest_market_data.tz_convert(LOCAL_TIMEZONE)
        cols[0].metric("Newest data", newest_local.strftime("%H:%M %Z"))
        cols[1].metric("Sources", sources)
        cols[2].metric("Active assets", active_assets)
        cols[3].metric("Open alerts", open_alerts)
'''

new = '''        newest_market_data = active_df["timestamp"].max()
        active_assets = len(currently_collected_tickers)

        cols = st.columns(3)
        newest_local = newest_market_data.tz_convert(LOCAL_TIMEZONE)
        cols[0].metric("Newest data", newest_local.strftime("%H:%M %Z"))
        cols[1].metric("Active assets", active_assets)
        cols[2].metric("Open alerts", open_alerts)
'''

if old not in text:
    raise SystemExit("ERROR: Last Data top-metrics block not found; no changes written.")
text = text.replace(old, new, 1)

# 4) Clarify caption semantics if exact text exists.
old_caption = '''WaitToTrade and WaitToOpening show the remaining time to the relevant trading phase.'''
new_caption = '''WaitToTrade and WaitToOpening are calculated from Newest data and show the remaining time to the relevant trading phase.'''
if old_caption in text:
    text = text.replace(old_caption, new_caption, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: waits now use Newest data; Sources metric removed; Healthy/Market ignore crypto.")
