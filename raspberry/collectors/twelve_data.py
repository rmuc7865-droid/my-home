from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shared.models import MeasurementRecord
from .base import Collector


logger = logging.getLogger(__name__)


class TwelveDataCollector(Collector):
    def __init__(self, config: dict):
        self.system = config["system"]
        self.ticker_file = Path(
            config["ticker_file"]
        )

        self.interval = str(
            config.get(
                "interval",
                "15min",
            )
        )

        self.timeout_seconds = int(
            config.get(
                "timeout_seconds",
                30,
            )
        )

        self.base_url = str(
            config.get(
                "base_url",
                "https://api.twelvedata.com",
            )
        ).rstrip("/")

        api_key_env = str(
            config.get(
                "api_key_env",
                "TWELVE_DATA_API_KEY",
            )
        )

        self.api_key = os.environ.get(
            api_key_env
        )

        if not self.api_key:
            raise ValueError(
                "Twelve Data API key is not set "
                f"in environment variable "
                f"{api_key_env}"
            )

    def _load_tickers(self) -> list[dict]:
        if not self.ticker_file.exists():
            raise FileNotFoundError(
                "Ticker file does not exist: "
                f"{self.ticker_file}"
            )

        with self.ticker_file.open(
            "r",
            encoding="utf-8",
        ) as handle:
            payload = json.load(handle)

        rows = payload.get("tickers")

        if not isinstance(rows, list):
            raise ValueError(
                f"{self.ticker_file} must contain "
                "a 'tickers' JSON array"
            )

        result: list[dict] = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            symbol = str(
                row.get("symbol") or ""
            ).strip()

            exchange = str(
                row.get("exchange") or ""
            ).strip()

            zero_ticker = str(
                row.get("zero_ticker")
                or symbol
            ).strip().upper()

            if not symbol or not exchange:
                logger.warning(
                    "Ignoring invalid Twelve Data "
                    "instrument: %r",
                    row,
                )
                continue

            result.append(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "zero_ticker": zero_ticker,
                    "isin": str(
                        row.get("isin") or ""
                    ).strip(),
                    "name": str(
                        row.get("name") or ""
                    ).strip(),
                }
            )

        if not result:
            raise ValueError(
                "No valid international "
                f"instruments in {self.ticker_file}"
            )

        return result

    async def collect(
        self,
    ) -> list[MeasurementRecord]:
        instruments = self._load_tickers()

        logger.info(
            "Loaded %d international tickers "
            "from %s",
            len(instruments),
            self.ticker_file,
        )

        records: list[MeasurementRecord] = []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
        ) as client:
            for instrument in instruments:
                try:
                    record = (
                        await self._collect_instrument(
                            client,
                            instrument,
                        )
                    )

                    if record is not None:
                        records.append(record)

                except Exception:
                    logger.exception(
                        "Failed to collect international "
                        "ticker %s (%s)",
                        instrument["symbol"],
                        instrument["exchange"],
                    )

        if not records:
            raise RuntimeError(
                "Twelve Data returned no usable "
                "international records"
            )

        logger.info(
            "Collected %d/%d international "
            "ticker records",
            len(records),
            len(instruments),
        )

        return records

    async def _collect_instrument(
        self,
        client: httpx.AsyncClient,
        instrument: dict,
    ) -> MeasurementRecord | None:
        symbol = instrument["symbol"]
        exchange = instrument["exchange"]

        response = await client.get(
            f"{self.base_url}/time_series",
            headers={
                "Authorization": (
                    f"apikey {self.api_key}"
                ),
            },
            params={
                "symbol": symbol,
                "exchange": exchange,
                "interval": self.interval,
                "outputsize": 1,
                "order": "desc",
                "timezone": "UTC",
            },
        )

        try:
            payload = response.json()
        except Exception:
            logger.warning(
                "Twelve Data non-JSON response "
                "for %s (%s): status=%s body=%r",
                symbol,
                exchange,
                response.status_code,
                response.text[:500],
            )
            return None

        if response.is_error:
            logger.warning(
                "Twelve Data error for %s (%s): "
                "HTTP %s code=%r message=%r",
                symbol,
                exchange,
                response.status_code,
                payload.get("code"),
                payload.get("message"),
            )
            return None

        if payload.get("status") == "error":
            logger.warning(
                "Twelve Data rejected %s (%s): %s",
                symbol,
                exchange,
                payload.get("message"),
            )
            return None

        values = payload.get("values") or []

        if not values:
            logger.warning(
                "No Twelve Data values for %s (%s)",
                symbol,
                exchange,
            )
            return None

        bar = values[0]

        datetime_text = str(
            bar.get("datetime") or ""
        ).strip()

        if not datetime_text:
            logger.warning(
                "No datetime for %s (%s)",
                symbol,
                exchange,
            )
            return None

        try:
            timestamp = datetime.fromisoformat(
                datetime_text
            )

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(
                    tzinfo=timezone.utc
                )
            else:
                timestamp = (
                    timestamp.astimezone(
                        timezone.utc
                    )
                )

        except ValueError:
            logger.warning(
                "Invalid Twelve Data datetime "
                "for %s: %r",
                symbol,
                datetime_text,
            )
            return None

        measurements = {}

        for field in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        ):
            value = bar.get(field)

            if value in (
                None,
                "",
            ):
                continue

            try:
                measurements[field] = float(
                    value
                )
            except (
                TypeError,
                ValueError,
            ):
                logger.warning(
                    "Invalid %s for %s: %r",
                    field,
                    symbol,
                    value,
                )

        if "close" not in measurements:
            logger.warning(
                "No usable close price for "
                "%s (%s)",
                symbol,
                exchange,
            )
            return None

        meta = payload.get("meta") or {}

        return MeasurementRecord(
            system=self.system,
            timestamp=timestamp,
            measurements=measurements,
            metadata={
                "ticker": (
                    instrument["zero_ticker"]
                ),
                "provider": "twelve_data",
                "asset_type": "stock",
                "symbol": symbol,
                "exchange": exchange,
                "isin": instrument["isin"],
                "name": instrument["name"],
                "quote_currency": (
                    meta.get("currency")
                ),
                "provider_exchange": (
                    meta.get("exchange")
                ),
                "provider_mic_code": (
                    meta.get("mic_code")
                ),
                "interval": self.interval,
            },
        )
