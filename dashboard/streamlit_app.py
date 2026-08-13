from __future__ import annotations

import os
from datetime import datetime, timezone

import math
import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.getenv("MONITOR_API_URL", "http://api:8000")
API_KEY = os.getenv("MONITOR_API_KEY", "CHANGE_ME")
HEADERS = {"X-API-Key": API_KEY}
LOCAL_TIMEZONE = "Europe/Berlin"

st.set_page_config(page_title="Home Monitor", page_icon="🏠", layout="wide")
st.title("🏠 Home Monitor")


def api_get(path: str, params: dict | None = None):
    response = httpx.get(f"{API_URL}{path}", headers=HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def api_post(path: str):
    response = httpx.post(f"{API_URL}{path}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


try:
    measurements = api_get("/api/v1/measurements", {"limit": 5000})
    alerts = api_get("/api/v1/alerts", {"limit": 500})
except Exception as exc:
    st.error(f"Cannot reach monitoring API: {exc}")
    st.stop()

measurement_rows: list[dict] = []

for record in measurements:
    metadata = record.get("metadata") or {}

    ticker = metadata.get("ticker")

    if not ticker:
        ticker = record["system"]

    asset_type = metadata.get("asset_type")

    if not asset_type:
        if record["system"] == "crypto":
            asset_type = "crypto"
        elif record["system"] == "polygon":
            asset_type = "stock"
        else:
            asset_type = "other"

    base = {
        "id": record["id"],
        "system": record["system"],
        "device": record["device"],
        "ticker": ticker,
        "asset_type": asset_type,
        "timestamp": pd.to_datetime(
            record["timestamp"],
            utc=True,
        ),
        "received_at": pd.to_datetime(
            record.get("received_at"),
            utc=True,
        ),
        "eur_usd": metadata.get("eur_usd"),
    }


    measurement_rows.append(
        {
            **base,
            **record["measurements"],
        }
    )

df = pd.DataFrame(measurement_rows)
alerts_df = pd.DataFrame(alerts)

def build_live_overview(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()

    rows = []

    for ticker, ticker_df in data.groupby("ticker"):
        ticker_df = (
            ticker_df
            .sort_values(["timestamp", "id"])
            .drop_duplicates(
                subset=["timestamp"],
                keep="last",
            )
        )

        latest = ticker_df.iloc[-1]
        latest_time = latest["timestamp"]
        baseline_time = latest_time - pd.Timedelta(hours=2)

        price = latest.get("close")
        eur_usd = latest.get("eur_usd")

        sell_time_seconds = latest.get(
            "sell_time_seconds"
        )

        sell_time_over_seconds = latest.get(
            "sell_time_over_seconds"
        )

        buy_qty = None
        buy_value_eur = None

        if (
            latest["asset_type"] == "stock"
            and pd.notna(price)
            and pd.notna(eur_usd)
            and float(price) > 0
            and float(eur_usd) > 0
        ):
            price_eur = (
                float(price)
                / float(eur_usd)
            )

            buy_qty = math.ceil(
                10000.0 / price_eur
            )

            buy_value_eur = (
                buy_qty * price_eur
            )

        row = {
            "Ticker": ticker,
            "Type": latest["asset_type"],
            "_timestamp": latest_time,
            #"Time": latest_time.strftime("%H:%M"),
            "Time": latest_time.tz_convert(
                 LOCAL_TIMEZONE
            ).strftime("%H:%M"),
            "Price": price_eur,
            "BuyQty": buy_qty,
            "BuyValueEUR": buy_value_eur,
            "SellTime": sell_time_seconds,
            "SellTimeOver": sell_time_over_seconds,
            "OpenB": None,
            "LowB": None,
            "HighB": None,
            "CloseB": None,
        }

        candidate_rows = ticker_df[
            (
                ticker_df["timestamp"]
                >= baseline_time - pd.Timedelta(minutes=30)
            )
            &
            (
                ticker_df["timestamp"]
                <= baseline_time + pd.Timedelta(minutes=30)
            )
        ].copy()

        if candidate_rows.empty:
            rows.append(row)
            continue

        candidate_rows["baseline_distance"] = (
            candidate_rows["timestamp"] - baseline_time
        ).abs()

        baseline = (
            candidate_rows
            .sort_values(
                ["baseline_distance", "timestamp"],
                ascending=[True, True],
            )
            .iloc[0]
        )

        baseline_age = (
             baseline_time - baseline["timestamp"]
        ).total_seconds()
        if baseline_age > 1800:
            rows.append(row)
            continue

        baseline_close = baseline.get("close")

        if pd.isna(baseline_close) or baseline_close == 0:
            rows.append(row)
            continue

        window = ticker_df[
            (ticker_df["timestamp"] >= baseline_time)
            & (ticker_df["timestamp"] <= latest_time)
        ]

        row["OpenB"] = 0.0

        if "high" in window.columns:
            high_value = pd.to_numeric(
                window["high"],
                errors="coerce",
            ).max()

            if pd.notna(high_value):
                row["HighB"] = (
                    high_value / baseline_close - 1
                ) * 100

        if "low" in window.columns:
            low_value = pd.to_numeric(
                window["low"],
                errors="coerce",
            ).min()

            if pd.notna(low_value):
                row["LowB"] = (
                    low_value / baseline_close - 1
                ) * 100

        if pd.notna(price):
            row["CloseB"] = (
                price / baseline_close - 1
            ) * 100

        rows.append(row)

    result = pd.DataFrame(rows)

    if not result.empty:
        result = result.sort_values(
            by=["_timestamp", "CloseB"],
            ascending=[False, False],
            na_position="last",
        )
    return result

page = st.sidebar.radio("Page", ["Live Overview", "Alerts", "Historical Trends", "Simulation", "System Health"])
if st.sidebar.button("Refresh now", use_container_width=True):
    st.rerun()

local_now = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
st.sidebar.caption(
    f"Last loaded: "
    f"{local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
)

if page == "Live Overview":
    if df.empty:
        st.info("No measurements received yet.")

    else:
        open_alerts = (
            0
            if alerts_df.empty
            else int(
                (~alerts_df["acknowledged"]).sum()
            )
        )

        #
        # Determine currently tracked assets using collection
        # time, not market-bar time.
        #
        newest_received = df["received_at"].max()

        active_cutoff = (
            newest_received
            - pd.Timedelta(minutes=30)
        )

        latest_received = (
            df.groupby("ticker")["received_at"]
            .max()
        )

        active_tickers = latest_received[
            latest_received >= active_cutoff
        ].index

        active_df = df[
            df["ticker"].isin(active_tickers)
        ].copy()

        live = build_live_overview(active_df)

        newest_market_data = (
            active_df["timestamp"].max()
        )

        sources = active_df["system"].nunique()
        tracked_assets = len(active_tickers)

        #
        # Summary
        #
        cols = st.columns(5)

        #cols[0].metric(
        #    "Newest data",
        #    newest_market_data.strftime("%H:%M UTC"),
        #)
        newest_local = newest_market_data.tz_convert(
            LOCAL_TIMEZONE
        )

        cols[0].metric(
            "Newest data",
            newest_local.strftime("%H:%M %Z"),
        )

        cols[1].metric(
            "Sources",
            sources,
        )

        cols[2].metric(
            "Tracked assets",
            tracked_assets,
        )

        cols[3].metric(
            "Open alerts",
            open_alerts,
        )

        cols[4].metric(
            "Measurements",
            len(df),
        )

        #
        # Latest values
        #
        st.subheader("Latest values")

        if live.empty:
            st.info("No active assets available.")

        else:
            display_live = live.copy()

            def format_sell_time(row):
                value = row["SellTime"]

                if pd.notna(value):
                    return f"{int(value)} s"

                over = row["SellTimeOver"]

                if pd.notna(over):
                    return f"> {int(over)} s"

                return "—"

            display_live["SellTime"] = (
                display_live.apply(
                    format_sell_time,
                    axis=1,
                )
            )

            display_live["BuyQty"] = (
                pd.to_numeric(
                    display_live["BuyQty"],
                    errors="coerce",
                )
                .map(
                    lambda value:
                    str(int(value))
                    if pd.notna(value)
                    else "—"
                )
            )

            display_live["BuyValueEUR"] = (
                pd.to_numeric(
                    display_live["BuyValueEUR"],
                    errors="coerce",
                )
                .map(
                    lambda value:
                    f"€{value:,.0f}"
                    if pd.notna(value)
                    else "—"
                )
            )

            display_live["Price"] = (
                pd.to_numeric(
                    display_live["Price"],
                    errors="coerce",
                )
                .map(
                    lambda value:
                    f"€{value:,.2f}"
                    if pd.notna(value)
                    else "—"
                )
            )

            for column in [
                "OpenB",
                "HighB",
                "LowB",
                "CloseB",
            ]:
                display_live[column] = (
                    pd.to_numeric(
                        display_live[column],
                        errors="coerce",
                    )
                    .map(
                        lambda value:
                        f"{value:+.2f}%"
                        if pd.notna(value)
                        else "—"
                    )
                )

            st.dataframe(
                display_live[
                    [
                        "Ticker",
                        "Type",
                        "Time",
                        "Price",
                        "BuyQty",
                        "BuyValueEUR",
                        "SellTime",
                        "OpenB",
                        "LowB",
                        "HighB",
                        "CloseB",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        #st.caption(
        #    "SellTime will be populated after trade-level "
        #    "collection is added."
        #)
        #st.caption(
        #    "LiquidityAge = seconds since the most recent "
        #    "15-minute bar with estimated traded notional "
        #    "of at least $10,000 (volume × VWAP)."
        #)
        st.caption(
            "SellTime is an estimated liquidity time for a €10,000 "
            "position using recent 1-second market turnover and a "
            "10% participation assumption. > 1800 s means insufficient "
            "estimated liquidity in the previous 30 minutes. "
            "BuyQty is the minimum whole-share quantity whose estimated "
            "value is at least €10,000 using the ECB EUR/USD reference rate."
        )

elif page == "Alerts":
    if alerts_df.empty:
        st.success("No alerts recorded.")
    else:
        alerts_df["created_at"] = pd.to_datetime(alerts_df["created_at"], utc=True)
        show_open = st.toggle("Only unacknowledged", value=True)
        shown = alerts_df[~alerts_df["acknowledged"]] if show_open else alerts_df
        st.dataframe(
            shown[["id", "created_at", "severity", "system", "rule_name", "actual_value", "acknowledged"]],
            use_container_width=True,
            hide_index=True,
        )
        alert_id = st.number_input("Alert ID to acknowledge", min_value=1, step=1)
        if st.button("Acknowledge alert"):
            try:
                api_post(f"/api/v1/alerts/{int(alert_id)}/acknowledge")
                st.success("Alert acknowledged.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

elif page == "Historical Trends":
    if df.empty:
        st.info("No historical data available.")

    else:
        historical = df.copy()
        ranking_df = build_live_overview(historical)

        if not ranking_df.empty:
            ranked_assets = ranking_df["Ticker"].tolist()
        else:
            ranked_assets = sorted(
                historical["ticker"]
                .dropna()
                .astype(str)
                .unique()
            )

        available_assets = sorted(
            historical["ticker"]
            .dropna()
            .astype(str)
            .unique()
        )

        if not available_assets:
            st.info("No assets available.")

        else:
            control_cols = st.columns(4)

            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Single",
                    "Top K",
                    "All",
                    "Custom",
                ],
            )

            if asset_mode == "Single":
                selected_assets = [
                    st.selectbox(
                        "Asset",
                        ranked_assets,
                    )
                ]

            elif asset_mode == "Top K":
                k = st.number_input(
                        "Number of tickers",
                        min_value=1,
                        max_value=len(ranked_assets),
                        value=min(10, len(ranked_assets)),
                        step=1,
                )

                selected_assets = ranked_assets[:int(k)]
                st.caption(
                    "Selected from the current Live Overview ranking: "
                    + ", ".join(selected_assets)
                )

            elif asset_mode == "All":
                selected_assets = ranked_assets
                st.caption(
                    f"Selected all {len(selected_assets)} tracked assets."
                )

            else:
                selected_assets = st.multiselect(
                    "Assets",
                    ranked_assets,
                    default=ranked_assets[:5],
                )

            asset_df = (
                historical[
                    historical["ticker"].isin(selected_assets)
                ]
            #asset_df = (
            #    historical[
            #        historical["ticker"] == asset
            #    ]
                .sort_values(["timestamp", "id"])
                .drop_duplicates(
                    subset=["ticker", "timestamp"],
                    keep="last",
                )
                .copy()
            )

            excluded_numeric = {
                "id",
                "sell_time_seconds",
                "sell_time_over_seconds",
                "eur_usd",
            }

            preferred_metrics = [
                "close",
                "open",
                "high",
                "low",
                "vwap",
                "volume",
                "transactions",
            ]

            numeric_columns = [
                column
                for column in asset_df.select_dtypes(
                    include="number"
                ).columns
                if column not in excluded_numeric
            ]

            metric_options = [
                metric
                for metric in preferred_metrics
                if metric in numeric_columns
            ]

            metric_options += [
                metric
                for metric in numeric_columns
                if metric not in metric_options
            ]

            if not metric_options:
                st.info(
                    "This asset has no numeric measurements "
                    "available to chart."
                )

            else:
                default_metric_index = (
                    metric_options.index("close")
                    if "close" in metric_options
                    else 0
                )

                metric = control_cols[1].selectbox(
                    "Metric",
                    metric_options,
                    index=default_metric_index,
                )

                range_label = control_cols[2].selectbox(
                    "Range",
                    [
                        "2h",
                        "6h",
                        "12h",
                        "24h",
                        "48h",
                        "7d",
                        "All",
                    ],
                    index=3,
                )

                norm = control_cols[3].radio(
                    "Norm",
                    ["Absolute", "Relative"],
                    horizontal=True,
                )

                range_map = {
                    "2h": pd.Timedelta(hours=2),
                    "6h": pd.Timedelta(hours=6),
                    "12h": pd.Timedelta(hours=12),
                    "24h": pd.Timedelta(hours=24),
                    "48h": pd.Timedelta(hours=48, unit="h"),
                    "7d": pd.Timedelta(days=7),
                }

                chart_columns = [
                    "ticker",
                    "timestamp",
                    metric,
                ]

                if "eur_usd" in asset_df.columns:
                    chart_columns.append("eur_usd")

                chart_df = asset_df[
                    chart_columns
                ].copy()

                chart_df[metric] = pd.to_numeric(
                    chart_df[metric],
                    errors="coerce",
                )
                if "eur_usd" in chart_df.columns:
                    chart_df["eur_usd"] = pd.to_numeric(
                        chart_df["eur_usd"],
                        errors="coerce",
                    )

                chart_df = chart_df.dropna(
                    subset=[metric]
                )

                if chart_df.empty:
                    st.info(
                        "No values available for the selected "
                        "asset and metric."
                    )

                else:
                    latest_time = chart_df[
                        "timestamp"
                    ].max()

                    if range_label != "All":
                        start_time = (
                            latest_time
                            - range_map[range_label]
                        )

                        chart_df = chart_df[
                            chart_df["timestamp"]
                            >= start_time
                        ].copy()

                    if chart_df.empty:
                        st.info(
                            "No measurements available in "
                            "the selected range."
                        )

                    else:
                        chart_df["Local Time"] = (
                            chart_df["timestamp"]
                            .dt.tz_convert(
                                LOCAL_TIMEZONE
                            )
                        )

                    y_column = metric
                    y_label = metric

                    price_metrics = {
                        "open",
                        "high",
                        "low",
                        "close",
                        "vwap",
                    }

                    if (
                        norm == "Absolute"
                        and metric in price_metrics
                        and "eur_usd" in chart_df.columns
                    ):
                        chart_df["Value EUR"] = (
                            chart_df[metric]
                            / chart_df["eur_usd"]
                        )

                        y_column = "Value EUR"
                        y_label = f"{metric} (EUR)"

                    elif norm == "Relative":
                        chart_df["Relative %"] = (
                            chart_df
                            .groupby("ticker")[metric]
                            .transform(
                                lambda series:
                                (
                                    series / series.iloc[0] - 1
                                ) * 100
                                if len(series) > 0
                                and pd.notna(series.iloc[0])
                                and series.iloc[0] != 0
                                else float("nan")
                            )
                        )

                        y_column = "Relative %"
                        y_label = f"{metric} change (%)"

                    figure = px.line(
                        chart_df,
                        x="Local Time",
                        y=y_column,
                        color="ticker",
                        markers=True,
                        title=(
                            f"{metric} — "
                            f"{len(selected_assets)} asset(s) "
                            f"({range_label}, {norm})"
                        ),
                    )

                    figure.update_layout(
                        xaxis_title="Local time",
                        yaxis_title=y_label,
                        hovermode="x unified",
                    )

                    st.plotly_chart(
                        figure,
                        use_container_width=True,
                    )

                    first_time = chart_df[
                        "Local Time"
                    ].iloc[0]

                    last_time = chart_df[
                        "Local Time"
                    ].iloc[-1]

                    info_cols = st.columns(4)

                    info_cols[0].metric(
                        "Points",
                        len(chart_df),
                    )

                    info_cols[1].metric(
                        "From",
                        first_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )

                    info_cols[2].metric(
                        "To",
                        last_time.strftime(
                            "%d.%m %H:%M"
                        ),
                    )

                    if norm == "Relative":
                        latest_relative = chart_df[
                            "Relative %"
                        ].iloc[-1]

                        info_cols[3].metric(
                            "Change",
                            f"{latest_relative:+.2f}%",
                        )

                    else:
                        latest_value = chart_df[
                            metric
                        ].iloc[-1]

                        info_cols[3].metric(
                            "Latest",
                            f"{latest_value:,.4f}",
                        )

elif page == "Simulation":
    st.subheader("Simulation")
    st.caption(
        "BUY/SELL results proposed by the system and sent to Telegram during the last year. "
        "Open BUY signals remain visible until a corresponding SELL is recorded."
    )
    try:
        simulation_rows = api_get("/api/v1/simulation", {"days": 365, "include_open": True})
    except Exception as exc:
        st.error(f"Cannot load simulation data: {exc}")
    else:
        simulation_df = pd.DataFrame(simulation_rows)
        if simulation_df.empty:
            st.info("No BUY/SELL simulation records are available for the last year yet.")
        else:
            simulation_df["BuyTime"] = pd.to_datetime(simulation_df["BuyTime"], utc=True)
            simulation_df["SellTime"] = pd.to_datetime(simulation_df["SellTime"], utc=True, errors="coerce")

            closed = simulation_df[simulation_df["Status"] == "CLOSED"].copy()
            open_count = int((simulation_df["Status"] == "OPEN").sum())
            wins = int((closed["RelativeDifference"] > 0).sum()) if not closed.empty else 0
            win_rate = (wins / len(closed) * 100.0) if len(closed) else 0.0
            total_abs = closed["AbsoluteDifference"].sum() if not closed.empty else 0.0
            avg_rel = closed["RelativeDifference"].mean() if not closed.empty else 0.0

            cols = st.columns(4)
            cols[0].metric("Closed trades", len(closed))
            cols[1].metric("Open trades", open_count)
            cols[2].metric("Win rate", f"{win_rate:.1f}%")
            cols[3].metric("Total difference", f"€{total_abs:,.2f}")
            st.caption(f"Average relative difference across closed trades: {avg_rel:.2f}%")

            ticker_options = ["All"] + sorted(simulation_df["Ticker"].dropna().unique().tolist())
            selected_ticker = st.selectbox("Ticker", ticker_options)
            status_filter = st.multiselect("Status", ["OPEN", "CLOSED"], default=["OPEN", "CLOSED"])
            shown = simulation_df.copy()
            if selected_ticker != "All":
                shown = shown[shown["Ticker"] == selected_ticker]
            if status_filter:
                shown = shown[shown["Status"].isin(status_filter)]
            else:
                shown = shown.iloc[0:0]

            required_columns = [
                "Ticker",
                "TickerName",
                "BuyTime",
                "SellTime",
                "RelativeDifference",
                "AbsoluteDifference",
                "Status",
            ]
            display = shown[required_columns].copy()
            display["RelativeDifference"] = display["RelativeDifference"].map(
                lambda value: None if pd.isna(value) else round(float(value), 2)
            )
            display["AbsoluteDifference"] = display["AbsoluteDifference"].map(
                lambda value: None if pd.isna(value) else round(float(value), 2)
            )
            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "RelativeDifference": st.column_config.NumberColumn("RelativeDifference (%)", format="%.2f%%"),
                    "AbsoluteDifference": st.column_config.NumberColumn("AbsoluteDifference (EUR)", format="€ %.2f"),
                },
            )

elif page == "System Health":
    if df.empty:
        st.warning("No systems have reported data.")
    else:
        now = pd.Timestamp.now(tz="UTC")
        latest = df.groupby("system")["timestamp"].max().reset_index()
        latest["age_minutes"] = (now - latest["timestamp"]).dt.total_seconds() / 60
        latest["status"] = latest["age_minutes"].apply(
            lambda age: "OK" if age <= 30 else ("STALE" if age <= 120 else "OFFLINE")
        )
        st.dataframe(latest, use_container_width=True, hide_index=True)
