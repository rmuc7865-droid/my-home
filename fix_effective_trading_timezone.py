#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                        tz=polygon_timezone,
                    ):
'''

new = '''                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                    ):
'''

if old not in text:
    raise SystemExit(
        "ERROR: Effective Trading pd.date_range timezone block not found; no changes written."
    )

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Effective Trading timezone assertion fixed "
    "by using the timezone already attached to start/end."
)
