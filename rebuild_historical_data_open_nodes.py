#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------------
# 1) Rename sidebar page Historical Trends -> Historical Data.
# ------------------------------------------------------------------
radio_pos = text.find("st.sidebar.radio")
if radio_pos == -1:
    raise SystemExit("ERROR: sidebar page selector not found; no changes written.")

radio_end = text.find("\n)", radio_pos)
if radio_end == -1:
    radio_end = radio_pos + 1800

radio_block = text[radio_pos:radio_end + 2]

if '"Historical Trends"' in radio_block:
    radio_block = radio_block.replace(
        '"Historical Trends"',
        '"Historical Data"',
        1,
    )
elif '"Historical Data"' not in radio_block:
    raise SystemExit(
        "ERROR: Historical Trends sidebar option not found; no changes written."
    )

text = text[:radio_pos] + radio_block + text[radio_end + 2:]

# ------------------------------------------------------------------
# 2) Rename page condition.
# ------------------------------------------------------------------
if 'elif page == "Historical Trends":' in text:
    text = text.replace(
        'elif page == "Historical Trends":',
        'elif page == "Historical Data":',
        1,
    )
elif 'elif page == "Historical Data":' not in text:
    raise SystemExit(
        "ERROR: Historical Trends page block not found; no changes written."
    )

page_start = text.find('elif page == "Historical Data":')
page_end = text.find('elif page == "Sim-Trading":', page_start)

if page_start == -1 or page_end == -1:
    raise SystemExit(
        "ERROR: Historical Data page boundaries not found; no changes written."
    )

section = text[page_start:page_end]

# ------------------------------------------------------------------
# 3) Assets menu:
#    Top K (default), OPEN positions, Single, All.
#    Remove Custom.
# ------------------------------------------------------------------
old_assets = '''            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Single",
                    "Top K",
                    "OPEN positions",
                    "All",
                    "Custom",
                ],
            )
'''

new_assets = '''            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Top K",
                    "OPEN positions",
                    "Single",
                    "All",
                ],
                index=0,
                key="historical_data_assets_v2",
            )
'''

if old_assets not in section:
    raise SystemExit(
        "ERROR: Historical Assets selector not found; no changes written."
    )

section = section.replace(old_assets, new_assets, 1)

# Remove the Custom fallback and make unexpected modes safe.
old_custom = '''            else:
                selected_assets = st.multiselect(
                    "Assets",
                    ranked_assets,
                    default=ranked_assets[:5],
                )
'''

new_custom = '''            else:
                selected_assets = ranked_assets[:int(
                    min(10, len(ranked_assets))
                )]
'''

if old_custom not in section:
    raise SystemExit(
        "ERROR: Historical Custom-assets fallback not found; no changes written."
    )

section = section.replace(old_custom, new_custom, 1)

# Update old wording mentioning Historical Trends in OPEN-position empty message.
section = section.replace(
    '"Historical Trends data."',
    '"Historical Data."',
)

# ------------------------------------------------------------------
# 4) Replace currently-open-only trade loading with ALL historical
#    simulator trades and derive:
#      - every OPEN interval [BuyTime, SellTime)
#      - re-entry BUY points where previous SellTime + 15min == BuyTime
# ------------------------------------------------------------------
old_trade_loader_start = '''                    #
                    # Load currently OPEN Simulation trades.
                    # Their Historical Trends nodes at/after
                    # BuyTime will be highlighted in green.
                    #
                    open_buy_times = {}

                    try:
'''

start = section.find(old_trade_loader_start)

figure_marker = '''                    figure = px.line(
'''

end = section.find(figure_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "ERROR: Historical Simulation-highlight preparation block not found; no changes written."
    )

new_trade_loader = '''                    #
                    # Load ALL historical Simulation trades so every chart
                    # timepoint can be classified by the simulator state that
                    # applied at that time.
                    #
                    historical_trade_intervals = []
                    reentry_buy_points = set()

                    try:
                        simulation_rows = load_simulation_cached()
                        simulation_df = pd.DataFrame(
                            simulation_rows
                        )

                        if not simulation_df.empty:
                            for column in [
                                "BuyTime",
                                "SellTime",
                            ]:
                                if column not in simulation_df.columns:
                                    simulation_df[column] = pd.NaT

                                simulation_df[column] = pd.to_datetime(
                                    simulation_df[column],
                                    utc=True,
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

                            simulation_df = simulation_df[
                                simulation_df["Ticker"].ne("")
                                & simulation_df["BuyTime"].notna()
                            ].copy()

                            simulation_df = simulation_df.sort_values(
                                ["Ticker", "BuyTime"]
                            )

                            for ticker, ticker_trades in (
                                simulation_df.groupby(
                                    "Ticker",
                                    sort=False,
                                )
                            ):
                                ticker_trades = ticker_trades.sort_values(
                                    "BuyTime"
                                )

                                previous_sell = pd.NaT

                                for _, trade in ticker_trades.iterrows():
                                    buy_time = trade.get("BuyTime")
                                    sell_time = trade.get("SellTime")

                                    historical_trade_intervals.append(
                                        (
                                            ticker,
                                            buy_time,
                                            sell_time,
                                        )
                                    )

                                    if pd.notna(previous_sell):
                                        previous_sell_point = (
                                            pd.Timestamp(previous_sell)
                                            .round("15min")
                                        )
                                        buy_point = (
                                            pd.Timestamp(buy_time)
                                            .round("15min")
                                        )

                                        if (
                                            buy_point
                                            == previous_sell_point
                                            + pd.Timedelta(minutes=15)
                                        ):
                                            reentry_buy_points.add(
                                                (
                                                    ticker,
                                                    buy_point,
                                                )
                                            )

                                    if pd.notna(sell_time):
                                        previous_sell = sell_time

                    except Exception as exc:
                        st.warning(
                            "Cannot load historical Simulation trades for "
                            f"chart highlighting: {exc}"
                        )

'''

