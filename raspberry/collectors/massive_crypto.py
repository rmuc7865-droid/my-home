from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from shared.market_data import market_bar_record_id
from shared.models import MeasurementRecord
from .base import Collector

logger = logging.getLogger(__name__)


class MassiveCryptoCollector(Collector):
    def __init__(self, config: dict):
        self.system = config["system"]
        self.ticker_file = Path(config["ticker_file"])
        self.multiplier = int(config.get("multiplier", 15))
        self.timespan = config.get("timespan", "minute")
        self.timeout_seconds = int(config.get("timeout_seconds", 30))

        self.base_url = config.get(
            "base_url",
            "https://api.polygon.io",
        ).rstrip("/")

        api_key_env = config.get("api_key_env", "POLYGON_API_KEY")
        self.api_key = os.environ.get(api_key_env)

        if not self.api_key:
            raise ValueError(
                f"Massive API key is not set in environment variable "
                f"{api_key_env}"
            )

    def _load_tickers(self) -> list[str]:
        with self.ticker_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        tickers = data.get("tickers", [])

        if not isinstance(tickers, list):
            raise ValueError("'tickers' must be a list")

        return [
            str(ticker).strip().upper()
            for ticker in tickers
            if str(ticker).strip()
        ]

    async def collect(self) -> list[MeasurementRecord]:
        tickers = self._load_tickers()

        logger.info(
            "Loaded %d crypto tickers from %s",
            len(tickers),
            self.ticker_file,
        )

        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=2)).date().isoformat()
        to_date = now.date().isoformat()

        records = []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds
        ) as client:
            for ticker in tickers:
                try:
                    record = await self._collect_ticker(
                        client,
                        ticker,
                        from_date,
                        to_date,
                    )
                    if record:
                        records.append(record)

                except Exception:
                    logger.exception(
                        "Failed to collect crypto ticker %s",
                        ticker,
                    )

        if not records:
            raise RuntimeError("Massive crypto returned no usable records")

        logger.info(
            "Collected %d/%d crypto ticker records",
            len(records),
            len(tickers),
        )

        return records

    async def _collect_ticker(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        from_date: str,
        to_date: str,
    ) -> MeasurementRecord | None:

        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}"
            f"/range/{self.multiplier}/{self.timespan}"
            f"/{from_date}/{to_date}"
        )

        response = await client.get(
            url,
            params={
                "sort": "desc",
                "limit": 5000,
                "apiKey": self.api_key,
            },
        )

        response.raise_for_status()
        payload = response.json()

        results = payload.get("results") or []

        if not results:
            logger.warning("No crypto aggregate returned for %s", ticker)
            return None

        bar = results[0]

        timestamp = datetime.fromtimestamp(
            bar["t"] / 1000,
            tz=timezone.utc,
        )

        measurements = {}

        mapping = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
            "vwap": "vw",
            "transactions": "n",
        }

        for output_name, key in mapping.items():
            if key in bar:
                measurements[output_name] = bar[key]

        return MeasurementRecord(
            record_id=market_bar_record_id(
                provider="massive",
                system=self.system,
                ticker=ticker,
                multiplier=self.multiplier,
                timespan=self.timespan,
                timestamp=timestamp,
            ),
            system=self.system,
            timestamp=timestamp,
            measurements=measurements,
            metadata={
                "ticker": ticker,
                "asset": "BTC",
                "quote_currency": "USD",
                "provider": "massive",
                "asset_type": "crypto",
                "multiplier": self.multiplier,
                "timespan": self.timespan,
            },
        )
