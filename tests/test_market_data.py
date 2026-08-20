from datetime import datetime, timedelta, timezone

import pytest

from shared.market_data import market_bar_record_id


def test_market_bar_record_id_is_stable_for_same_bar() -> None:
    timestamp = datetime(2026, 8, 20, 10, 15, tzinfo=timezone.utc)

    first = market_bar_record_id(
        provider="massive",
        system="crypto",
        ticker="X:BTCUSD",
        multiplier=15,
        timespan="minute",
        timestamp=timestamp,
    )
    second = market_bar_record_id(
        provider="MASSIVE",
        system="CRYPTO",
        ticker="x:btcusd",
        multiplier=15,
        timespan="MINUTE",
        timestamp=timestamp,
    )

    assert first == second


def test_market_bar_record_id_normalizes_timestamp_to_utc() -> None:
    utc_timestamp = datetime(2026, 8, 20, 10, 15, tzinfo=timezone.utc)
    cest = timezone(timedelta(hours=2))
    local_timestamp = datetime(2026, 8, 20, 12, 15, tzinfo=cest)

    assert market_bar_record_id(
        provider="massive",
        system="stocks",
        ticker="META",
        multiplier=15,
        timespan="minute",
        timestamp=utc_timestamp,
        variant="adjusted=True",
    ) == market_bar_record_id(
        provider="massive",
        system="stocks",
        ticker="META",
        multiplier=15,
        timespan="minute",
        timestamp=local_timestamp,
        variant="adjusted=True",
    )


def test_market_bar_record_id_changes_for_different_bar() -> None:
    first_timestamp = datetime(2026, 8, 20, 10, 15, tzinfo=timezone.utc)
    second_timestamp = datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)

    assert market_bar_record_id(
        provider="massive",
        system="crypto",
        ticker="X:BTCUSD",
        multiplier=15,
        timespan="minute",
        timestamp=first_timestamp,
    ) != market_bar_record_id(
        provider="massive",
        system="crypto",
        ticker="X:BTCUSD",
        multiplier=15,
        timespan="minute",
        timestamp=second_timestamp,
    )


def test_market_bar_record_id_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        market_bar_record_id(
            provider="massive",
            system="crypto",
            ticker="X:BTCUSD",
            multiplier=15,
            timespan="minute",
            timestamp=datetime(2026, 8, 20, 10, 15),
        )
