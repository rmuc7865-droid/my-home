#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# 1) Keep all historical rows, but separately remember which tickers are
# actually still being collected now.
old = '''        newest_received = market_df["received_at"].max()
        latest_received = market_df.groupby("ticker")["received_at"].max()
        active_tickers = latest_received.index
        active_df = market_df.copy()
'''

new = '''        newest_received = market_df["received_at"].max()
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
'''

if old not in text:
    raise SystemExit("ERROR: Last Data newest_received block not found; no changes written.")
text = text.replace(old, new, 1)

# 2) Extend the per-ticker derivation loop with a display timestamp and with
# inactive/historical handling.
old = '''                movement_percent = float(SELL_CONFIG.get("movement_percent", 1.1))
                phase_values = {}
                duration_values = {}
                for ticker in relevant["Ticker"].astype(str):
                    source = active_df[active_df["ticker"].astype(str) == ticker].copy()
                    if source.empty:
                        continue
                    source = source.sort_values(["timestamp", "id"]).drop_duplicates(
                        subset=["timestamp"], keep="last"
                    )
                    latest_source = source.iloc[-1]
                    region = market_region_for_ticker(ticker, latest_source.get("asset_type"))
                    phase_values[ticker] = market_phase_info(
                        latest_source["timestamp"], region
                    )
                    duration_values[ticker] = movement_durations(
                        source,
                        latest_source["timestamp"],
                        latest_source.get("close"),
                        movement_percent,
                    )
'''

new = '''                movement_percent = float(SELL_CONFIG.get("movement_percent", 1.1))
                phase_values = {}
                duration_values = {}
                last_collect_values = {}

                newest_market_local_date = newest_market_data.tz_convert(
                    LOCAL_TIMEZONE
                ).date()

                for ticker in relevant["Ticker"].astype(str):
                    source = active_df[active_df["ticker"].astype(str) == ticker].copy()
                    if source.empty:
                        continue
                    source = source.sort_values(["timestamp", "id"]).drop_duplicates(
                        subset=["timestamp"], keep="last"
                    )
                    latest_source = source.iloc[-1]

                    latest_local = pd.to_datetime(
                        latest_source["timestamp"], utc=True
                    ).tz_convert(LOCAL_TIMEZONE)
                    if latest_local.date() == newest_market_local_date:
                        last_collect_values[ticker] = latest_local.strftime("%H:%M")
                    else:
                        last_collect_values[ticker] = latest_local.strftime("%d.%m %H:%M")

                    # Historical/inactive tickers remain visible in Last Data,
                    # but a current trading phase/wait value would be misleading.
                    if ticker not in currently_collected_tickers:
                        phase_values[ticker] = ("-", "-", "-")
                        duration_values[ticker] = ("-", "-")
                        continue

                    region = market_region_for_ticker(ticker, latest_source.get("asset_type"))
                    phase_values[ticker] = market_phase_info(
                        latest_source["timestamp"], region
                    )
                    duration_values[ticker] = movement_durations(
                        source,
                        latest_source["timestamp"],
                        latest_source.get("close"),
                        movement_percent,
                    )
'''

if old not in text:
    raise SystemExit("ERROR: Last Data phase/duration loop not found; no changes written.")
text = text.replace(old, new, 1)

# 3) Replace LastCollect with the unambiguous display value and blank values
# that should not look actionable for inactive historical instruments.
anchor = '''                relevant["StaticDuration"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: duration_values.get(ticker, ("-", "-"))[1]
                )

                live_columns = [
'''

replacement = '''                relevant["StaticDuration"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: duration_values.get(ticker, ("-", "-"))[1]
                )

                relevant["LastCollect"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: last_collect_values.get(ticker, "—")
                )

                inactive_mask = ~relevant["Ticker"].astype(str).isin(
                    currently_collected_tickers
                )
                relevant.loc[inactive_mask, "LastPrice"] = "—"
                relevant.loc[inactive_mask, "SellingTime"] = "—"
                for column in [
                    "Phase",
                    "DropDuration",
                    "StaticDuration",
                    "WaitToTrade",
                    "WaitToOpening",
                ]:
                    relevant.loc[inactive_mask, column] = "-"

                live_columns = [
'''

if anchor not in text:
    raise SystemExit("ERROR: Last Data column anchor not found; no changes written.")
text = text.replace(anchor, replacement, 1)

# 4) Clarify the caption.
old = '''            "bar, while LastPrice is its estimated EUR price. Records counts unique market "
            "bars from 03:00 Europe/Berlin, which is the operating-day boundary for this view."
'''

new = '''            "bar; when it is from an earlier date, LastCollect also shows DD.MM. LastPrice is "
            "its estimated EUR price. Records counts unique market bars from 03:00 Europe/Berlin, "
            "which is the operating-day boundary for this view. Historical tickers that are no "
            "longer being received remain visible, but their actionable Phase/wait/price fields "
            "are shown as unavailable."
'''

if old not in text:
    raise SystemExit("ERROR: Last Data caption anchor not found; no changes written.")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: Last Data now distinguishes current vs historical tickers and shows dates for older LastCollect values.")
