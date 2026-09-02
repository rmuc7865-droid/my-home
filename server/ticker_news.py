from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Instrument, TickerNews


logger = logging.getLogger(__name__)

MASSIVE_NEWS_URL = "https://api.polygon.io/v2/reference/news"

# We want current context, not old company history.
NEWS_LOOKBACK_DAYS = 3

# Ask Massive for several candidates because some will be rejected as weak.
NEWS_REQUEST_LIMIT = 10

# Keep the dashboard context intentionally compact.
MAX_NEWS_PER_TICKER = 2

# Avoid sending the complete ticker universe as an API burst.
NEWS_REQUEST_INTERVAL_SECONDS = 0.25
NEWS_429_RETRY_SECONDS = 2.0


CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Earnings",
        (
            r"\bearnings?\b",
            r"\bquarterly results?\b",
            r"\bfinancial results?\b",
            r"\brevenue\b",
            r"\beps\b",
        ),
    ),
    (
        "Guidance",
        (
            r"\bguidance\b",
            r"\boutlook\b",
            r"\bforecast\b",
            r"\braises? (?:its )?(?:full[- ]year )?guidance\b",
            r"\blowers? (?:its )?(?:full[- ]year )?guidance\b",
        ),
    ),
    (
        "Analyst",
        (
            r"\bupgrade[ds]?\b",
            r"\bdowngrade[ds]?\b",
            r"\bprice target\b",
            r"\banalyst\b",
            r"\brating\b",
        ),
    ),
    (
        "M&A",
        (
            r"\bacquisition\b",
            r"\bacquire[ds]?\b",
            r"\bmerger\b",
            r"\btakeover\b",
            r"\bbuyout\b",
        ),
    ),
    (
        "Regulatory",
        (
            r"\bfda\b",
            r"\bsec\b",
            r"\bregulator",
            r"\bregulatory\b",
            r"\bapproval\b",
            r"\bantitrust\b",
        ),
    ),
    (
        "Management",
        (
            r"\bceo\b",
            r"\bcfo\b",
            r"\bchief executive\b",
            r"\bchief financial officer\b",
            r"\bmanagement change\b",
            r"\bresign",
            r"\bappoint",
        ),
    ),
    (
        "Product",
        (
            r"\blaunch(?:es|ed)?\b",
            r"\bproduct\b",
            r"\bplatform\b",
            r"\bservice\b",
            r"\bpartnership\b",
            r"\bcollaboration\b",
        ),
    ),
)


def _normalize(value: object) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: object) -> datetime | None:
    text = _normalize(value)
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def classify_news(title: str, description: str = "") -> str:
    # Category describes the headline event only. Descriptions often contain
    # background references to earnings, revenue, guidance, etc. that are not
    # the actual event being reported.
    title_text = _normalize(title).lower()

    for category, patterns in CATEGORY_PATTERNS:
        if any(
            re.search(pattern, title_text, flags=re.IGNORECASE)
            for pattern in patterns
        ):
            return category

    return "Other"


