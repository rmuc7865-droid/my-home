#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

changed = 0

# Current compact Effective Trading main-axis form.
old = '                    x=alt.X("TimeLocal:T", title="Time"),'
new = '''                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                        axis=alt.Axis(
                            format="%H:%M",
                            labelAngle=0,
                        ),
                    ),'''
if old in text:
    text = text.replace(old, new, 1)
    changed += 1

# Also support the earlier multiline form.
old_multiline = '''                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                    ),
'''
new_multiline = '''                    x=alt.X(
                        "TimeLocal:T",
                        title="Time",
                        axis=alt.Axis(
                            format="%H:%M",
                            labelAngle=0,
                        ),
                    ),
'''
if old_multiline in text:
    text = text.replace(old_multiline, new_multiline, 1)
    changed += 1

# Current compact Polygon-band x-axis form.
old_band = '                        x=alt.X("StartLocal:T", title="Time"),'
new_band = '''                        x=alt.X(
                            "StartLocal:T",
                            title="Time",
                            axis=alt.Axis(
                                format="%H:%M",
                                labelAngle=0,
                            ),
                        ),'''
if old_band in text:
    text = text.replace(old_band, new_band, 1)
    changed += 1

# Earlier multiline Polygon-band form.
old_band_multiline = '''                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                            ),
'''
new_band_multiline = '''                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                                axis=alt.Axis(
                                    format="%H:%M",
                                    labelAngle=0,
                                ),
                            ),
'''
if old_band_multiline in text:
    text = text.replace(old_band_multiline, new_band_multiline, 1)
    changed += 1

if changed == 0:
    raise SystemExit(
        "ERROR: Effective Trading Time axis encoding not found; no changes written."
    )

# Strengthen the displayed note.
old_note = (
    '"15-minute timepoint. Shaded vertical bands identify timepoints "'
)
new_note = (
    '"15-minute timepoint. Time is displayed on a 24-hour HH:MM scale "
    "(for example 00:00, 00:15, 13:00, 16:15, 23:45). "
    "Shaded vertical bands identify timepoints "'
)
if old_note in text:
    text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    f"SUCCESS: Effective Trading Time axis forced to 24-hour HH:MM "
    f"format ({changed} axis encoding(s) updated)."
)
