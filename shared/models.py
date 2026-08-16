from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MeasurementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: UUID = Field(default_factory=uuid4)
    system: str = Field(min_length=1, max_length=64)
    timestamp: datetime
    measurements: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class UploadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: str = Field(min_length=1, max_length=128)
    records: list[MeasurementRecord] = Field(min_length=1, max_length=1000)


class UploadResult(BaseModel):
    accepted: int
    duplicates: int
    alerts_created: int

class TradeSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: str
    ticker: str = Field(min_length=1, max_length=32)
    ticker_name: str = Field(default="", max_length=256)
    timestamp: datetime
    price: float = Field(gt=0)

    price_eur: float | None = Field(
        default=None,
        gt=0,
    )

    buy_price_eur: float | None = Field(
        default=None,
        gt=0,
    )

    closeb_gt0_count: int | None = Field(
        default=None,
        ge=0,
    )

    closeb_gt2_count: int | None = Field(
        default=None,
        ge=0,
    )

    sell_reason: str | None = Field(
        default=None,
        max_length=64,
    )

    absolute_difference_eur: float | None = None
    telegram_sent: bool = True

    @field_validator("side")
    @classmethod
    def side_must_be_buy_or_sell(cls, value: str) -> str:
        normalized = value.upper().strip()
        if normalized not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def signal_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)
