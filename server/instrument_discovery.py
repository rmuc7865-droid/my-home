from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import Instrument, SimulationTrade
from tools.generate_provider_inputs import MASSIVE_STOCK_TICKERS

logger = logging.getLogger(__name__)

MAX_ACTIVE_INSTRUMENTS = 100
MAX_DAILY_GAINERS = 5
MIN_GAINER_VOLUME = 10_000

MASSIVE_GAINERS_PATH = "/v2/snapshot/locale/us/markets/stocks/gainers"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

ISIN_OVERRIDES_PATH = Path("config/isin_overrides.json")

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
CUSIP_RE = re.compile(
    r"CUSIP(?:\s+No\.?|\s+Number|\s*[:#])?\s*"
    r"([0-9A-Z]{6}[\s-]?[0-9A-Z]{2}[\s-]?[0-9])",
    re.I,
)

SEC_FORMS_PRIORITY = (
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
)

SEC_FORMS_SECONDARY = (
    "10-K",
    "10-Q",
    "8-K",
    "S-1",
    "S-3",
)

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "HomeMonitor ISIN resolver admin@example.com",
)


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _isin_valid(isin: str) -> bool:
    """Validate an ISIN using its ISO 6166/Luhn check digit."""
    isin = _norm(isin)
    if not ISIN_RE.fullmatch(isin):
        return False

    expanded = "".join(
        str(ord(ch) - 55) if ch.isalpha() else ch
        for ch in isin
    )

    total = 0
    parity = len(expanded) % 2

    for index, char in enumerate(expanded):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
        total += digit // 10 + digit % 10

    return total % 10 == 0


def _cusip_valid(cusip: str) -> bool:
    """Validate the standard 9-character CUSIP check digit."""
    cusip = _norm(cusip)
    if not re.fullmatch(r"[0-9A-Z*@#]{9}", cusip):
        return False

    # The ninth CUSIP character is always the numeric check digit.
    if not cusip[8].isdigit():
        return False

    total = 0

    for index, ch in enumerate(cusip[:8]):
        if ch.isdigit():
            value = int(ch)
        elif "A" <= ch <= "Z":
            value = ord(ch) - 55
        elif ch == "*":
            value = 36
        elif ch == "@":
            value = 37
        elif ch == "#":
            value = 38
        else:
            return False

        if index % 2 == 1:
            value *= 2

        total += value // 10 + value % 10

    check = (10 - total % 10) % 10
    return check == int(cusip[8])


def _us_isin_from_cusip(cusip: str) -> str | None:
    """Derive a US ISIN from a verified nine-character CUSIP."""
    cusip = _norm(cusip)

    if not _cusip_valid(cusip):
        return None

    base = "US" + cusip

    expanded = "".join(
        str(ord(ch) - 55) if ch.isalpha() else ch
        for ch in base
    )

    total = 0

    for index, char in enumerate(reversed(expanded)):
        value = int(char)

        if index % 2 == 0:
            value *= 2

        total += value // 10 + value % 10

    check = (10 - total % 10) % 10
    isin = base + str(check)

    return isin if _isin_valid(isin) else None


def _load_isin_overrides(
    path: Path = ISIN_OVERRIDES_PATH,
) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception(
            "Could not read ISIN overrides from %s",
            path,
        )
        return {}

    result: dict[str, str] = {}

    if isinstance(payload, dict):
        for ticker, isin in payload.items():
            ticker_n = _norm(ticker)
            isin_n = _norm(isin)

            if ticker_n and _isin_valid(isin_n):
                result[ticker_n] = isin_n

    return result


