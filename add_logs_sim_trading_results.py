#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

logs_start = text.find('elif page == "Logs":')
logs_end = text.find('elif page == "Historical Data":', logs_start)
if logs_end == -1:
    logs_end = text.find('elif page == "Historical Trends":', logs_start)

if logs_start == -1 or logs_end == -1:
    raise SystemExit("ERROR: Logs page boundaries not found; no changes written.")

section = text[logs_start:logs_end]

anchor = '''    simulation_timestamp, simulation_timestamp_source = get_simulation_timestamp(
        simulation_payload,
        market_df,
    )

'''

if anchor not in section:
    raise SystemExit(
        "ERROR: Logs simulation timestamp anchor not found; no changes written."
    )

new_block = '''    simulation_timestamp, simulation_timestamp_source = get_simulation_timestamp(
        simulation_payload,
        market_df,
    )

    # 1. Sim-Trading Results
    st.subheader("1. Sim-Trading Results")

    sim_results = simulation_df.copy()

    if not sim_results.empty:
        if "Ticker" not in sim_results.columns:
            sim_results["Ticker"] = ""

        sim_results["Ticker"] = (
            sim_results["Ticker"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        for column in ["BuyTime", "SellTime"]:
            if column not in sim_results.columns:
                sim_results[column] = pd.NaT
            sim_results[column] = pd.to_datetime(
                sim_results[column],
                utc=True,
                errors="coerce",
            )

        # DiffSellPrice is the simulator's percentage profit/loss for a
        # completed trade. Prefer the stored RelativeDifference value,
        # otherwise calculate it from sell/buy EUR prices where possible.
        if "RelativeDifference" in sim_results.columns:
            sim_results["DiffSellPrice"] = pd.to_numeric(
                sim_results["RelativeDifference"],
                errors="coerce",
            )
        elif "DiffSellPrice" in sim_results.columns:
            sim_results["DiffSellPrice"] = pd.to_numeric(
                sim_results["DiffSellPrice"],
                errors="coerce",
            )
        else:
            buy_price_column = next(
                (
                    column
                    for column in ["BuyPriceEUR", "InitPrice", "BuyPrice"]
                    if column in sim_results.columns
                ),
                None,
            )
            sell_price_column = next(
                (
                    column
                    for column in ["SellPriceEUR", "SellPrice"]
                    if column in sim_results.columns
                ),
                None,
            )

            sim_results["DiffSellPrice"] = float("nan")
            if buy_price_column and sell_price_column:
                buy_price = pd.to_numeric(
                    sim_results[buy_price_column],
                    errors="coerce",
                )
                sell_price = pd.to_numeric(
                    sim_results[sell_price_column],
                    errors="coerce",
                )
                valid_price = (
                    buy_price.notna()
                    & sell_price.notna()
                    & buy_price.ne(0)
                )
                sim_results.loc[
                    valid_price,
                    "DiffSellPrice",
                ] = (
                    (
                        sell_price.loc[valid_price]
                        / buy_price.loc[valid_price]
                        - 1.0
                    )
                    * 100.0
                )

    valid_collection_times = market_df["timestamp"].dropna()
    last_collected_time = (
        valid_collection_times.max()
        if not valid_collection_times.empty
        else pd.NaT
    )

    st.markdown("**Days of the last week**")

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fr"]

    if pd.isna(last_collected_time):
        day_rows = [
            {
                "Day": day_name,
                "Buy Assets": 0,
                "Sell Assets": 0,
                "Profit": 0.0,
            }
            for day_name in day_names
        ]
        st.info(
            "No collected market-data timestamp is available; "
            "the daily Sim-Trading summary is shown with zeros."
        )
    else:
        last_local = pd.Timestamp(last_collected_time).tz_convert(
            LOCAL_TIMEZONE
        )

        current_week_monday = (
            last_local.normalize()
            - pd.Timedelta(days=last_local.weekday())
        )
        previous_week_monday = (
            current_week_monday
            - pd.Timedelta(days=7)
        )

        day_rows = []

        for day_offset, day_name in enumerate(day_names):
            day_start_local = (
                previous_week_monday
                + pd.Timedelta(days=day_offset)
            )
            day_end_local = (
                day_start_local
                + pd.Timedelta(days=1)
            )

            day_start_utc = day_start_local.tz_convert("UTC")
            day_end_utc = day_end_local.tz_convert("UTC")

            if sim_results.empty:
                buy_assets = 0
                sell_assets = 0
                profit = 0.0
            else:
                buy_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] >= day_start_utc)
                    & (sim_results["BuyTime"] < day_end_utc)
                )
                sell_mask = (
                    sim_results["SellTime"].notna()
                    & (sim_results["SellTime"] >= day_start_utc)
                    & (sim_results["SellTime"] < day_end_utc)
                )

                # Count simulator transactions/tickers bought or sold that day.
                buy_assets = int(buy_mask.sum())
                sell_assets = int(sell_mask.sum())

                profit = float(
                    sim_results.loc[
                        sell_mask,
                        "DiffSellPrice",
                    ]
                    .fillna(0.0)
                    .sum()
                )

            day_rows.append(
                {
                    "Day": day_name,
                    "Buy Assets": buy_assets,
                    "Sell Assets": sell_assets,
                    "Profit": profit,
                }
            )

        previous_week_end = (
            previous_week_monday
            + pd.Timedelta(days=4)
        )
        st.caption(
            "Complete calendar week before the week containing the latest "
            f"collected market-data point: "
            f"{previous_week_monday.strftime('%d.%m.%Y')}–"
            f"{previous_week_end.strftime('%d.%m.%Y')}."
        )

    days_df = pd.DataFrame(day_rows)
    st.dataframe(
        days_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Profit": st.column_config.NumberColumn(
                "Profit",
                format="%.2f%%",
            ),
        },
    )

    st.markdown("**Weeks of the last year**")

    week_rows = []

    if pd.isna(last_collected_time):
        st.info(
            "No collected market-data timestamp is available for the "
            "52-week Sim-Trading summary."
        )
    else:
        last_local = pd.Timestamp(last_collected_time).tz_convert(
            LOCAL_TIMEZONE
        )
        current_week_monday = (
            last_local.normalize()
            - pd.Timedelta(days=last_local.weekday())
        )

        # Use the previous 52 complete calendar weeks so partial current-week
        # activity does not distort comparison between weeks.
        first_week_monday = (
            current_week_monday
            - pd.Timedelta(weeks=52)
        )

        for week_index in range(52):
            week_start_local = (
                first_week_monday
                + pd.Timedelta(weeks=week_index)
            )
            week_end_local = (
                week_start_local
                + pd.Timedelta(days=7)
            )

            week_start_utc = week_start_local.tz_convert("UTC")
            week_end_utc = week_end_local.tz_convert("UTC")

            iso = week_start_local.isocalendar()
            week_number = int(iso.week)

            if sim_results.empty:
                buy_assets = 0
                sell_assets = 0
                profit = 0.0
            else:
                buy_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] >= week_start_utc)
                    & (sim_results["BuyTime"] < week_end_utc)
                )
                sell_mask = (
                    sim_results["SellTime"].notna()
                    & (sim_results["SellTime"] >= week_start_utc)
                    & (sim_results["SellTime"] < week_end_utc)
                )

                buy_assets = int(buy_mask.sum())
                sell_assets = int(sell_mask.sum())
                profit = float(
                    sim_results.loc[
                        sell_mask,
                        "DiffSellPrice",
                    ]
                    .fillna(0.0)
                    .sum()
                )

            week_rows.append(
                {
                    "Week": week_number,
                    "Buy Assets": buy_assets,
                    "Sell Assets": sell_assets,
                    "Profit": profit,
                }
            )

    weeks_df = pd.DataFrame(
        week_rows,
        columns=[
            "Week",
            "Buy Assets",
            "Sell Assets",
            "Profit",
        ],
    )

    st.dataframe(
        weeks_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Profit": st.column_config.NumberColumn(
                "Profit",
                format="%.2f%%",
            ),
        },
    )

    st.caption(
        "Sim-Trading Results: Buy Assets = number of simulator BUY transactions "
        "in the period. Sell Assets = number of simulator SELL transactions in "
        "the period. Profit = sum of DiffSellPrice percentage values for trades "
        "sold in that period. The weekly table contains the previous 52 complete "
        "ISO calendar weeks, based on the simulator trade history currently "
        "available to the dashboard."
    )

'''

section = section.replace(anchor, new_block, 1)

# Renumber existing sections after inserting the new first section.
section = section.replace(
    '# 1. Imported assets that were not tracked at simulationTimestamp.',
    '# 2. Imported assets that were not tracked at simulationTimestamp.',
    1,
)
section = section.replace(
    'st.subheader("1. Non-tracked imported assets")',
    'st.subheader("2. Non-tracked imported assets")',
    1,
)
section = section.replace(
    '# 2. Previous simulator day statistics (03:00 -> 03:00 Europe/Berlin).',
    '# 3. Previous simulator day statistics (03:00 -> 03:00 Europe/Berlin).',
    1,
)
section = section.replace(
    'st.subheader("2. Previous simulator day")',
    'st.subheader("3. Previous simulator day")',
    1,
)
section = section.replace(
    '# 3. Timing of the newest 15-minute market-data block.',
    '# 4. Timing of the newest 15-minute market-data block.',
    1,
)
section = section.replace(
    'st.subheader("3. Latest 15-minute data collection")',
    'st.subheader("4. Latest 15-minute data collection")',
    1,
)

text = text[:logs_start] + section + text[logs_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Logs now starts with Sim-Trading Results containing "
    "Days of the last week and Weeks of the last year tables."
)