def _company_terms(instrument: Instrument) -> set[str]:
    terms = {instrument.ticker.upper()}

    name = _normalize(instrument.name)
    if not name:
        return terms

    # Remove common corporate suffixes so "Dell Technologies Inc."
    # also produces the useful term "Dell Technologies".
    cleaned = re.sub(
        r"\b(incorporated|inc|corp|corporation|company|co|ltd|limited|plc|holdings?)\b\.?",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if cleaned:
        terms.add(cleaned.lower())

        words = [
            word
            for word in re.findall(r"[A-Za-z0-9]+", cleaned)
            if len(word) >= 4
        ]

        # A distinctive first company-name word is often enough for titles
        # such as "Dell Reports Record Results".
        if words:
            terms.add(words[0].lower())

    return terms


def headline_is_low_value(title: str) -> bool:
    title_lower = _normalize(title).lower()

    # These headlines are predominantly investment opinion, forecasts,
    # generic valuation commentary, or investor-solicitation material.
    # They are poor evidence for a concrete company catalyst.
    patterns = (
        r"\bbuying opportunity\b",
        r"\bonce-in-a-lifetime\b",
        r"\bgenerational buying opportunity\b",
        r"\bprediction:",
        r"\bstock price in a year\b",
        r"\bwould (?:still )?buy\b",
        r"\bi(?:'|’)d (?:still )?buy\b",
        r"\bbuy and hold\b",
        r"\bis .* stock (?:a )?buy\b",
        r"\bis .* cheap\b",
        r"\bhere(?:'|’)s the math\b",
        r"\bfurther upside\b",
        r"\bpoised to beat earnings estimates\b",
        r"\bencouraged to contact .* law firm\b",
        r"\bsuffered losses\b",
        r"\binvestor news:",
        r"\bvaluation reality\b",
        r"\bvaluation (?:risk|risks|concern|concerns)\b",
    )

    return any(
        re.search(pattern, title_lower, flags=re.IGNORECASE)
        for pattern in patterns
    )


def article_is_relevant(article: dict, instrument: Instrument) -> bool:
    ticker = instrument.ticker.upper()

    article_tickers = {
        _normalize(value).upper()
        for value in article.get("tickers") or []
        if _normalize(value)
    }

    title = _normalize(article.get("title"))
    title_lower = title.lower()

    if headline_is_low_value(title):
        return False

    terms = _company_terms(instrument)

    # Massive can associate broad ETF/market articles with many tickers.
    # For a compact ticker-specific explanation, require the company or
    # ticker to appear directly in the headline.
    headline_company_reference = any(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])",
            title_lower,
        )
        for term in terms
        if len(term) >= 3
    )

    if ticker not in article_tickers or not headline_company_reference:
        return False

    # The ticker/company should also be a headline subject, not merely a
    # company mentioned later in an article about somebody else. Accept a
    # company reference within roughly the first 45 headline characters.
    headline_prefix = title_lower[:45]

    subject_reference = any(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])",
            headline_prefix,
        )
        for term in terms
        if len(term) >= 3
    )

    if not subject_reference:
        return False

    # Reject common roundup/comparison headlines. They may mention the
    # ticker directly but are usually not a company-specific catalyst.
    roundup_patterns = (
        r"\bfeature highlights?\b",
        r"\binvestment ideas?\b",
        r"\branking\b",
        r"\bwhich .* stock\b",
        r"\bstocks? (?:to|with|that|for)\b",
        r"\bbetter .* stock\b",
        r"\bvs\.?\b",
        r"\bversus\b",
        r"\btop \d+\b",
        r"\b\d+ stocks?\b",
        r"\bmagnificent seven\b",
        r"\b\d+ trending stocks?\b",
        r"^[A-Z.]+(?:,\s*[A-Z.]+)+(?:,?\s+and\s+[A-Z.]+)?:",
    )

    if any(
        re.search(pattern, title_lower, flags=re.IGNORECASE)
        for pattern in roundup_patterns
    ):
        return False

    # A headline that names several Massive-associated tickers is usually
    # a market/sector roundup rather than a ticker-specific catalyst.
    if len(article_tickers) >= 4:
        return False

    description = _normalize(article.get("description")).lower()

    # Reject headlines whose primary subject is another company/product and
    # the target company is merely the technology supplier. Use all known
    # company terms, not only the ticker symbol (for example NVIDIA vs NVDA).
    powered_by_target = any(
        re.search(
            r"\bpowered by\s+" + re.escape(term.lower()) + r"\b",
            title_lower,
            flags=re.IGNORECASE,
        )
        for term in terms
        if len(term) >= 3
    )

    if powered_by_target:
        return False

    # Generic stock-price-movement stories are useful only when their
    # description identifies a concrete company-specific catalyst.
    #
    # Cover both:
    #   "Why Arm Holdings Stock Fell..."
    #   "BlackRock Declines More Than Market..."
    price_move_terms = (
        r"fell",
        r"falling",
        r"sank",
        r"sinking",
        r"rose",
        r"rising",
        r"jumped",
        r"surged",
        r"gained",
        r"dropped",
        r"declined",
        r"declines",
        r"stepped on the gas",
    )

    price_move_headline = any(
        re.search(
            rf"\b{term}\b",
            title_lower,
            flags=re.IGNORECASE,
        )
        for term in price_move_terms
    )

    if price_move_headline:
        catalyst_patterns = (
            r"\bfiling\b",
            r"\binsider\b",
            r"\bsold\s+[\d,]+\s+shares\b",
            r"\bleak(?:ed|s|ing)?\b",
            r"\bdelay(?:ed|s)?\b",
            r"\blaunch(?:ed|es)?\b",
            r"\bapproval\b",
            r"\bfda\b",
            r"\bcontract\b",
            r"\bdeal\b",
            r"\bacquisition\b",
            r"\bmerger\b",
            r"\bguidance\b",
            r"\breported\b.*\bresults\b",
            r"\bearnings report\b",
            r"\bceo\b",
            r"\bcfo\b",
        )

        if not any(
            re.search(
                pattern,
                description,
                flags=re.IGNORECASE,
            )
            for pattern in catalyst_patterns
        ):
            return False

        # A catalyst concerning another named business is not automatically
        # a catalyst for the target ticker. This catches cases such as a
        # Tesla article whose explanation is primarily a SpaceX development.
        if (
            ticker == "TSLA"
            and "spacex" in description
            and re.search(
                r"\bspacex\b.*\b(?:manufactur|develop|build|launch|post)",
                description,
                flags=re.IGNORECASE,
            )
        ):
            return False

    return True


