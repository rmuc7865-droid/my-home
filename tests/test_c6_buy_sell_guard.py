import pandas as pd

from server.telegram_notifier import c6_holding_preexists_window


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
