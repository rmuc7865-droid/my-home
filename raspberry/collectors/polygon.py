from __future__ import annotations

import xml.etree.ElementTree as ET
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


class PolygonCollector(Collector):
    def __init__(self, config: dict):
        self.system = config["system"]
        self.ticker_file = Path(config["ticker_file"])
        self.registry_url = str(config.get("registry_url") or "").strip()
        self.registry_api_key = str(config.get("registry_api_key") or "").strip()
        self.multiplier = int(config.get("multiplier", 15))
        self.timespan = config.get("timespan", "minute")
        self.adjusted = bool(config.get("adjusted", True))
        self.timeout_seconds = int(config.get("timeout_seconds", 30))

        self.base_url = config.get(
            "base_url",
            "https://api.polygon.io",
        ).rstrip("/")
        self.liquidity_target_eur = float(
            config.get("liquidity_target_eur", 10000)
        )
        self.liquidity_participation_rate = float(
            config.get("liquidity_participation_rate", 0.10)
        )
        self.liquidity_window_seconds = int(
            config.get("liquidity_window_seconds", 1800)
        )

        # Prefer environment variable rather than putting the API key
        # into config.yaml.
        api_key_env = config.get("api_key_env", "POLYGON_API_KEY")
        self.api_key = os.environ.get(api_key_env)

        if not self.api_key:
            raise ValueError(
                f"Massive/Polygon API key is not set in environment variable "
                f"{api_key_env}"
            )

    def _load_tickers(self) -> list[str]:
        if not self.ticker_file.exists():
            raise FileNotFoundError(
                f"Ticker file does not exist: {self.ticker_file}"
            )

        with self.ticker_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        raw_tickers = data.get("tickers")

        if not isinstance(raw_tickers, list):
            raise ValueError(
                f"{self.ticker_file} must contain a 'tickers' JSON array"
            )

        tickers: list[str] = []
        seen: set[str] = set()

        for item in raw_tickers:
            if not isinstance(item, str):
                logger.warning("Ignoring non-string ticker: %r", item)
                continue

            ticker = item.strip().upper()

            if not ticker:
                continue

            if ticker in seen:
                continue

            seen.add(ticker)
            tickers.append(ticker)

        if not tickers:
            raise ValueError(
                f"No valid tickers found in {self.ticker_file}"
            )

        return tickers

    async def _load_runtime_tickers(self, client: httpx.AsyncClient) -> list[str]:
        local = self._load_tickers()
        if not self.registry_url:
            return local
        try:
            response = await client.get(
                self.registry_url,
                headers={"X-API-Key": self.registry_api_key} if self.registry_api_key else {},
            )
            response.raise_for_status()
            remote = response.json().get("tickers") or []
            merged: list[str] = []
            seen: set[str] = set()
            for item in [*local, *remote]:
                ticker = str(item or "").strip().upper()
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    merged.append(ticker)
            return merged
        except Exception:
            logger.exception("Could not load dynamic ticker registry; using local ticker file")
            return local

    async def collect(self) -> list[MeasurementRecord]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as registry_client:
            tickers = await self._load_runtime_tickers(registry_client)

        logger.info(
            "Loaded %d tickers from %s",
            len(tickers),
            self.ticker_file,
        )

        now = datetime.now(timezone.utc)

        # Query enough history to survive weekends, holidays, and
        # periods when no recent aggregate exists.
        from_date = (now - timedelta(days=7)).date().isoformat()
        to_date = now.date().isoformat()

        records: list[MeasurementRecord] = []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds
        ) as client:
            eur_usd = await self._get_eur_usd(client)

            logger.info(
                "ECB EUR/USD reference rate: %.4f",
                eur_usd,
            )
            for ticker in tickers:
                try:
                    record = await self._collect_ticker(
                        client,
                        ticker,
                        from_date,
                        to_date,
                        eur_usd,
                    )
                    #record = await self._collect_ticker(
                    #    client,
                    #    ticker,
                    #    from_date,
                    #    to_date,
                    #)

                    if record is not None:
                        records.append(record)

                except Exception:
                    logger.exception(
                        "Failed to collect ticker %s",
                        ticker,
                    )

        if not records:
            raise RuntimeError(
                "Massive/Polygon returned no usable ticker records"
            )

        logger.info(
            "Collected %d/%d ticker records",
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
        eur_usd: float,
    ) -> MeasurementRecord | None:
    #async def _collect_ticker(
    #    self,
    #    client: httpx.AsyncClient,
    #    ticker: str,
    #    from_date: str,
    #    to_date: str,
    #) -> MeasurementRecord | None:
        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}"
            f"/range/{self.multiplier}/{self.timespan}"
            f"/{from_date}/{to_date}"
        )

        response = await client.get(
            url,
            params={
                "adjusted": str(self.adjusted).lower(),
                "sort": "desc",
                "limit": 5000,
                "apiKey": self.api_key,
            },
        )

        response.raise_for_status()
        payload = response.json()

        if payload.get("status") not in ("OK", "DELAYED"):
            logger.warning(
                "Ticker %s returned status %r",
                ticker,
                payload.get("status"),
            )
            return None

        results = payload.get("results") or []

        if not results:
            logger.warning(
                "No aggregate data returned for %s",
                ticker,
            )
            return None

        bar = results[0]

        timestamp_ms = bar.get("t")
        if timestamp_ms is None:
            logger.warning(
                "Ticker %s result has no timestamp",
                ticker,
            )
            return None

        timestamp = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        )
        sell_time_seconds = None
        sell_time_over_seconds = None

        try:
            sell_time_seconds = await self._estimate_sell_time(
                client=client,
                ticker=ticker,
                anchor_time=timestamp,
                eur_usd=eur_usd,
            )

            if sell_time_seconds is None:
                sell_time_over_seconds = (
                    self.liquidity_window_seconds
                )

        except Exception:
            logger.exception(
                "Liquidity estimate failed for %s",
                ticker,
            )
        measurements = {}

        field_mapping = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
            "vwap": "vw",
            "transactions": "n",
        }

        for output_name, massive_name in field_mapping.items():
            if massive_name in bar:
                measurements[output_name] = bar[massive_name]

        measurements["sell_time_seconds"] = sell_time_seconds
        measurements["sell_time_over_seconds"] = sell_time_over_seconds

        return MeasurementRecord(
        #for output_name, massive_name in field_mapping.items():
        #    if massive_name in bar:
        #        measurements[output_name] = bar[massive_name]

        #return MeasurementRecord(
            record_id=market_bar_record_id(
                provider="massive",
                system=self.system,
                ticker=ticker,
                multiplier=self.multiplier,
                timespan=self.timespan,
                timestamp=timestamp,
                variant=f"adjusted={self.adjusted}",
            ),
            system=self.system,
            timestamp=timestamp,
            measurements=measurements,
            metadata={
                "ticker": ticker,
                "provider": "massive",
                "multiplier": self.multiplier,
                "timespan": self.timespan,
                "adjusted": self.adjusted,
                "liquidity_target_eur": self.liquidity_target_eur,
                "liquidity_participation_rate": self.liquidity_participation_rate,
                "liquidity_window_seconds": self.liquidity_window_seconds,
                "eur_usd": eur_usd,
            },
        )

    async def _get_eur_usd(
        self,
        client: httpx.AsyncClient,
    ) -> float:
        url = (
            "https://www.ecb.europa.eu/"
            "stats/eurofxref/eurofxref-daily.xml"
        )

        response = await client.get(url)
        response.raise_for_status()

        root = ET.fromstring(response.text)

        for element in root.iter():
            if (
                element.attrib.get("currency") == "USD"
                and "rate" in element.attrib
            ):
                return float(element.attrib["rate"])

        raise RuntimeError(
            "USD rate not found in ECB reference rates"
        )


    async def _estimate_sell_time(
        self,
        client: httpx.AsyncClient,
        ticker: str,
        anchor_time: datetime,
        eur_usd: float,
    ) -> float | None:

        target_usd = (
            self.liquidity_target_eur
            * eur_usd
        )

        required_market_notional = (
            target_usd
            / self.liquidity_participation_rate
        )

        to_ms = int(
            anchor_time.timestamp() * 1000
        )

        from_ms = int(
            (
                anchor_time
                - timedelta(
                    seconds=self.liquidity_window_seconds
                )
            ).timestamp()
            * 1000
        )

        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker}"
            f"/range/1/second/{from_ms}/{to_ms}"
        )

        response = await client.get(
            url,
            params={
                "adjusted": "true",
                "sort": "desc",
                "limit": 50000,
            },
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        response.raise_for_status()

        payload = response.json()
        results = payload.get("results") or []

        if not results:
            return None

        cumulative_market_notional = 0.0

        for second_bar in results:
            volume = second_bar.get("v")

            price = second_bar.get("vw")

            if price is None:
                price = second_bar.get("c")

            if volume is None or price is None:
                continue

            cumulative_market_notional += (
                float(volume)
                * float(price)
            )

            if (
                cumulative_market_notional
                >= required_market_notional
            ):
                bar_time = datetime.fromtimestamp(
                    second_bar["t"] / 1000,
                    tz=timezone.utc,
                )

                return max(
                    0.0,
                    (
                        anchor_time - bar_time
                    ).total_seconds(),
                )

        return None
