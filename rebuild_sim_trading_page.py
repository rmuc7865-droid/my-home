#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

radio_pos = text.find("st.sidebar.radio")
if radio_pos == -1:
    raise SystemExit("ERROR: st.sidebar.radio page selector not found; no changes written.")

radio_end = text.find("\n)", radio_pos)
if radio_end == -1:
    radio_end = radio_pos + 1200

radio_block = text[radio_pos:radio_end + 2]
if '"Simulation"' not in radio_block:
    raise SystemExit("ERROR: Simulation option not found in sidebar radio block; no changes written.")

radio_block_new = radio_block.replace('"Simulation"', '"Sim-Trading"', 1)
text = text[:radio_pos] + radio_block_new + text[radio_end + 2:]

start_marker = 'elif page == "Simulation":'
end_marker = 'elif page == "Trade Analysis":'

start = text.find(start_marker)
end = text.find(end_marker, start)

if start == -1 or end == -1:
    raise SystemExit("ERROR: Simulation page block could not be located; no changes written.")

new_page = '''elif page == "Sim-Trading":
    st.subheader("Sim-Trading")

    st.caption(
        "Current simulator trading state for every active market ticker. "
        "OPEN means the latest simulated trade is still open; CLOSED means "
        "the latest simulated trade has been sold; NO TRADE means no "
        "simulation BUY has been recorded for that ticker."
    )

    try:
        simulation_rows = load_simulation_cached()
    except Exception as exc:
        st.error(f"Cannot load simulation data: {exc}")
    else:
        simulation_df = pd.DataFrame(simulation_rows)

        market_df = df[
            df["asset_type"].isin(["stock", "crypto"])
        ].copy()

        if market_df.empty:
            st.info("No market ticker data are available.")
        else:
            market_df["timestamp"] = pd.to_datetime(
                market_df["timestamp"],
                utc=True,
                errors="coerce",
            )
            market_df["received_at"] = pd.to_datetime(
                market_df["received_at"],
                utc=True,
                errors="coerce",
            )

            newest_received = market_df["received_at"].max()
            latest_received = market_df.groupby("ticker")["received_at"].max()

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

            configured_now = set()
            for ticker in latest_received.index:
                ticker_key = str(ticker).strip().upper()
                configured_sources = CONFIGURED_TICKER_SOURCES.get(
                    ticker_key, set()
                )
                if configured_sources & healthy_sources:
                    configured_now.add(str(ticker))

            latest_market_rows = (
                market_df.sort_values(["timestamp", "id"])
                .drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")
            )

            market_reference_time = pd.to_datetime(
                market_df["timestamp"].max(),
                utc=True,
            )

            stale_tickers = set()
            for ticker in configured_now:
                if ticker not in latest_market_rows.index:
                    stale_tickers.add(ticker)
                    continue

                latest_row = latest_market_rows.loc[ticker]
                latest_bar_time = pd.to_datetime(
                    latest_row.get("timestamp"),
                    utc=True,
                    errors="coerce",
                )

                if pd.isna(latest_bar_time):
                    stale_tickers.add(ticker)
                    continue

                asset_type = str(
                    latest_row.get("asset_type") or ""
                ).strip().lower()

                maximum_bar_age = (
                    pd.Timedelta(minutes=60)
                    if asset_type == "crypto"
                    else pd.Timedelta(hours=72)
                )

                if market_reference_time - latest_bar_time > maximum_bar_age:
                    stale_tickers.add(ticker)

            active_tickers = sorted(configured_now - stale_tickers)

            active_market_df = market_df[
                market_df["ticker"].astype(str).isin(active_tickers)
            ].copy()

            live_now = (
                build_live_overview(active_market_df)
                if not active_market_df.empty
                else pd.DataFrame()
            )

            current_price_map = {}
            if not live_now.empty and "Ticker" in live_now.columns:
                current_price_map = (
                    live_now
                    .dropna(subset=["Ticker"])
                    .drop_duplicates(subset=["Ticker"], keep="first")
                    .set_index("Ticker")["Price"]
                    .to_dict()
                )

            last_time_map = (
                active_market_df
                .sort_values(["timestamp", "id"])
                .drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")["timestamp"]
                .to_dict()
                if not active_market_df.empty
                else {}
            )

            if simulation_df.empty:
                latest_sim = pd.DataFrame(columns=[
                    "Ticker",
                    "TickerName",
                    "BuyTime",
                    "BuyPriceEUR",
                    "SellTime",
                    "SellPriceEUR",
                    "RelativeDifference",
                    "Status",
                ])
            else:
                for column in ["BuyTime", "SellTime"]:
                    if column not in simulation_df.columns:
                        simulation_df[column] = pd.NaT
                    simulation_df[column] = pd.to_datetime(
                        simulation_df[column],
                        utc=True,
                        errors="coerce",
                    )

                for column in [
                    "BuyPriceEUR",
                    "SellPriceEUR",
                    "RelativeDifference",
                ]:
                    if column not in simulation_df.columns:
                        simulation_df[column] = pd.NA
                    simulation_df[column] = pd.to_numeric(
                        simulation_df[column],
                        errors="coerce",
                    )

                if "Ticker" not in simulation_df.columns:
                    simulation_df["Ticker"] = ""

                simulation_df["Ticker"] = (
                    simulation_df["Ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                latest_sim = (
                    simulation_df
                    .sort_values(["Ticker", "BuyTime"], na_position="first")
                    .groupby("Ticker", as_index=False)
                    .tail(1)
                    .copy()
                )

            shown = pd.DataFrame({"Ticker": active_tickers})

            ticker_name_map = dict(TICKER_NAMES)
            if not latest_sim.empty and "TickerName" in latest_sim.columns:
                sim_name_map = (
                    latest_sim
                    .dropna(subset=["Ticker"])
                    .drop_duplicates(subset=["Ticker"], keep="last")
                    .set_index("Ticker")["TickerName"]
                    .to_dict()
                )
                for ticker, name in sim_name_map.items():
                    if pd.notna(name) and str(name).strip():
                        ticker_name_map[str(ticker)] = str(name).strip()

            shown["TickerName"] = shown["Ticker"].map(
                lambda ticker: ticker_name_map.get(ticker, ticker)
            )

            if not latest_sim.empty:
                merge_columns = [
                    column
                    for column in [
                        "Ticker",
                        "BuyTime",
                        "BuyPriceEUR",
                        "SellTime",
                        "SellPriceEUR",
                        "RelativeDifference",
                        "Status",
                    ]
                    if column in latest_sim.columns
                ]
                shown = shown.merge(
                    latest_sim[merge_columns],
                    on="Ticker",
                    how="left",
                )

            for column, default in {
                "BuyTime": pd.NaT,
                "BuyPriceEUR": pd.NA,
                "SellTime": pd.NaT,
                "SellPriceEUR": pd.NA,
                "RelativeDifference": pd.NA,
                "Status": pd.NA,
            }.items():
                if column not in shown.columns:
                    shown[column] = default

            shown["LastTimeRaw"] = shown["Ticker"].map(last_time_map)
            shown["LastPriceRaw"] = shown["Ticker"].map(current_price_map)

            shown["BuyPriceEUR"] = pd.to_numeric(
                shown["BuyPriceEUR"], errors="coerce"
            )
            shown["SellPriceEUR"] = pd.to_numeric(
                shown["SellPriceEUR"], errors="coerce"
            )
            shown["LastPriceRaw"] = pd.to_numeric(
                shown["LastPriceRaw"], errors="coerce"
            )

            shown["SimStatus"] = shown["Status"].map(
                lambda value:
                str(value).strip().upper()
                if pd.notna(value) and str(value).strip()
                else "NO TRADE"
            )

            shown["DiffSellPriceRaw"] = pd.to_numeric(
                shown["RelativeDifference"],
                errors="coerce",
            )

            shown["DiffLastPriceRaw"] = pd.NA
            valid_last_price = (
                shown["BuyPriceEUR"].notna()
                & shown["LastPriceRaw"].notna()
                & (shown["BuyPriceEUR"] > 0)
            )
            shown.loc[
                valid_last_price,
                "DiffLastPriceRaw",
            ] = (
                (
                    shown.loc[valid_last_price, "LastPriceRaw"]
                    / shown.loc[valid_last_price, "BuyPriceEUR"]
                )
                - 1.0
            ) * 100.0

            def format_elapsed(delta):
                if pd.isna(delta):
                    return "—"
                total_minutes = int(delta.total_seconds() // 60)
                if total_minutes < 0:
                    return "—"
                days, remainder = divmod(total_minutes, 24 * 60)
                hours, minutes = divmod(remainder, 60)
                return f"{days} days {hours:02d}:{minutes:02d}"

            shown["DiffSellTime"] = (
                shown["SellTime"] - shown["BuyTime"]
            ).map(format_elapsed)

            shown["LastTimeRaw"] = pd.to_datetime(
                shown["LastTimeRaw"],
                utc=True,
                errors="coerce",
            )
            shown["DiffLastTime"] = (
                shown["LastTimeRaw"] - shown["BuyTime"]
            ).map(format_elapsed)

            def format_local_time(value):
                value = pd.to_datetime(
                    value,
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(value):
                    return "—"
                return value.tz_convert(
                    LOCAL_TIMEZONE
                ).strftime("%Y-%m-%d %H:%M")

            shown["InitTime"] = shown["BuyTime"].map(format_local_time)
            shown["SellTimeDisplay"] = shown["SellTime"].map(
                format_local_time
            )
            shown["LastTime"] = shown["LastTimeRaw"].map(
                format_local_time
            )

            def format_eur(value):
                return f"€{float(value):+.2f}" if pd.notna(value) else "—"

            def format_percent(value):
                return f"{float(value):+.2f}%" if pd.notna(value) else "—"

            shown["InitPrice"] = shown["BuyPriceEUR"].map(format_eur)
            shown["SellPrice"] = shown["SellPriceEUR"].map(format_eur)
            shown["LastPrice"] = shown["LastPriceRaw"].map(format_eur)
            shown["DiffSellPrice"] = shown["DiffSellPriceRaw"].map(
                format_percent
            )
            shown["DiffLastPrice"] = shown["DiffLastPriceRaw"].map(
                format_percent
            )

            status_order = {
                "OPEN": 0,
                "CLOSED": 1,
                "NO TRADE": 2,
            }
            shown["_StatusSort"] = shown["SimStatus"].map(
                lambda value: status_order.get(value, 99)
            )
            shown["_InitSort"] = shown["BuyTime"]

            shown = shown.sort_values(
                by=["_StatusSort", "_InitSort", "Ticker"],
                ascending=[True, False, True],
                na_position="last",
            )

            display = shown[
                [
                    "SimStatus",
                    "Ticker",
                    "TickerName",
                    "InitTime",
                    "InitPrice",
                    "SellTimeDisplay",
                    "SellPrice",
                    "DiffSellTime",
                    "DiffSellPrice",
                    "LastTime",
                    "LastPrice",
                    "DiffLastTime",
                    "DiffLastPrice",
                ]
            ].rename(
                columns={
                    "SellTimeDisplay": "SellTime",
                }
            )

            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                "SimStatus is the state of the ticker's latest simulator trade: "
                "OPEN, CLOSED, or NO TRADE. InitTime and InitPrice are the latest "
                "simulated BUY time and EUR price. SellTime and SellPrice are the "
                "simulated SELL values when the latest trade is closed."
            )

            st.caption(
                "DiffSellTime = SellTime - InitTime. DiffSellPrice is the "
                "percentage change from InitPrice to SellPrice. LastTime and "
                "LastPrice are the latest collected market-data time and current "
                "EUR price. DiffLastTime = LastTime - InitTime. DiffLastPrice is "
                "the percentage change from InitPrice to LastPrice. Elapsed times "
                "are shown as D days HH:MM."
            )

'''

text = text[:start] + new_page + text[end:]

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Simulation renamed to Sim-Trading and rebuilt as "
    "a current per-ticker simulator-state table."
)
