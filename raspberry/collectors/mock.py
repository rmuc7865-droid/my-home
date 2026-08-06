from __future__ import annotations

from datetime import datetime, timezone

from shared.models import MeasurementRecord
from .base import Collector


class MockCollector(Collector):
    def __init__(self, config: dict):
        self.system = config["system"]
        self.values = config.get("values", {})
        self.metadata = config.get("metadata", {})

    async def collect(self) -> MeasurementRecord:
        return MeasurementRecord(
            system=self.system,
            timestamp=datetime.now(timezone.utc),
            measurements=self.values,
            metadata=self.metadata,
        )
