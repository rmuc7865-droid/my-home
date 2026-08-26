#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------
# 1) Default Effective Trading Range -> 1 day
# ------------------------------------------------------------
effective_start = text.find('elif page == "Effective Trading":')
if effective_start == -1:
    raise SystemExit("ERROR: Effective Trading page not found; no changes written.")

effective_end = text.find('elif page == "System Health":', effective_start)
if effective_end == -1:
    effective_end = len(text)

section = text[effective_start:effective_end]

old_range = '''    range_label = st.selectbox(
        "Range",
        [
            "7 days",
            "1 day",
            "6 hours",
            "2 hours",
        ],
        index=0,
    )
'''

new_range = '''    range_label = st.selectbox(
        "Range",
        [
            "7 days",
            "1 day",
            "6 hours",
            "2 hours",
        ],
        index=1,
    )
'''

if old_range not in section:
    raise SystemExit(
        "ERROR: Effective Trading Range selector block not found; no changes written."
    )

section = section.replace(old_range, new_range, 1)

# ------------------------------------------------------------
# 2) Replace separate phase-band layers with one unified layer.
#    This ensures Pre/Post are actually rendered.
# ------------------------------------------------------------
start_marker = '''                layers = []

                if polygon_prepost_intervals:
'''
start = section.find(start_marker)

end_marker = '''                layers.extend([lines, points])
'''
end = section.find(end_marker, start)

if start == -1 or end == -1:
    raise SystemExit(
        "ERROR: Current Effective Trading phase-band layer block not found; no changes written."
    )

end += len(end_marker)

new_layers = '''                layers = []

                phase_band_rows = []

                for interval in polygon_prepost_intervals:
                    row = dict(interval)
                    row["BandGroup"] = "Pre/Post"
                    phase_band_rows.append(row)

                for interval in polygon_open_intervals:
                    row = dict(interval)
                    row["BandGroup"] = "Opening"
                    phase_band_rows.append(row)

                if phase_band_rows:
                    phase_band_df = pd.DataFrame(
                        phase_band_rows
                    )

                    phase_bands = (
                        alt.Chart(phase_band_df)
                        .mark_rect(
                            opacity=0.12,
                        )
                        .encode(
                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                                axis=alt.Axis(
                                    format="%H:%M",
                                    labelAngle=0,
                                ),
                            ),
                            x2="EndLocal:T",
                            color=alt.Color(
                                "BandGroup:N",
                                title="Market phase",
                                scale=alt.Scale(
                                    domain=[
                                        "Opening",
                                        "Pre/Post",
                                    ],
                                    range=[
                                        "#c6dbef",
                                        "#f4a261",
                                    ],
                                ),
                            ),
                            tooltip=[
                                alt.Tooltip(
                                    "Phase:N",
                                    title="Phase",
                                ),
                                alt.Tooltip(
                                    "StartLocal:T",
                                    title="Start",
                                    format="%Y-%m-%d %H:%M",
                                ),
                                alt.Tooltip(
                                    "EndLocal:T",
                                    title="End",
                                    format="%Y-%m-%d %H:%M",
                                ),
                            ],
                        )
                    )

                    layers.append(phase_bands)

                layers.extend([lines, points])
'''

section = section[:start] + new_layers + section[end:]

# ------------------------------------------------------------
# 3) Update explanatory text.
# ------------------------------------------------------------
old_note = '''                    "Shaded vertical bands identify the configured US/Polygon trading phases. "
                    "Opening uses the existing opening-band color; Pre-Trading and Post-Trading "
                    "use the same second color. Phase times come from the trading_phases settings "
                    "and market weekdays/closed dates come from the current Settings calendar. "
'''

new_note = '''                    "Shaded vertical bands identify the configured US/Polygon trading phases. "
                    "Opening uses one band color; Pre-Trading and Post-Trading share a second "
                    "band color. Phase times come from the trading_phases settings and market "
                    "weekdays/closed dates come from the current Settings calendar. "
'''

if old_note in section:
    section = section.replace(old_note, new_note, 1)

text = text[:effective_start] + section + text[effective_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Effective Trading default Range is 1 day; "
    "Opening and Pre/Post phase bands now use one unified chart layer."
)
