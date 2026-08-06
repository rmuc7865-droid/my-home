from __future__ import annotations

from .base import Collector
from .json_http import JsonHttpCollector
from .energomonitor import EnergomonitorCollector
from .mock import MockCollector


def build_collectors(configs: list[dict]) -> list[Collector]:
    collectors: list[Collector] = []
    for config in configs:
        if not config.get("enabled", True):
            continue
        collector_type = config["type"]
        if collector_type == "mock":
            collectors.append(MockCollector(config))
        elif collector_type == "json_http":
            collectors.append(JsonHttpCollector(config))
        elif collector_type == "energomonitor":
            collectors.append(EnergomonitorCollector(config))
        else:
            raise ValueError(f"Unsupported collector type: {collector_type}")
    return collectors
