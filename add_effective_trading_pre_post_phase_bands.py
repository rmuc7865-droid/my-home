#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

# ------------------------------------------------------------
# Replace the current single Polygon opening-band construction
# with phase-aware Pre / Opening / Post intervals.
# ------------------------------------------------------------
old = '''                polygon_config = TRADING_WINDOWS.get("US") or {}
                polygon_open_intervals = []

                if polygon_config.get("enabled", True):
                    polygon_timezone = str(
                        polygon_config.get(
                            "timezone",
                            "America/New_York",
                        )
                    )
                    polygon_tz = ZoneInfo(polygon_timezone)

                    def _minutes_from_hhmm(value, default):
                        raw = str(value or default)
                        hour_text, minute_text = raw.split(":", 1)
                        return int(hour_text) * 60 + int(minute_text)

                    configured_starts = [
                        _minutes_from_hhmm(
                            polygon_config.get("buy_start"),
                            "00:00",
                        ),
                        _minutes_from_hhmm(
                            polygon_config.get("sell_start"),
                            "00:00",
                        ),
                    ]
                    configured_ends = [
                        _minutes_from_hhmm(
                            polygon_config.get("buy_end"),
                            "23:59",
                        ),
                        _minutes_from_hhmm(
                            polygon_config.get("sell_end"),
                            "23:59",
                        ),
                    ]

                    open_minute = min(configured_starts)
                    close_minute = max(configured_ends)

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

                    local_start = (
                        analysis_start
                        .tz_convert(polygon_tz)
                        .normalize()
                    )
                    local_end = (
                        reference_time
                        .tz_convert(polygon_tz)
                        .normalize()
                    )

                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                    ):
                        weekday_key = (
                            local_day.strftime("%a")
                            .lower()[:3]
                        )
                        date_key = local_day.strftime("%Y-%m-%d")

                        if (
                            weekday_key not in allowed_weekdays
                            or date_key in closed_dates
                        ):
                            continue

                        interval_start = (
                            local_day
                            + pd.Timedelta(minutes=open_minute)
                        )
                        interval_end = (
                            local_day
                            + pd.Timedelta(minutes=close_minute)
                        )

                        polygon_open_intervals.append(
                            {
                                "StartLocal": interval_start.tz_convert(
                                    LOCAL_TIMEZONE
                                ),
                                "EndLocal": interval_end.tz_convert(
                                    LOCAL_TIMEZONE
                                ),
                            }
                        )
'''

new = '''                polygon_config = TRADING_WINDOWS.get("US") or {}
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
                        or polygon_config.get(
                            "timezone",
                            "America/New_York",
                        )
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

                    local_start = (
                        analysis_start
                        .tz_convert(polygon_tz)
                        .normalize()
                    )
                    local_end = (
                        reference_time
                        .tz_convert(polygon_tz)
                        .normalize()
                    )

                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                    ):
                        weekday_key = (
                            local_day.strftime("%a")
                            .lower()[:3]
                        )
                        date_key = local_day.strftime("%Y-%m-%d")

                        if (
                            weekday_key not in allowed_weekdays
                            or date_key in closed_dates
                        ):
                            continue

                        pre_start = (
                            local_day
                            + pd.Timedelta(minutes=pre_start_minute)
                        )
                        opening_start = (
                            local_day
                            + pd.Timedelta(minutes=opening_start_minute)
                        )
                        opening_end = (
                            local_day
                            + pd.Timedelta(minutes=opening_end_minute)
                        )
                        post_end = (
                            local_day
                            + pd.Timedelta(minutes=post_end_minute)
                        )

                        if pre_start < opening_start:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": pre_start.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "EndLocal": opening_start.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "Phase": "Pre-Trading",
                                }
                            )

                        if opening_start < opening_end:
                            polygon_open_intervals.append(
                                {
                                    "StartLocal": opening_start.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "EndLocal": opening_end.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "Phase": "Opening",
                                }
                            )

                        if opening_end < post_end:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": opening_end.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "EndLocal": post_end.tz_convert(
                                        LOCAL_TIMEZONE
                                    ),
                                    "Phase": "Post-Trading",
                                }
                            )
'''

if old not in text:
    raise SystemExit(
        "ERROR: Effective Trading Polygon interval block not found; no changes written."
    )

text = text.replace(old, new, 1)

# ------------------------------------------------------------
# Add a second layer for Pre-Trading + Post-Trading.
# Preserve the existing Opening layer.
# ------------------------------------------------------------
old = '''                layers = []

                if polygon_open_intervals:
                    polygon_open_df = pd.DataFrame(
                        polygon_open_intervals
                    )

                    open_bands = (
                        alt.Chart(polygon_open_df)
                        .mark_rect(opacity=0.08)
                        .encode(
'''

new = '''                layers = []

                if polygon_prepost_intervals:
                    polygon_prepost_df = pd.DataFrame(
                        polygon_prepost_intervals
                    )

                    prepost_bands = (
                        alt.Chart(polygon_prepost_df)
                        .mark_rect(
                            opacity=0.10,
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
                        .mark_rect(opacity=0.08)
                        .encode(
'''

if old not in text:
    raise SystemExit(
        "ERROR: Effective Trading opening-band chart layer not found; no changes written."
    )

text = text.replace(old, new, 1)

# Add phase tooltip to Opening if the exact tooltip block is available.
old_tooltip = '''                            tooltip=[
                                alt.Tooltip(
                                    "StartLocal:T",
                                    title="Polygon open",
'''
new_tooltip = '''                            tooltip=[
                                alt.Tooltip(
                                    "Phase:N",
                                    title="Phase",
                                ),
                                alt.Tooltip(
                                    "StartLocal:T",
                                    title="Polygon open",
'''
if old_tooltip in text:
    text = text.replace(old_tooltip, new_tooltip, 1)

# Update the note to describe both band colors.
old_note = '''                    "Shaded vertical bands identify timepoints inside the configured US/Polygon "
                    "opening interval. The band uses the current Settings calendar and spans the "
                    "earliest configured BUY/SELL start through the latest BUY/SELL end. "
'''

new_note = '''                    "Shaded vertical bands identify the configured US/Polygon trading phases. "
                    "Opening uses the existing opening-band color; Pre-Trading and Post-Trading "
                    "use the same second color. Phase times come from the trading_phases settings "
                    "and market weekdays/closed dates come from the current Settings calendar. "
'''

if old_note in text:
    text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")

print(
    "SUCCESS: Effective Trading now shades Opening plus Pre-Trading/Post-Trading "
    "with a shared second color."
)