def seed_manual_instruments(
    db: Session,
    path: str = "config/instruments.json",
) -> int:
    file_path = Path(path)

    if not file_path.exists():
        logger.warning(
            "Instrument seed file not found: %s",
            file_path,
        )
        return 0

    payload = json.loads(
        file_path.read_text(encoding="utf-8")
    )

    if not isinstance(payload, list):
        raise ValueError(
            f"{file_path} must contain a JSON list"
        )

    added = 0
    now = datetime.now(timezone.utc)

    massive_tickers = set(
        MASSIVE_STOCK_TICKERS.values()
    )

    for row in payload:
        ticker = _norm(row.get("Ticker"))

        if not ticker:
            continue

        existing = db.scalar(
            select(Instrument).where(
                Instrument.ticker == ticker
            )
        )

        if existing:
            existing.source = "MANUAL"
            existing.active = True

            if row.get("ISIN"):
                existing.isin = _norm(row.get("ISIN"))

            if row.get("Name"):
                existing.name = str(
                    row.get("Name")
                ).strip()

            continue

        db.add(
            Instrument(
                ticker=ticker,
                name=str(
                    row.get("Name") or ""
                ).strip(),
                isin=_norm(row.get("ISIN")),
                asset_type=(
                    "crypto"
                    if ticker == "BTC"
                    else "stock"
                ),
                provider=(
                    "massive_crypto"
                    if ticker == "BTC"
                    else "massive"
                    if ticker in massive_tickers
                    else "international"
                ),
                source="MANUAL",
                active=True,
                discovered_at=now,
                updated_at=now,
            )
        )

        added += 1

    db.commit()
    return added


def _last_buy_by_ticker(
    db: Session,
) -> dict[str, datetime]:
    rows = db.execute(
        select(
            SimulationTrade.ticker,
            func.max(SimulationTrade.buy_time),
        )
        .where(
            SimulationTrade.buy_telegram_sent.is_(True)
        )
        .group_by(SimulationTrade.ticker)
    ).all()

    result: dict[str, datetime] = {}

    for ticker, last_buy in rows:
        if last_buy is None:
            continue

        if last_buy.tzinfo is None:
            last_buy = last_buy.replace(
                tzinfo=timezone.utc
            )

        result[_norm(ticker)] = last_buy

    return result


def replacement_candidates(
    db: Session,
) -> list[Instrument]:
    """AUTO_GAINER instruments ordered least relevant first."""
    open_tickers = set(
        db.scalars(
            select(SimulationTrade.ticker).where(
                SimulationTrade.sell_time.is_(None),
                SimulationTrade.buy_telegram_sent.is_(True),
            )
        ).all()
    )

    open_tickers = {
        _norm(ticker)
        for ticker in open_tickers
    }

    last_buy = _last_buy_by_ticker(db)

    rows = list(
        db.scalars(
            select(Instrument).where(
                Instrument.active.is_(True),
                Instrument.source == "AUTO_GAINER",
            )
        ).all()
    )

    eligible = [
        row
        for row in rows
        if _norm(row.ticker) not in open_tickers
    ]

    floor = datetime.min.replace(
        tzinfo=timezone.utc
    )

    eligible.sort(
        key=lambda row: (
            last_buy.get(
                _norm(row.ticker),
                floor,
            ),
            row.ticker,
        )
    )

    return eligible


async def _massive_ticker_details(
    client: httpx.AsyncClient,
    ticker: str,
    massive_key: str,
) -> dict:
    response = await client.get(
        f"https://api.polygon.io/v3/reference/tickers/{ticker}",
        params={"apiKey": massive_key},
    )

    response.raise_for_status()

    return response.json().get("results") or {}


