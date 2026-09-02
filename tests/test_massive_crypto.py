from datetime import date, datetime, timezone

import httpx
import pytest

from raspberry.collectors.massive_crypto import MassiveCryptoCollector


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeClient:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, params: dict):
        self.calls.append((url, params))
        date_text = url.rsplit("/", 2)[-1]
        return FakeResponse(self.payloads[date_text])


def make_collector(tmp_path, monkeypatch, **overrides) -> MassiveCryptoCollector:
    ticker_file = tmp_path / "crypto_tickers.json"
    ticker_file.write_text('{"tickers":["X:BTCUSD"]}', encoding="utf-8")
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    config = {
        "system": "crypto",
        "ticker_file": str(ticker_file),
        "multiplier": 15,
        "timespan": "minute",
        "backfill_days": 2,
    }
    config.update(overrides)
    return MassiveCryptoCollector(config)


def bar(ts: datetime, close: float) -> dict:
    return {
        "t": int(ts.timestamp() * 1000),
        "o": close - 1,
        "h": close + 1,
        "l": close - 2,
        "c": close,
        "v": 10,
    }


def test_backfill_dates_are_utc_calendar_days(tmp_path, monkeypatch) -> None:
    collector = make_collector(tmp_path, monkeypatch, backfill_days=3)
    now = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
    assert collector._backfill_dates(now) == [
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
    ]


@pytest.mark.asyncio
async def test_collect_ticker_returns_all_bars_across_days(tmp_path, monkeypatch) -> None:
    collector = make_collector(tmp_path, monkeypatch)
    first = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    second = datetime(2026, 8, 20, 23, 45, tzinfo=timezone.utc)
    third = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    client = FakeClient({
        "2026-08-20": {"status": "DELAYED", "results": [bar(first, 100), bar(second, 101)]},
        "2026-08-21": {"status": "DELAYED", "results": [bar(third, 102)]},
    })

    records = await collector._collect_ticker(
        client,
        "X:BTCUSD",
        [date(2026, 8, 20), date(2026, 8, 21)],
    )

    assert [record.timestamp for record in records] == [first, second, third]
    assert [record.measurements["close"] for record in records] == [100, 101, 102]
    assert len({record.record_id for record in records}) == 3
    assert all(call[1]["sort"] == "asc" for call in client.calls)
    assert all(call[1]["limit"] == 50000 for call in client.calls)


@pytest.mark.asyncio
async def test_collect_ticker_deduplicates_overlapping_api_bars(tmp_path, monkeypatch) -> None:
    collector = make_collector(tmp_path, monkeypatch)
    same = datetime(2026, 8, 20, 23, 45, tzinfo=timezone.utc)
    client = FakeClient({
        "2026-08-20": {"status": "DELAYED", "results": [bar(same, 101)]},
        "2026-08-21": {"status": "DELAYED", "results": [bar(same, 101)]},
    })

    records = await collector._collect_ticker(
        client,
        "X:BTCUSD",
        [date(2026, 8, 20), date(2026, 8, 21)],
    )

    assert len(records) == 1
    assert records[0].timestamp == same

@pytest.mark.asyncio
async def test_collect_ticker_keeps_prior_day_when_today_is_forbidden(tmp_path, monkeypatch) -> None:
    collector = make_collector(tmp_path, monkeypatch)
    first = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    second = datetime(2026, 8, 20, 23, 45, tzinfo=timezone.utc)

    class PartialAccessClient:
        async def get(self, url: str, params: dict):
            date_text = url.rsplit("/", 2)[-1]
            if date_text == "2026-08-21":
                request = httpx.Request("GET", url)
                response = httpx.Response(403, request=request)
                raise httpx.HTTPStatusError(
                    "Client error '403 Forbidden'",
                    request=request,
                    response=response,
                )
            return FakeResponse({
                "status": "DELAYED",
                "results": [bar(first, 100), bar(second, 101)],
            })

    records = await collector._collect_ticker(
        PartialAccessClient(),
        "X:BTCUSD",
        [date(2026, 8, 20), date(2026, 8, 21)],
    )

    assert [record.timestamp for record in records] == [first, second]
    assert [record.measurements["close"] for record in records] == [100, 101]
