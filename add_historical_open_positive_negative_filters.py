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
# 1) Add the two new Assets selector values.
# ------------------------------------------------------------
old_assets = '''            asset_mode = control_cols[0].selectbox(
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

new_assets = '''            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Top K",
                    "OPEN positions",
                    "OPEN positive positions",
                    "OPEN negative positions",
                    "Single",
                    "All",
                ],
                index=0,
                key="historical_data_assets_v3",
            )
'''

if old_assets not in section:
    raise SystemExit(
        "ERROR: Historical Data Assets selector block not found; no changes written."
    )

section = section.replace(old_assets, new_assets, 1)

# ------------------------------------------------------------
# 2) Build latest CloseB lookup from ranking/live overview.
#    ranking_df is already created at the top of Historical Data.
# ------------------------------------------------------------
anchor = '''        if not ranking_df.empty:
            ranked_assets = ranking_df["Ticker"].tolist()
        else:
'''

insert = '''        if not ranking_df.empty:
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
'''

if anchor not in section:
    raise SystemExit(
        "ERROR: Historical Data ranking block not found; no changes written."
    )

section = section.replace(anchor, insert, 1)

# Ensure map exists also in the ranking-empty case.
old_else = '''            ranked_assets = sorted(
                historical["ticker"]
                .dropna()
                .astype(str)
                .unique()
            )
'''

new_else = '''            latest_closeb_map = {}
            ranked_assets = sorted(
                historical["ticker"]
                .dropna()
                .astype(str)
                .unique()
            )
'''

if old_else not in section:
    raise SystemExit(
        "ERROR: Historical Data ranking fallback block not found; no changes written."
    )

section = section.replace(old_else, new_else, 1)

# ------------------------------------------------------------
# 3) Add selection logic after OPEN positions branch.
# ------------------------------------------------------------
open_branch = '''            elif asset_mode == "OPEN positions":
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
                        "Historical Data."
                    )

            elif asset_mode == "All":
'''

replacement = '''            elif asset_mode == "OPEN positions":
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
                        "Historical Data."
                    )

            elif asset_mode == "OPEN positive positions":
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

            elif asset_mode == "All":
'''

if open_branch not in section:
    raise SystemExit(
        "ERROR: Historical Data OPEN positions branch not found; no changes written."
    )

section = section.replace(open_branch, replacement, 1)

# ------------------------------------------------------------
# 4) Add a concise explanatory note near the selector logic.
# ------------------------------------------------------------
caption_anchor = '''            asset_df = (
                historical[
                    historical["ticker"].isin(selected_assets)
                ]
'''

note = '''            st.caption(
                "Asset filters: OPEN positions = currently open simulator trades. "
                "OPEN positive positions = currently OPEN tickers whose latest "
                "approximately two-hour CloseB is > 0%. OPEN negative positions = "
                "currently OPEN tickers whose latest CloseB is < 0%. A value of "
                "exactly 0% belongs to neither positive nor negative."
            )

'''

if caption_anchor not in section:
    raise SystemExit(
        "ERROR: Historical Data asset-data anchor not found; no changes written."
    )

section = section.replace(
    caption_anchor,
    note + caption_anchor,
    1,
)

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data Assets now includes OPEN positive positions "
    "and OPEN negative positions based on latest CloseB."
)
