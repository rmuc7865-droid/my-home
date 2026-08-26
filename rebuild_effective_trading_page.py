#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# Rename sidebar option.
radio_pos = text.find("st.sidebar.radio")
if radio_pos == -1:
    raise SystemExit("ERROR: sidebar page selector not found; no changes written.")
radio_end = text.find("\n)", radio_pos)
if radio_end == -1:
    radio_end = radio_pos + 1600
radio_block = text[radio_pos:radio_end + 2]
if '"Trade Analysis"' in radio_block:
    radio_block = radio_block.replace('"Trade Analysis"', '"Effective Trading"', 1)
elif '"Effective Trading"' not in radio_block:
    raise SystemExit("ERROR: Trade Analysis page option not found; no changes written.")
text = text[:radio_pos] + radio_block + text[radio_end + 2:]

# Replace analysis helper and cached wrapper.
helper_start = text.find("def build_trade_analysis(")
cached_start = text.find("@st.cache_data(ttl=300)\ndef build_trade_analysis_cached", helper_start)
cached_end = text.find("\ndef build_live_overview(", cached_start)
if helper_start == -1 or cached_start == -1 or cached_end == -1:
    raise SystemExit("ERROR: build_trade_analysis helper block not found; no changes written.")

new_helper = '''def build_trade_analysis(
    measurements_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    reference_time,
    period,
) -> pd.DataFrame:
    # Build Effective Trading counters on a common 15-minute timeline.
    if measurements_df.empty:
        return pd.DataFrame()

    reference_time = pd.to_datetime(reference_time, utc=True, errors="coerce")
    if pd.isna(reference_time):
        return pd.DataFrame()

    start_time = reference_time - period
    tolerance = pd.Timedelta(minutes=30)
    history_start = start_time - pd.Timedelta(hours=2) - tolerance

    wanted_columns = ["ticker", "timestamp", "close", "id", "asset_type", "eur_usd"]
    available_columns = [c for c in wanted_columns if c in measurements_df.columns]
    market = measurements_df[
        measurements_df["asset_type"].isin(["stock", "crypto"])
    ][available_columns].copy()

    if market.empty:
        return pd.DataFrame()
    if "eur_usd" not in market.columns:
        market["eur_usd"] = pd.NA
    if "asset_type" not in market.columns:
        market["asset_type"] = "stock"
    if "id" not in market.columns:
        market["id"] = range(len(market))

    market["timestamp"] = pd.to_datetime(market["timestamp"], utc=True, errors="coerce")
    market["close"] = pd.to_numeric(market["close"], errors="coerce")
    market["eur_usd"] = pd.to_numeric(market["eur_usd"], errors="coerce")
    market = market[
        market["timestamp"].notna()
        & market["ticker"].notna()
        & market["close"].notna()
        & (market["timestamp"] >= history_start)
        & (market["timestamp"] <= reference_time)
    ].copy()
    if market.empty:
        return pd.DataFrame()

    market["ticker"] = market["ticker"].astype(str).str.strip().str.upper()
    market["timepoint"] = market["timestamp"].dt.round("15min")
    current = (
        market[market["timepoint"] >= start_time]
        .sort_values(["ticker", "timepoint", "timestamp", "id"])
        .drop_duplicates(subset=["ticker", "timepoint"], keep="last")
        .copy()
    )
    if current.empty:
        return pd.DataFrame()

    all_timepoints = pd.date_range(
        start=start_time.ceil("15min"),
        end=reference_time.floor("15min"),
        freq="15min",
        tz="UTC",
    )
    if len(all_timepoints) == 0:
        return pd.DataFrame()

    closeb_parts = []
    for ticker, ticker_current in current.groupby("ticker", sort=False):
        history = market[market["ticker"] == ticker][["timestamp", "close"]].sort_values("timestamp")
        if history.empty:
            continue
        work = ticker_current[["ticker", "timepoint", "close"]].copy()
        work = work.rename(columns={"close": "current_price"})
        work["baseline_target"] = work["timepoint"] - pd.Timedelta(hours=2)
        work = work.sort_values("baseline_target")
        hist = history.rename(columns={"timestamp": "baseline_timestamp", "close": "baseline_price"}).sort_values("baseline_timestamp")

        backward = pd.merge_asof(
            work,
            hist,
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="backward",
            tolerance=tolerance,
        ).rename(columns={"baseline_timestamp": "backward_timestamp", "baseline_price": "backward_price"})
        forward = pd.merge_asof(
            work,
            hist,
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="forward",
            tolerance=tolerance,
        ).rename(columns={"baseline_timestamp": "forward_timestamp", "baseline_price": "forward_price"})

        matched = backward[["ticker", "timepoint", "current_price", "baseline_target", "backward_timestamp", "backward_price"]].copy()
        matched["forward_timestamp"] = pd.to_datetime(forward["forward_timestamp"].reset_index(drop=True), utc=True, errors="coerce")
        matched["forward_price"] = forward["forward_price"].reset_index(drop=True)
        for column in ["baseline_target", "backward_timestamp", "forward_timestamp"]:
            matched[column] = pd.to_datetime(matched[column], utc=True, errors="coerce")

        backward_distance = (matched["baseline_target"] - matched["backward_timestamp"]).abs()
        forward_distance = (matched["forward_timestamp"] - matched["baseline_target"]).abs()
        use_backward = matched["backward_timestamp"].notna() & (
            matched["forward_timestamp"].isna() | (backward_distance <= forward_distance)
        )
        matched["baseline_price"] = matched["forward_price"]
        matched.loc[use_backward, "baseline_price"] = matched.loc[use_backward, "backward_price"]
        matched = matched[matched["baseline_price"].notna() & (matched["baseline_price"] > 0)].copy()
        if matched.empty:
            continue
        matched["CloseB"] = (matched["current_price"] / matched["baseline_price"] - 1.0) * 100.0
        closeb_parts.append(matched[["ticker", "timepoint", "CloseB"]])

    closeb_df = pd.concat(closeb_parts, ignore_index=True) if closeb_parts else pd.DataFrame(columns=["ticker", "timepoint", "CloseB"])

    current["PriceEUR"] = pd.NA
    stock_mask = (
        current["asset_type"].astype(str).str.lower().eq("stock")
        & current["close"].notna()
        & current["eur_usd"].notna()
        & (current["eur_usd"] > 0)
    )
    current.loc[stock_mask, "PriceEUR"] = current.loc[stock_mask, "close"] / current.loc[stock_mask, "eur_usd"]
    crypto_mask = current["asset_type"].astype(str).str.lower().eq("crypto") & current["close"].notna()
    current.loc[crypto_mask, "PriceEUR"] = current.loc[crypto_mask, "close"]
    current_price_lookup = {
        (row.ticker, row.timepoint): row.PriceEUR
        for row in current[["ticker", "timepoint", "PriceEUR"]].itertuples(index=False)
    }

    trades = trades_df.copy()
    if trades.empty:
        trades = pd.DataFrame(columns=["Ticker", "BuyTime", "SellTime", "BuyPriceEUR"])
    else:
        for column in ["BuyTime", "SellTime"]:
            if column not in trades.columns:
                trades[column] = pd.NaT
            trades[column] = pd.to_datetime(trades[column], utc=True, errors="coerce")
        if "Ticker" not in trades.columns:
            trades["Ticker"] = ""
        trades["Ticker"] = trades["Ticker"].astype(str).str.strip().str.upper()
        if "BuyPriceEUR" not in trades.columns:
            trades["BuyPriceEUR"] = pd.NA
        trades["BuyPriceEUR"] = pd.to_numeric(trades["BuyPriceEUR"], errors="coerce")

    buy_map = {tp: set() for tp in all_timepoints}
    sell_map = {tp: set() for tp in all_timepoints}
    for _, trade in trades.iterrows():
        ticker = str(trade.get("Ticker") or "").strip().upper()
        buy_time = trade.get("BuyTime")
        sell_time = trade.get("SellTime")
        if ticker and pd.notna(buy_time):
            buy_point = pd.Timestamp(buy_time).round("15min")
            if buy_point in buy_map:
                buy_map[buy_point].add(ticker)
        if ticker and pd.notna(sell_time):
            sell_point = pd.Timestamp(sell_time).round("15min")
            if sell_point in sell_map:
                sell_map[sell_point].add(ticker)

    result_rows = []
    for timepoint in all_timepoints:
        closeb_at_time = closeb_df[closeb_df["timepoint"] == timepoint]
        for threshold in [2.0, 1.0]:
            tickers = sorted(set(closeb_at_time.loc[closeb_at_time["CloseB"] >= threshold, "ticker"].astype(str)))
            result_rows.append({
                "Time": timepoint,
                "Series": f"CloseB >= {threshold:g}%",
                "Count": len(tickers),
                "Tickers": ", ".join(tickers) if tickers else "—",
            })

        open_tickers = set()
        profitable_open_tickers = set()
        for _, trade in trades.iterrows():
            ticker = str(trade.get("Ticker") or "").strip().upper()
            buy_time = trade.get("BuyTime")
            sell_time = trade.get("SellTime")
            buy_price = trade.get("BuyPriceEUR")
            if not ticker or pd.isna(buy_time):
                continue
            is_open = timepoint >= buy_time and (pd.isna(sell_time) or timepoint < sell_time)
            if not is_open:
                continue
            open_tickers.add(ticker)
            current_price = current_price_lookup.get((ticker, timepoint))
            if pd.notna(buy_price) and float(buy_price) > 0 and pd.notna(current_price) and float(current_price) > float(buy_price):
                profitable_open_tickers.add(ticker)

        open_sorted = sorted(open_tickers)
        profit_sorted = sorted(profitable_open_tickers)
        buy_sorted = sorted(buy_map.get(timepoint, set()))
        sell_sorted = sorted(sell_map.get(timepoint, set()))
        for series, tickers in [
            ("OPEN", open_sorted),
            ("OPEN & Profit", profit_sorted),
            ("BUY", buy_sorted),
            ("SELL", sell_sorted),
        ]:
            result_rows.append({
                "Time": timepoint,
                "Series": series,
                "Count": len(tickers),
                "Tickers": ", ".join(tickers) if tickers else "—",
            })

    return pd.DataFrame(result_rows)


@st.cache_data(ttl=300)
def build_trade_analysis_cached(
    _measurements_df: pd.DataFrame,
    _trades_df: pd.DataFrame,
    reference_time,
) -> pd.DataFrame:
    return build_trade_analysis(
        measurements_df=_measurements_df,
        trades_df=_trades_df,
        reference_time=reference_time,
        period=pd.Timedelta(days=7),
    )

'''