async def _completed_session_volume(
    client: httpx.AsyncClient,
    *,
    ticker: str,
    massive_key: str,
) -> int | None:
    """Return volume from the latest completed daily aggregate."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=10)

    url = (
        "https://api.polygon.io/v2/aggs/ticker/"
        f"{ticker}/range/1/day/{start.isoformat()}/"
        f"{today.isoformat()}"
    )

    response = await client.get(
        url,
        params={
            "adjusted": "true",
            "sort": "desc",
            "limit": 10,
            "apiKey": massive_key,
        },
    )

    response.raise_for_status()

    rows = response.json().get("results") or []

    if not rows:
        return None

    today_utc = datetime.now(timezone.utc).date()

    for row in rows:
        timestamp = row.get("t")

        if timestamp is None:
            continue

        session_date = datetime.fromtimestamp(
            timestamp / 1000,
            tz=timezone.utc,
        ).date()

        # At the 03:00 discovery run, today's US session has
        # not started. This also avoids accidentally using a
        # partial same-day bar if discovery is triggered later.
        if session_date < today_utc:
            try:
                return int(row.get("v") or 0)
            except (TypeError, ValueError):
                return None

    return None


async def _sec_cusip_candidates(
    client: httpx.AsyncClient,
    *,
    cik: str,
) -> list[str]:
    """Extract checksum-valid CUSIPs from recent SEC filings."""
    digits = "".join(
        ch for ch in str(cik or "")
        if ch.isdigit()
    )

    if not digits:
        return []

    cik10 = digits.zfill(10)

    response = await client.get(
        SEC_SUBMISSIONS_URL.format(cik=cik10),
        headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        },
    )

    response.raise_for_status()

    payload = response.json()
    recent = (
        payload.get("filings", {})
        .get("recent", {})
    )

    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []

    filing_rows: list[tuple[int, str]] = []

    for index, form in enumerate(forms):
        if form in SEC_FORMS_PRIORITY:
            filing_rows.append((index, form))

    for index, form in enumerate(forms):
        if form in SEC_FORMS_SECONDARY:
            filing_rows.append((index, form))

    found: list[str] = []
    checked = 0

    for index, form in filing_rows:
        if checked >= 40:
            break

        if (
            index >= len(accessions)
            or index >= len(primary_docs)
        ):
            continue

        accession = accessions[index]
        primary = primary_docs[index]

        if not accession or not primary:
            continue

        accession_clean = accession.replace("-", "")
        cik_nozero = str(int(cik10))

        filing_url = (
            f"{SEC_ARCHIVES_BASE}/"
            f"{cik_nozero}/"
            f"{accession_clean}/"
            f"{primary}"
        )

        filing_response = await client.get(
            filing_url,
            headers={
                "User-Agent": SEC_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            },
        )

        checked += 1

        if filing_response.status_code != 200:
            continue

        for match in CUSIP_RE.finditer(
            filing_response.text
        ):
            cusip = re.sub(
                r"[^0-9A-Z*@#]",
                "",
                match.group(1).upper(),
            )

            if (
                _cusip_valid(cusip)
                and cusip not in found
            ):
                found.append(cusip)

        # Avoid unnecessarily hammering SEC.
        await asyncio.sleep(0.11)

    return found


async def _openfigi_matches_massive(
    client: httpx.AsyncClient,
    *,
    cusip: str,
    massive_details: dict,
) -> bool:
    massive_composite = _norm(
        massive_details.get("composite_figi")
    )
    massive_share = _norm(
        massive_details.get("share_class_figi")
    )

    if not massive_composite and not massive_share:
        return False

    response = await client.post(
        OPENFIGI_URL,
        json=[
            {
                "idType": "ID_CUSIP",
                "idValue": cusip,
            }
        ],
        headers={
            "Content-Type": "application/json",
        },
    )

    if response.status_code == 429:
        await asyncio.sleep(3.0)

        response = await client.post(
            OPENFIGI_URL,
            json=[
                {
                    "idType": "ID_CUSIP",
                    "idValue": cusip,
                }
            ],
            headers={
                "Content-Type": "application/json",
            },
        )

    response.raise_for_status()

    payload = response.json()

    if not payload:
        return False

    rows = payload[0].get("data") or []

    for row in rows:
        composite = _norm(
            row.get("compositeFIGI")
        )
        share = _norm(
            row.get("shareClassFIGI")
        )
        figi = _norm(
            row.get("figi")
        )

        if (
            massive_composite
            and massive_composite
            in {composite, figi}
        ):
            return True

        if (
            massive_share
            and massive_share
            in {share, figi}
        ):
            return True

    return False


async def resolve_isin(
    client: httpx.AsyncClient,
    *,
    ticker: str,
    massive_details: dict,
    overrides: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Resolve and independently verify an ISIN."""
    ticker = _norm(ticker)
    overrides = overrides or {}

    override = _norm(
        overrides.get(ticker)
    )

    if override and _isin_valid(override):
        return override, "OVERRIDE"

    cik = str(
        massive_details.get("cik") or ""
    )

    if not cik:
        return None, "NO_CIK"

    try:
        cusips = await _sec_cusip_candidates(
            client,
            cik=cik,
        )
    except Exception as exc:
        logger.warning(
            "SEC CUSIP lookup failed for %s: %s",
            ticker,
            exc,
        )
        return None, "SEC_ERROR"

    if not cusips:
        return None, "NO_CUSIP"

    verified: list[str] = []

    for cusip in cusips:
        try:
            matches = await _openfigi_matches_massive(
                client,
                cusip=cusip,
                massive_details=massive_details,
            )
        except Exception as exc:
            logger.warning(
                "OpenFIGI lookup failed for %s/%s: %s",
                ticker,
                cusip,
                exc,
            )
            continue

        if matches:
            verified.append(cusip)

        # Anonymous OpenFIGI rate limit protection.
        await asyncio.sleep(2.5)

    verified = list(dict.fromkeys(verified))

    if not verified:
        return None, "FIGI_MISMATCH"

    if len(verified) != 1:
        logger.warning(
            "Ambiguous verified CUSIPs for %s: %s",
            ticker,
            verified,
        )
        return None, "AMBIGUOUS_CUSIP"

    cusip = verified[0]

    # Conservative rule: only construct a US ISIN for a
    # standard US CUSIP. Foreign issuer CUSIPs commonly start
    # with a letter and are deliberately rejected here.
    if not cusip[0].isdigit():
        return None, "NON_US_CUSIP"

    isin = _us_isin_from_cusip(cusip)

    if not isin:
        return None, "INVALID_ISIN"

    return isin, "SEC_OPENFIGI"


