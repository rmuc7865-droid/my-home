from __future__ import annotations

from datetime import datetime, timezone

import httpx
import asyncio

from raspberry.collectors.energomonitor import EnergomonitorCollector, _matches, _transform


def test_stream_selector_uses_current_config() -> None:
    stream = {
        "id": "power123",
        "type": "processed",
        "channel": 4,
        "combined": False,
        "configs": [
            {"title": "Old", "medium": "power", "unit": "W", "valid_to": "2024-01-01"},
            {"title": "Main power", "medium": "power", "unit": "W", "valid_to": None},
        ],
    }
    assert _matches(stream, {"title": "Main power", "unit": "W", "channel": 4})
    assert not _matches(stream, {"title": "Old"})


def test_transform() -> None:
    assert _transform(1234, {"multiplier": 0.001, "decimals": 3}) == 1.234
    assert _transform(10, {"offset": -2}) == 8


def test_collect_with_known_stream_ids() -> None:
    now = int(datetime.now(timezone.utc).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path.endswith("/power/data"):
            return httpx.Response(200, json=[[now - 10, 721.5]])
        if request.url.path.endswith("/energy/data"):
            return httpx.Response(200, json=[[now - 20, 12.3456]])
        return httpx.Response(404)

    collector = EnergomonitorCollector(
        {
            "type": "energomonitor",
            "system": "energomonitor",
            "access_token": "token",
            "feed_id": "200242",
            "stale_after_seconds": 60,
            "streams": {
                "power_w": {"stream_id": "power", "unit": "W"},
                "energy_kwh": {"stream_id": "energy", "unit": "kWh", "decimals": 3},
            },
        },
        transport=httpx.MockTransport(handler),
    )

    record = asyncio.run(collector.collect())
    assert record.system == "energomonitor"
    assert record.measurements == {"power_w": 721.5, "energy_kwh": 12.346}
    assert record.metadata["stream_ids"] == {"power_w": "power", "energy_kwh": "energy"}
