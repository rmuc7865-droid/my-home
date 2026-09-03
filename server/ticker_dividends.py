from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Instrument, TickerDividend


logger = logging.getLogger(__name__)

MASSIVE_DIVIDENDS_URL = (
    "https://api.polygon.io/stocks/v1/dividends"
)
ECB_DAILY_FX_URL = (
    "https://www.ecb.europa.eu/"
    "stats/eurofxref/eurofxref-daily.xml"
)

REQUEST_INTERVAL_SECONDS = 0.25
HTTP_429_RETRY_SECONDS = 2.0


def _normalize(value) -> str:
    return str(value or "").strip()


def _parse_date(value) -> date | None:
    text = _normalize(value)

    if not text:
        return None

    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _positive_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result < 0:
        return None

    return result


def _positive_int(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None

    return result if result > 0 else None


def _estimate_next_dividend_date(
    last_date: date | None,
    frequency: int | None,
    today: date,
) -> date | None:
    if last_date is None or not frequency:
        return None

    # Massive expresses frequency as distributions per year:
    # 4 = quarterly, 2 = semiannual, 1 = annual, etc.
    interval_days = max(
        1,
        round(365 / frequency),
    )

    # Do not keep projecting dividends indefinitely for a
    # company that appears to have stopped paying them.
    # Once more than two expected payment intervals have
    # elapsed, there is not enough evidence for a useful
    # expected next-dividend date.
    days_since_last = (
        today - last_date
    ).days

    if days_since_last > (2 * interval_days):
        return None

    expected = (
        last_date
        + timedelta(days=interval_days)
    )

    while expected <= today:
        expected += timedelta(
            days=interval_days
        )

    return expected


async def _get_eur_usd(
    client: httpx.AsyncClient,
) -> float:
    response = await client.get(
        ECB_DAILY_FX_URL
    )
    response.raise_for_status()

    root = ET.fromstring(response.text)

    for element in root.iter():
        if (
            element.attrib.get("currency") == "USD"
            and "rate" in element.attrib
        ):
            rate = float(element.attrib["rate"])

            if rate > 0:
                return rate

    raise RuntimeError(
        "USD rate not found in ECB reference rates"
    )


def _amount_eur(
    amount: float | None,
    currency: str,
    eur_usd: float | None,
) -> float | None:
    if amount is None:
        return None

    currency = currency.upper()

    if currency == "EUR":
        return amount

    if (
        currency == "USD"
        and eur_usd is not None
        and eur_usd > 0
    ):
        return amount / eur_usd

    return None


async def _request_dividends(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    ticker: str,
    params: dict,
) -> list[dict]:
    request_params = {
        "ticker": ticker,
        "limit": 10,
        "apiKey": api_key,
        **params,
    }

    response = await client.get(
        MASSIVE_DIVIDENDS_URL,
        params=request_params,
    )

    if response.status_code == 429:
        logger.warning(
            "Ticker dividends %s: HTTP 429; "
            "retrying once",
            ticker,
        )
        await asyncio.sleep(
            HTTP_429_RETRY_SECONDS
        )
        response = await client.get(
            MASSIVE_DIVIDENDS_URL,
            params=request_params,
        )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        return []

    rows = payload.get("results") or []

    return [
        row
        for row in rows
        if isinstance(row, dict)
    ]


async def collect_ticker_dividends(
    db: Session,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now = now.astimezone(timezone.utc)
    today = now.date()

    api_key = _normalize(
        os.environ.get("POLYGON_API_KEY")
        or os.environ.get("MASSIVE_API_KEY")
    )

    if not api_key:
        logger.error(
            "Ticker dividends skipped: "
            "Massive API key is not configured"
        )
        return {
            "status": "skipped",
            "reason": "missing_api_key",
        }

    instruments = list(
        db.scalars(
            select(Instrument)
            .where(
                Instrument.active.is_(True),
                Instrument.asset_type == "stock",
                Instrument.provider == "massive",
            )
            .order_by(Instrument.ticker.asc())
        ).all()
    )

    updated = 0
    without_dividend = 0
    request_errors = 0
    fx_error = False

    async with httpx.AsyncClient(
        timeout=20.0,
    ) as client:
        try:
            eur_usd = await _get_eur_usd(
                client
            )
        except (
            httpx.HTTPError,
            ET.ParseError,
            RuntimeError,
            ValueError,
        ) as exc:
            logger.warning(
                "Ticker dividends: ECB EUR/USD "
                "unavailable: %s",
                type(exc).__name__,
            )
            eur_usd = None
            fx_error = True

        for instrument in instruments:
            ticker = instrument.ticker.upper()

            try:
                await asyncio.sleep(
                    REQUEST_INTERVAL_SECONDS
                )

                past_rows = (
                    await _request_dividends(
                        client,
                        api_key=api_key,
                        ticker=ticker,
                        params={
                            "ex_dividend_date.lte":
                                today.isoformat(),
                            "sort":
                                "ex_dividend_date.desc",
                        },
                    )
                )

                await asyncio.sleep(
                    REQUEST_INTERVAL_SECONDS
                )

                future_rows = (
                    await _request_dividends(
                        client,
                        api_key=api_key,
                        ticker=ticker,
                        params={
                            "ex_dividend_date.gt":
                                today.isoformat(),
                            "sort":
                                "ex_dividend_date.asc",
                        },
                    )
                )

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Ticker dividends %s: "
                    "HTTP %s %s",
                    ticker,
                    exc.response.status_code,
                    exc.response.reason_phrase,
                )
                request_errors += 1
                continue

            except httpx.RequestError as exc:
                logger.warning(
                    "Ticker dividends %s: %s",
                    ticker,
                    type(exc).__name__,
                )
                request_errors += 1
                continue

            last = (
                past_rows[0]
                if past_rows
                else None
            )

            future = (
                future_rows[0]
                if future_rows
                else None
            )

            if last is None and future is None:
                without_dividend += 1

                existing = db.scalar(
                    select(TickerDividend)
                    .where(
                        TickerDividend.ticker
                        == ticker
                    )
                )

                if existing is not None:
                    db.delete(existing)

                continue

            last_date = (
                _parse_date(
                    last.get("ex_dividend_date")
                )
                if last
                else None
            )

            last_amount = (
                _positive_float(
                    last.get("cash_amount")
                )
                if last
                else None
            )

            last_currency = (
                _normalize(
                    last.get("currency")
                ).upper()
                if last
                else ""
            )

            frequency = (
                _positive_int(
                    last.get("frequency")
                )
                if last
                else None
            )

            next_date = (
                _parse_date(
                    future.get(
                        "ex_dividend_date"
                    )
                )
                if future
                else None
            )

            next_amount = (
                _positive_float(
                    future.get("cash_amount")
                )
                if future
                else None
            )

            next_currency = (
                _normalize(
                    future.get("currency")
                ).upper()
                if future
                else ""
            )

            next_is_estimated = False

            if next_date is None:
                next_date = (
                    _estimate_next_dividend_date(
                        last_date,
                        frequency,
                        today,
                    )
                )

                next_is_estimated = (
                    next_date is not None
                )

            # Prefer the announced next amount. An estimated
            # date does not imply an estimated cash amount.
            if (
                future is not None
                and next_amount is not None
            ):
                selected_amount = next_amount
                selected_currency = (
                    next_currency
                )
            else:
                selected_amount = last_amount
                selected_currency = (
                    last_currency
                )

            dividend_eur = _amount_eur(
                selected_amount,
                selected_currency,
                eur_usd,
            )

            row = db.scalar(
                select(TickerDividend)
                .where(
                    TickerDividend.ticker
                    == ticker
                )
            )

            if row is None:
                row = TickerDividend(
                    ticker=ticker,
                )
                db.add(row)

            row.last_dividend_date = last_date
            row.next_dividend_date = next_date
            row.next_is_estimated = (
                next_is_estimated
            )
            row.dividend_amount = (
                selected_amount
            )
            row.dividend_currency = (
                selected_currency
            )
            row.dividend_eur = dividend_eur
            row.frequency = frequency
            row.collected_at = now

            updated += 1

        db.commit()

    return {
        "status": "ok",
        "instruments": len(instruments),
        "updated": updated,
        "without_dividend": without_dividend,
        "request_errors": request_errors,
        "fx_error": fx_error,
    }
