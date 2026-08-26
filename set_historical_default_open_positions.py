#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''            asset_mode = control_cols[0].selectbox(
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

new = '''            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Top K",
                    "OPEN positions",
                    "OPEN positive positions",
                    "OPEN negative positions",
                    "Single",
                    "All",
                ],
                index=1,
                key="historical_data_assets_v4",
            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Historical Data Assets selector block not found; no changes written."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data default Assets value changed to OPEN positions."
)
