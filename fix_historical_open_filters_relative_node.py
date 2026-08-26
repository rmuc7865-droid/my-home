#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

page_start = text.find('elif page == "Historical Data":')
page_end = text.find('elif page == "Sim-Trading":', page_start)

if page_start == -1 or page_end == -1:
    raise SystemExit(
        "ERROR: Historical Data page boundaries not found; no changes written."
    )

section = text[page_start:page_end]

# ------------------------------------------------------------
# 1) Remove the CloseB lookup previously added for these filters.
# ------------------------------------------------------------
old_ranking = '''        if not ranking_df.empty:
            ranked_assets = ranking_df["Ticker"].tolist()

            latest_closeb_map = {}
            if (
                "Ticker" in ranking_df.columns
                and "CloseB" in ranking_df.columns
            ):
                closeb_lookup = ranking_df[
                    ["Ticker", "CloseB"]
                ].copy()
                closeb_lookup["Ticker"] = (
                    closeb_lookup["Ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )
                closeb_lookup["CloseB"] = pd.to_numeric(
                    closeb_lookup["CloseB"],
                    errors="coerce",
                )
                latest_closeb_map = (
                    closeb_lookup
                    .drop_duplicates(
                        subset=["Ticker"],
                        keep="first",
                    )
                    .set_index("Ticker")["CloseB"]
                    .to_dict()
                )
        else:
            latest_closeb_map = {}
            ranked_assets = sorted(
'''

new_ranking = '''        if not ranking_df.empty:
            ranked_assets = ranking_df["Ticker"].tolist()
        else:
            ranked_assets = sorted(
'''

if old_ranking not in section:
    raise SystemExit(
        "ERROR: Previous CloseB lookup block not found; no changes written."
    )

section = section.replace(old_ranking, new_ranking, 1)

# ------------------------------------------------------------
# 2) For positive/negative modes, initially select all currently
#    OPEN tickers. The final filtering must happen later, after
#    Range/Metric and Relative % have been calculated.
# ------------------------------------------------------------
old_positive = '''            elif asset_mode == "OPEN positive positions":
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if (
                        ticker in available_assets
                        and pd.notna(
                            latest_closeb_map.get(
                                str(ticker).upper()
                            )
                        )
                        and float(
                            latest_closeb_map.get(
                                str(ticker).upper()
                            )
                        ) > 0
                    )
                ]

                if selected_assets:
                    st.caption(
                        "Current Simulation OPEN positions "
                        "with latest CloseB > 0%: "
                        + ", ".join(selected_assets)
                    )
                else:
                    st.info(
                        "There are currently no OPEN "
                        "Simulation positions with a "
                        "positive latest CloseB."
                    )

            elif asset_mode == "OPEN negative positions":
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if (
                        ticker in available_assets
                        and pd.notna(
                            latest_closeb_map.get(
                                str(ticker).upper()
                            )
                        )
                        and float(
                            latest_closeb_map.get(
                                str(ticker).upper()
                            )
                        ) < 0
                    )
                ]

                if selected_assets:
                    st.caption(
                        "Current Simulation OPEN positions "
                        "with latest CloseB < 0%: "
                        + ", ".join(selected_assets)
                    )
                else:
                    st.info(
                        "There are currently no OPEN "
                        "Simulation positions with a "
                        "negative latest CloseB."
                    )
'''

new_positive = '''            elif asset_mode == "OPEN positive positions":
                # Start with all currently OPEN positions. The final positive
                # filter is applied after the selected Range/Metric has been
                # converted into the chart's Relative % values.
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if ticker in available_assets
                ]

            elif asset_mode == "OPEN negative positions":
                # Start with all currently OPEN positions. The final negative
                # filter is applied after the selected Range/Metric has been
                # converted into the chart's Relative % values.
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if ticker in available_assets
                ]
'''

if old_positive not in section:
    raise SystemExit(
        "ERROR: Previous OPEN positive/negative selection block not found; no changes written."
    )

section = section.replace(old_positive, new_positive, 1)

# ------------------------------------------------------------
# 3) Replace the old CloseB explanatory note.
# ------------------------------------------------------------
old_note = '''            st.caption(
                "Asset filters: OPEN positions = currently open simulator trades. "
                "OPEN positive positions = currently OPEN tickers whose latest "
                "approximately two-hour CloseB is > 0%. OPEN negative positions = "
                "currently OPEN tickers whose latest CloseB is < 0%. A value of "
                "exactly 0% belongs to neither positive nor negative."
            )

'''

new_note = '''            st.caption(
                "Asset filters: OPEN positions = currently open simulator trades. "
                "For OPEN positive/negative positions, the dashboard first builds "
                "the selected Historical Data chart and then classifies each OPEN "
                "ticker by its last plotted Relative % node: > 0% = positive, "
                "< 0% = negative, and exactly 0% belongs to neither group."
            )

'''

if old_note not in section:
    raise SystemExit(
        "ERROR: Previous OPEN filter explanatory note not found; no changes written."
    )

section = section.replace(old_note, new_note, 1)

# ------------------------------------------------------------
# 4) Apply the actual filter immediately after Relative % is built.
#    The Relative % calculation is based on the currently selected
#    metric and currently selected range, exactly like the plotted nodes.
# ------------------------------------------------------------
anchor = '''                        y_column = "Relative %"
                        y_label = f"{metric} change (%)"

                    #
                    # Load ALL historical Simulation trades so every chart
'''

replacement = '''                        y_column = "Relative %"
                        y_label = f"{metric} change (%)"

                    #
                    # OPEN positive/negative filtering is based on the final
                    # Relative % node that will actually be plotted for each
                    # ticker in the currently selected range and metric.
                    #
                    if asset_mode in {
                        "OPEN positive positions",
                        "OPEN negative positions",
                    }:
                        relative_for_filter = chart_df.copy()

                        # The requested filters are explicitly defined by the
                        # Relative node, regardless of whether the user selected
                        # Absolute for the visible y-axis.
                        relative_for_filter["_FilterRelative"] = (
                            relative_for_filter
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

                        last_relative = (
                            relative_for_filter
                            .sort_values(["ticker", "timestamp"])
                            .groupby("ticker", as_index=False)
                            .tail(1)
                            .set_index("ticker")["_FilterRelative"]
                        )

                        if asset_mode == "OPEN positive positions":
                            matching_tickers = set(
                                last_relative[
                                    last_relative > 0
                                ].index.astype(str)
                            )
                            filter_description = (
                                "last plotted Relative % node > 0%"
                            )
                        else:
                            matching_tickers = set(
                                last_relative[
                                    last_relative < 0
                                ].index.astype(str)
                            )
                            filter_description = (
                                "last plotted Relative % node < 0%"
                            )

                        selected_assets = [
                            ticker
                            for ticker in selected_assets
                            if str(ticker) in matching_tickers
                        ]

                        chart_df = chart_df[
                            chart_df["ticker"]
                            .astype(str)
                            .isin(selected_assets)
                        ].copy()

                        if selected_assets:
                            st.caption(
                                f"{asset_mode}: "
                                + ", ".join(selected_assets)
                                + f" ({filter_description})."
                            )
                        else:
                            st.info(
                                "No currently OPEN Simulation positions "
                                f"have {filter_description} for the selected "
                                "metric and range."
                            )

                    #
                    # Load ALL historical Simulation trades so every chart
'''

if anchor not in section:
    raise SystemExit(
        "ERROR: Relative % calculation anchor not found; no changes written."
    )

section = section.replace(anchor, replacement, 1)

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data OPEN positive/negative filters now use "
    "the last plotted Relative % node for the selected metric and range."
)
