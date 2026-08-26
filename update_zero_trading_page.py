#!/usr/bin/env python3
from pathlib import Path
import shutil
from datetime import datetime, timezone

path = Path("dashboard/streamlit_app.py")

if not path.exists():
    raise SystemExit("ERROR: dashboard/streamlit_app.py not found. Run from /opt/home-monitor.")

text = path.read_text(encoding="utf-8")
start = text.find('if page == "Zero-Trading":')
end = text.find('elif page == "Last Data":', start)
if start == -1 or end == -1:
    raise SystemExit("ERROR: Zero-Trading page boundaries not found; no changes written.")

new_page = r'''if page == "Zero-Trading":
    st.header("Zero-Trading")

    market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()

    if market_df.empty:
        st.info("No measurements received yet.")
    else:
        market_df["timestamp"] = pd.to_datetime(market_df["timestamp"], utc=True, errors="coerce")
        market_df["received_at"] = pd.to_datetime(market_df["received_at"], utc=True, errors="coerce")

        newest_received = market_df["received_at"].max()
        active_cutoff = newest_received - pd.Timedelta(minutes=30)
        latest_received = market_df.groupby("ticker")["received_at"].max()
        active_tickers = latest_received[latest_received >= active_cutoff].index
        active_df = market_df[market_df["ticker"].isin(active_tickers)].copy()
        advisor_live = build_live_overview(active_df)

        if advisor_live.empty:
            st.info("No active assets available.")
        else:
            newest_market_data = active_df["timestamp"].max()
            closeb_numeric = pd.to_numeric(advisor_live.get("CloseB"), errors="coerce")
            c2_group_count = int((closeb_numeric >= BUY_MIN_CLOSEB_PERCENT).sum())

            try:
                zero_sim_payload = load_simulation_payload_cached()
                if isinstance(zero_sim_payload, dict):
                    zero_sim_rows = (
                        zero_sim_payload.get("trades")
                        or zero_sim_payload.get("rows")
                        or zero_sim_payload.get("items")
                        or []
                    )
                else:
                    zero_sim_rows = zero_sim_payload or []

                zero_open_tickers = set()
                for trade in zero_sim_rows:
                    ticker_value = str(trade.get("Ticker") or trade.get("ticker") or "").strip().upper()
                    status_value = str(trade.get("Status") or trade.get("status") or "").strip().upper()
                    sell_time_value = trade.get("SellTime") if "SellTime" in trade else trade.get("sell_time")
                    is_open_trade = status_value == "OPEN" or (not status_value and not sell_time_value)
                    if ticker_value and is_open_trade:
                        zero_open_tickers.add(ticker_value)
                zero_open_count = len(zero_open_tickers)
            except Exception:
                zero_open_count = 0
                zero_open_tickers = set()

            zero_max_open = BUY_MAX_OPEN_TICKERS
            zero_available_open_slots = max(0, zero_max_open - zero_open_count)
            portfolio_cols = st.columns(3)
            portfolio_cols[0].metric("OPEN tickers", zero_open_count)
            portfolio_cols[1].metric("Maximum OPEN", zero_max_open)
            portfolio_cols[2].metric("Available BUY slots", zero_available_open_slots)

            if zero_available_open_slots <= 0:
                st.warning(
                    "BUY portfolio limit reached: "
                    f"{zero_open_count}/{zero_max_open} OPEN tickers. "
                    "New BUYs are blocked until a position is closed or the limit is increased in Settings."
                )

            if "ShouldBuy" in advisor_live.columns:
                buy_decision = advisor_live["ShouldBuy"].fillna(False).astype(bool)
            elif "CanBuy" in advisor_live.columns:
                buy_decision = advisor_live["CanBuy"].fillna(False).astype(bool)
            else:
                buy_decision = pd.Series(False, index=advisor_live.index)

            if zero_available_open_slots <= 0:
                buy_decision = pd.Series(False, index=advisor_live.index)
            else:
                buy_candidates = advisor_live.loc[buy_decision].copy()
                if not buy_candidates.empty:
                    buy_candidates["_CloseBSort"] = pd.to_numeric(buy_candidates.get("CloseB"), errors="coerce")
                    allowed_buy_indexes = set(
                        buy_candidates.sort_values(
                            ["_CloseBSort", "Ticker"],
                            ascending=[False, True],
                            na_position="last",
                        ).head(min(6, zero_available_open_slots)).index.tolist()
                    )
                    buy_decision = pd.Series(
                        [bool(buy_decision.loc[idx]) and idx in allowed_buy_indexes for idx in advisor_live.index],
                        index=advisor_live.index,
                    )

            if "ShouldSell" in advisor_live.columns:
                sell_decision = advisor_live["ShouldSell"].fillna(False).astype(bool)
            else:
                sell_decision = pd.Series(False, index=advisor_live.index)

            advisor = advisor_live[buy_decision | sell_decision].copy()

            if advisor.empty:
                st.success("No current Buy or Sell recommendations.")
            else:
                advisor["_BuyDecision"] = buy_decision.loc[advisor.index]
                advisor["_SellDecision"] = sell_decision.loc[advisor.index]
                advisor["Action"] = advisor.apply(
                    lambda row: "Sell" if bool(row["_SellDecision"]) else "Buy",
                    axis=1,
                )

                def _zero_timedelta_text(value):
                    if value is None:
                        return "—"
                    if isinstance(value, str):
                        raw = value.strip()
                        if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                            return "—"
                        try:
                            delta = pd.to_timedelta(raw)
                        except Exception:
                            return raw
                    else:
                        try:
                            delta = pd.to_timedelta(value)
                        except Exception:
                            return "—"
                    if pd.isna(delta):
                        return "—"
                    total_minutes = max(0, int(round(delta.total_seconds() / 60.0)))
                    days, remainder = divmod(total_minutes, 24 * 60)
                    hours, minutes = divmod(remainder, 60)
                    return f"{days}d {hours:02d}:{minutes:02d}"

                def _zero_wait_is_zero(value):
                    if value is None:
                        return False
                    try:
                        delta = pd.to_timedelta(value)
                        if pd.isna(delta):
                            return False
                        return abs(delta.total_seconds()) < 30
                    except Exception:
                        return str(value).strip().lower() in {
                            "0", "0s", "00:00", "0:00", "0d 00:00", "0 days 00:00:00"
                        }

                def _zero_local_timestamp(value):
                    parsed = pd.to_datetime(value, utc=True, errors="coerce")
                    if pd.isna(parsed):
                        return "—"
                    return parsed.tz_convert(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M")

                def _zero_interval_metrics(ticker, last_time, start_time):
                    last_time = pd.to_datetime(last_time, utc=True, errors="coerce")
                    start_time = pd.to_datetime(start_time, utc=True, errors="coerce")
                    if pd.isna(last_time) or pd.isna(start_time):
                        return (None, None)
                    source = market_df[market_df["ticker"].astype(str) == str(ticker)].copy()
                    if source.empty:
                        return (None, None)
                    source = source[(source["timestamp"] >= start_time) & (source["timestamp"] <= last_time)].copy()
                    if source.empty:
                        return (None, None)
                    source["_CloseNumeric"] = pd.to_numeric(source["close"], errors="coerce")
                    source = source[source["_CloseNumeric"].notna() & (source["_CloseNumeric"] > 0)].copy()
                    if source.empty:
                        return (None, None)
                    sort_cols = ["timestamp", "id"] if "id" in source.columns else ["timestamp"]
                    source = source.sort_values(sort_cols).drop_duplicates(subset=["timestamp"], keep="last")
                    last_price = float(source.iloc[-1]["_CloseNumeric"])
                    peak_price = float(source["_CloseNumeric"].max())
                    if last_price <= 0 or peak_price <= 0:
                        return (None, None)
                    drop_percent = max(0.0, ((peak_price - last_price) / peak_price) * 100.0)
                    change_percent = float((((source["_CloseNumeric"] / last_price) - 1.0).abs().max()) * 100.0)
                    return (drop_percent, change_percent)

                advisor["_LastClose2hRaw"] = pd.to_numeric(advisor.get("CloseB"), errors="coerce")
                advisor["_LastSellingRaw"] = pd.to_numeric(advisor.get("SellTime"), errors="coerce")
                advisor["_InitTimeLatestRaw"] = (
                    pd.to_datetime(advisor["BoughtBefore"], utc=True, errors="coerce")
                    if "BoughtBefore" in advisor.columns
                    else pd.NaT
                )
                advisor["_LastCollectRaw"] = (
                    pd.to_datetime(advisor["Time"], utc=True, errors="coerce")
                    if "Time" in advisor.columns
                    else pd.NaT
                )

                wait_to_trade_raw = {}
                wait_to_opening_raw = {}
                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    ticker_measurements = active_df[active_df["ticker"].astype(str) == ticker]
                    asset_type = None
                    if not ticker_measurements.empty:
                        asset_type = ticker_measurements.sort_values("timestamp").iloc[-1].get("asset_type")
                    region = market_region_for_ticker(ticker, asset_type)
                    phase_info = market_phase_info(newest_market_data, region)
                    wait_to_trade_raw[idx] = phase_info[1] if len(phase_info) > 1 else None
                    wait_to_opening_raw[idx] = phase_info[2] if len(phase_info) > 2 else None

                advisor["_WaitToTradeRaw"] = pd.Series(wait_to_trade_raw)
                advisor["_WaitToOpeningRaw"] = pd.Series(wait_to_opening_raw)

                drop24 = {}
                drop_init = {}
                change24 = {}
                change_init = {}
                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    last_time = row["_LastCollectRaw"]
                    init_time = row["_InitTimeLatestRaw"]
                    if pd.notna(last_time):
                        d24, c24 = _zero_interval_metrics(ticker, last_time, last_time - pd.Timedelta(hours=24))
                    else:
                        d24, c24 = (None, None)
                    if pd.notna(last_time) and pd.notna(init_time) and init_time <= last_time:
                        di, ci = _zero_interval_metrics(ticker, last_time, init_time)
                    else:
                        di, ci = (None, None)
                    drop24[idx] = d24
                    drop_init[idx] = di
                    change24[idx] = c24
                    change_init[idx] = ci

                advisor["_Drop24hRaw"] = pd.Series(drop24)
                advisor["_DropInitTimeLatestRaw"] = pd.Series(drop_init)
                advisor["_Change24hRaw"] = pd.Series(change24)
                advisor["_ChangeInitTimeLatestRaw"] = pd.Series(change_init)

                advisor["WaitToTrade"] = advisor["_WaitToTradeRaw"].map(_zero_timedelta_text)
                advisor["WaitToOpening"] = advisor["_WaitToOpeningRaw"].map(_zero_timedelta_text)

                if "BuyQty" in advisor.columns:
                    advisor["Qty"] = pd.to_numeric(advisor["BuyQty"], errors="coerce")
                elif "Qty" not in advisor.columns:
                    advisor["Qty"] = pd.NA
                advisor.loc[advisor["Action"] == "Sell", "Qty"] = pd.NA

                advisor["InitTimeLatest"] = advisor["_InitTimeLatestRaw"].map(_zero_local_timestamp)
                advisor["LastCollect"] = advisor["_LastCollectRaw"].map(_zero_local_timestamp)
                advisor["LastSelling"] = advisor["_LastSellingRaw"].map(
                    lambda value: f"{int(round(float(value)))}s" if pd.notna(value) else "—"
                )
                advisor["LastTops"] = c2_group_count
                advisor.loc[advisor["Action"] == "Sell", "LastTops"] = "-"
                advisor["LastClose2h"] = advisor["_LastClose2hRaw"].map(
                    lambda value: f"{float(value):+.2f}%" if pd.notna(value) else "—"
                )

                def _zero_percent(value):
                    return f"{float(value):.2f}%" if pd.notna(value) else "—"

                advisor["Drop24h"] = advisor["_Drop24hRaw"].map(_zero_percent)
                advisor["DropInitTimeLatest"] = advisor["_DropInitTimeLatestRaw"].map(_zero_percent)
                advisor["Change24h"] = advisor["_Change24hRaw"].map(_zero_percent)
                advisor["ChangeInitTimeLatest"] = advisor["_ChangeInitTimeLatestRaw"].map(_zero_percent)
                advisor["Qty"] = advisor["Qty"].map(
                    lambda value: str(int(round(float(value)))) if pd.notna(value) else "-"
                )

                advisor["_ActionSort"] = advisor["Action"].map({"Buy": 0, "Sell": 1}).fillna(99)
                advisor = advisor.sort_values(
                    by=["_ActionSort", "_LastClose2hRaw", "Ticker"],
                    ascending=[True, False, True],
                    na_position="last",
                )

                requested_columns = [
                    "Action", "Ticker", "TickerName", "WaitToTrade", "WaitToOpening", "Qty",
                    "InitTimeLatest", "LastCollect", "LastSelling", "LastTops", "LastClose2h",
                    "Drop24h", "DropInitTimeLatest", "Change24h", "ChangeInitTimeLatest",
                ]
                for column in requested_columns:
                    if column not in advisor.columns:
                        advisor[column] = "—"
                display = advisor[requested_columns].copy()

                raw_lookup = advisor[[
                    "Action", "_WaitToTradeRaw", "_WaitToOpeningRaw", "_LastSellingRaw",
                    "_LastClose2hRaw", "_Drop24hRaw", "_DropInitTimeLatestRaw",
                    "_Change24hRaw", "_ChangeInitTimeLatestRaw",
                ]].loc[display.index].copy()

                def _zero_row_style(row):
                    raw = raw_lookup.loc[row.name]
                    action = str(raw.get("Action") or "")
                    styles = ["" for _ in row.index]

                    def bold(column):
                        if column in row.index:
                            styles[row.index.get_loc(column)] = "font-weight: 700;"

                    if _zero_wait_is_zero(raw.get("_WaitToTradeRaw")):
                        bold("WaitToTrade")
                    if _zero_wait_is_zero(raw.get("_WaitToOpeningRaw")):
                        bold("WaitToOpening")

                    last_selling = pd.to_numeric(raw.get("_LastSellingRaw"), errors="coerce")
                    if pd.notna(last_selling) and float(last_selling) < 120.0:
                        bold("LastSelling")

                    if action == "Buy":
                        last_close = pd.to_numeric(raw.get("_LastClose2hRaw"), errors="coerce")
                        if pd.notna(last_close) and float(last_close) > 2.0:
                            bold("LastClose2h")

                    if action == "Sell":
                        for display_column, raw_column in [
                            ("Drop24h", "_Drop24hRaw"),
                            ("DropInitTimeLatest", "_DropInitTimeLatestRaw"),
                            ("Change24h", "_Change24hRaw"),
                            ("ChangeInitTimeLatest", "_ChangeInitTimeLatestRaw"),
                        ]:
                            numeric = pd.to_numeric(raw.get(raw_column), errors="coerce")
                            if pd.notna(numeric) and float(numeric) > 2.0:
                                bold(display_column)
                    return styles

                styled_display = display.style.apply(_zero_row_style, axis=1)
                st.dataframe(styled_display, use_container_width=True, hide_index=True)

        st.caption(
            "Zero-Trading shows only actionable Buy/Sell recommendations. Rows are sorted first by "
            "Action (Buy before Sell), then by LastClose2h from highest to lowest. WaitToTrade and "
            "WaitToOpening use the same Newest-data market-phase calculation as Last Data and are "
            "displayed as Xd HH:mm."
        )

        st.caption(
            "Parameters: LastSelling is the estimated liquidity time to sell; LastTops is the current "
            "C2 ticker count and is '-' for Sell rows; LastClose2h is the approximately two-hour CloseB. "
            "Drop24h and Change24h use LastCollect-24h through LastCollect. DropInitTimeLatest and "
            "ChangeInitTimeLatest use InitTimeLatest through LastCollect. Drop is the percentage fall "
            "from the highest price in the interval to LastPrice. Change is the maximum absolute "
            "percentage deviation from LastPrice within the interval."
        )

        st.subheader("Steps to buy a ticker")
        st.markdown(
            "1. Is **LastClose2h** high?\n"
            "2. Is now the trading time (**WaitToTrade = 0**)?\n"
            "3. Is **LastSelling** short?\n"
            "4. If all are yes, buy **Qty** in the ZERO app and set a **2% Stop-Loss**."
        )

        st.subheader("Steps to sell a ticker")
        st.markdown(
            "1. Is the ticker open in the ZERO app?\n"
            "2. Was the ticker bought before **InitTimeLatest**?\n"
            "3. Is the ticker price drop larger than 2%, or is the ticker price change smaller than 2%?\n"
            "4. Is now the trading time (**WaitToTrade = 0**)?\n"
            "5. Is **LastSelling** short?\n"
            "6. If all are yes, sell the ticker in the ZERO app."
        )

'''

new_text = text[:start] + new_page + text[end:]
try:
    compile(new_text, str(path), "exec")
except Exception as exc:
    raise SystemExit(f"ERROR: generated dashboard would not compile; no changes written: {exc}")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.bak-before-zero-trading-v2-{stamp}")
shutil.copy2(path, backup)
path.write_text(new_text, encoding="utf-8")
print("SUCCESS: Zero-Trading page updated.")
print(f"Backup: {backup}")
