from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.models import TradeSignal, UploadBatch, UploadResult
from .database import Alert, Measurement, SimulationTrade, SessionLocal, init_db
from .rules import evaluate_rule, load_rules
from .settings import settings
from .telegram import send_alert


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


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
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(Measurement).order_by(desc(Measurement.timestamp)).limit(limit)
    if system:
        statement = statement.where(Measurement.system == system)
    rows = db.scalars(statement).all()
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
            SimulationTrade.sell_telegram_sent.is_(True),
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
                and row.sell_telegram_sent
                else "OPEN"
            ),
        }
        for row in rows
    ]
