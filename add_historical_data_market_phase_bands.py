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

# Insert phase interval construction immediately before the Plotly figure.
figure_marker = '''                    figure = px.line(
'''

if figure_marker not in section:
    raise SystemExit(
        "ERROR: Historical Data figure marker not found; no changes written."
    )

phase_block = '''                    #
                    # Market-phase background bands. Use exactly the same
                    # US phase configuration and colors as Resources.
                    #
                    historical_open_intervals = []
                    historical_prepost_intervals = []

                    polygon_config = TRADING_WINDOWS.get("US") or {}

                    if (
                        polygon_config.get("enabled", True)
                        and not chart_df.empty
                    ):
                        phase_config = dict(
                            DEFAULT_TRADING_PHASES.get("US") or {}
                        )
                        phase_config.update(
                            TRADING_PHASES.get("US") or {}
                        )

                        polygon_timezone = str(
                            phase_config.get("timezone")
                            or polygon_config.get(
                                "timezone",
                                "America/New_York",
                            )
                        )
                        polygon_tz = ZoneInfo(
                            polygon_timezone
                        )

                        def _historical_minutes_from_hhmm(
                            value,
                            default,
                        ):
                            raw = str(value or default)
                            hour_text, minute_text = raw.split(
                                ":",
                                1,
                            )
                            return (
                                int(hour_text) * 60
                                + int(minute_text)
                            )

                        pre_start_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get("pre_start"),
                                "04:00",
                            )
                        )
                        opening_start_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get(
                                    "opening_start"
                                ),
                                "09:30",
                            )
                        )
                        opening_end_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get(
                                    "opening_end"
                                ),
                                "16:00",
                            )
                        )
                        post_end_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get("post_end"),
                                "20:00",
                            )
                        )

                        raw_weekdays = polygon_config.get(
                            "open_weekdays"
                        )
                        allowed_weekdays = (
                            {
                                "mon",
                                "tue",
                                "wed",
                                "thu",
                                "fri",
                            }
                            if raw_weekdays is None
                            else {
                                str(value)
                                .strip()
                                .lower()[:3]
                                for value in raw_weekdays
                            }
                        )

                        closed_dates = {
                            str(value)
                            for value in (
                                polygon_config.get(
                                    "closed_dates"
                                )
                                or []
                            )
                        }

                        historical_start = pd.to_datetime(
                            chart_df["timestamp"].min(),
                            utc=True,
                            errors="coerce",
                        )
                        historical_end = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )

                        if (
                            pd.notna(historical_start)
                            and pd.notna(historical_end)
                        ):
                            local_start = (
                                historical_start
                                .tz_convert(polygon_tz)
                                .normalize()
                            )
                            local_end = (
                                historical_end
                                .tz_convert(polygon_tz)
                                .normalize()
                            )

                            for local_day in pd.date_range(
                                start=local_start,
                                end=local_end,
                                freq="D",
                            ):
                                weekday_key = (
                                    local_day
                                    .strftime("%a")
                                    .lower()[:3]
                                )
                                date_key = (
                                    local_day
                                    .strftime("%Y-%m-%d")
                                )

                                if (
                                    weekday_key
                                    not in allowed_weekdays
                                    or date_key
                                    in closed_dates
                                ):
                                    continue

                                pre_start = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=pre_start_minute
                                    )
                                )
                                opening_start = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=opening_start_minute
                                    )
                                )
                                opening_end = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=opening_end_minute
                                    )
                                )
                                post_end = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=post_end_minute
                                    )
                                )

                                if pre_start < opening_start:
                                    historical_prepost_intervals.append(
                                        (
                                            pre_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            opening_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Pre-Trading",
                                        )
                                    )

                                if opening_start < opening_end:
                                    historical_open_intervals.append(
                                        (
                                            opening_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            opening_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Opening",
                                        )
                                    )

                                if opening_end < post_end:
                                    historical_prepost_intervals.append(
                                        (
                                            opening_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            post_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Post-Trading",
                                        )
                                    )

'''

section = section.replace(
    figure_marker,
    phase_block + figure_marker,
    1,
)

# Add the rectangles directly after the px.line(...) statement and before the
# historical OPEN marker overlays. Use a stable marker from the existing page.
open_overlay_marker = '''                    #
                    # Highlight every historical point at which the ticker
'''

if open_overlay_marker not in section:
    raise SystemExit(
        "ERROR: Historical OPEN-overlay marker not found; no changes written."
    )

band_render = '''                    # Draw phase bands behind the ticker lines.
                    # Same colors/opacities as the Resources diagram.
                    for phase_start, phase_end, phase_name in (
                        historical_prepost_intervals
                    ):
                        figure.add_vrect(
                            x0=phase_start,
                            x1=phase_end,
                            fillcolor="#f4a261",
                            opacity=0.16,
                            layer="below",
                            line_width=0,
                        )

                    for phase_start, phase_end, phase_name in (
                        historical_open_intervals
                    ):
                        figure.add_vrect(
                            x0=phase_start,
                            x1=phase_end,
                            fillcolor="#c6dbef",
                            opacity=0.12,
                            layer="below",
                            line_width=0,
                        )

'''

section = section.replace(
    open_overlay_marker,
    band_render + open_overlay_marker,
    1,
)

# Extend the Historical Data explanatory caption if the node note exists.
note_marker = '''                    st.caption(
                        "Historical Data node meaning: the normal line markers are market-data "
'''

note_pos = section.find(note_marker)

if note_pos != -1:
    # Insert a separate concise phase note immediately before the node note.
    phase_note = '''                    st.caption(
                        "Market phases: background bands use the same US/Polygon phase "
                        "configuration as Resources. Opening is light blue; Pre-Trading "
                        "and Post-Trading share the orange background. Weekdays, closed "
                        "dates, phase times, and timezone come from the current settings."
                    )

'''
    section = (
        section[:note_pos]
        + phase_note
        + section[note_pos:]
    )

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Historical Data now shows Pre-Trading, Opening, and "
    "Post-Trading background bands using the same colors/settings as Resources."
)
