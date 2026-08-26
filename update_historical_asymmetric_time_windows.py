#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old = '''                    # Keep market-phase rectangles from expanding the visible
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
'''

new = '''                    # Keep market-phase rectangles from expanding the visible
                    # x-axis to the full Pre/Opening/Post trading day.
                    #
                    # The display window intentionally leaves more room before
                    # the latest point than after it:
                    #   2h  -> LastTime - 3h   ... LastTime + 1h
                    #   6h  -> LastTime - 8h   ... LastTime + 2h
                    #   12h -> LastTime - 16h  ... LastTime + 4h
                    #   24h -> LastTime - 32h  ... LastTime + 8h
                    #   48h -> LastTime - 64h  ... LastTime + 16h
                    #   7d  -> LastTime - 9d8h ... LastTime + 2d8h
                    historical_axis_windows = {
                        "2h": (
                            pd.Timedelta(hours=3),
                            pd.Timedelta(hours=1),
                        ),
                        "6h": (
                            pd.Timedelta(hours=8),
                            pd.Timedelta(hours=2),
                        ),
                        "12h": (
                            pd.Timedelta(hours=16),
                            pd.Timedelta(hours=4),
                        ),
                        "24h": (
                            pd.Timedelta(hours=32),
                            pd.Timedelta(hours=8),
                        ),
                        "48h": (
                            pd.Timedelta(hours=64),
                            pd.Timedelta(hours=16),
                        ),
                        "7d": (
                            pd.Timedelta(days=7) * 4 / 3,
                            pd.Timedelta(days=7) / 3,
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
        "ERROR: Current Historical Data x-axis window block not found; no changes written."
    )

text = text.replace(old, new, 1)

old_note = '''                        "dates, phase times, and timezone come from the current settings. "
                        "For a selected Range R, the visible time axis is fixed to "
                        "LastTime - 2×R through LastTime + R so phase bands do not "
                        "artificially expand the diagram."
'''

new_note = '''                        "dates, phase times, and timezone come from the current settings. "
                        "The visible time axis deliberately leaves more context before the "
                        "latest point than after it (for example, 2h = LastTime - 3h through "
                        "LastTime + 1h; 6h = -8h/+2h; 12h = -16h/+4h), so phase bands do "
                        "not artificially expand the diagram."
'''

if old_note in text:
    text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data display windows updated to the new asymmetric ranges."
)
