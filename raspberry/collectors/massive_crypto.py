from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
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
        self.backfill_days = max(1, int(config.get("backfill_days", 2)))
        self.limit = min(50000, max(1, int(config.get("limit", 50000))))

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

    def _backfill_dates(self, now: datetime) -> list[date]:
        """Return UTC calendar dates to query, oldest first."""
        today = now.astimezone(timezone.utc).date()
        first_day = today - timedelta(days=self.backfill_days - 1)
        return [first_day + timedelta(days=offset) for offset in range(self.backfill_days)]

    async def collect(self) -> list[MeasurementRecord]:
        tickers = self._load_tickers()

        logger.info(
            "Loaded %d crypto tickers from %s",
            len(tickers),
            self.ticker_file,
        )

        now = datetime.now(timezone.utc)
        query_dates = self._backfill_dates(now)
        records: list[MeasurementRecord] = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for ticker in tickers:
                try:
                    ticker_records = await self._collect_ticker(
                        client,
                        ticker,
                        query_dates,
                    )
                    records.extend(ticker_records)
                except Exception:
                    logger.exception(
                        "Failed to collect crypto ticker %s",
                        ticker,
                    )

        if not records:
            raise RuntimeError("Massive crypto returned no usable records")

        logger.info(
            "Collected %d crypto bars for %d/%d tickers across %d UTC day(s)",
            len(records),
            len({record.metadata.get("ticker") for record in records}),
            len(tickers),
            len(query_dates),
        )

        return records

    async def _collect_ticker(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        query_dates: list[date],
    ) -> list[MeasurementRecord]:
        # Key by deterministic record_id so overlapping/duplicated API responses
        # cannot create duplicate entries even within a single collection cycle.
        records_by_id: dict[str, MeasurementRecord] = {}

        for query_date in query_dates:
            date_text = query_date.isoformat()
            try:
                bars = await self._fetch_bars_for_day(client, ticker, date_text)
            except httpx.HTTPError as exc:
                # Access/availability can differ by UTC day for delayed plans.
                # Keep bars already fetched for other days instead of discarding
                # the entire ticker when one date (often today) is unavailable.
                logger.warning(
                    "Massive crypto %s %s: request failed; skipping day: %s",
                    ticker,
                    date_text,
                    exc,
                )
                continue

            logger.info(
                "Massive crypto %s %s: returned %d bar(s)",
                ticker,
                date_text,
                len(bars),
            )

            for bar in bars:
                record = self._record_from_bar(ticker, bar)
                records_by_id[str(record.record_id)] = record

        # Oldest first makes backfilled history arrive in chronological order.
        return sorted(records_by_id.values(), key=lambda record: record.timestamp)

    async def _fetch_bars_for_day(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        date_text: str,
    ) -> list[dict]:
        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}"
            f"/range/{self.multiplier}/{self.timespan}"
            f"/{date_text}/{date_text}"
        )

        response = await client.get(
            url,
            params={
                "sort": "asc",
                "limit": self.limit,
                "apiKey": self.api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()

        results = payload.get("results") or []
        if not results:
            logger.warning(
                "No crypto aggregate returned for %s on %s (status=%s)",
                ticker,
                date_text,
                payload.get("status"),
            )

        return results

    def _record_from_bar(self, ticker: str, bar: dict) -> MeasurementRecord:
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
                "asset": ticker.removeprefix("X:").removesuffix("USD"),
                "quote_currency": "USD",
                "provider": "massive",
                "asset_type": "crypto",
                "multiplier": self.multiplier,
                "timespan": self.timespan,
            },
        )
