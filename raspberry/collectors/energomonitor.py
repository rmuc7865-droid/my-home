from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from shared.models import MeasurementRecord
from .base import Collector


DEFAULT_BASE_URL = "https://api.energomonitor.com/v1"


@dataclass(frozen=True)
class StreamSelection:
    output_name: str
    stream_id: str
    title: str | None = None
    unit: str | None = None
    medium: str | None = None
    multiplier: float = 1.0
    offset: float = 0.0
    decimals: int | None = None


def _current_config(stream: dict[str, Any]) -> dict[str, Any]:
    configs = stream.get("configs") or []
    if not configs:
        return {}
    for config in reversed(configs):
        if config.get("valid_to") is None:
            return config
    return configs[-1]


def _matches(stream: dict[str, Any], selector: dict[str, Any]) -> bool:
    config = _current_config(stream)
    checks = {
        "title": config.get("title"),
        "medium": config.get("medium"),
        "unit": config.get("unit"),
        "type": stream.get("type"),
        "channel": stream.get("channel"),
        "combined": stream.get("combined"),
        "index": stream.get("index"),
    }
    return all(checks[key] == value for key, value in selector.items() if key in checks)


def _transform(value: int | float, config: dict[str, Any]) -> int | float:
    transformed = float(value) * float(config.get("multiplier", 1.0)) + float(
        config.get("offset", 0.0)
    )
    decimals = config.get("decimals")
    if decimals is not None:
        transformed = round(transformed, int(decimals))
    if transformed.is_integer() and decimals is None:
        return int(transformed)
    return transformed


class EnergomonitorCollector(Collector):
    """Collect latest values from Energomonitor's cloud REST API.

    A configured output can reference a known ``stream_id`` directly or select a
    processed stream by fields such as title, medium, unit, channel, combined,
    and index. Direct IDs are preferred in production because titles can change.
    """

    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport
        self.base_url = config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self.feed_id = str(config["feed_id"])
        self.token = str(config["access_token"])
        self.timeout = float(config.get("timeout_seconds", 20))
        self.lookback_seconds = int(config.get("lookback_seconds", 3600))
        self.stale_after_seconds = int(config.get("stale_after_seconds", 1800))
        self.fail_on_stale = bool(config.get("fail_on_stale", True))
        self.stream_configs = config.get("streams") or {}
        if not self.stream_configs:
            raise ValueError("Energomonitor collector requires at least one entry in 'streams'")

    async def _request_json(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _resolve_streams(self, client: httpx.AsyncClient) -> list[StreamSelection]:
        definitions = self.stream_configs
        if all(definition.get("stream_id") for definition in definitions.values()):
            available: list[dict[str, Any]] = []
        else:
            available = await self._request_json(
                client,
                f"/feeds/{self.feed_id}/streams",
                params={"type": "processed"},
            )

        selections: list[StreamSelection] = []
        for output_name, definition in definitions.items():
            stream: dict[str, Any] | None = None
            stream_id = definition.get("stream_id")
            if stream_id:
                stream_id = str(stream_id)
            else:
                selector = {
                    key: definition[key]
                    for key in ("title", "medium", "unit", "type", "channel", "combined", "index")
                    if key in definition
                }
                matches = [candidate for candidate in available if _matches(candidate, selector)]
                if not matches:
                    raise ValueError(
                        f"No Energomonitor stream matched output {output_name!r} selector {selector!r}"
                    )
                if len(matches) > 1:
                    ids = [candidate.get("id") for candidate in matches]
                    raise ValueError(
                        f"Energomonitor selector for {output_name!r} is ambiguous; matched {ids}. "
                        "Add stream_id, channel, combined, or index."
                    )
                stream = matches[0]
                stream_id = str(stream["id"])

            current = _current_config(stream or {})
            selections.append(
                StreamSelection(
                    output_name=output_name,
                    stream_id=stream_id,
                    title=current.get("title") or definition.get("title"),
                    unit=current.get("unit") or definition.get("unit"),
                    medium=current.get("medium") or definition.get("medium"),
                    multiplier=float(definition.get("multiplier", 1.0)),
                    offset=float(definition.get("offset", 0.0)),
                    decimals=definition.get("decimals"),
                )
            )
        return selections

    async def _latest_point(
        self, client: httpx.AsyncClient, selection: StreamSelection, now: datetime
    ) -> tuple[StreamSelection, datetime, int | float]:
        time_to = int(now.timestamp())
        time_from = int((now - timedelta(seconds=self.lookback_seconds)).timestamp())
        data = await self._request_json(
            client,
            f"/feeds/{self.feed_id}/streams/{selection.stream_id}/data",
            params={"time_from": time_from, "time_to": time_to, "limit": 1},
        )
        if not data:
            raise ValueError(
                f"Energomonitor stream {selection.stream_id!r} returned no data in the last "
                f"{self.lookback_seconds} seconds"
            )
        timestamp_raw, value = data[-1]
        measured_at = datetime.fromtimestamp(int(timestamp_raw), tz=timezone.utc)
        age_seconds = (now - measured_at).total_seconds()
        if self.fail_on_stale and age_seconds > self.stale_after_seconds:
            raise ValueError(
                f"Energomonitor stream {selection.stream_id!r} is stale: "
                f"latest point is {int(age_seconds)} seconds old"
            )
        definition = self.stream_configs[selection.output_name]
        return selection, measured_at, _transform(value, definition)

    async def collect(self) -> MeasurementRecord:
        now = datetime.now(timezone.utc)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": self.config.get("user_agent", "home-monitor/1.0"),
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            selections = await self._resolve_streams(client)
            points = await asyncio.gather(
                *(self._latest_point(client, selection, now) for selection in selections)
            )

        measurements = {selection.output_name: value for selection, _, value in points}
        point_times = {
            selection.output_name: measured_at.isoformat()
            for selection, measured_at, _ in points
        }
        stream_ids = {
            selection.output_name: selection.stream_id for selection, _, _ in points
        }
        units = {
            selection.output_name: selection.unit
            for selection, _, _ in points
            if selection.unit is not None
        }
        timestamp = max(measured_at for _, measured_at, _ in points)
        metadata = {
            **self.config.get("metadata", {}),
            "feed_id": self.feed_id,
            "stream_ids": stream_ids,
            "units": units,
            "point_timestamps": point_times,
        }
        return MeasurementRecord(
            system=self.config.get("system", "energomonitor"),
            timestamp=timestamp,
            measurements=measurements,
            metadata=metadata,
        )
