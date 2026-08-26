#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                    ),
'''

new = '''                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                        axis=alt.Axis(
                            format="%H:%M",
                        ),
                    ),
'''

if old not in text:
    raise SystemExit(
        "ERROR: Effective Trading main Time axis block not found; no changes written."
    )

text = text.replace(old, new, 1)

# The shaded Polygon opening bands share the same x scale. Give their
# temporal x encoding the same explicit 24-hour axis format as well.
old_band = '''                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                            ),
'''

new_band = '''                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                                axis=alt.Axis(
                                    format="%H:%M",
                                ),
                            ),
'''

if old_band in text:
    text = text.replace(old_band, new_band, 1)

# Update the explanatory note if present.
needle = '''Each node represents a 15-minute timepoint. '''
replacement = '''Each node represents a 15-minute timepoint and the Time axis uses 24-hour HH:MM notation. '''
if needle in text:
    text = text.replace(needle, replacement, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Effective Trading Time axis now uses 24-hour HH:MM notation."
)
