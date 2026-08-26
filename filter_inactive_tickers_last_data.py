#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''                inactive_mask = ~relevant["Ticker"].astype(str).isin(
                    currently_collected_tickers
                )
                relevant.loc[inactive_mask, "LastPrice"] = "—"
                relevant.loc[inactive_mask, "SellingTime"] = "—"
                for column in [
                    "Phase",
                    "DropDuration",
                    "StaticDuration",
                    "WaitToTrade",
                    "WaitToOpening",
                ]:
                    relevant.loc[inactive_mask, column] = "-"

                live_columns = [
'''

new = '''                inactive_mask = ~relevant["Ticker"].astype(str).isin(
                    currently_collected_tickers
                )

                # Last Data is an actionable ZERO trading view. Historical,
                # inactive-source, and stale-bar tickers remain in storage and
                # other historical views, but do not help the user decide what
                # can currently be bought or sold, so exclude them here.
                relevant = relevant.loc[~inactive_mask].copy()

                live_columns = [
'''

if old not in text:
    raise SystemExit(
        "ERROR: Last Data inactive-row display block not found; no changes written."
    )

text = text.replace(old, new, 1)

old_caption = '''            "which is the operating-day boundary for this view. Historical tickers, including "
            "tickers whose configured collector source is inactive or whose latest market bar is "
            "too stale, remain visible, but their actionable Phase/wait/price fields are shown "
            "as unavailable. Crypto bars older than 60 minutes and non-crypto bars older than "
            "72 hours are treated as stale."
'''

new_caption = '''            "which is the operating-day boundary for this view. The table shows only currently "
            "actionable tickers: tickers from inactive collector sources and tickers with stale "
            "market bars are filtered out. Crypto bars older than 60 minutes and non-crypto bars "
            "older than 72 hours are treated as stale."
'''

if old_caption not in text:
    raise SystemExit(
        "ERROR: Last Data stale-bar caption not found; no changes written."
    )

text = text.replace(old_caption, new_caption, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: Last Data now filters inactive and stale tickers out of the table.")
