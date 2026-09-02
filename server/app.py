from __future__ import annotations

import json
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.models import TradeSignal, UploadBatch, UploadResult
from .database import Alert, Instrument, Measurement, SimulationTrade, SessionLocal, init_db
from .rules import evaluate_rule, load_rules
from .settings import settings
from .telegram import send_alert
from .instrument_discovery import discover_top_gainers, seed_manual_instruments
from .ticker_news import collect_ticker_news


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


MEASUREMENT_RETENTION_DAYS = 183

def cleanup_old_measurements() -> None:
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=MEASUREMENT_RETENTION_DAYS)
    )

    db = SessionLocal()

    try:
        old_measurement_ids = select(
            Measurement.id
        ).where(
            Measurement.timestamp < cutoff
        )

        deleted_alerts = db.execute(
            delete(Alert).where(
                Alert.measurement_id.in_(
                    old_measurement_ids
                )
            )
        )

        deleted_measurements = db.execute(
            delete(Measurement).where(
                Measurement.timestamp < cutoff
            )
        )

        db.commit()

        print(
            "Measurement retention cleanup: "
            f"cutoff={cutoff.isoformat()}, "
            f"measurements_deleted="
            f"{deleted_measurements.rowcount}, "
            f"alerts_deleted="
            f"{deleted_alerts.rowcount}"
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    cleanup_old_measurements()
    db = SessionLocal()
    try:
        seed_manual_instruments(db)
    finally:
        db.close()

    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

    async def discovery_job() -> None:
        job_db = SessionLocal()
        try:
            result = await discover_top_gainers(job_db)
            print(f"Automatic gainer discovery: {result}")
        except Exception as exc:
            print(f"Automatic gainer discovery failed: {exc}")
        finally:
            job_db.close()

    async def ticker_news_job() -> None:
        job_db = SessionLocal()
        try:
            result = await collect_ticker_news(job_db)
            print(f"Automatic ticker news collection: {result}")
        except Exception as exc:
            print(
                "Automatic ticker news collection failed: "
                f"{type(exc).__name__}"
            )
        finally:
            job_db.close()

    scheduler.add_job(
        discovery_job,
        "cron",
        hour=3,
        minute=0,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        ticker_news_job,
        "cron",
        hour=4,
        minute=0,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)

app = FastAPI(title="Home Monitor API", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/upload", response_model=UploadResult, dependencies=[Depends(require_api_key)])
async def upload(batch: UploadBatch, db: Session = Depends(get_db)) -> UploadResult:
    rules = load_rules(settings.rules_file)
    accepted = duplicates = alerts_created = 0

    for record in batch.records:
        measurement = Measurement(
            record_id=str(record.record_id),
            device=batch.device,
            system=record.system,
            timestamp=record.timestamp,
            measurements_json=json.dumps(record.measurements, separators=(",", ":")),
            metadata_json=json.dumps(record.metadata, separators=(",", ":")),
            received_at=datetime.now(timezone.utc),
        )
        db.add(measurement)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            duplicates += 1
            continue

        accepted += 1
        for rule in (rule for rule in rules if rule.system == record.system):
            satisfied, actual = evaluate_rule(rule, record.measurements)
            if satisfied:
                continue

            cooldown_start = datetime.now(timezone.utc) - timedelta(minutes=settings.alert_cooldown_minutes)
            existing = db.scalar(
                select(Alert)
                .where(Alert.rule_name == rule.name, Alert.system == rule.system, Alert.created_at >= cooldown_start)
                .limit(1)
            )
            if existing:
                continue

            alert_message = (
                f"⚠️ {rule.name}\n"
                f"System: {record.system}\n"
                f"Field: {rule.field}\n"
                f"Actual: {actual!r}\n"
                f"Expected: {rule.operator} {rule.value!r}\n"
                f"Time: {record.timestamp.isoformat()}\n\n"
                f"{rule.message}"
            )
            alert = Alert(
                measurement_id=measurement.id,
                rule_name=rule.name,
                system=record.system,
                field=rule.field,
                actual_value=repr(actual),
                expected_operator=rule.operator,
                expected_value=repr(rule.value),
                severity=rule.severity,
                message=alert_message,
                created_at=datetime.now(timezone.utc),
            )
            db.add(alert)
            db.flush()
            alert.telegram_sent = await send_alert(alert_message)
            alerts_created += 1

        db.commit()

    return UploadResult(accepted=accepted, duplicates=duplicates, alerts_created=alerts_created)


@app.get("/api/v1/measurements", dependencies=[Depends(require_api_key)])
def measurements(
    system: str | None = None,
    limit: int = Query(default=500, ge=1, le=50000),
    compact: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(Measurement).order_by(desc(Measurement.timestamp)).limit(limit)
    if system:
        statement = statement.where(Measurement.system == system)

    rows = db.scalars(statement).all()

    if not compact:
        return [
            {
                "id": row.id,
                "record_id": row.record_id,
                "device": row.device,
                "system": row.system,
                "timestamp": row.timestamp,
                "measurements": json.loads(row.measurements_json),
                "metadata": json.loads(row.metadata_json),
                "received_at": row.received_at,
            }
            for row in rows
        ]

    result = []

    for row in rows:
        measurements_payload = json.loads(row.measurements_json)
        metadata = json.loads(row.metadata_json)

        ticker = metadata.get("ticker")
        if not ticker:
            ticker = row.system

        asset_type = metadata.get("asset_type")
        if not asset_type:
            if row.system == "crypto":
                asset_type = "crypto"
            elif row.system == "polygon":
                asset_type = "stock"
            else:
                asset_type = "other"

        result.append(
            {
                "id": row.id,
                "device": row.device,
                "system": row.system,
                "ticker": ticker,
                "asset_type": asset_type,
                "timestamp": row.timestamp,
                "received_at": row.received_at,
                "eur_usd": metadata.get("eur_usd"),
                **measurements_payload,
            }
        )

    return result


@app.get("/api/v1/alerts", dependencies=[Depends(require_api_key)])
def alerts(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(limit)).all()
    return [
        {
            "id": row.id,
            "rule_name": row.rule_name,
            "system": row.system,
            "field": row.field,
            "actual_value": row.actual_value,
            "severity": row.severity,
            "message": row.message,
            "created_at": row.created_at,
            "acknowledged": row.acknowledged,
            "telegram_sent": row.telegram_sent,
        }
        for row in rows
    ]


@app.post("/api/v1/alerts/{alert_id}/acknowledge", dependencies=[Depends(require_api_key)])
def acknowledge(alert_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    db.commit()
    return {"acknowledged": True}

@app.post("/api/v1/simulation/signals", dependencies=[Depends(require_api_key)])
def record_simulation_signal(
    signal: TradeSignal = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """Record a BUY/SELL signal after its Telegram send attempt.

    Only Telegram messages reported as successfully sent are included in simulation
    results. SELL closes the oldest open BUY for the same ticker.
    """
    if not signal.telegram_sent:
        return {"recorded": False, "reason": "telegram_not_sent"}

    now = datetime.now(timezone.utc)
    ticker = signal.ticker.upper().strip()
    if signal.side == "BUY":
        existing_open = db.scalar(
            select(SimulationTrade)
            .where(
                SimulationTrade.ticker == ticker,
                SimulationTrade.sell_time.is_(None),
                SimulationTrade.buy_telegram_sent.is_(True),
            )
            .order_by(SimulationTrade.buy_time.asc())
            .limit(1)
        )
        if existing_open:
            return {
                "recorded": False,
                "reason": "already_open",
                "trade_id": existing_open.id,
                "ticker": ticker,
            }

        trade = SimulationTrade(
            ticker=ticker,
            ticker_name=signal.ticker_name.strip(),
            buy_time=signal.timestamp,
            buy_price=signal.price,
            buy_price_eur=signal.price_eur,
            closeb_gt0_count=signal.closeb_gt0_count,
            closeb_gt2_count=signal.closeb_gt2_count,
            buy_telegram_sent=True,
            created_at=now,
            updated_at=now,
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return {"recorded": True, "trade_id": trade.id, "status": "OPEN"}

    trade = db.scalar(
        select(SimulationTrade)
        .where(
            SimulationTrade.ticker == ticker,
            SimulationTrade.sell_time.is_(None),
            SimulationTrade.buy_telegram_sent.is_(True),
        )
        .order_by(SimulationTrade.buy_time.asc())
        .limit(1)
    )
    if not trade:
        raise HTTPException(status_code=409, detail=f"No open BUY found for {ticker}")
    buy_time = trade.buy_time
    if buy_time.tzinfo is None:
        buy_time = buy_time.replace(tzinfo=timezone.utc)
    if signal.timestamp < buy_time:
        raise HTTPException(status_code=422, detail="SELL timestamp cannot be before BUY timestamp")

    trade.sell_time = signal.timestamp
    trade.sell_price = signal.price
    trade.sell_price_eur = signal.price_eur
    trade.sell_reason = signal.sell_reason

    if (
        trade.buy_price_eur is None
        and signal.buy_price_eur is not None
    ):
        trade.buy_price_eur = (
            signal.buy_price_eur
        )

    trade.relative_difference = (
        (
            signal.price / trade.buy_price
        ) - 1
    ) * 100

    if signal.absolute_difference_eur is not None:
        trade.absolute_difference = (
            signal.absolute_difference_eur
        )
    else:
        trade.absolute_difference = (
            signal.price - trade.buy_price
        )

    trade.sell_telegram_sent = True
    trade.updated_at = now
    if signal.ticker_name.strip() and not trade.ticker_name:
        trade.ticker_name = signal.ticker_name.strip()
    db.commit()
    db.refresh(trade)
    return {
        "recorded": True,
        "trade_id": trade.id,
        "status": "CLOSED",
        "relative_difference": trade.relative_difference,
        "absolute_difference": trade.absolute_difference,
    }


@app.get("/api/v1/simulation/open-tickers", dependencies=[Depends(require_api_key)])
def simulation_open_tickers(db: Session = Depends(get_db)) -> dict[str, list[str]]:
    """Return tickers that already have a Telegram-sent BUY without a SELL.

    BUY candidate generation should exclude these tickers before composing the
    next Telegram BUY message. A ticker becomes eligible again after its SELL
    signal closes the open trade.
    """
    tickers = list(
        db.scalars(
            select(SimulationTrade.ticker)
            .where(
                SimulationTrade.sell_time.is_(None),
                SimulationTrade.buy_telegram_sent.is_(True),
            )
            .distinct()
            .order_by(SimulationTrade.ticker.asc())
        ).all()
    )
    return {"tickers": tickers}

@app.post(
    "/api/v1/simulation/reset-open",
    dependencies=[Depends(require_api_key)],
)
def reset_open_simulation_trades(
    db: Session = Depends(get_db),
) -> dict:
    """Administratively close all currently OPEN simulator positions.

    This is used to synchronize the simulator after the user has manually
    cleared positions in the external broker.

    It is intentionally NOT a normal simulated SELL:
    - no SELL price is recorded;
    - no simulated P/L is calculated;
    - no Telegram SELL is marked as sent;
    - SellReason is MANUAL_RESET.
    """
    now = datetime.now(timezone.utc)

    trades = list(
        db.scalars(
            select(SimulationTrade)
            .where(
                SimulationTrade.sell_time.is_(None),
                SimulationTrade.buy_telegram_sent.is_(True),
            )
            .order_by(
                SimulationTrade.buy_time.asc(),
                SimulationTrade.id.asc(),
            )
        ).all()
    )

    tickers = []

    for trade in trades:
        trade.sell_time = now
        trade.sell_price = None
        trade.sell_price_eur = None
        trade.sell_reason = "MANUAL_RESET"
        trade.relative_difference = None
        trade.absolute_difference = None
        trade.sell_telegram_sent = False
        trade.updated_at = now

        tickers.append(trade.ticker)

    db.commit()

    return {
        "reset": True,
        "closed_count": len(trades),
        "tickers": tickers,
        "reason": "MANUAL_RESET",
        "timestamp": now,
    }


@app.get("/api/v1/simulation", dependencies=[Depends(require_api_key)])
def simulation(
    days: int = Query(default=365, ge=0, le=3660),
    include_open: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = (
        select(SimulationTrade)
        .where(
            SimulationTrade.buy_telegram_sent.is_(True),
        )
        .order_by(desc(SimulationTrade.buy_time))
    )

    #
    # days=0 means all available simulation history.
    #
    if days > 0:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=days)
        )

        statement = statement.where(
            SimulationTrade.buy_time
            >= cutoff
        )    
    if not include_open:
        statement = statement.where(
            SimulationTrade.sell_time.is_not(None),
        )
    rows = db.scalars(statement).all()
    return [
        {
            "id": row.id,
            "Ticker": row.ticker,
            "TickerName": row.ticker_name,
            "BuyTime": row.buy_time,
            "BuyPrice": row.buy_price,
            "BuyPriceEUR": row.buy_price_eur,
            "CloseB>0": row.closeb_gt0_count,
            "CloseB>2": row.closeb_gt2_count,
            "SellTime": row.sell_time,
            "SellPrice": row.sell_price,
            "SellPriceEUR": row.sell_price_eur,
            "RelativeDifference": row.relative_difference,
            "AbsoluteDifference": row.absolute_difference,
            "SellReason": row.sell_reason,
            "Status": (
                "CLOSED"
                if row.sell_time is not None
                else "OPEN"
            ),
        }
        for row in rows
    ]


@app.get("/api/v1/instruments", dependencies=[Depends(require_api_key)])
def instruments(active: bool = Query(default=True), db: Session = Depends(get_db)) -> list[dict]:
    statement = select(Instrument).order_by(Instrument.ticker.asc())
    if active:
        statement = statement.where(Instrument.active.is_(True))
    rows = db.scalars(statement).all()
    return [
        {
            "Ticker": row.ticker, "Name": row.name, "ISIN": row.isin,
            "AssetType": row.asset_type, "Provider": row.provider,
            "Source": row.source, "Active": row.active,
            "DiscoveredAt": row.discovered_at, "GainerPercent": row.gainer_percent,
            "GainerVolume": row.gainer_volume, "PreviousClose": row.previous_close,
        } for row in rows
    ]


@app.get("/api/v1/instruments/massive-tickers", dependencies=[Depends(require_api_key)])
def massive_tickers(db: Session = Depends(get_db)) -> dict[str, list[str]]:
    tickers = list(db.scalars(
        select(Instrument.ticker).where(
            Instrument.active.is_(True),
            Instrument.provider == "massive",
            Instrument.asset_type == "stock",
        ).order_by(Instrument.ticker.asc())
    ).all())
    return {"tickers": tickers}


@app.post("/api/v1/instruments/discover", dependencies=[Depends(require_api_key)])
async def run_instrument_discovery(db: Session = Depends(get_db)) -> dict:
    return await discover_top_gainers(db)


@app.post("/api/v1/instruments/news", dependencies=[Depends(require_api_key)])
async def run_ticker_news_collection(
    db: Session = Depends(get_db),
) -> dict:
    return await collect_ticker_news(db)