def build_summary(title: str, description: str, category: str) -> str:
    source = _normalize(description) or _normalize(title)
    source = re.sub(r"\s+", " ", source).strip()

    if not source:
        return category

    # Compact dashboard text. We are deliberately not using an LLM here.
    if len(source) > 180:
        source = source[:177].rstrip() + "..."

    return f"{category}: {source}"


async def collect_ticker_news(
    db: Session,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    now = now or datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now = now.astimezone(timezone.utc)
    published_after = now - timedelta(days=NEWS_LOOKBACK_DAYS)

    api_key = _normalize(os.environ.get("POLYGON_API_KEY"))
    if not api_key:
        logger.error("Ticker news skipped: POLYGON_API_KEY is not configured")
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

    stored = 0
    duplicates = 0
    rejected = 0
    request_errors = 0
    ticker_results: dict[str, int] = {}

    async with httpx.AsyncClient(timeout=20.0) as client:
        for instrument in instruments:
            ticker = instrument.ticker.upper()

            try:
                await asyncio.sleep(NEWS_REQUEST_INTERVAL_SECONDS)

                request_params = {
                    "ticker": ticker,
                    "published_utc.gte": published_after.isoformat(),
                    "limit": NEWS_REQUEST_LIMIT,
                    "order": "desc",
                    "sort": "published_utc",
                    "apiKey": api_key,
                }

                response = await client.get(
                    MASSIVE_NEWS_URL,
                    params=request_params,
                )

                if response.status_code == 429:
                    logger.warning(
                        "Ticker news %s: HTTP 429; retrying once",
                        ticker,
                    )
                    await asyncio.sleep(NEWS_429_RETRY_SECONDS)
                    response = await client.get(
                        MASSIVE_NEWS_URL,
                        params=request_params,
                    )

                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Ticker news %s: HTTP %s %s",
                    ticker,
                    exc.response.status_code,
                    exc.response.reason_phrase,
                )
                request_errors += 1
                continue
            except httpx.RequestError as exc:
                logger.warning(
                    "Ticker news %s: %s",
                    ticker,
                    type(exc).__name__,
                )
                request_errors += 1
                continue

            articles = response.json().get("results") or []
            accepted_for_ticker = 0

            for article in articles:
                if accepted_for_ticker >= MAX_NEWS_PER_TICKER:
                    break

                article_id = _normalize(article.get("id"))
                title = _normalize(article.get("title"))
                published_at = _parse_timestamp(article.get("published_utc"))

                if not article_id or not title or published_at is None:
                    rejected += 1
                    continue

                if published_at < published_after:
                    rejected += 1
                    continue

                if not article_is_relevant(article, instrument):
                    rejected += 1
                    continue

                exists = db.scalar(
                    select(TickerNews.id).where(
                        TickerNews.ticker == ticker,
                        TickerNews.article_id == article_id,
                    )
                )

                if exists is not None:
                    duplicates += 1
                    accepted_for_ticker += 1
                    continue

                description = _normalize(article.get("description"))
                category = classify_news(title, description)

                publisher_data = article.get("publisher") or {}
                publisher = _normalize(
                    publisher_data.get("name")
                    if isinstance(publisher_data, dict)
                    else publisher_data
                )

                news = TickerNews(
                    article_id=article_id,
                    ticker=ticker,
                    published_at=published_at,
                    title=title,
                    description=description,
                    publisher=publisher,
                    article_url=_normalize(article.get("article_url")),
                    category=category,
                    summary=build_summary(
                        title,
                        description,
                        category,
                    ),
                    collected_at=now,
                )

                if not dry_run:
                    db.add(news)

                stored += 1
                accepted_for_ticker += 1

            ticker_results[ticker] = accepted_for_ticker

    if not dry_run:
        db.commit()

    return {
        "status": "ok",
        "tickers": len(instruments),
        "stored": stored,
        "duplicates": duplicates,
        "rejected": rejected,
        "request_errors": request_errors,
        "ticker_results": ticker_results,
        "dry_run": dry_run,
    }
