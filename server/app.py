from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.models import UploadBatch, UploadResult
from .database import Alert, Measurement, SessionLocal, init_db
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
    limit: int = Query(default=500, ge=1, le=5000),
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
