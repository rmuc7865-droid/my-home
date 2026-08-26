#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

changed = 0

# Effective Trading main x-axis: compact form.
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

# Effective Trading main x-axis: multiline form.
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

# Polygon opening-band x-axis: compact form.
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

# Polygon opening-band x-axis: multiline form.
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

path.write_text(text, encoding="utf-8")

print(
    f"SUCCESS: Effective Trading Time axis forced to 24-hour HH:MM "
    f"format ({changed} axis encoding(s) updated)."
)
