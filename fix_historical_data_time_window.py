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

# Insert explicit x-axis range immediately before the existing update_layout call.
anchor = '''                    figure.update_layout(
                        xaxis_title="Local time",
                        yaxis_title=y_label,
                        hovermode="x unified",
                    )
'''

replacement = '''                    # Keep market-phase rectangles from expanding the visible
                    # x-axis to the full Pre/Opening/Post trading day.
                    #
                    # For a selected historical range R, show:
                    #     latest point - 2*R  ->  latest point + R
                    #
                    # Example for 2h with latest point 22:15:
                    #     18:15 -> 00:15
                    if (
                        range_label != "All"
                        and range_label in range_map
                        and not chart_df.empty
                    ):
                        visible_range = range_map[range_label]
                        visible_last = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )

                        if pd.notna(visible_last):
                            xaxis_start = (
                                visible_last
                                - 2 * visible_range
                            ).tz_convert(LOCAL_TIMEZONE)
                            xaxis_end = (
                                visible_last
                                + visible_range
                            ).tz_convert(LOCAL_TIMEZONE)

                            figure.update_xaxes(
                                range=[
                                    xaxis_start,
                                    xaxis_end,
                                ]
                            )

                    figure.update_layout(
                        xaxis_title="Local time",
                        yaxis_title=y_label,
                        hovermode="x unified",
                    )
'''

if anchor not in section:
    raise SystemExit(
        "ERROR: Historical Data figure.update_layout block not found; no changes written."
    )

section = section.replace(anchor, replacement, 1)

# Update/add explanatory note near the phase note.
old_note = '''                    st.caption(
                        "Market phases: background bands use the same US/Polygon phase "
                        "configuration as Resources. Opening is light blue; Pre-Trading "
                        "and Post-Trading share the orange background. Weekdays, closed "
                        "dates, phase times, and timezone come from the current settings."
                    )
'''

new_note = '''                    st.caption(
                        "Market phases: background bands use the same US/Polygon phase "
                        "configuration as Resources. Opening is light blue; Pre-Trading "
                        "and Post-Trading share the orange background. Weekdays, closed "
                        "dates, phase times, and timezone come from the current settings. "
                        "For a selected Range R, the visible time axis is fixed to "
                        "LastTime - 2×R through LastTime + R so phase bands do not "
                        "artificially expand the diagram."
                    )
'''

if old_note in section:
    section = section.replace(old_note, new_note, 1)

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data x-axis now uses "
    "LastTime - 2xRange through LastTime + Range."
)
