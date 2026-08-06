from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml

from .energomonitor import DEFAULT_BASE_URL, _current_config


def _find_config(document: dict[str, Any]) -> dict[str, Any]:
    for collector in document.get("collectors", []):
        if collector.get("type") == "energomonitor":
            return collector
    raise ValueError("No collector with type: energomonitor exists in the configuration")


async def discover(config_path: str) -> None:
    with Path(config_path).open("r", encoding="utf-8") as handle:
        config = _find_config(yaml.safe_load(handle))

    base_url = config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=float(config.get("timeout_seconds", 20)),
    ) as client:
        response = await client.get(f"/feeds/{config['feed_id']}/streams")
        response.raise_for_status()
        streams = response.json()

    print("stream_id\ttype\tchannel\tcombined\tindex\tmedium\tunit\ttitle")
    for stream in streams:
        current = _current_config(stream)
        print(
            "\t".join(
                str(value if value is not None else "")
                for value in (
                    stream.get("id"),
                    stream.get("type"),
                    stream.get("channel"),
                    stream.get("combined"),
                    stream.get("index"),
                    current.get("medium"),
                    current.get("unit"),
                    current.get("title"),
                )
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="List Energomonitor streams and IDs")
    parser.add_argument("--config", default="raspberry/config.yaml")
    args = parser.parse_args()
    asyncio.run(discover(args.config))


if __name__ == "__main__":
    main()