text = text[:helper_start] + new_helper + text[cached_end + 1:]

# Replace page.
page_start = text.find('elif page == "Trade Analysis":')
if page_start == -1:
    page_start = text.find('elif page == "Effective Trading":')
page_end = text.find('elif page == "System Health":', page_start)
if page_start == -1 or page_end == -1:
    raise SystemExit("ERROR: Trade Analysis page block not found; no changes written.")

new_page = '''elif page == "Effective Trading":
    st.header("Effective Trading")

    range_label = st.selectbox(
        "Range",
        ["7 days", "1 day", "6 hours", "2 hours"],
        index=0,
    )
    range_map = {
        "7 days": pd.Timedelta(days=7),
        "1 day": pd.Timedelta(days=1),
        "6 hours": pd.Timedelta(hours=6),
        "2 hours": pd.Timedelta(hours=2),
    }

    counter_options = [
        "CloseB >= 2%",
        "CloseB >= 1%",
        "OPEN",
        "OPEN & Profit",
        "BUY",
        "SELL",
    ]
    selected_counters = st.multiselect(
        "Counters",
        counter_options,
        default=["CloseB >= 2%", "OPEN"],
    )

    st.caption(
        "Use Counters to compare market breadth with the simulator trading profile. "
        "CloseB counters measure how many tickers are at least 1% or 2% above "
        "their approximately two-hour baseline. OPEN counts simulated positions "
        "that are open. OPEN & Profit counts OPEN positions whose market price "
        "is above InitPrice. BUY and SELL count simulator transactions at each "
        "15-minute timepoint."
    )

    market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    if market_df.empty:
        st.info("No Effective Trading market data available.")
    else:
        reference_time = pd.to_datetime(
            market_df["timestamp"], utc=True, errors="coerce"
        ).max()

        try:
            simulation_rows = load_simulation_cached()
        except Exception as exc:
            st.error(f"Cannot load simulation data: {exc}")
        else:
            simulation_df = pd.DataFrame(simulation_rows)
            full_analysis_df = build_trade_analysis_cached(
                df, simulation_df, reference_time
            )
            analysis_start = reference_time - range_map[range_label]
            analysis_df = full_analysis_df[
                pd.to_datetime(full_analysis_df["Time"], utc=True, errors="coerce") >= analysis_start
            ].copy()
            analysis_df = analysis_df[
                analysis_df["Series"].isin(selected_counters)
            ].copy()

            if not selected_counters:
                st.info("Select at least one Counter to display the diagram.")
            elif analysis_df.empty:
                st.info("No Effective Trading data are available for the selected range.")
            else:
                analysis_df["TimeLocal"] = pd.to_datetime(
                    analysis_df["Time"], utc=True, errors="coerce"
                ).dt.tz_convert(LOCAL_TIMEZONE)

                polygon_config = TRADING_WINDOWS.get("US") or {}
                polygon_open_intervals = []
                if polygon_config.get("enabled", True):
                    polygon_timezone = str(
                        polygon_config.get("timezone", "America/New_York")
                    )
                    polygon_tz = ZoneInfo(polygon_timezone)

                    def _minutes_from_hhmm(value, default):
                        raw = str(value or default)
                        hour_text, minute_text = raw.split(":", 1)
                        return int(hour_text) * 60 + int(minute_text)

                    configured_starts = [
                        _minutes_from_hhmm(polygon_config.get("buy_start"), "00:00"),
                        _minutes_from_hhmm(polygon_config.get("sell_start"), "00:00"),
                    ]
                    configured_ends = [
                        _minutes_from_hhmm(polygon_config.get("buy_end"), "23:59"),
                        _minutes_from_hhmm(polygon_config.get("sell_end"), "23:59"),
                    ]
                    open_minute = min(configured_starts)
                    close_minute = max(configured_ends)

                    raw_weekdays = polygon_config.get("open_weekdays")
                    allowed_weekdays = (
                        {"mon", "tue", "wed", "thu", "fri"}
                        if raw_weekdays is None
                        else {str(v).strip().lower()[:3] for v in raw_weekdays}
                    )
                    closed_dates = {
                        str(v) for v in (polygon_config.get("closed_dates") or [])
                    }

                    local_start = analysis_start.tz_convert(polygon_tz).normalize()
                    local_end = reference_time.tz_convert(polygon_tz).normalize()
                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                        tz=polygon_timezone,
                    ):
                        weekday_key = local_day.strftime("%a").lower()[:3]
                        date_key = local_day.strftime("%Y-%m-%d")
                        if weekday_key not in allowed_weekdays or date_key in closed_dates:
                            continue
                        interval_start = local_day + pd.Timedelta(minutes=open_minute)
                        interval_end = local_day + pd.Timedelta(minutes=close_minute)
                        polygon_open_intervals.append({
                            "StartLocal": interval_start.tz_convert(LOCAL_TIMEZONE),
                            "EndLocal": interval_end.tz_convert(LOCAL_TIMEZONE),
                        })

                base = alt.Chart(analysis_df).encode(
                    x=alt.X("TimeLocal:T", title="Time"),
                    y=alt.Y("Count:Q", title="Number of tickers", scale=alt.Scale(zero=True)),
                    color=alt.Color("Series:N", title="Counter", sort=counter_options),
                )
                lines = base.mark_line()
                points = base.mark_circle(size=55).encode(
                    tooltip=[
                        alt.Tooltip("TimeLocal:T", title="Time", format="%Y-%m-%d %H:%M"),
                        alt.Tooltip("Series:N", title="Counter"),
                        alt.Tooltip("Count:Q", title="Count"),
                        alt.Tooltip("Tickers:N", title="Tickers"),
                    ]
                )

                layers = []
                if polygon_open_intervals:
                    polygon_open_df = pd.DataFrame(polygon_open_intervals)
                    open_bands = alt.Chart(polygon_open_df).mark_rect(opacity=0.08).encode(
                        x=alt.X("StartLocal:T", title="Time"),
                        x2="EndLocal:T",
                        tooltip=[
                            alt.Tooltip("StartLocal:T", title="Polygon open", format="%Y-%m-%d %H:%M"),
                            alt.Tooltip("EndLocal:T", title="Polygon close", format="%Y-%m-%d %H:%M"),
                        ],
                    )
                    layers.append(open_bands)
                layers.extend([lines, points])
                chart = alt.layer(*layers).properties(height=500).interactive()
                st.altair_chart(chart, use_container_width=True)

                polygon_window_text = "unavailable"
                if polygon_config:
                    polygon_window_text = (
                        f"{polygon_config.get('timezone', 'America/New_York')}: "
                        f"BUY {polygon_config.get('buy_start', '—')}–{polygon_config.get('buy_end', '—')}, "
                        f"SELL {polygon_config.get('sell_start', '—')}–{polygon_config.get('sell_end', '—')}"
                    )

                st.caption(
                    f"Selected range: {range_label}. Each node represents a 15-minute timepoint. "
                    "Shaded vertical bands identify timepoints inside the configured US/Polygon "
                    "opening interval. The band uses the current Settings calendar and spans the "
                    "earliest configured BUY/SELL start through the latest BUY/SELL end. "
                    f"Current Polygon settings: {polygon_window_text}."
                )
                st.caption(
                    "Counter definitions: CloseB >= 2% and CloseB >= 1% count tickers whose "
                    "market price is at least that percentage above the approximately two-hour "
                    "baseline. OPEN counts simulator positions active at the timepoint. "
                    "OPEN & Profit counts OPEN positions with market price > InitPrice. BUY and "
                    "SELL count simulator buy/sell actions occurring at the timepoint."
                )

'''

text = text[:page_start] + new_page + text[page_end:]
path.write_text(text, encoding="utf-8")
print("SUCCESS: Trade Analysis renamed to Effective Trading; Range/Counters/chart/opening-hour bands updated.")
