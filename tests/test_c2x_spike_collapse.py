import pandas as pd

from shared.buy_signals import c2x_path_signal


def frame(closes):
    start = pd.Timestamp("2026-10-01 14:00:00", tz="UTC")
    return pd.DataFrame({
        "timestamp": [
            start + pd.Timedelta(minutes=15 * i)
            for i in range(len(closes))
        ],
        "close": closes,
    })


def signal(closes):
    df = frame(closes)
    return c2x_path_signal(
        df,
        df.iloc[-1]["timestamp"],
        window_minutes=60,
        min_rise_percent=25.0,
        min_drawdown_percent=15.0,
    )


def test_spike_collapse_blocks_at_exact_boundaries():
    # 100 -> 125 = exactly +25%.
    # Latest = 125 / 1.15 gives exactly 15% using replay DD definition:
    # peak/current - 1.
    latest = 125.0 / 1.15
    sig = signal([100.0, 125.0, 120.0, 112.0, latest])

    assert abs(sig.rise_percent - 25.0) < 1e-9
    assert abs(sig.drawdown_percent - 15.0) < 1e-9
    assert sig.excluded is True
    assert sig.trigger == "spike_collapse_60m"


def test_spike_collapse_allows_rise_below_boundary():
    sig = signal([100.0, 124.99, 120.0, 115.0, 108.0])

    assert sig.rise_percent < 25.0
    assert sig.drawdown_percent > 15.0
    assert sig.excluded is False


def test_spike_collapse_allows_drawdown_below_boundary():
    # Rise is safely above 25%, but drawdown is just below 15%.
    peak = 130.0
    latest = peak / 1.1499
    sig = signal([100.0, peak, 125.0, 120.0, latest])

    assert sig.rise_percent >= 25.0
    assert sig.drawdown_percent < 15.0
    assert sig.excluded is False


def test_large_rise_without_collapse_is_allowed():
    sig = signal([100.0, 110.0, 120.0, 130.0, 128.0])

    assert sig.rise_percent >= 25.0
    assert sig.drawdown_percent < 15.0
    assert sig.excluded is False


def test_path_uses_only_trailing_sixty_minutes():
    # Ancient 100 -> 200 spike must be outside the trailing 60m window.
    start = pd.Timestamp("2026-10-01 12:00:00", tz="UTC")
    df = pd.DataFrame({
        "timestamp": [
            start,
            start + pd.Timedelta(minutes=15),
            start + pd.Timedelta(minutes=120),
            start + pd.Timedelta(minutes=135),
            start + pd.Timedelta(minutes=150),
            start + pd.Timedelta(minutes=165),
            start + pd.Timedelta(minutes=180),
        ],
        "close": [100.0, 200.0, 100.0, 102.0, 104.0, 103.0, 102.0],
    })

    sig = c2x_path_signal(
        df,
        df.iloc[-1]["timestamp"],
        window_minutes=60,
        min_rise_percent=25.0,
        min_drawdown_percent=15.0,
    )

    assert sig.rise_percent < 25.0
    assert sig.excluded is False
