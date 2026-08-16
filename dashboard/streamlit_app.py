from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import json
import math
import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
import altair as alt
import yaml

from shared.trading_decisions import (
    evaluate_sell_history,
    format_duration,
    trading_window_info,
)

API_URL = os.getenv("MONITOR_API_URL", "http://api:8000")
API_KEY = os.getenv("MONITOR_API_KEY", "CHANGE_ME")
HEADERS = {"X-API-Key": API_KEY}
LOCAL_TIMEZONE = "Europe/Berlin"

st.set_page_config(page_title="Home Monitor", page_icon="🏠", layout="wide")
st.title("🏠 Home Monitor")

@st.cache_data(ttl=300)
def load_alerts_cached():
    return api_get(
        "/api/v1/alerts",
        {
            "limit": 500,
        },
    )

@st.cache_data(ttl=30)
def load_simulation_cached():
    payload = api_get(
        "/api/v1/simulation",
        {
            "days": 0,
            "include_open": True,
        },
    )

    if isinstance(payload, dict):
        return (
            payload.get("trades")
            or payload.get("rows")
            or payload.get("items")
            or []
        )

    return payload or []

def api_get(path: str, params: dict | None = None):
    response = httpx.get(f"{API_URL}{path}", headers=HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=300)
def load_measurement_df_cached():
    measurements = api_get(
        "/api/v1/measurements",
        {
            "limit": 50000,
        },
    )

    measurement_rows = []

    for record in measurements:
        metadata = (
            record.get("metadata")
            or {}
        )

        ticker = metadata.get(
            "ticker"
        )

        if not ticker:
            ticker = record["system"]

        asset_type = metadata.get(
            "asset_type"
        )

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
            "eur_usd": metadata.get(
                "eur_usd"
            ),
        }

        measurement_rows.append(
            {
                **base,
                **record["measurements"],
            }
        )

    return pd.DataFrame(
        measurement_rows
    )

def api_post(path: str):
    response = httpx.post(f"{API_URL}{path}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()

try:
    df = load_measurement_df_cached()
    alerts = load_alerts_cached()

except Exception as exc:
    st.error(
        f"Cannot reach monitoring API: {exc}"
    )
    st.stop()

alerts_df = pd.DataFrame(alerts)

def load_ticker_names() -> dict[str, str]:
    path = Path("/app/config/zero.json")

    if not path.exists():
        return {}

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}

    names = {}

    if isinstance(payload, list):
        rows = payload

    elif isinstance(payload, dict):
        rows = (
            payload.get("tickers")
            or payload.get("instruments")
            or payload.get("rows")
            or []
        )

    else:
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        ticker = str(
            row.get("ticker")
            or row.get("Ticker")
            or ""
        ).strip().upper()

        name = str(
            row.get("name")
            or row.get("Name")
            or ticker
        ).strip()

        if ticker:
            names[ticker] = name or ticker

    return names

TICKER_NAMES = load_ticker_names()


def load_trading_config() -> dict:
    path = Path("/app/server/telegram_notifications.yaml")
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / "server" / "telegram_notifications.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_market_regions() -> dict[str, str]:
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


TRADING_CONFIG = load_trading_config()
TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
BUY_MIN_CLOSEB_COUNT = int(BUY_CONFIG.get("minimum_closeb_count", BUY_CONFIG.get("minimum_closeb_ge2_count", 6)))
BUY_MIN_CLOSEB_PERCENT = float(BUY_CONFIG.get("minimum_closeb_percent", 2.0))
SELL_CONFIG = TRADING_CONFIG.get("sell") or {}


def market_region_for_ticker(ticker: str, asset_type: str) -> str | None:
    if str(asset_type).lower() == "crypto":
        return "CRYPTO"
    return MARKET_REGIONS.get(str(ticker).upper())


