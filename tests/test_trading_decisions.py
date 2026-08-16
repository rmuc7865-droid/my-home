import pandas as pd

from shared.trading_decisions import (
    evaluate_sell_history,
    sell_applies_to_holding,
    trading_window_info,
)


MARKET = {
    "timezone": "Europe/Berlin",
    "buy_start": "09:15",
    "buy_end": "17:20",
    "sell_start": "09:05",
    "sell_end": "17:25",
}


def test_trading_window_open_has_remaining_time():
    info = trading_window_info("2026-08-17T14:00:00Z", MARKET, "sell")
    assert info.is_open is True
    assert int(info.remaining_time.total_seconds()) == 85 * 60


def test_trading_window_closed_has_next_start():
    info = trading_window_info("2026-08-17T18:00:00Z", MARKET, "sell")
    assert info.is_open is False
    assert info.first_next_time.isoformat() == "2026-08-18T07:05:00+00:00"


def test_c4_true_after_more_than_configured_drop_from_peak():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-08-14T06:00:00Z",
                    "2026-08-14T06:15:00Z",
                    "2026-08-14T06:30:00Z",
                    "2026-08-14T06:45:00Z",
                ],
                utc=True,
            ),
            "close": [100.0, 103.0, 105.0, 102.0],
        }
    )
    decision = evaluate_sell_history(df, "2026-08-14T06:45:00Z", 102.0, movement_percent=1.1)
    assert decision.c4_satisfied is True
    assert decision.should_sell is True
    assert decision.max_price == 105.0
    assert decision.max_time.isoformat() == "2026-08-14T06:30:00+00:00"


def test_c4_false_at_exactly_configured_drop_from_peak():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-08-14T06:00:00Z", "2026-08-14T06:15:00Z", "2026-08-14T06:30:00Z"],
                utc=True,
            ),
            "close": [100.0, 105.0, 103.845],
        }
    )
    decision = evaluate_sell_history(df, "2026-08-14T06:30:00Z", 103.845, movement_percent=1.1)
    assert decision.c4_satisfied is False


def test_max_time_is_latest_sample_when_peak_repeats():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-08-14T06:00:00Z",
                    "2026-08-14T06:15:00Z",
                    "2026-08-14T06:30:00Z",
                    "2026-08-14T06:45:00Z",
                ],
                utc=True,
            ),
            "close": [100.0, 105.0, 105.0, 102.0],
        }
    )
    decision = evaluate_sell_history(df, "2026-08-14T06:45:00Z", 102.0)
    assert decision.max_time.isoformat() == "2026-08-14T06:30:00+00:00"

def test_c5_requires_full_24h_coverage_and_stable_band():
    times = pd.date_range("2026-08-14T10:00:00Z", periods=25, freq="h")
    df = pd.DataFrame({"timestamp": times, "close": [100.5] * 24 + [100.0]})
    decision = evaluate_sell_history(df, times[-1], 100.0)
    assert decision.c5_satisfied is True
    assert decision.should_sell is True


def test_c5_last_one_proc_time_is_before_stable_24h_window():
    # A price outside the current +/-1.1% band occurred before the stable 24h
    # period. C5 is true, but LastOneProcTime remains available as the cutoff.
    times = pd.date_range("2026-08-14T09:00:00Z", periods=26, freq="h")
    closes = [95.0] + [100.5] * 24 + [100.0]
    df = pd.DataFrame({"timestamp": times, "close": closes})
    decision = evaluate_sell_history(df, times[-1], 100.0)
    assert decision.c5_satisfied is True
    assert decision.last_one_proc_time.isoformat() == "2026-08-14T09:00:00+00:00"
    assert decision.bought_before >= decision.last_one_proc_time


def test_bought_before_uses_latest_of_max_and_last_outside_one_percent():
    times = pd.to_datetime(
        [
            "2026-08-13T08:00:00Z",
            "2026-08-14T09:00:00Z",
            "2026-08-15T10:00:00Z",
            "2026-08-16T10:00:00Z",
        ],
        utc=True,
    )
    df = pd.DataFrame({"timestamp": times, "close": [120.0, 95.0, 100.2, 100.0]})
    decision = evaluate_sell_history(df, times[-1], 100.0)
    assert decision.max_time.isoformat() == "2026-08-13T08:00:00+00:00"
    assert decision.last_one_proc_time.isoformat() == "2026-08-14T09:00:00+00:00"
    assert decision.bought_before.isoformat() == "2026-08-14T09:00:00+00:00"


def test_bought_before_is_strict_cutoff_for_holding():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-08-14T10:00:00Z", "2026-08-15T10:00:00Z"], utc=True
            ),
            "close": [110.0, 107.0],
        }
    )
    decision = evaluate_sell_history(df, "2026-08-15T10:00:00Z", 107.0, movement_percent=2.0)
    assert decision.c4_satisfied is True
    assert decision.bought_before.isoformat() == "2026-08-14T10:00:00+00:00"
    assert sell_applies_to_holding(decision, "2026-08-14T09:59:00Z") is True
    assert sell_applies_to_holding(decision, "2026-08-14T10:00:00Z") is False
    assert sell_applies_to_holding(decision, "2026-08-14T10:01:00Z") is False


