from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


@dataclass(frozen=True)
class TradingWindowInfo:
    is_open: bool
    remaining_time: timedelta | None
    first_next_time: pd.Timestamp | None


@dataclass(frozen=True)
class SellDecision:
    should_sell: bool
    c4_satisfied: bool
    c5_satisfied: bool
    max_time: pd.Timestamp | None
    last_one_proc_time: pd.Timestamp | None
    bought_before: pd.Timestamp | None
    max_price: float | None


def _parse_hhmm(value: str) -> dt_time:
    return dt_time.fromisoformat(str(value))


def trading_window_info(
    timestamp,
    market_config: dict,
    kind: str,
) -> TradingWindowInfo:
    """Return whether timestamp is in the YAML-configured trading window.

    Calendar behavior is configuration-driven. ``enabled`` can explicitly
    close a market, ``open_weekdays`` controls recurring trading days, and
    ``closed_dates`` can list holidays or one-off closures. Python does not
    hard-code exchange weekdays or holidays. When the market is closed because
    of its configured calendar, ``first_next_time`` is the next configured
    start time.
    """
    ts = pd.to_datetime(timestamp, utc=True)
    tz = ZoneInfo(str(market_config["timezone"]))
    local = ts.tz_convert(tz)

    if not bool(market_config.get("enabled", True)):
        configured_next = market_config.get(f"next_{kind}_time")
        next_time = (
            pd.to_datetime(configured_next, utc=True)
            if configured_next
            else None
        )
        return TradingWindowInfo(
            is_open=False,
            remaining_time=None,
            first_next_time=next_time,
        )

    start = _parse_hhmm(market_config[f"{kind}_start"])
    end = _parse_hhmm(market_config[f"{kind}_end"])

    weekday_names = {
        "mon": 0, "monday": 0,
        "tue": 1, "tues": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    raw_weekdays = market_config.get("open_weekdays")
    if raw_weekdays is None:
        allowed_weekdays = set(range(7))
    else:
        allowed_weekdays = set()
        for value in raw_weekdays:
            if isinstance(value, int):
                allowed_weekdays.add(value)
            else:
                key = str(value).strip().lower()
                if key not in weekday_names:
                    raise ValueError(f"Invalid open_weekdays value: {value!r}")
                allowed_weekdays.add(weekday_names[key])

    closed_dates = {
        str(value).strip()
        for value in (market_config.get("closed_dates") or [])
    }

    def date_allowed(day) -> bool:
        return day.weekday() in allowed_weekdays and day.isoformat() not in closed_dates

    def local_timestamp(day, clock: dt_time) -> pd.Timestamp:
        value = datetime.combine(day, clock, tzinfo=tz)
        return pd.Timestamp(value)

    def next_allowed_start(first_day) -> pd.Timestamp | None:
        day = first_day
        for _ in range(370):
            if date_allowed(day):
                return local_timestamp(day, start).tz_convert("UTC")
            day += timedelta(days=1)
        return None

    if start <= end:
        start_ts = local_timestamp(local.date(), start)
        end_ts = local_timestamp(local.date(), end)
        if date_allowed(local.date()) and start_ts <= local <= end_ts:
            return TradingWindowInfo(
                is_open=True,
                remaining_time=(end_ts - local).to_pytimedelta(),
                first_next_time=ts,
            )

        if date_allowed(local.date()) and local < start_ts:
            next_start = start_ts.tz_convert("UTC")
        else:
            next_start = next_allowed_start(local.date() + timedelta(days=1))
        return TradingWindowInfo(
            is_open=False,
            remaining_time=None,
            first_next_time=next_start,
        )

    # Window crosses midnight. The early-morning portion belongs to the
    # previous configured trading day.
    today_start = local_timestamp(local.date(), start)
    tomorrow_end = local_timestamp(local.date() + timedelta(days=1), end)
    yesterday = local.date() - timedelta(days=1)
    today_end = local_timestamp(local.date(), end)

    if date_allowed(local.date()) and local >= today_start:
        return TradingWindowInfo(
            is_open=True,
            remaining_time=(tomorrow_end - local).to_pytimedelta(),
            first_next_time=ts,
        )
    if date_allowed(yesterday) and local <= today_end:
        return TradingWindowInfo(
            is_open=True,
            remaining_time=(today_end - local).to_pytimedelta(),
            first_next_time=ts,
        )

    first_day = local.date() if local < today_start else local.date() + timedelta(days=1)
    return TradingWindowInfo(
        is_open=False,
        remaining_time=None,
        first_next_time=next_allowed_start(first_day),
    )


DEFAULT_TRADING_PHASES = {
    "US": {
        "timezone": "America/New_York",
        "pre_start": "04:00",
        "post_end": "20:00",
    },
    "DE": {
        "timezone": "Europe/Berlin",
        "pre_start": "08:00",
        "post_end": "22:00",
    },
    "CRYPTO": {
        "timezone": "UTC",
        "pre_start": "00:00",
        "post_end": "23:59",
    },
}


def _hhmm_minutes(value: str) -> int:
    parsed = _parse_hhmm(str(value))
    return parsed.hour * 60 + parsed.minute


def _market_date_allowed(local_date, market_config: dict | None) -> bool:
    if not market_config or not bool(market_config.get("enabled", True)):
        return False

    weekday_names = {
        "mon": 0, "monday": 0,
        "tue": 1, "tues": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    raw_weekdays = market_config.get("open_weekdays")
    if raw_weekdays is None:
        allowed_weekdays = set(range(7))
    else:
        allowed_weekdays = set()
        for value in raw_weekdays:
            if isinstance(value, int):
                allowed_weekdays.add(value)
            else:
                key = str(value).strip().lower()
                if key not in weekday_names:
                    raise ValueError(f"Invalid open_weekdays value: {value!r}")
                allowed_weekdays.add(weekday_names[key])

    closed_dates = {
        str(value).strip() for value in (market_config.get("closed_dates") or [])
    }
    return (
        local_date.weekday() in allowed_weekdays
        and local_date.isoformat() not in closed_dates
    )


def trading_time_window_start(
    latest_time,
    trading_hours: float,
    market_region: str | None = None,
    market_config: dict | None = None,
    phase_config: dict | None = None,
):
    """Return the UTC start after counting backwards only market-open time.

    ``trading_hours`` therefore excludes the interval between post-trading and
    the next pre-trading session, weekends, and configured closed dates.
    Crypto and calls without a market schedule remain continuous.
    """
    latest = pd.to_datetime(latest_time, utc=True, errors="coerce")
    if pd.isna(latest):
        return pd.NaT

    duration = pd.Timedelta(hours=float(trading_hours))
    if duration <= pd.Timedelta(0):
        return latest

    if market_region == "CRYPTO":
        return latest - duration

    merged_phase = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    merged_phase.update(phase_config or {})
    if not market_config or not merged_phase:
        return latest - duration

    tz_name = str(
        merged_phase.get("timezone")
        or market_config.get("timezone")
        or "UTC"
    )
    market_tz = ZoneInfo(tz_name)
    pre_start_minute = _hhmm_minutes(merged_phase.get("pre_start", "00:00"))
    post_end_minute = _hhmm_minutes(merged_phase.get("post_end", "23:59"))

    remaining = duration
    cursor = latest
    session_date = cursor.tz_convert(market_tz).date()

    # 720 configured hours can span many weekends/holidays; two years gives
    # ample room while still guarding against invalid calendars.
    for _ in range(740):
        if _market_date_allowed(session_date, market_config):
            day_start = pd.Timestamp(session_date, tz=market_tz)
            session_start = (
                day_start + pd.Timedelta(minutes=pre_start_minute)
            ).tz_convert("UTC")
            session_end_local = day_start + pd.Timedelta(minutes=post_end_minute)
            if post_end_minute <= pre_start_minute:
                session_end_local += pd.Timedelta(days=1)
            session_end = session_end_local.tz_convert("UTC")

            usable_end = min(cursor, session_end)
            if usable_end > session_start:
                available = usable_end - session_start
                if available >= remaining:
                    return usable_end - remaining
                remaining -= available

        session_date = session_date - timedelta(days=1)
        previous_day = pd.Timestamp(session_date, tz=market_tz)
        previous_end = previous_day + pd.Timedelta(minutes=post_end_minute)
        if post_end_minute <= pre_start_minute:
            previous_end += pd.Timedelta(days=1)
        cursor = previous_end.tz_convert("UTC")

    # Defensive fallback for pathological configurations.
    return latest - duration


def format_duration(value: timedelta | None) -> str:
    if value is None:
        return "—"
    seconds = max(0, int(value.total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def evaluate_sell_history(
    ticker_df: pd.DataFrame,
    latest_time,
    current_price: float,
    movement_percent: float = 1.1,
    c5_hours: float = 24.0,
    init_time=None,
    market_region: str | None = None,
    market_config: dict | None = None,
    phase_config: dict | None = None,
) -> SellDecision:
    """Evaluate proposal conditions C4 and C5 using available ticker history.

    C4: current price has dropped by more than movement_percent from the
    historical maximum observed from InitTime through latest_time. When
    init_time is supplied, measurements before it are excluded. MaxTime is the
    latest sampled timestamp at which that maximum occurred.

    C5: after a full trailing c5_hours of *trading time* has elapsed since
    InitTime, every collected close in that window lies within
    +/- movement_percent of the current price. Closed overnight periods,
    weekends, and configured closed dates do not count. C5 requires historical coverage
    reaching the beginning of that window. LastOneProcTime is the latest
    measurement at or before latest_time whose price is outside that band, so
    it remains useful as a BoughtBefore cutoff even when C5 is satisfied.
    """
    if ticker_df.empty or current_price <= 0:
        return SellDecision(False, False, False, None, None, None, None)

    latest = pd.to_datetime(latest_time, utc=True)
    init = pd.to_datetime(init_time, utc=True, errors="coerce") if init_time is not None else None
    if init is not None and pd.isna(init):
        init = None

    work = ticker_df[["timestamp", "close"]].copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    work = (
        work.dropna(subset=["timestamp", "close"])
        .loc[lambda frame: frame["timestamp"] <= latest]
        .sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"], keep="last")
    )
    if init is not None:
        work = work[work["timestamp"] >= init]
    if work.empty:
        return SellDecision(False, False, False, None, None, None, None)

    max_price = float(work["close"].max())
    max_rows = work[work["close"] == max_price]
    max_time = pd.to_datetime(max_rows.iloc[-1]["timestamp"], utc=True)
    # "More than the configured percentage" is strict: an exact threshold match does not satisfy C4.
    c4_satisfied = float(current_price) < max_price * (1.0 - movement_percent / 100.0)

    period_start = trading_time_window_start(
        latest,
        c5_hours,
        market_region=market_region,
        market_config=market_config,
        phase_config=phase_config,
    )
    trailing = work[work["timestamp"] >= period_start].copy()
    has_full_window = bool(work["timestamp"].min() <= period_start)

    lower = float(current_price) * (1.0 - movement_percent / 100.0)
    upper = float(current_price) * (1.0 + movement_percent / 100.0)
    c5_satisfied = bool(
        has_full_window
        and not trailing.empty
        and trailing["close"].between(lower, upper, inclusive="both").all()
    )

    outside_band = work[~work["close"].between(lower, upper, inclusive="both")]
    last_one_proc_time = None
    if not outside_band.empty:
        last_one_proc_time = pd.to_datetime(outside_band.iloc[-1]["timestamp"], utc=True)

    candidates = [value for value in (max_time, last_one_proc_time) if value is not None]
    bought_before = max(candidates) if candidates else None

    return SellDecision(
        should_sell=bool(c4_satisfied or c5_satisfied),
        c4_satisfied=bool(c4_satisfied),
        c5_satisfied=bool(c5_satisfied),
        max_time=max_time,
        last_one_proc_time=last_one_proc_time,
        bought_before=bought_before,
        max_price=max_price,
    )


def sell_applies_to_holding(decision: SellDecision, buy_time) -> bool:
    """Return whether a sell advisory applies to a holding bought at buy_time.

    BoughtBefore is a strict cutoff: a holding bought exactly at the cutoff is
    not considered "bought before" it. This prevents a fresh simulated BUY
    from being immediately closed when the current bar also defines MaxTime.
    """
    if not decision.should_sell or decision.bought_before is None:
        return False
    parsed_buy_time = pd.to_datetime(buy_time, utc=True, errors="coerce")
    if pd.isna(parsed_buy_time):
        return False
    return bool(parsed_buy_time < decision.bought_before)