def format_local_timestamp(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return (
        pd.to_datetime(value, utc=True)
        .tz_convert(LOCAL_TIMEZONE)
        .strftime("%Y-%m-%d %H:%M")
    )


def build_trade_analysis(
    measurements_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    reference_time,
    period,
) -> pd.DataFrame:
    if measurements_df.empty:
        return pd.DataFrame()

    reference_time = pd.to_datetime(
        reference_time,
        utc=True,
    )

    start_time = (
        reference_time
        - period
    )

    tolerance = pd.Timedelta(
        30,
        unit="min",
    )

    history_start = (
        start_time
        - pd.Timedelta(
            2,
            unit="h",
        )
        - tolerance
    )

    #
    # Market data required for the visible period
    # plus the 2-hour CloseB baseline.
    #
    market = measurements_df[
        measurements_df[
            "asset_type"
        ].isin(
            ["stock", "crypto"]
        )
    ][
        [
            "ticker",
            "timestamp",
            "close",
            "id",
        ]
    ].copy()

    market["timestamp"] = pd.to_datetime(
        market["timestamp"],
        utc=True,
        errors="coerce",
    )

    market["close"] = pd.to_numeric(
        market["close"],
        errors="coerce",
    )

    market = market[
        (market["timestamp"] >= history_start)
        & (
            market["timestamp"]
            <= reference_time
        )
    ].dropna(
        subset=[
            "ticker",
            "timestamp",
            "close",
        ]
    )

    if market.empty:
        return pd.DataFrame()

    market["ticker"] = (
        market["ticker"]
        .astype(str)
        .str.upper()
    )

    market["timepoint"] = (
        market["timestamp"]
        .dt.round("15min")
    )

    #
    # One current observation per ticker/timepoint.
    #
    current = (
        market[
            market["timepoint"]
            >= start_time
        ]
        .sort_values(
            [
                "ticker",
                "timepoint",
                "timestamp",
                "id",
            ]
        )
        .drop_duplicates(
            subset=[
                "ticker",
                "timepoint",
            ],
            keep="last",
        )
        .copy()
    )

    if current.empty:
        return pd.DataFrame()

    #
    # All 15-minute points where real market
    # measurements exist. OPEN curve will only
    # have nodes at these times.
    #
    market_timepoints = (
        current["timepoint"]
        .drop_duplicates()
        .sort_values()
    )

    #
    # -----------------------------
    # Curve 1: CloseB >= configured C2 percentage
    # -----------------------------
    #
    # Find the measurement nearest to 2 hours
    # before each current timepoint, within
    # +/-30 minutes.
    #
    closeb_parts = []

    for ticker, ticker_current in (
        current.groupby(
            "ticker",
            sort=False,
        )
    ):
        history = market[
            market["ticker"] == ticker
        ][
            [
                "timestamp",
                "close",
            ]
        ].sort_values(
            "timestamp"
        )

        if history.empty:
            continue

        work = ticker_current[
            [
                "ticker",
                "timepoint",
                "close",
            ]
        ].copy()

        work = work.rename(
            columns={
                "close": "current_price",
            }
        )

        work["baseline_target"] = (
            work["timepoint"]
            - pd.Timedelta(
                2,
                unit="h",
            )
        )

        work = work.sort_values(
            "baseline_target"
        )

        #
        # Previous baseline candidate.
        #
        backward = pd.merge_asof(
            work,
            history.rename(
                columns={
                    "timestamp": (
                        "baseline_timestamp"
                    ),
                    "close": (
                        "baseline_price"
                    ),
                }
            ).sort_values(
                "baseline_timestamp"
            ),
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="backward",
            tolerance=tolerance,
        )

        backward = backward.rename(
            columns={
                "baseline_timestamp": (
                    "backward_timestamp"
                ),
                "baseline_price": (
                    "backward_price"
                ),
            }
        )

        #
        # Following baseline candidate.
        #
        forward = pd.merge_asof(
            work,
            history.rename(
                columns={
                    "timestamp": (
                        "baseline_timestamp"
                    ),
                    "close": (
                        "baseline_price"
                    ),
                }
            ).sort_values(
                "baseline_timestamp"
            ),
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="forward",
            tolerance=tolerance,
        )

        forward = forward.rename(
            columns={
                "baseline_timestamp": (
                    "forward_timestamp"
                ),
                "baseline_price": (
                    "forward_price"
                ),
            }
        )

        matched = backward[
            [
                "ticker",
                "timepoint",
                "current_price",
                "baseline_target",
                "backward_timestamp",
                "backward_price",
            ]
        ].copy()

        matched[
            "forward_timestamp"
        ] = pd.to_datetime(
            forward[
                "forward_timestamp"
            ].reset_index(
                drop=True
            ),
            utc=True,
            errors="coerce",
        )

        matched[
            "forward_price"
        ] = (
            forward[
                "forward_price"
            ]
            .reset_index(
                drop=True
            )
        )

        #
        # Ensure all datetime columns use the
        # same UTC-aware dtype before subtraction.
        #
        matched["baseline_target"] = pd.to_datetime(
            matched["baseline_target"],
            utc=True,
            errors="coerce",
        )

        matched["backward_timestamp"] = pd.to_datetime(
            matched["backward_timestamp"],
            utc=True,
            errors="coerce",
        )

        matched["forward_timestamp"] = pd.to_datetime(
            matched["forward_timestamp"],
            utc=True,
            errors="coerce",
        )

        backward_distance = (
            matched["baseline_target"]
            - matched["backward_timestamp"]
        ).abs()

        forward_distance = (
            matched["forward_timestamp"]
            - matched["baseline_target"]
        ).abs()

        #
        # Prefer the earlier point if both
        # candidates are equally distant,
        # matching the previous implementation.
        #
        use_backward = (
            matched[
                "backward_timestamp"
            ].notna()
            & (
                matched[
                    "forward_timestamp"
                ].isna()
                | (
                    backward_distance
                    <= forward_distance
                )
            )
        )

        matched["baseline_price"] = (
            matched["forward_price"]
        )

        matched.loc[
            use_backward,
            "baseline_price",
        ] = matched.loc[
            use_backward,
            "backward_price",
        ]

        valid = (
            matched[
                "baseline_price"
            ].notna()
            & (
                matched[
                    "baseline_price"
                ] > 0
            )
        )

        matched = matched[
            valid
        ].copy()

        if matched.empty:
            continue

        matched["CloseB"] = (
            matched["current_price"]
            / matched["baseline_price"]
            - 1.0
        ) * 100.0

        closeb_parts.append(
            matched[
                [
                    "ticker",
                    "timepoint",
                    "CloseB",
                ]
            ]
        )

    if closeb_parts:
        closeb_df = pd.concat(
            closeb_parts,
            ignore_index=True,
        )
    else:
        closeb_df = pd.DataFrame(
            columns=[
                "ticker",
                "timepoint",
                "CloseB",
            ]
        )

    qualifying = closeb_df[
        closeb_df["CloseB"] >= BUY_MIN_CLOSEB_PERCENT
    ].copy()

    closeb_summary = (
        qualifying.groupby(
            "timepoint"
        )["ticker"]
        .agg(
            lambda values:
            sorted(
                set(values)
            )
        )
    )

    #
    # CloseB curve gets a node at every
    # 15-minute point in the selected period,
    # including zero-count points.
    #
    all_timepoints = pd.date_range(
        start=start_time,
        end=reference_time,
        freq="15min",
        tz="UTC",
    )

    result_rows = []

    for timepoint in all_timepoints:
        tickers = (
            closeb_summary.get(
                timepoint,
                [],
            )
        )

        result_rows.append(
            {
                "Time": timepoint,
                "Series": f"CloseB ≥ {BUY_MIN_CLOSEB_PERCENT:g}%",
                "Count": len(tickers),
                "Tickers": (
                    ", ".join(tickers)
                    if tickers
                    else "—"
                ),
            }
        )

    #
    # -----------------------------
    # Curve 2: OPEN trades
    # -----------------------------
    #
    # Only create OPEN points where real market
    # data exists, preserving gaps.
    #
    trades = trades_df.copy()

    if not trades.empty:
        trades["BuyTime"] = pd.to_datetime(
            trades["BuyTime"],
            utc=True,
            errors="coerce",
        )

        trades["SellTime"] = pd.to_datetime(
            trades["SellTime"],
            utc=True,
            errors="coerce",
        )

        trades["Ticker"] = (
            trades["Ticker"]
            .astype(str)
            .str.upper()
        )

    open_map = {
        timepoint: set()
        for timepoint in market_timepoints
    }

    #
    # There are relatively few trades, so looping
    # over trades is much cheaper than looping
    # over every timepoint and every trade.
    #
    for _, trade in trades.iterrows():
        buy_time = trade.get(
            "BuyTime"
        )

        sell_time = trade.get(
            "SellTime"
        )

        ticker = str(
            trade.get("Ticker")
            or ""
        )

        if (
            not ticker
            or pd.isna(buy_time)
        ):
            continue

        active_points = market_timepoints[
            market_timepoints
            >= buy_time
        ]

        if pd.notna(sell_time):
            active_points = active_points[
                active_points
                < sell_time
            ]

        for timepoint in active_points:
            open_map[
                timepoint
            ].add(
                ticker
            )

    for timepoint in market_timepoints:
        tickers = sorted(
            open_map.get(
                timepoint,
                set(),
            )
        )

        result_rows.append(
            {
                "Time": timepoint,
                "Series": "OPEN",
                "Count": len(tickers),
                "Tickers": (
                    ", ".join(tickers)
                    if tickers
                    else "—"
                ),
            }
        )

    return pd.DataFrame(
        result_rows
    )

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
        price_eur = None

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
            "TickerName": TICKER_NAMES.get(
                str(ticker).upper(),
                ticker,
            ),
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
            "MarketRegion": market_region_for_ticker(ticker, latest["asset_type"]),
            "CanBuy": False,
            "BuyInfo": "",
            "ShouldSell": False,
            "CanSellNow": False,
            "BoughtBefore": None,
            "SellTiming": "",
            "SellInfo": "",
        }

        market_config = TRADING_WINDOWS.get(row["MarketRegion"]) if row["MarketRegion"] else None
        if pd.notna(price) and float(price) > 0:
            decision = evaluate_sell_history(
                ticker_df=ticker_df,
                latest_time=latest_time,
                current_price=float(price),
                movement_percent=float(SELL_CONFIG.get("movement_percent", 1.1)),
                c5_hours=float(SELL_CONFIG.get("c5_hours", 24.0)),
            )
            row["ShouldSell"] = decision.should_sell
            row["BoughtBefore"] = decision.bought_before
            if market_config:
                sell_window = trading_window_info(
                    pd.Timestamp.now(tz="UTC"),
                    market_config,
                    "sell",
                )
                row["CanSellNow"] = sell_window.is_open
                if sell_window.is_open:
                    row["SellTiming"] = f"RemainingTime={format_duration(sell_window.remaining_time)}"
                else:
                    row["SellTiming"] = f"FirstNextSellTime={format_local_timestamp(sell_window.first_next_time)}"
            row["SellInfo"] = (
                f"C4={decision.c4_satisfied} (> {float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% drop from peak; MaxTime={format_local_timestamp(decision.max_time)}); "
                f"C5={decision.c5_satisfied} (full {float(SELL_CONFIG.get('c5_hours', 24.0)):g}h within +/-{float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% of current price; LastOneProcTime={format_local_timestamp(decision.last_one_proc_time)}); "
                f"{row['SellTiming']}"
            )

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
        closeb_ge2_count = int(
            (pd.to_numeric(result["CloseB"], errors="coerce") >= BUY_MIN_CLOSEB_PERCENT).sum()
        )
        c2_satisfied = closeb_ge2_count >= BUY_MIN_CLOSEB_COUNT
        for index, current in result.iterrows():
            market_region = current.get("MarketRegion")
            market_config = TRADING_WINDOWS.get(market_region) if market_region else None
            c1_satisfied = False
            remaining_text = "—"
            if market_config:
                buy_window = trading_window_info(
                    current["_timestamp"],
                    market_config,
                    "buy",
                )
                c1_satisfied = buy_window.is_open
                if buy_window.is_open:
                    remaining_text = format_duration(buy_window.remaining_time)
            result.at[index, "CanBuy"] = bool(c1_satisfied and c2_satisfied)
            result.at[index, "BuyInfo"] = (
                f"C1={c1_satisfied} (RemainingTime={remaining_text}); "
                f"C2={c2_satisfied} (CloseB>={BUY_MIN_CLOSEB_PERCENT:g}%: {closeb_ge2_count}/{BUY_MIN_CLOSEB_COUNT})"
            )

        result = result.sort_values(
            by=["_timestamp", "CloseB"],
            ascending=[False, False],
            na_position="last",
        )
    return result