section = section[:start] + new_trade_loader + section[end:]

# ------------------------------------------------------------------
# 5) Replace current OPEN-only marker logic.
# ------------------------------------------------------------------
old_highlight_start = '''                    #
                    # Highlight observations belonging to
                    # currently OPEN trades.
                    #
                    open_points_parts = []
'''

start = section.find(old_highlight_start)

layout_marker = '''                    figure.update_layout(
'''

end = section.find(layout_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "ERROR: Historical OPEN marker block not found; no changes written."
    )

new_highlight = '''                    #
                    # Highlight every historical point at which the ticker
                    # was in simulator status OPEN. SellTime itself is not
                    # OPEN; intervals therefore use [BuyTime, SellTime).
                    #
                    open_points_parts = []

                    for (
                        ticker,
                        buy_time,
                        sell_time,
                    ) in historical_trade_intervals:
                        mask = (
                            chart_df["ticker"]
                            .astype(str)
                            .str.upper()
                            .eq(ticker)
                            & (
                                chart_df["timestamp"]
                                >= buy_time
                            )
                        )

                        if pd.notna(sell_time):
                            mask = (
                                mask
                                & (
                                    chart_df["timestamp"]
                                    < sell_time
                                )
                            )

                        ticker_points = chart_df[
                            mask
                        ].copy()

                        if not ticker_points.empty:
                            ticker_points[
                                "_OpenTicker"
                            ] = ticker
                            open_points_parts.append(
                                ticker_points
                            )

                    if open_points_parts:
                        open_points = pd.concat(
                            open_points_parts,
                            ignore_index=True,
                        )

                        # Avoid duplicate overlay markers if malformed or
                        # overlapping simulator records exist.
                        open_points = (
                            open_points
                            .sort_values(
                                ["ticker", "timestamp"]
                            )
                            .drop_duplicates(
                                subset=[
                                    "ticker",
                                    "timestamp",
                                ],
                                keep="last",
                            )
                        )

                        #
                        # Re-entry BUY points are drawn separately as
                        # triangles. Other OPEN points use larger circles.
                        #
                        open_points[
                            "_RoundedTime"
                        ] = (
                            open_points["timestamp"]
                            .dt.round("15min")
                        )

                        open_points[
                            "_IsReentry"
                        ] = open_points.apply(
                            lambda row: (
                                str(
                                    row["ticker"]
                                ).upper(),
                                row["_RoundedTime"],
                            )
                            in reentry_buy_points,
                            axis=1,
                        )

                        regular_open_points = open_points[
                            ~open_points["_IsReentry"]
                        ].copy()

                        reentry_points = open_points[
                            open_points["_IsReentry"]
                        ].copy()

                        if not regular_open_points.empty:
                            figure.add_scatter(
                                x=regular_open_points[
                                    "Local Time"
                                ],
                                y=regular_open_points[
                                    y_column
                                ],
                                mode="markers",
                                marker={
                                    "color": "green",
                                    "size": 10,
                                    "symbol": "circle",
                                    "line": {
                                        "width": 2,
                                    },
                                },
                                name="Simulator OPEN",
                                customdata=regular_open_points[
                                    ["ticker"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
                            )

                        if not reentry_points.empty:
                            figure.add_scatter(
                                x=reentry_points[
                                    "Local Time"
                                ],
                                y=reentry_points[
                                    y_column
                                ],
                                mode="markers",
                                marker={
                                    "color": "green",
                                    "size": 13,
                                    "symbol": "triangle-up",
                                    "line": {
                                        "width": 2,
                                    },
                                },
                                name="Re-BUY after SELL",
                                customdata=reentry_points[
                                    ["ticker"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Re-BUY: SELL at previous "
                                    "15-minute point"
                                    "<br>Simulator status: OPEN"
                                    "<br>%{x}"
                                    "<br>%{y:.2f}"
                                    "<extra></extra>"
                                ),
                            )

'''

section = section[:start] + new_highlight + section[end:]

# ------------------------------------------------------------------
# 6) Add / update explanatory notes directly below the chart.
# ------------------------------------------------------------------
chart_call = '''                    st.plotly_chart(
                        figure,
                        use_container_width=True,
                    )
'''

note = '''                    st.caption(
                        "Historical Data node meaning: the normal line markers are market-data "
                        "observations. Larger green circular nodes mark every displayed 15-minute "
                        "timepoint during which that ticker had simulator status OPEN. A green "
                        "triangle marks a new simulator BUY when the same ticker was sold at the "
                        "immediately preceding 15-minute timepoint and bought again at the next one. "
                        "SellTime itself is treated as CLOSED, so OPEN highlighting ends before "
                        "the SellTime point."
                    )
'''

if chart_call not in section:
    raise SystemExit(
        "ERROR: Historical chart display call not found; no changes written."
    )

section = section.replace(
    chart_call,
    chart_call + "\n" + note,
    1,
)

text = text[:page_start] + section + text[page_end:]

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Trends renamed to Historical Data; "
    "Top K is default; Custom removed; all historical OPEN nodes and "
    "triangle re-BUY markers added; notes updated."
)
