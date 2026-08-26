#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

page_start = text.find('elif page == "Effective Trading":')
page_end = text.find('elif page == "System Health":', page_start)

if page_start == -1 or page_end == -1:
    raise SystemExit("ERROR: Effective Trading page boundaries not found; no changes written.")

section = text[page_start:page_end]

old_range = '''    range_label = st.selectbox(
        "Range",
        ["7 days", "1 day", "6 hours", "2 hours"],
        index=0,
    )
'''
new_range = '''    range_label = st.selectbox(
        "Range",
        ["7 days", "1 day", "6 hours", "2 hours"],
        index=1,
        key="effective_trading_range_v3",
    )
'''

if old_range not in section:
    raise SystemExit("ERROR: Current Range selector not found; no changes written.")

section = section.replace(old_range, new_range, 1)

phase_start_marker = '                polygon_config = TRADING_WINDOWS.get("US") or {}\n'
phase_end_marker = '                base = alt.Chart('

phase_start = section.find(phase_start_marker)
phase_end = section.find(phase_end_marker, phase_start)

if phase_start == -1 or phase_end == -1:
    raise SystemExit("ERROR: Polygon phase-construction boundaries not found; no changes written.")

new_phase_block = '''                polygon_config = TRADING_WINDOWS.get("US") or {}
                polygon_open_intervals = []
                polygon_prepost_intervals = []

                if polygon_config.get("enabled", True):
                    phase_config = dict(
                        DEFAULT_TRADING_PHASES.get("US") or {}
                    )
                    phase_config.update(
                        TRADING_PHASES.get("US") or {}
                    )

                    polygon_timezone = str(
                        phase_config.get("timezone")
                        or polygon_config.get("timezone", "America/New_York")
                    )
                    polygon_tz = ZoneInfo(polygon_timezone)

                    def _minutes_from_hhmm(value, default):
                        raw = str(value or default)
                        hour_text, minute_text = raw.split(":", 1)
                        return int(hour_text) * 60 + int(minute_text)

                    pre_start_minute = _minutes_from_hhmm(
                        phase_config.get("pre_start"),
                        "04:00",
                    )
                    opening_start_minute = _minutes_from_hhmm(
                        phase_config.get("opening_start"),
                        "09:30",
                    )
                    opening_end_minute = _minutes_from_hhmm(
                        phase_config.get("opening_end"),
                        "16:00",
                    )
                    post_end_minute = _minutes_from_hhmm(
                        phase_config.get("post_end"),
                        "20:00",
                    )

                    raw_weekdays = polygon_config.get("open_weekdays")
                    allowed_weekdays = (
                        {"mon", "tue", "wed", "thu", "fri"}
                        if raw_weekdays is None
                        else {
                            str(value).strip().lower()[:3]
                            for value in raw_weekdays
                        }
                    )

                    closed_dates = {
                        str(value)
                        for value in (
                            polygon_config.get("closed_dates")
                            or []
                        )
                    }

                    local_start = analysis_start.tz_convert(polygon_tz).normalize()
                    local_end = reference_time.tz_convert(polygon_tz).normalize()

                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                    ):
                        weekday_key = local_day.strftime("%a").lower()[:3]
                        date_key = local_day.strftime("%Y-%m-%d")

                        if (
                            weekday_key not in allowed_weekdays
                            or date_key in closed_dates
                        ):
                            continue

                        pre_start = local_day + pd.Timedelta(minutes=pre_start_minute)
                        opening_start = local_day + pd.Timedelta(minutes=opening_start_minute)
                        opening_end = local_day + pd.Timedelta(minutes=opening_end_minute)
                        post_end = local_day + pd.Timedelta(minutes=post_end_minute)

                        if pre_start < opening_start:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": pre_start.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": opening_start.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Pre-Trading",
                                }
                            )

                        if opening_start < opening_end:
                            polygon_open_intervals.append(
                                {
                                    "StartLocal": opening_start.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": opening_end.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Opening",
                                }
                            )

                        if opening_end < post_end:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": opening_end.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": post_end.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Post-Trading",
                                }
                            )

'''

section = section[:phase_start] + new_phase_block + section[phase_end:]

layers_start_marker = '                layers = []\n'
layers_end_marker = '                layers.extend([lines, points])\n'

layers_start = section.find(layers_start_marker)
layers_end = section.find(layers_end_marker, layers_start)

if layers_start == -1 or layers_end == -1:
    raise SystemExit("ERROR: Chart layer boundaries not found; no changes written.")

layers_end += len(layers_end_marker)

new_layers = '''                layers = []

                if polygon_prepost_intervals:
                    polygon_prepost_df = pd.DataFrame(
                        polygon_prepost_intervals
                    )
                    prepost_bands = (
                        alt.Chart(polygon_prepost_df)
                        .mark_rect(
                            opacity=0.16,
                            color="#f4a261",
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
                    layers.append(prepost_bands)

                if polygon_open_intervals:
                    polygon_open_df = pd.DataFrame(
                        polygon_open_intervals
                    )
                    open_bands = (
                        alt.Chart(polygon_open_df)
                        .mark_rect(
                            opacity=0.12,
                            color="#c6dbef",
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
                    layers.append(open_bands)

                layers.extend([lines, points])
'''

section = section[:layers_start] + new_layers + section[layers_end:]

old_caption = '''                    "Shaded vertical bands identify timepoints inside the configured US/Polygon "
                    "opening interval. The band uses the current Settings calendar and spans the "
                    "earliest configured BUY/SELL start through the latest BUY/SELL end. "
'''
new_caption = '''                    "Shaded vertical bands identify the configured US/Polygon phases. "
                    "Opening uses the light-blue background; Pre-Trading and Post-Trading "
                    "share the second background color. The phase boundaries use the same "
                    "trading_phases configuration as Last Data. "
'''

if old_caption in section:
    section = section.replace(old_caption, new_caption, 1)

text = text[:page_start] + section + text[page_end:]
path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: exact Effective Trading block patched: default Range=1 day, "
    "Pre-Trading/Post-Trading bands added, Opening band preserved."
)