def test_c5_user_example_full_24h_stagnation_uses_shared_movement_percent():
    # InitTime is 26 hours before the current sample. The first two hours are
    # outside the current-price band, but the complete trailing 24 hours stay
    # within +/-1.1% of the current price 104.
    times = pd.date_range("2026-08-14T10:00:00Z", periods=27, freq="h")
    stable = [104.0, 105.0, 103.0, 105.0, 104.0, 103.0]
    closes = [100.0, 102.0] + [stable[i % len(stable)] for i in range(24)] + [104.0]
    df = pd.DataFrame({"timestamp": times, "close": closes})

    decision = evaluate_sell_history(
        df,
        times[-1],
        104.0,
        movement_percent=1.1,
        c5_hours=24.0,
    )

    assert decision.c5_satisfied is True
    assert decision.should_sell is True
    assert decision.last_one_proc_time.isoformat() == "2026-08-14T11:00:00+00:00"


def test_c5_false_until_full_24h_exists_after_init_time():
    times = pd.date_range("2026-08-14T12:00:00Z", periods=24, freq="h")
    df = pd.DataFrame({"timestamp": times, "close": [104.0] * len(times)})

    decision = evaluate_sell_history(
        df,
        times[-1],
        104.0,
        movement_percent=1.1,
        c5_hours=24.0,
    )

    # 24 hourly samples span only 23 elapsed hours, so C5 must still be false.
    assert decision.c5_satisfied is False


def test_sell_history_ignores_measurements_before_init_time():
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-08-12T10:00:00Z",  # pre-buy peak: must be ignored
                    "2026-08-14T15:15:00Z",  # BuyTime / InitTime
                    "2026-08-14T15:30:00Z",
                    "2026-08-14T15:45:00Z",
                ],
                utc=True,
            ),
            "close": [120.0, 100.0, 100.5, 100.0],
        }
    )

    decision = evaluate_sell_history(
        df,
        "2026-08-14T15:45:00Z",
        100.0,
        movement_percent=1.1,
        c5_hours=24.0,
        init_time="2026-08-14T15:15:00Z",
    )

    assert decision.c4_satisfied is False
    assert decision.max_price == 100.5
    assert decision.max_time.isoformat() == "2026-08-14T15:30:00+00:00"
    assert decision.last_one_proc_time is None
    assert decision.should_sell is False


def test_c5_cannot_become_true_from_pre_init_time_coverage():
    times = pd.date_range("2026-08-13T10:00:00Z", periods=27, freq="h")
    df = pd.DataFrame({"timestamp": times, "close": [100.0] * len(times)})

    decision = evaluate_sell_history(
        df,
        times[-1],
        100.0,
        init_time="2026-08-14T09:00:00Z",
        c5_hours=24.0,
    )

    # Only two elapsed hours exist after InitTime even though the dataframe
    # contains more than 24 hours of older history.
    assert decision.c5_satisfied is False


def test_trading_window_respects_configured_weekdays_on_sunday():
    market = dict(MARKET, open_weekdays=["mon", "tue", "wed", "thu", "fri"])
    info = trading_window_info("2026-08-16T12:00:00Z", market, "sell")
    assert info.is_open is False
    assert info.remaining_time is None
    assert info.first_next_time.isoformat() == "2026-08-17T07:05:00+00:00"


def test_trading_window_crypto_can_be_configured_for_all_weekdays():
    market = {
        "timezone": "UTC",
        "open_weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "buy_start": "00:00",
        "buy_end": "23:59",
        "sell_start": "00:00",
        "sell_end": "23:59",
    }
    info = trading_window_info("2026-08-16T17:42:00Z", market, "sell")
    assert info.is_open is True


def test_trading_window_skips_configured_closed_date():
    market = dict(
        MARKET,
        open_weekdays=["mon", "tue", "wed", "thu", "fri"],
        closed_dates=["2026-08-17"],
    )
    info = trading_window_info("2026-08-16T12:00:00Z", market, "sell")
    assert info.is_open is False
    assert info.first_next_time.isoformat() == "2026-08-18T07:05:00+00:00"


def test_trading_window_enabled_false_uses_configured_next_time():
    market = dict(
        MARKET,
        enabled=False,
        next_sell_time="2026-08-18T07:05:00Z",
    )
    info = trading_window_info("2026-08-17T12:00:00Z", market, "sell")
    assert info.is_open is False
    assert info.remaining_time is None
    assert info.first_next_time.isoformat() == "2026-08-18T07:05:00+00:00"
