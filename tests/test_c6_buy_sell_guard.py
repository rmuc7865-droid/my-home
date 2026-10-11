import pandas as pd

from server.telegram_notifier import (
    c6_has_new_market_observation,
    c6_holding_preexists_window,
)


def test_c6_rejects_holding_bought_at_same_timestamp():
    action = pd.Timestamp("2026-09-24 19:45:00", tz="UTC")
    assert c6_holding_preexists_window(action, action, 15.0, 30.0) is False


def test_c6_rejects_holding_bought_after_liquidation_window_started():
    action = pd.Timestamp("2026-09-24 19:45:00", tz="UTC")
    buy = pd.Timestamp("2026-09-24 19:35:00", tz="UTC")
    assert c6_holding_preexists_window(buy, action, 15.0, 30.0) is False


def test_c6_allows_holding_that_predates_liquidation_window():
    action = pd.Timestamp("2026-09-24 19:45:00", tz="UTC")
    buy = pd.Timestamp("2026-09-24 19:00:00", tz="UTC")
    assert c6_holding_preexists_window(buy, action, 15.0, 30.0) is True


def test_c6_rejects_same_market_observation_as_buy():
    buy = pd.Timestamp("2026-10-05 19:00:00", tz="UTC")
    assert c6_has_new_market_observation(buy, buy) is False


def test_c6_accepts_newer_market_observation_than_buy():
    buy = pd.Timestamp("2026-10-05 19:00:00", tz="UTC")
    latest = pd.Timestamp("2026-10-05 19:15:00", tz="UTC")
    assert c6_has_new_market_observation(buy, latest) is True


def test_c6_action_time_guard_rejects_delayed_buy_inside_window():
    buy_action = pd.Timestamp("2026-10-05 19:31:27", tz="UTC")
    sell_action = pd.Timestamp("2026-10-05 19:32:37", tz="UTC")
    remaining = 27.3833333333

    assert (
        c6_holding_preexists_window(
            buy_action,
            sell_action,
            remaining,
            30.0,
        )
        is False
    )
