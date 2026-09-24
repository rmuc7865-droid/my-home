import pandas as pd

from server.telegram_notifier import calculate_latest_highb


def _frame(closes):
    start = pd.Timestamp("2026-09-24 14:00:00", tz="UTC")
    rows = []
    for idx, close in enumerate(closes):
        ts = start + pd.Timedelta(minutes=15 * idx)
        rows.append({
            "id": idx + 1,
            "ticker": "TEST",
            "timestamp": ts,
            "close": close,
            "high": close,
            "system": "test",
            "asset_type": "stock",
        })
    return pd.DataFrame(rows)


def _result(closes):
    rows = calculate_latest_highb(
        _frame(closes),
        baseline_hours=2,
        tolerance_minutes=1,
        c2x_max_closeb_percent=8.0,
        c2x_max_peak_age_minutes=30.0,
    )
    assert len(rows) == 1
    return rows[0]


def test_c2x_blocks_full_two_hour_rise_above_eight_percent():
    # 100 -> 116.54 over 2h. The rise need not have happened in the last 30m.
    row = _result([100, 104, 110, 116.5, 117, 117, 116.8, 116.7, 116.54])
    assert row["closeb"] > 16.5
    assert row["c2x_excluded"] is True
    assert row["c2x_trigger"] == "closeb_too_high"


def test_c2x_blocks_positive_closeb_when_two_hour_peak_is_stale():
    # Still +5.88% vs 2h ago, but the maximum close was 45 minutes ago.
    row = _result([100, 101, 103, 105, 107, 110, 109, 107, 105.88])
    assert 5.8 < row["closeb"] < 6.0
    assert row["c2x_peak_age120_minutes"] == 45.0
    assert row["c2x_excluded"] is True
    assert row["c2x_trigger"] == "stale_2h_peak"


def test_c2x_allows_positive_closeb_when_peak_is_recent():
    row = _result([100, 100.5, 101, 102, 103, 104, 105, 106, 105.8])
    assert row["c2x_peak_age120_minutes"] == 15.0
    assert row["closeb"] < 8.0
    assert row["c2x_excluded"] is False
