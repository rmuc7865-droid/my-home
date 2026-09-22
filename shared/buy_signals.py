from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SpikeSignal:
    rise_percent: float | None
    low_rise_percent: float | None
    excluded: bool
    acceleration_ratio: float | None = None
    trigger: str | None = None


def _clean_history(ticker_df: pd.DataFrame, latest_time) -> pd.DataFrame:
    if ticker_df is None or ticker_df.empty or "timestamp" not in ticker_df.columns:
        return pd.DataFrame()
    frame = ticker_df.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    frame = frame.dropna(subset=["timestamp", "close"])
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return frame
    when = pd.to_datetime(latest_time, utc=True, errors="coerce")
    if pd.isna(when):
        return pd.DataFrame()
    return frame[frame["timestamp"] <= when].sort_values("timestamp")


def c2x_hybrid_signal(
    ticker_df: pd.DataFrame,
    latest_time,
    *,
    lowrise_window_minutes: int = 30,
    hard_lowrise_percent: float = 8.0,
    soft_lowrise_percent: float = 6.0,
    acceleration_ratio_threshold: float = 4.0,
    baseline_days: int = 7,
    pair_tolerance_minutes: int = 10,
) -> SpikeSignal:
    """Past-only C2X short-term acceleration/exhaustion filter.

    Exclude when LowRise30 is above the hard threshold, or when it is above
    the soft threshold and is unusually large relative to the ticker's own
    recent absolute 30-minute moves.  The relative baseline is the median
    absolute exact-window move over the preceding ``baseline_days`` only.
    """
    history = _clean_history(ticker_df, latest_time)
    if history.empty:
        return SpikeSignal(None, None, False)
    when = pd.to_datetime(latest_time, utc=True)
    w = pd.Timedelta(minutes=int(lowrise_window_minutes))
    current_close = float(history.iloc[-1]["close"])
    current_window = history[(history["timestamp"] >= when - w) & (history["timestamp"] <= when)]
    if current_window.empty:
        return SpikeSignal(None, None, False)
    low_close = pd.to_numeric(current_window["close"], errors="coerce").min()
    if pd.isna(low_close) or float(low_close) <= 0:
        return SpikeSignal(None, None, False)
    low_rise = (current_close / float(low_close) - 1.0) * 100.0

    # Historical baseline excludes the current 30-minute window to avoid
    # letting the event being classified inflate its own normality baseline.
    hist = history[(history["timestamp"] >= when - pd.Timedelta(days=int(baseline_days))) &
                   (history["timestamp"] < when - w)].copy()
    moves = []
    if len(hist) >= 3:
        times = hist["timestamp"].tolist()
        closes = pd.to_numeric(hist["close"], errors="coerce").tolist()
        tol = pd.Timedelta(minutes=int(pair_tolerance_minutes))
        import bisect
        for i in range(1, len(times)):
            target = times[i] - w
            j = bisect.bisect_left(times, target, 0, i)
            candidates = [k for k in (j - 1, j) if 0 <= k < i]
            if not candidates:
                continue
            k = min(candidates, key=lambda x: abs(times[x] - target))
            if abs(times[k] - target) <= tol and closes[k] and closes[k] > 0:
                moves.append(abs((float(closes[i]) / float(closes[k]) - 1.0) * 100.0))
    typical = float(pd.Series(moves).median()) if moves else None
    ratio = (low_rise / typical) if typical is not None and typical > 0 else None

    hard = round(low_rise, 10) > round(float(hard_lowrise_percent), 10)
    soft = (round(low_rise, 10) > round(float(soft_lowrise_percent), 10) and
            ratio is not None and ratio >= float(acceleration_ratio_threshold))
    trigger = "hard_lowrise" if hard else ("soft_plus_abnormal" if soft else None)
    return SpikeSignal(None, float(low_rise), bool(hard or soft), ratio, trigger)
