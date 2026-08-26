#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''                    # Keep market-phase rectangles from expanding the visible
                    # x-axis beyond the selected Historical Data range.
                    #
                    # The visible window ends exactly at the latest plotted point:
                    #   2h  -> LastTime - 2h  ... LastTime
                    #   6h  -> LastTime - 6h  ... LastTime
                    #   12h -> LastTime - 12h ... LastTime
                    #   24h -> LastTime - 24h ... LastTime
                    #   48h -> LastTime - 48h ... LastTime
                    #   7d  -> LastTime - 7d  ... LastTime
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
                                visible_last - visible_range
                            ).tz_convert(LOCAL_TIMEZONE)
                            xaxis_end = (
                                visible_last
                            ).tz_convert(LOCAL_TIMEZONE)

                            figure.update_xaxes(
                                range=[
                                    xaxis_start,
                                    xaxis_end,
                                ]
                            )
'''

new = '''                    # Keep market-phase rectangles from expanding the visible
                    # x-axis too far beyond the selected Historical Data range.
                    #
                    # Add a small amount of context after the latest plotted point:
                    #   2h  -> LastTime - 2.5h ... LastTime + 0.5h
                    #   6h  -> LastTime - 7h   ... LastTime + 1h
                    #   12h -> LastTime - 14h  ... LastTime + 2h
                    #   24h -> LastTime - 28h  ... LastTime + 4h
                    #   48h -> LastTime - 56h  ... LastTime + 8h
                    #   7d  -> LastTime - 8d4h ... LastTime + 1d4h
                    historical_axis_windows = {
                        "2h": (
                            pd.Timedelta(hours=2.5),
                            pd.Timedelta(hours=0.5),
                        ),
                        "6h": (
                            pd.Timedelta(hours=7),
                            pd.Timedelta(hours=1),
                        ),
                        "12h": (
                            pd.Timedelta(hours=14),
                            pd.Timedelta(hours=2),
                        ),
                        "24h": (
                            pd.Timedelta(hours=28),
                            pd.Timedelta(hours=4),
                        ),
                        "48h": (
                            pd.Timedelta(hours=56),
                            pd.Timedelta(hours=8),
                        ),
                        "7d": (
                            pd.Timedelta(days=8, hours=4),
                            pd.Timedelta(days=1, hours=4),
                        ),
                    }

                    if (
                        range_label in historical_axis_windows
                        and not chart_df.empty
                    ):
                        before_window, after_window = (
                            historical_axis_windows[range_label]
                        )
                        visible_last = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )

                        if pd.notna(visible_last):
                            xaxis_start = (
                                visible_last - before_window
                            ).tz_convert(LOCAL_TIMEZONE)
                            xaxis_end = (
                                visible_last + after_window
                            ).tz_convert(LOCAL_TIMEZONE)

                            figure.update_xaxes(
                                range=[
                                    xaxis_start,
                                    xaxis_end,
                                ]
                            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Current Historical Data exact x-axis block not found; no changes written."
    )

text = text.replace(old, new, 1)

old_note = '''                        "dates, phase times, and timezone come from the current settings. "
                        "The visible time axis matches the selected Range exactly and ends at "
                        "the latest plotted point (for example, 2h = LastTime - 2h through "
                        "LastTime; 6h = LastTime - 6h through LastTime), so phase bands do "
                        "not artificially expand the diagram."
'''

new_note = '''                        "dates, phase times, and timezone come from the current settings. "
                        "The visible time axis leaves a small amount of context after the latest "
                        "point (for example, 2h = -2.5h/+0.5h, 6h = -7h/+1h, "
                        "12h = -14h/+2h), while preventing phase bands from expanding the "
                        "diagram too far."
'''

if old_note in text:
    text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data x-axis updated with balanced before/after spacing."
)
