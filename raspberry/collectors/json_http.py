from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from shared.models import MeasurementRecord
from .base import Collector


def get_path(document: Any, path: str) -> Any:
    current = document
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"Cannot traverse {part!r} in {path!r}")
    return current


class JsonHttpCollector(Collector):
    def __init__(self, config: dict):
        self.config = config

    async def collect(self) -> MeasurementRecord:
        timeout = self.config.get("timeout_seconds", 20)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                self.config["url"],
                headers=self.config.get("headers"),
                params=self.config.get("params"),
            )
            response.raise_for_status()
            payload = response.json()

        measurements = {
            output_name: get_path(payload, source_path)
            for output_name, source_path in self.config["mapping"].items()
        }
        timestamp_path = self.config.get("timestamp_path")
        timestamp = (
            datetime.fromisoformat(get_path(payload, timestamp_path).replace("Z", "+00:00"))
            if timestamp_path
            else datetime.now(timezone.utc)
        )
        return MeasurementRecord(
            system=self.config["system"],
            timestamp=timestamp,
            measurements=measurements,
            metadata=self.config.get("metadata", {}),
        )
