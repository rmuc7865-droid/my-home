from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base, Instrument, SimulationTrade
from server.instrument_discovery import replacement_candidates


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _instrument(ticker: str, source: str = "AUTO_GAINER") -> Instrument:
    now = datetime.now(timezone.utc)
    return Instrument(
        ticker=ticker, name=ticker, isin="US0000000000", asset_type="stock",
        provider="massive", source=source, active=True,
        discovered_at=now, updated_at=now,
    )


def _trade(ticker: str, days_ago: int, open_trade: bool = False) -> SimulationTrade:
    now = datetime.now(timezone.utc)
    buy_time = now - timedelta(days=days_ago)
    return SimulationTrade(
        ticker=ticker, ticker_name=ticker, buy_time=buy_time, buy_price=10.0,
        sell_time=None if open_trade else buy_time + timedelta(hours=1),
        buy_telegram_sent=True, created_at=now, updated_at=now,
    )


def test_replacement_prefers_never_bought_then_oldest_buy_and_protects_manual_open():
    db = _db()
    db.add_all([
        _instrument("NEVER"), _instrument("OLD"), _instrument("NEW"),
        _instrument("OPEN"), _instrument("MANUAL", "MANUAL"),
        _trade("OLD", 20), _trade("NEW", 2), _trade("OPEN", 30, open_trade=True),
    ])
    db.commit()
    assert [x.ticker for x in replacement_candidates(db)] == ["NEVER", "OLD", "NEW"]


def test_isin_checksum_validation():
    from server.instrument_discovery import _isin_valid
    assert _isin_valid("US0378331005")
    assert _isin_valid("US5949181045")
    assert not _isin_valid("US0378331004")
    assert not _isin_valid("NOT-AN-ISIN")


def test_replacement_candidates_exclude_manual_and_open_positions():
    db = _db()

    db.add_all([
        _instrument("MANUAL1", "MANUAL"),
        _instrument("AUTO1"),
        _instrument("OPEN1"),
        _trade("OPEN1", 30, open_trade=True),
    ])
    db.commit()

    candidates = [
        row.ticker
        for row in replacement_candidates(db)
    ]

    assert candidates == ["AUTO1"]
    assert "MANUAL1" not in candidates
    assert "OPEN1" not in candidates


def test_replacement_candidates_never_bought_before_oldest_buy():
    db = _db()

    db.add_all([
        _instrument("NEVER1"),
        _instrument("NEVER2"),
        _instrument("OLD"),
        _instrument("RECENT"),
        _trade("OLD", 30),
        _trade("RECENT", 2),
    ])
    db.commit()

    candidates = [
        row.ticker
        for row in replacement_candidates(db)
    ]

    assert candidates[:2] == ["NEVER1", "NEVER2"]
    assert candidates[2:] == ["OLD", "RECENT"]


def test_replacement_candidates_use_latest_buy_not_first_buy():
    db = _db()

    db.add_all([
        _instrument("MULTI"),
        _instrument("OTHER"),
        _trade("MULTI", 40),
        _trade("MULTI", 1),
        _trade("OTHER", 10),
    ])
    db.commit()

    candidates = [
        row.ticker
        for row in replacement_candidates(db)
    ]

    assert candidates == ["OTHER", "MULTI"]


def test_no_replacement_candidate_when_only_manual_or_open_auto():
    db = _db()

    db.add_all([
        _instrument("MANUAL1", "MANUAL"),
        _instrument("MANUAL2", "MANUAL"),
        _instrument("OPEN1"),
        _trade("OPEN1", 20, open_trade=True),
    ])
    db.commit()

    assert replacement_candidates(db) == []


