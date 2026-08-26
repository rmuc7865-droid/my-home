#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------------
# Rename sidebar entry.
# ------------------------------------------------------------------
old_sidebar = '''page = st.sidebar.radio("Page", ["Advisor", "Last Data", "Historical Data", "Sim-Trading", "Effective Trading", "System Health", "Alerts", "Settings", "Logs"])'''
new_sidebar = '''page = st.sidebar.radio("Page", ["Zero-Trading", "Last Data", "Historical Data", "Sim-Trading", "Effective Trading", "System Health", "Alerts", "Settings", "Logs"])'''

if old_sidebar not in text:
    raise SystemExit("ERROR: sidebar Advisor entry not found; no changes written.")

text = text.replace(old_sidebar, new_sidebar, 1)

# ------------------------------------------------------------------
# Replace the complete Advisor page with the new Zero-Trading page.
# ------------------------------------------------------------------
page_start = text.find('if page == "Advisor":')
page_end = text.find('elif page == "Last Data":', page_start)

if page_start == -1 or page_end == -1:
    raise SystemExit("ERROR: Advisor page boundaries not found; no changes written.")

new_page = '''if page == "Zero-Trading":
    st.header("Zero-Trading")

    market_df = df[
        df["asset_type"].isin(["stock", "crypto"])
    ].copy()

    if market_df.empty:
        st.info("No measurements received yet.")
    else:
        # Use the same active-ticker definition and Live Overview builder so
        # Zero-Trading stays synchronized with the current decision support.
        newest_received = market_df["received_at"].max()
        active_cutoff = newest_received - pd.Timedelta(minutes=30)
        latest_received = market_df.groupby("ticker")["received_at"].max()
        active_tickers = latest_received[
            latest_received >= active_cutoff
        ].index

        active_df = market_df[
            market_df["ticker"].isin(active_tickers)
        ].copy()

        advisor_live = build_live_overview(active_df)

        if advisor_live.empty:
            st.info("No active assets available.")
        else:
            closeb_numeric = pd.to_numeric(
                advisor_live.get("CloseB"),
                errors="coerce",
            )
            c2_group_count = int(
                (closeb_numeric >= BUY_MIN_CLOSEB_PERCENT).sum()
            )

            # Prefer an explicit ShouldBuy field when available. Older dashboard
            # builds expose CanBuy as the actionable BUY decision, so retain that
            # as a compatibility fallback.
            if "ShouldBuy" in advisor_live.columns:
                buy_decision = (
                    advisor_live["ShouldBuy"]
                    .fillna(False)
                    .astype(bool)
                )
            elif "CanBuy" in advisor_live.columns:
                buy_decision = (
                    advisor_live["CanBuy"]
                    .fillna(False)
                    .astype(bool)
                )
            else:
                buy_decision = pd.Series(
                    False,
                    index=advisor_live.index,
                )

            if "ShouldSell" in advisor_live.columns:
                sell_decision = (
                    advisor_live["ShouldSell"]
                    .fillna(False)
                    .astype(bool)
                )
            else:
                sell_decision = pd.Series(
                    False,
                    index=advisor_live.index,
                )

            advisor = advisor_live[
                buy_decision | sell_decision
            ].copy()

            if advisor.empty:
                st.success(
                    "No current Buy or Sell recommendations."
                )
            else:
                advisor["_BuyDecision"] = buy_decision.loc[
                    advisor.index
                ]
                advisor["_SellDecision"] = sell_decision.loc[
                    advisor.index
                ]

                # SELL takes precedence if a row were ever to satisfy both
                # decisions simultaneously; otherwise BUY represents the
                # actionable buy decision.
                advisor["Action"] = advisor.apply(
                    lambda row: (
                        "Sell"
                        if bool(row["_SellDecision"])
                        else "Buy"
                    ),
                    axis=1,
                )

                advisor["TopTickers"] = c2_group_count

                advisor = advisor.rename(
                    columns={
                        "Time": "LastCollect",
                        "SellTime": "LastSellTime",
                        "SellTiming": "NextTrading",
                        "BoughtBefore": "InitTimeBefore",
                        "CloseB": "LastPriceChange",
                        "BuyQty": "Qty",
                        "Drop": "DropPrice",
                        "Static": "ChangePrice",
                    }
                )

                # Raw numeric value retained for stable sorting before display
                # formatting converts LastPriceChange into a percentage string.
                advisor["_LastPriceChangeSort"] = pd.to_numeric(
                    advisor.get("LastPriceChange"),
                    errors="coerce",
                )

                # Buy rows first, Sell rows second; within each Action show
                # strongest recent price increase first.
                advisor["_ActionSort"] = advisor["Action"].map(
                    {
                        "Buy": 0,
                        "Sell": 1,
                    }
                ).fillna(99)

                advisor = advisor.sort_values(
                    by=[
                        "_ActionSort",
                        "_LastPriceChangeSort",
                    ],
                    ascending=[
                        True,
                        False,
                    ],
                    na_position="last",
                )

                # InitTimeBefore is the latest simulator BUY/init timestamp used
                # by the sell decision.
                if "InitTimeBefore" in advisor.columns:
                    advisor["InitTimeBefore"] = (
                        advisor["InitTimeBefore"]
                        .map(format_local_timestamp)
                    )

                # Last collected market-data timestamp.
                if "LastCollect" in advisor.columns:
                    advisor["LastCollect"] = (
                        advisor["LastCollect"]
                        .map(format_local_timestamp)
                    )

                # Remove implementation prefix from the user-facing next
                # trading time.
                if "NextTrading" in advisor.columns:
                    advisor["NextTrading"] = (
                        advisor["NextTrading"]
                        .fillna("—")
                        .astype(str)
                        .str.replace(
                            "FirstNextSellTime=",
                            "",
                            regex=False,
                        )
                    )
                    advisor.loc[
                        advisor["NextTrading"].isin(
                            ["", "None", "nan", "NaT"]
                        ),
                        "NextTrading",
                    ] = "—"

                # Last two-hour price change.
                if "LastPriceChange" in advisor.columns:
                    advisor["LastPriceChange"] = pd.to_numeric(
                        advisor["LastPriceChange"],
                        errors="coerce",
                    ).map(
                        lambda value: (
                            f"{value:+.2f}%"
                            if pd.notna(value)
                            else "—"
                        )
                    )

                # Liquidity-time estimate in seconds, without a separating
                # space, e.g. 236s.
                if "LastSellTime" in advisor.columns:
                    advisor["LastSellTime"] = pd.to_numeric(
                        advisor["LastSellTime"],
                        errors="coerce",
                    ).map(
                        lambda value: (
                            f"{int(value)}s"
                            if pd.notna(value)
                            else "—"
                        )
                    )

                # Price movement KPIs remain percentage values.
                for column in [
                    "DropPrice",
                    "ChangePrice",
                ]:
                    if column in advisor.columns:
                        advisor[column] = pd.to_numeric(
                            advisor[column],
                            errors="coerce",
                        ).map(
                            lambda value: (
                                f"{value:.2f}%"
                                if pd.notna(value)
                                else "—"
                            )
                        )

                # Qty should remain numeric where supplied by the decision
                # logic. Missing quantities (normally SELL rows) are shown as —.
                if "Qty" in advisor.columns:
                    advisor["Qty"] = pd.to_numeric(
                        advisor["Qty"],
                        errors="coerce",
                    )

                requested_columns = [
                    "Action",
                    "Ticker",
                    "TickerName",
                    "NextTrading",
                    "InitTimeBefore",
                    "TopTickers",
                    "LastPriceChange",
                    "Qty",
                    "LastSellTime",
                    "DropPrice",
                    "ChangePrice",
                    "LastCollect",
                ]

                # Guarantee the requested schema even if an older backend
                # does not supply one of the optional fields.
                for column in requested_columns:
                    if column not in advisor.columns:
                        advisor[column] = pd.NA

                display = advisor[
                    requested_columns
                ].copy()

                st.dataframe(
                    display,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "TopTickers": st.column_config.NumberColumn(
                            "TopTickers",
                            format="%d",
                        ),
                        "Qty": st.column_config.NumberColumn(
                            "Qty",
                            format="%d",
                        ),
                    },
                )

        st.caption(
            "Zero-Trading shows only actionable ticker recommendations. Action = Buy "
            "when the current BUY decision is true (ShouldBuy when available, otherwise "
            "the existing CanBuy decision); Action = Sell when ShouldSell is true. "
            "Rows are ordered first by Action (Buy before Sell) and then by "
            "LastPriceChange from highest to lowest."
        )

        st.caption(
            "Columns: Ticker and TickerName identify the security in the ZERO app. "
            "NextTrading is the next relevant trading time; the internal "
            "FirstNextSellTime= prefix is removed. InitTimeBefore is the latest "
            "simulator BUY/init time used when evaluating a SELL. TopTickers is the "
            f"number of active tickers whose approximately two-hour CloseB is at least "
            f"{BUY_MIN_CLOSEB_PERCENT:g}%. LastPriceChange is the ticker's approximately "
            "two-hour price change. Qty is the suggested BUY quantity. LastSellTime is "
            "the estimated liquidity time needed to sell the position, in seconds. "
            "DropPrice is the C4 drop-from-peak percentage. ChangePrice is the C5 "
            "maximum absolute price deviation over the configured static-price window. "
            "LastCollect is the latest market-data time used for the recommendation."
        )

'''

text = text[:page_start] + new_page + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Advisor renamed to Zero-Trading and rebuilt with actionable "
    "Buy/Sell rows, requested columns, formatting, sorting, and updated notes."
)
