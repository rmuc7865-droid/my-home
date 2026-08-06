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