def test_cap_never_exceeds_100_when_replacing_auto_gainers():
    import asyncio
    import os
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import func, select
    from server.instrument_discovery import discover_top_gainers

    db = _db()

    # 95 protected MANUAL instruments.
    for i in range(95):
        db.add(_instrument(f"M{i:03d}", "MANUAL"))

    # Five removable AUTO_GAINER instruments.
    for i in range(5):
        db.add(_instrument(f"A{i:03d}"))

    db.commit()

    movers = [
        {
            "ticker": f"NEW{i}",
            "todaysChangePerc": 20.0 - i,
            "prevDay": {"c": 10.0},
        }
        for i in range(5)
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tickers": movers}

    async def fake_get(*args, **kwargs):
        return FakeResponse()

    async def fake_details(client, ticker, massive_key):
        return {
            "ticker": ticker,
            "name": ticker,
            "active": True,
            "market": "stocks",
            "locale": "us",
            "type": "CS",
            "primary_exchange": "XNAS",
            "cik": "0000000001",
            "composite_figi": "BBG000000001",
            "share_class_figi": "BBG001000001",
        }

    async def fake_volume(client, *, ticker, massive_key):
        return 100_000

    async def fake_isin(
        client,
        *,
        ticker,
        massive_details,
        overrides,
    ):
        # Validity is irrelevant here because resolve_isin is mocked.
        return f"US{ticker:0<9}"[:11] + "0", "TEST"

    old_key = os.environ.get("POLYGON_API_KEY")
    os.environ["POLYGON_API_KEY"] = "test-key"

    try:
        with patch(
            "server.instrument_discovery.httpx.AsyncClient.get",
            new=fake_get,
        ), patch(
            "server.instrument_discovery._massive_ticker_details",
            new=fake_details,
        ), patch(
            "server.instrument_discovery._completed_session_volume",
            new=fake_volume,
        ), patch(
            "server.instrument_discovery.resolve_isin",
            new=fake_isin,
        ):
            result = asyncio.run(discover_top_gainers(db))
    finally:
        if old_key is None:
            os.environ.pop("POLYGON_API_KEY", None)
        else:
            os.environ["POLYGON_API_KEY"] = old_key

    active_count = db.scalar(
        select(func.count())
        .select_from(Instrument)
        .where(Instrument.active.is_(True))
    )

    active_manual = db.scalar(
        select(func.count())
        .select_from(Instrument)
        .where(
            Instrument.active.is_(True),
            Instrument.source == "MANUAL",
        )
    )

    assert result["active_count"] == 100
    assert active_count == 100
    assert active_manual == 95

    assert len(result["added"]) == 5
    assert set(result["added"]) == {
        "NEW0", "NEW1", "NEW2", "NEW3", "NEW4"
    }

    assert len(result["deactivated"]) == 5
    assert set(result["deactivated"]) == {
        "A000", "A001", "A002", "A003", "A004"
    }


def test_cap_refuses_new_ticker_when_no_safe_replacement_exists():
    import asyncio
    import os
    from unittest.mock import patch

    from sqlalchemy import func, select
    from server.instrument_discovery import discover_top_gainers

    db = _db()

    # All 100 instruments are MANUAL and therefore protected.
    for i in range(100):
        db.add(_instrument(f"M{i:03d}", "MANUAL"))

    db.commit()

    movers = [
        {
            "ticker": "NEWONE",
            "todaysChangePerc": 25.0,
            "prevDay": {"c": 10.0},
        }
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tickers": movers}

    async def fake_get(*args, **kwargs):
        return FakeResponse()

    async def fake_details(client, ticker, massive_key):
        return {
            "ticker": ticker,
            "name": ticker,
            "active": True,
            "market": "stocks",
            "locale": "us",
            "type": "CS",
            "primary_exchange": "XNAS",
            "cik": "0000000001",
            "composite_figi": "BBG000000001",
            "share_class_figi": "BBG001000001",
        }

    async def fake_volume(client, *, ticker, massive_key):
        return 100_000

    async def fake_isin(
        client,
        *,
        ticker,
        massive_details,
        overrides,
    ):
        return "US0000000000", "TEST"

    old_key = os.environ.get("POLYGON_API_KEY")
    os.environ["POLYGON_API_KEY"] = "test-key"

    try:
        with patch(
            "server.instrument_discovery.httpx.AsyncClient.get",
            new=fake_get,
        ), patch(
            "server.instrument_discovery._massive_ticker_details",
            new=fake_details,
        ), patch(
            "server.instrument_discovery._completed_session_volume",
            new=fake_volume,
        ), patch(
            "server.instrument_discovery.resolve_isin",
            new=fake_isin,
        ):
            result = asyncio.run(discover_top_gainers(db))
    finally:
        if old_key is None:
            os.environ.pop("POLYGON_API_KEY", None)
        else:
            os.environ["POLYGON_API_KEY"] = old_key

    active_count = db.scalar(
        select(func.count())
        .select_from(Instrument)
        .where(Instrument.active.is_(True))
    )

    assert active_count == 100
    assert result["active_count"] == 100
    assert result["added"] == []
    assert result["deactivated"] == []

    assert db.scalar(
        select(Instrument).where(
            Instrument.ticker == "NEWONE"
        )
    ) is None


def test_massive_gainers_http_error_does_not_log_api_key():
    import asyncio
    import logging
    import os
    from io import StringIO
    from unittest.mock import patch

    import httpx

    from server.instrument_discovery import discover_top_gainers

    db = _db()

    secret = "SUPER_SECRET_TEST_KEY"

    request = httpx.Request(
        "GET",
        "https://api.polygon.io/v2/snapshot/"
        "locale/us/markets/stocks/gainers"
        f"?apiKey={secret}",
    )
    response = httpx.Response(
        401,
        request=request,
    )

    async def fake_get(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "401 Unauthorized",
            request=request,
            response=response,
        )

    stream = StringIO()
    handler = logging.StreamHandler(stream)

    logger = logging.getLogger(
        "server.instrument_discovery"
    )
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    old_key = os.environ.get("POLYGON_API_KEY")
    os.environ["POLYGON_API_KEY"] = secret

    try:
        with patch(
            "server.instrument_discovery.httpx.AsyncClient.get",
            new=fake_get,
        ):
            result = asyncio.run(
                discover_top_gainers(db)
            )
    finally:
        if old_key is None:
            os.environ.pop("POLYGON_API_KEY", None)
        else:
            os.environ["POLYGON_API_KEY"] = old_key

        logger.removeHandler(handler)
        logger.setLevel(old_level)

    log_output = stream.getvalue()

    assert result == {
        "status": "skipped",
        "reason": "massive_http_error",
    }

    assert secret not in log_output
    assert "apiKey=" not in log_output
    assert "401" in log_output
    assert "Unauthorized" in log_output
