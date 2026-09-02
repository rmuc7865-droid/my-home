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