async def discover_top_gainers(
    db: Session,
    *,
    dry_run: bool = False,
) -> dict:
    massive_key = (
        os.environ.get("POLYGON_API_KEY")
        or os.environ.get("MASSIVE_API_KEY")
    )

    if not massive_key:
        return {
            "status": "skipped",
            "reason": "missing_massive_api_key",
        }

    overrides = _load_isin_overrides()

    existing = {
        _norm(t)
        for t in db.scalars(
            select(Instrument.ticker).where(
                Instrument.active.is_(True)
            )
        ).all()
    }

    candidates: list[dict] = []

    skipped: dict[str, list[str]] = {
        "existing": [],
        "not_common_stock": [],
        "inactive": [],
        "wrong_market": [],
        "no_exchange": [],
        "low_volume": [],
        "volume_unavailable": [],
        "reference_error": [],
        "no_cik": [],
        "no_cusip": [],
        "sec_error": [],
        "figi_mismatch": [],
        "ambiguous_cusip": [],
        "non_us_cusip": [],
        "invalid_isin": [],
        "other_isin": [],
    }

    async with httpx.AsyncClient(
        timeout=45,
        follow_redirects=True,
    ) as client:
        response = await client.get(
            "https://api.polygon.io"
            + MASSIVE_GAINERS_PATH,
            params={"apiKey": massive_key},
        )

        response.raise_for_status()

        movers = (
            response.json().get("tickers")
            or []
        )

        for row in movers:
            ticker = _norm(
                row.get("ticker")
            )

            if not ticker:
                continue

            if ticker in existing:
                skipped["existing"].append(ticker)
                continue

            try:
                details = await _massive_ticker_details(
                    client,
                    ticker,
                    massive_key,
                )
            except Exception as exc:
                logger.warning(
                    "Ticker details failed for %s: %s",
                    ticker,
                    exc,
                )
                skipped["reference_error"].append(
                    ticker
                )
                continue

            if not details.get("active", True):
                skipped["inactive"].append(ticker)
                continue

            if _norm(
                details.get("market")
            ) not in {"", "STOCKS"}:
                skipped["wrong_market"].append(
                    ticker
                )
                continue

            if _norm(
                details.get("locale")
            ) not in {"", "US"}:
                skipped["wrong_market"].append(
                    ticker
                )
                continue

            if _norm(
                details.get("type")
            ) != "CS":
                skipped[
                    "not_common_stock"
                ].append(ticker)
                continue

            if not _norm(
                details.get("primary_exchange")
            ):
                skipped["no_exchange"].append(
                    ticker
                )
                continue

            try:
                volume = await _completed_session_volume(
                    client,
                    ticker=ticker,
                    massive_key=massive_key,
                )
            except Exception as exc:
                logger.warning(
                    "Volume lookup failed for %s: %s",
                    ticker,
                    exc,
                )
                volume = None

            if volume is None:
                skipped[
                    "volume_unavailable"
                ].append(ticker)
                continue

            if volume < MIN_GAINER_VOLUME:
                skipped["low_volume"].append(
                    ticker
                )
                continue

            isin, isin_source = await resolve_isin(
                client,
                ticker=ticker,
                massive_details=details,
                overrides=overrides,
            )

            if not isin:
                reason_map = {
                    "NO_CIK": "no_cik",
                    "NO_CUSIP": "no_cusip",
                    "SEC_ERROR": "sec_error",
                    "FIGI_MISMATCH": "figi_mismatch",
                    "AMBIGUOUS_CUSIP": "ambiguous_cusip",
                    "NON_US_CUSIP": "non_us_cusip",
                    "INVALID_ISIN": "invalid_isin",
                }

                bucket = reason_map.get(
                    isin_source,
                    "other_isin",
                )

                skipped[bucket].append(ticker)
                continue

            candidates.append(
                {
                    "ticker": ticker,
                    "isin": isin,
                    "isin_source": isin_source,
                    "name": str(
                        details.get("name")
                        or ticker
                    ).strip(),
                    "cik": _norm(
                        details.get("cik")
                    ),
                    "composite_figi": _norm(
                        details.get(
                            "composite_figi"
                        )
                    ),
                    "share_class_figi": _norm(
                        details.get(
                            "share_class_figi"
                        )
                    ),
                    "gainer_percent": row.get(
                        "todaysChangePerc"
                    ),
                    "gainer_volume": volume,
                    "previous_close": (
                        row.get("prevDay") or {}
                    ).get("c"),
                }
            )

            if (
                len(candidates)
                >= MAX_DAILY_GAINERS
            ):
                break

    active_count = (
        db.scalar(
            select(func.count())
            .select_from(Instrument)
            .where(
                Instrument.active.is_(True)
            )
        )
        or 0
    )

    preview = [
        {
            "ticker": row["ticker"],
            "isin": row["isin"],
            "isin_source": row["isin_source"],
            "volume": row["gainer_volume"],
            "gainer_percent": row["gainer_percent"],
        }
        for row in candidates
    ]

    if dry_run:
        return {
            "status": "dry_run",
            "would_add": preview,
            "skipped": skipped,
            "active_count": active_count,
        }

    now = datetime.now(timezone.utc)
    replacements = replacement_candidates(db)

    added: list[str] = []
    removed: list[str] = []

    for candidate in candidates:
        if active_count >= MAX_ACTIVE_INSTRUMENTS:
            if not replacements:
                break

            stale = replacements.pop(0)
            stale.active = False
            stale.deactivated_at = now
            stale.updated_at = now

            removed.append(stale.ticker)
            active_count -= 1

        instrument = db.scalar(
            select(Instrument).where(
                Instrument.ticker
                == candidate["ticker"]
            )
        )

        if instrument is None:
            instrument = Instrument(
                ticker=candidate["ticker"],
                discovered_at=now,
            )
            db.add(instrument)

        instrument.name = candidate["name"]
        instrument.isin = candidate["isin"]
        instrument.asset_type = "stock"
        instrument.provider = "massive"
        instrument.source = "AUTO_GAINER"
        instrument.active = True
        instrument.updated_at = now
        instrument.deactivated_at = None
        instrument.gainer_percent = (
            candidate["gainer_percent"]
        )
        instrument.gainer_volume = (
            candidate["gainer_volume"]
        )
        instrument.previous_close = (
            candidate["previous_close"]
        )
        instrument.last_gainer_date = now

        existing.add(candidate["ticker"])
        added.append(candidate["ticker"])
        active_count += 1

    db.commit()

    return {
        "status": "ok",
        "added": added,
        "deactivated": removed,
        "skipped": skipped,
        "active_count": active_count,
    }
