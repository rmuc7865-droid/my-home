from __future__ import annotations

from abc import ABC, abstractmethod

from shared.models import MeasurementRecord


class Collector(ABC):
    @abstractmethod
    async def collect(self) -> MeasurementRecord:
        """Fetch, normalize and return one measurement record."""