@st.cache_data(ttl=300)
def build_live_overview_cached(
    _data: pd.DataFrame,
    data_version,
) -> pd.DataFrame:
    return build_live_overview(
        _data
    )

page = st.sidebar.radio("Page", ["Live Overview", "Historical Trends", "Simulation", "Trade Analysis", "System Health", "Alerts"])
if st.sidebar.button(
    "Refresh now",
    use_container_width=True,
):
    st.cache_data.clear()
    st.rerun()

local_now = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
st.sidebar.caption(
    f"Last loaded: "
    f"{local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
)

if page == "Live Overview":
    market_df = df[
        df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

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
        newest_received = (
            market_df["received_at"].max()
        )

        active_cutoff = (
            newest_received
            - pd.Timedelta(minutes=30)
        )

        latest_received = (
            market_df.groupby("ticker")[
                "received_at"
            ]
            .max()
        )
 
        active_tickers = latest_received[
            latest_received >= active_cutoff
        ].index

        active_df = market_df[
            market_df["ticker"].isin(
                active_tickers
            )
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
            len(market_df),
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

            display_live["BoughtBefore"] = display_live["BoughtBefore"].map(format_local_timestamp)

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
                        "TickerName",
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
                        "CanBuy",
                        "BuyInfo",
                        "ShouldSell",
                        "CanSellNow",
                        "BoughtBefore",
                        "SellTiming",
                        "SellInfo",
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
        st.caption(
            "OpenB, LowB, HighB and CloseB describe the "
            "approximately 2-hour price block ending at Time. "
            "All four values are percentage changes relative "
            "to the baseline price approximately 2 hours earlier. "
            "OpenB is the start of the block, LowB is the minimum "
            "price during the block, HighB is the maximum price, "
            "and CloseB is the latest Price collected at Time."
        )
        st.caption(
            "ZERO decision support: CanBuy = C1 and C2. C1 checks the configured BUY window; "
            f"C2 requires at least {BUY_MIN_CLOSEB_COUNT} live tickers with CloseB >= {BUY_MIN_CLOSEB_PERCENT:g}%. "
            f"C4 is satisfied after a drop of more than {float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% from the sampled peak since InitTime. "
            f"C5 is satisfied after a full {float(SELL_CONFIG.get('c5_hours', 24.0)):g} hours when every sampled price stays within +/-{float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% of the current price. "
            "ShouldSell = C4 or C5. "
            "CanSellNow only indicates whether the configured SELL window is open now. "
            "If it is closed, SellTiming shows FirstNextSellTime; otherwise it shows RemainingTime."
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
    trends_df = df[
        df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

    if trends_df.empty:
        st.info("No historical data available.")

    else:
        historical = trends_df.copy()
        data_version = (
            historical["timestamp"].max()
        )

        data_version = (
            historical["timestamp"].max()
        )

        ranking_df = build_live_overview_cached(
            historical,
            data_version,
        )        

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

            #
            # Current Simulation OPEN positions
            #
            open_position_tickers = []
            open_buy_times = {}

            try:
                simulation_trades = (
                    load_simulation_cached()
                )

                for trade in simulation_trades:
                    if trade.get("SellTime"):
                        continue

                    ticker = str(
                        trade.get("Ticker") or ""
                    ).strip().upper()

                    buy_time = pd.to_datetime(
                        trade.get("BuyTime"),
                        utc=True,
                        errors="coerce",
                    )

                    if ticker:
                        open_position_tickers.append(
                            ticker
                        )

                        if pd.notna(buy_time):
                            open_buy_times[
                                ticker
                            ] = buy_time

                open_position_tickers = sorted(
                    set(open_position_tickers)
                )

            except Exception as exc:
                st.warning(
                    "Cannot load Simulation OPEN positions: "
                    f"{exc}"
                )

            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Single",
                    "Top K",
                    "OPEN positions",
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

            elif asset_mode == "OPEN positions":
                selected_assets = [
                    ticker
                    for ticker
                    in open_position_tickers
                    if ticker
                    in available_assets
                ]
                if selected_assets:
                    st.caption(
                        "Current Simulation OPEN positions: "
                        + ", ".join(
                            selected_assets
                        )
                    )
                else:
                    st.info(
                        "There are currently no OPEN "
                        "Simulation positions with "
                        "Historical Trends data."
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
                    ["Relative", "Absolute"],
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

                    #
                    # Load currently OPEN Simulation trades.
                    # Their Historical Trends nodes at/after
                    # BuyTime will be highlighted in green.
                    #
                    open_buy_times = {}

                    try:
                        simulation_rows = (
                            load_simulation_cached()
                        )

                        simulation_df = pd.DataFrame(
                            simulation_rows
                        )
                        simulation_trades = (
                            load_simulation_cached()
                        )
                        for trade in simulation_trades:
                            if trade.get("SellTime"):
                                continue

                            ticker = str(
                                trade.get("Ticker")
                                or ""
                            ).strip().upper()

                            buy_time = pd.to_datetime(
                                trade.get("BuyTime"),
                                utc=True,
                                errors="coerce",
                            )

                            if (
                                ticker
                                and pd.notna(buy_time)
                            ):
                                open_buy_times[
                                    ticker
                                ] = buy_time

                    except Exception as exc:
                        st.warning(
                            "Cannot load OPEN trades for "
                            f"chart highlighting: {exc}"
                        )

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
                    #
                    # Highlight observations belonging to
                    # currently OPEN trades.
                    #
                    open_points_parts = []

                    for ticker, buy_time in (
                        open_buy_times.items()
                    ):
                        ticker_points = chart_df[
                            (
                                chart_df["ticker"]
                                .astype(str)
                                .str.upper()
                                == ticker
                            )
                            & (
                                chart_df["timestamp"]
                                >= buy_time
                            )
                        ].copy()

                        if not ticker_points.empty:
                            open_points_parts.append(
                                ticker_points
                            )

                    if open_points_parts:
                        open_points = pd.concat(
                            open_points_parts,
                            ignore_index=True,
                        )

                        figure.add_scatter(
                            x=open_points[
                                "Local Time"
                            ],
                            y=open_points[
                                y_column
                            ],
                            mode="markers",
                            marker={
                                "color": "green",
                                "size": 9,
                            },
                            name="OPEN position",
                            customdata=open_points[
                                ["ticker"]
                            ],
                            hovertemplate=(
                                "<b>%{customdata[0]}</b>"
                                "<br>OPEN position"
                                "<br>%{x}"
                                "<br>%{y:.2f}"
                                "<extra></extra>"
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

                    info_cols = st.columns(3)

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

elif page == "Simulation":
    st.subheader("Simulation")

    st.caption(
        "BUY/SELL results proposed by the system and sent to Telegram. "
        "Open BUY signals remain visible until a corresponding SELL is recorded."
    )

    try:
        simulation_rows = load_simulation_cached()
    except Exception as exc:
        st.error(
            f"Cannot load simulation data: {exc}"
        )
    else:
        simulation_df = pd.DataFrame(
            simulation_rows
        )
        if simulation_df.empty:
            st.info(
                "No BUY/SELL simulation records "
                "are available yet."
            )

        else:
            #
            # Normalize timestamps.
            #
            simulation_df["BuyTime"] = pd.to_datetime(
                simulation_df["BuyTime"],
                utc=True,
                errors="coerce",
            )

            simulation_df["SellTime"] = pd.to_datetime(
                simulation_df["SellTime"],
                utc=True,
                errors="coerce",
            )

            #
            # Numeric columns.
            #
            for column in [
                "BuyPriceEUR",
                "SellPriceEUR",
                "RelativeDifference",
            ]:
                if column in simulation_df.columns:
                    simulation_df[column] = (
                        pd.to_numeric(
                            simulation_df[column],
                            errors="coerce",
                        )
                    )

            #
            # Correct known incomplete ticker names.
            #
            ticker_name_overrides = {
                "COIN": "Coinbase",
            }

            if "TickerName" in simulation_df.columns:
                simulation_df["TickerName"] = (
                    simulation_df.apply(
                        lambda row:
                        ticker_name_overrides.get(
                            row["Ticker"],
                            row["TickerName"],
                        )
                        if (
                            pd.isna(row["TickerName"])
                            or str(row["TickerName"]).strip()
                            == str(row["Ticker"]).strip()
                        )
                        else row["TickerName"],
                        axis=1,
                    )
                )

            #
            # Use exactly the same idea as Live Overview
            # to determine the newest current market-data
            # timestamp.
            #
            reference_time = pd.Timestamp.now(
                tz="UTC"
            )

            current_price_map = {}
            decision_maps = {}

            if not df.empty:
                newest_received = (
                    df["received_at"].max()
                )

                active_cutoff = (
                    newest_received
                    - pd.Timedelta(minutes=30)
                )

                latest_received = (
                    df.groupby("ticker")[
                        "received_at"
                    ]
                    .max()
                )

                active_tickers = (
                    latest_received[
                        latest_received
                        >= active_cutoff
                    ].index
                )

                active_df = df[
                    df["ticker"].isin(
                        active_tickers
                    )
                ].copy()

                if not active_df.empty:
                    reference_time = (
                        active_df[
                            "timestamp"
                        ].max()
                    )

                    live_now = (
                        build_live_overview(
                            active_df
                        )
                    )

                    if not live_now.empty:
                        current_price_map = (
                            live_now
                            .dropna(
                                subset=["Ticker"]
                            )
                            .drop_duplicates(
                                subset=["Ticker"],
                                keep="first",
                            )
                            .set_index("Ticker")[
                                "Price"
                            ]
                            .to_dict()
                        )
                        live_index = (
                            live_now
                            .dropna(subset=["Ticker"])
                            .drop_duplicates(subset=["Ticker"], keep="first")
                            .set_index("Ticker")
                        )
                        for decision_column in [
                            "CanBuy",
                            "BuyInfo",
                            "ShouldSell",
                            "CanSellNow",
                            "BoughtBefore",
                            "SellTiming",
                            "SellInfo",
                        ]:
                            if decision_column in live_index.columns:
                                decision_maps[decision_column] = live_index[decision_column].to_dict()


            #
            # Period selector.
            #
            period = st.selectbox(
                "Period",
                [
                    "Day",
                    "Week",
                    "Month",
                    "Year",
                    "All",
                ],
                index=4,
            )

            period_delta = {
                "Day": pd.Timedelta(days=1),
                "Week": pd.Timedelta(days=7),
                "Month": pd.Timedelta(days=30),
                "Year": pd.Timedelta(days=365),
                "All": None,
            }[period]

            if period_delta is None:
                cutoff_time = None

            else:
                cutoff_time = (
                    reference_time
                    - period_delta
                )

            #
            # Closed trades belong to a period according
            # to SellTime.
            #
            closed_all = simulation_df[
                simulation_df["Status"]
                == "CLOSED"
            ].copy()

            if cutoff_time is None:
                closed_period = (
                    closed_all.copy()
                )

            else:
                closed_period = closed_all[
                    (
                        closed_all[
                            "SellTime"
                        ] >= cutoff_time
                    )
                    & (
                        closed_all[
                            "SellTime"
                        ] <= reference_time
                    )
                ].copy()

            #
            # Open trades belong to a period according
            # to BuyTime.
            #
            open_all = simulation_df[
                simulation_df["Status"]
                == "OPEN"
            ].copy()

            if cutoff_time is None:
                open_period = (
                    open_all.copy()
                )

            else:
                open_period = open_all[
                    (
                        open_all[
                            "BuyTime"
                        ] >= cutoff_time
                    )
                    & (
                        open_all[
                            "BuyTime"
                        ] <= reference_time
                    )
                ].copy()

            #
            # Summary statistics.
            #
            closed_count = len(
                closed_period
            )

            open_count = len(
                open_period
            )

            wins = (
                int(
                    (
                        closed_period[
                            "RelativeDifference"
                        ] > 0
                    ).sum()
                )
                if closed_count
                else 0
            )

            win_rate = (
                wins
                / closed_count
                * 100.0
                if closed_count
                else 0.0
            )

            avg_return = (
                closed_period[
                    "RelativeDifference"
                ].mean()
                if closed_count
                else 0.0
            )

            cols = st.columns(4)

            cols[0].metric(
                "Closed trades",
                closed_count,
            )

            cols[1].metric(
                "Open trades",
                open_count,
            )

            cols[2].metric(
                "Win rate",
                f"{win_rate:.1f}%",
            )

            cols[3].metric(
                "Average return",
                f"{avg_return:+.2f}%",
            )
            reference_local = (
                reference_time.tz_convert(
                    LOCAL_TIMEZONE
                )
            )

            st.caption(
                "Win rate = percentage of closed "
                "trades with a positive RelDiff. "
                "Average return = arithmetic mean "
                "of RelDiff for closed trades. "
                f"Period reference: "
                f"{reference_local.strftime('%Y-%m-%d %H:%M %Z')}."
            )

            #
            # Apply period to table as well.
            #
            shown = pd.concat(
                [
                    closed_period,
                    open_period,
                ],
                ignore_index=True,
            )

            #
            # User filters.
            #
            ticker_options = (
                ["All"]
                + sorted(
                    shown["Ticker"]
                    .dropna()
                    .unique()
                    .tolist()
                )
            )

            selected_ticker = st.selectbox(
                "Ticker",
                ticker_options,
            )

            status_filter = st.multiselect(
                "Status",
                [
                    "OPEN",
                    "CLOSED",
                ],
                default=[
                    "OPEN",
                    "CLOSED",
                ],
            )

            if selected_ticker != "All":
                shown = shown[
                    shown["Ticker"]
                    == selected_ticker
                ]

            if status_filter:
                shown = shown[
                    shown["Status"].isin(
                        status_filter
                    )
                ]

            else:
                shown = shown.iloc[0:0]

            #
            # Current EUR price from Live Overview.
            #
            shown = shown.copy()

            for decision_column, mapping in decision_maps.items():
                shown[decision_column] = shown["Ticker"].map(mapping)
            if "BoughtBefore" in shown.columns:
                shown["BoughtBefore"] = shown["BoughtBefore"].map(format_local_timestamp)

            shown["CurrPriceEUR"] = (
                shown["Ticker"].map(
                    current_price_map
                )
            )
            shown["CurrRelDiff"] = pd.NA

            buy_price_eur_numeric = pd.to_numeric(
                shown["BuyPriceEUR"],
                errors="coerce",
            )

            current_price_eur_numeric = pd.to_numeric(
                shown["CurrPriceEUR"],
                errors="coerce",
            )

            valid_prices = (
                buy_price_eur_numeric.notna()
                & current_price_eur_numeric.notna()
                & (buy_price_eur_numeric > 0)
            )

            shown.loc[
                valid_prices,
                "CurrRelDiff",
            ] = (
                (
                    current_price_eur_numeric[
                        valid_prices
                    ]
                    /
                    buy_price_eur_numeric[
                        valid_prices
                    ]
                )
                - 1.0
            ) * 100.0


            #
            # Display column names.
            #
            shown["RelDiff"] = (
                shown[
                    "RelativeDifference"
                ]
            )

            #
            # Local CEST/CET timestamps.
            #
            shown["BuyTime"] = (
                shown["BuyTime"]
                .dt.tz_convert(
                    LOCAL_TIMEZONE
                )
                .dt.strftime(
                    "%Y-%m-%d %H:%M"
                )
            )

            shown["SellTime"] = (
                shown["SellTime"]
                .dt.tz_convert(
                    LOCAL_TIMEZONE
                )
                .dt.strftime(
                    "%Y-%m-%d %H:%M"
                )
            )

            shown["SellTime"] = (
                shown["SellTime"]
                .fillna("—")
            )

            #
            # Human-readable formatting.
            #
            shown["RelDiff"] = (
                shown["RelDiff"].map(
                    lambda value:
                    f"{float(value):+.2f}%"
                    if pd.notna(value)
                    else "—"
                )
            )

            shown["BuyPrice"] = (
                shown["BuyPrice"].map(
                    lambda value:
                    f"{float(value):,.2f}"
                    if pd.notna(value)
                    else "—"
                )
            )

            shown["SellPrice"] = (
                shown["SellPrice"].map(
                    lambda value:
                    f"{float(value):,.2f}"
                    if pd.notna(value)
                    else "—"
                )
            )

            shown["CurrPriceEUR"] = (
                shown["CurrPriceEUR"].map(
                    lambda value:
                    f"{float(value):,.2f}"
                    if pd.notna(value)
                    else "—"
                )
            )
            shown["CurrRelDiff"] = (
                shown["CurrRelDiff"].map(
                    lambda value:
                    f"{float(value):+.2f}%"
                    if pd.notna(value)
                    else "—"
                )
            )

            for column, default_value in {
                "CanBuy": False,
                "ShouldSell": False,
                "CanSellNow": False,
                "BoughtBefore": "—",
                "SellTiming": "—",
            }.items():
                if column not in shown.columns:
                    shown[column] = default_value

            required_columns = [
                "Ticker",
                "TickerName",
                "BuyTime",
                "BuyPriceEUR",
                "CloseB>0",
                "CloseB>2",
                "SellTime",
                "SellPriceEUR",
                "RelDiff",
                "CurrPriceEUR",
                "CurrRelDiff",
                "CanBuy",
                "ShouldSell",
                "CanSellNow",
                "BoughtBefore",
                "SellTiming",
                "SellReason",
                "Status",
            ]

            display = shown[
                required_columns
            ].copy()

            display = display.sort_values(
                by="BuyTime",
                ascending=False,
            )

            st.dataframe(
                display,
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                "SellReason: "
                "Rule1 = after at least 48 hours, SELL if all observed prices "
                "during the first 48 hours after BuyTime remained within ±1% "
                "of BuyPrice; "
                "Rule2 = SELL if CurrentPrice is at least 2% below BuyPrice; "
                "Rule3 = SELL if CurrentPrice is at least 2% below the highest "
                "previously observed price after BuyTime; "
                "multiple rules may appear together, e.g. Rule2+Rule3. "
                "CloseB>0 = number of Live Overview tickers with CloseB > 0% "
                "at BuyTime; "
                "CloseB>2 = number of Live Overview tickers with CloseB > 2% "
                "at BuyTime."
            )
            st.caption(
                "Rule4 = BUY US-tickers after 14:00 and German-tickers after 12:00;"
                "Rule5 = SELL US-tickers before 21:30 and German-tickers before 17:30."
            )

            st.caption(
                "LegacyRule1 = historical SELL created under the previous "
                "Rule1 definition; under the current strategy no "
                "Rule1/Rule2/Rule3 condition existed at that SellTime. "
            )

elif page == "Trade Analysis":
    st.header("Trade Analysis")

    range_label = st.selectbox(
        "Range",
        [
            "7 days",
            "12 hours",
            "6 hours",
            "2 hours",
        ],
        index=0,
    )

    range_map = {
        "7 days": pd.Timedelta(days=7),
        "12 hours": pd.Timedelta(hours=12),
        "6 hours": pd.Timedelta(hours=6),
        "2 hours": pd.Timedelta(hours=2),
    }

    st.caption(
        f"Selected period: {range_label}. "
        "Each node represents a 15-minute timepoint. "
        f"CloseB ≥ {BUY_MIN_CLOSEB_PERCENT:g}% shows the number of market tickers whose "
        f"price was at least {BUY_MIN_CLOSEB_PERCENT:g}% above its approximately 2-hour "
        "baseline. OPEN shows the number of Simulation tickers "
        "that were open at that time. OPEN nodes are omitted "
        "when no market data exists for that timepoint."
    )

    market_df = df[
        df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

    reference_time = (
        pd.to_datetime(
            market_df["timestamp"],
            utc=True,
        ).max()
    )

    try:
        simulation_rows = load_simulation_cached()
    except Exception as exc:
        st.error(
            f"Cannot load simulation data: {exc}"
        )

    else:
        simulation_df = pd.DataFrame(
            simulation_rows
        )

        if simulation_df.empty:
            st.info(
                "No BUY/SELL simulation records "
                "are available yet."
            )

        else:
            full_analysis_df = (
                build_trade_analysis_cached(
                    df,
                    simulation_df,
                    reference_time,
                )
            )

            analysis_start = (
                reference_time
                - range_map[range_label]
            )

            analysis_df = full_analysis_df[
                pd.to_datetime(
                    full_analysis_df["Time"],
                    utc=True,
                )
                >= analysis_start
            ].copy()

            if analysis_df.empty:
                st.info(
                    "No Trade Analysis data available."
                )

            else:
                analysis_df["TimeLocal"] = (
                    pd.to_datetime(
                        analysis_df["Time"],
                        utc=True,
                    )
                    .dt.tz_convert(
                        LOCAL_TIMEZONE
                    )
                )

                base = alt.Chart(
                    analysis_df
                ).encode(
                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                    ),
                    y=alt.Y(
                        "Count:Q",
                        title="Number of tickers",
                        scale=alt.Scale(
                            zero=True,
                        ),
                    ),
                    color=alt.Color(
                        "Series:N",
                        title="",
                    ),
                )

                lines = base.mark_line()

                points = base.mark_circle(
                    size=55,
                ).encode(
                    tooltip=[
                        alt.Tooltip(
                            "TimeLocal:T",
                            title="Time",
                            format=(
                                "%Y-%m-%d %H:%M"
                            ),
                        ),
                        alt.Tooltip(
                            "Series:N",
                            title="Series",
                        ),
                        alt.Tooltip(
                            "Count:Q",
                            title="Count",
                        ),
                        alt.Tooltip(
                            "Tickers:N",
                            title="Tickers",
                        ),
                    ]
                )

                chart = (
                    lines
                    + points
                ).properties(
                    height=500,
                ).interactive()

                st.altair_chart(
                    chart,
                    use_container_width=True,
                )

elif page == "System Health":
    if df.empty:
        st.warning(
            "No systems have reported data."
        )

    else:
        now = pd.Timestamp.now(
            tz="UTC"
        )

        latest = (
            df.groupby("system")
            .agg(
                last_market_time=(
                    "timestamp",
                    "max",
                ),
                last_received_time=(
                    "received_at",
                    "max",
                ),
            )
            .reset_index()
        )

        latest[
            "collector_age_minutes"
        ] = (
            (
                now
                - latest[
                    "last_received_time"
                ]
            )
            .dt.total_seconds()
            / 60
        )

        latest[
            "market_age_minutes"
        ] = (
            (
                now
                - latest[
                    "last_market_time"
                ]
            )
            .dt.total_seconds()
            / 60
        )

        def health_status(age):
            if pd.isna(age):
                return "UNKNOWN"

            if age <= 30:
                return "OK"

            if age <= 120:
                return "STALE"

            return "OFFLINE"

        latest[
            "CollectorStatus"
        ] = (
            latest[
                "collector_age_minutes"
            ]
            .apply(
                health_status
            )
        )

        latest[
            "MarketStatus"
        ] = (
            latest[
                "market_age_minutes"
            ]
            .apply(
                health_status
            )
        )

        latest[
            "LastReceived"
        ] = (
            latest[
                "last_received_time"
            ]
            .dt.tz_convert(
                LOCAL_TIMEZONE
            )
            .dt.strftime(
                "%Y-%m-%d %H:%M"
            )
        )

        latest[
            "LastMarketData"
        ] = (
            latest[
                "last_market_time"
            ]
            .dt.tz_convert(
                LOCAL_TIMEZONE
            )
            .dt.strftime(
                "%Y-%m-%d %H:%M"
            )
        )
        active_system_cutoff = (
            now - pd.Timedelta(hours=24)
        )

        latest = latest[
            latest["last_received_time"]
            >= active_system_cutoff
        ].copy()

        display_health = latest[
            [
                "system",
                "CollectorStatus",
                "MarketStatus",
                "LastReceived",
                "LastMarketData",
            ]
        ].copy()

        st.dataframe(
            display_health,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "CollectorStatus is based on when "
            "the server last received a record. "
            "MarketStatus is based on the timestamp "
            "of the latest market observation. "
            "If CollectorStatus is OK but MarketStatus "
            "is STALE or OFFLINE, the collector and "
            "network connection are working; check the "
            "upstream market-data provider or the age "
            "of the provider's latest bar."
        )

