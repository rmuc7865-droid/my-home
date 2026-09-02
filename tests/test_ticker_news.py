from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.database import Base, Instrument, TickerNews
from server.ticker_news import (
    article_is_relevant,
    build_summary,
    classify_news,
)


def _instrument(ticker: str, name: str) -> Instrument:
    now = datetime.now(timezone.utc)

    return Instrument(
        ticker=ticker,
        name=name,
        isin="",
        asset_type="stock",
        provider="massive",
        source="MANUAL",
        active=True,
        discovered_at=now,
        updated_at=now,
    )


def test_news_classification() -> None:
    assert (
        classify_news(
            "Dell Reports Record Quarterly Results",
            "Revenue increased strongly.",
        )
        == "Earnings"
    )

    assert (
        classify_news(
            "Nvidia upgraded with higher price target",
            "",
        )
        == "Analyst"
    )

    assert (
        classify_news(
            "Company raises full-year guidance",
            "",
        )
        == "Guidance"
    )


def test_direct_company_headline_is_relevant() -> None:
    dell = _instrument(
        "DELL",
        "Dell Technologies Inc.",
    )

    article = {
        "tickers": ["DELL"],
        "title": (
            "Dell Reports Record Results: "
            "AI Momentum Remains Robust"
        ),
    }

    assert article_is_relevant(article, dell) is True


def test_etf_noise_is_rejected() -> None:
    apple = _instrument(
        "AAPL",
        "Apple Inc.",
    )

    article = {
        "tickers": ["AAPL"],
        "title": (
            "Should iShares S&P 500 Value ETF "
            "(IVE) Be on Your Investing Radar?"
        ),
        "description": (
            "The fund holds Apple among many "
            "other companies."
        ),
    }

    assert article_is_relevant(article, apple) is False


def test_roundup_is_rejected() -> None:
    dell = _instrument(
        "DELL",
        "Dell Technologies Inc.",
    )

    article = {
        "tickers": [
            "CRM",
            "CSCO",
            "DELL",
            "UBER",
            "HOOD",
        ],
        "title": (
            "Zacks Investment Ideas feature highlights: "
            "Salesforce, Cisco, Dell, Uber and Robinhood"
        ),
    }

    assert article_is_relevant(article, dell) is False


def test_incidental_company_mention_is_rejected() -> None:
    nvidia = _instrument(
        "NVDA",
        "NVIDIA Corporation",
    )

    article = {
        "tickers": ["NVDA"],
        "title": (
            "Linkhome Establishes AI Computing Subsidiary "
            "That Could Involve NVIDIA GB300 GPUs"
        ),
    }

    assert article_is_relevant(article, nvidia) is False


def test_investor_story_is_rejected() -> None:
    nvidia = _instrument(
        "NVDA",
        "NVIDIA Corporation",
    )

    article = {
        "tickers": ["NVDA"],
        "title": (
            "Billionaire Stanley Druckenmiller Still Isn't "
            "Buying Nvidia"
        ),
    }

    assert article_is_relevant(article, nvidia) is False


def test_direct_nvidia_headline_is_relevant() -> None:
    nvidia = _instrument(
        "NVDA",
        "NVIDIA Corporation",
    )

    article = {
        "tickers": ["NVDA"],
        "title": (
            "Nvidia Launches New AI Platform "
            "for Enterprise Customers"
        ),
    }

    assert article_is_relevant(article, nvidia) is True


def test_summary_is_compact() -> None:
    summary = build_summary(
        "Dell Reports Record Results",
        "",
        "Earnings",
    )

    assert summary == (
        "Earnings: Dell Reports Record Results"
    )

    long_description = "A" * 300

    summary = build_summary(
        "Title",
        long_description,
        "Other",
    )

    assert summary.startswith("Other: ")
    assert len(summary) <= 187
    assert summary.endswith("...")


def test_same_article_can_be_stored_for_multiple_tickers() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(bind=engine)

    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        for ticker in ("NVDA", "DELL"):
            db.add(
                TickerNews(
                    article_id="shared-article-1",
                    ticker=ticker,
                    published_at=now,
                    title="Shared industry article",
                    description="",
                    publisher="Test",
                    article_url="https://example.invalid/shared",
                    category="Other",
                    summary="Other: Shared industry article",
                    collected_at=now,
                )
            )

        db.commit()

        rows = list(
            db.scalars(
                select(TickerNews).where(
                    TickerNews.article_id
                    == "shared-article-1"
                )
            ).all()
        )

        assert len(rows) == 2
        assert {
            row.ticker
            for row in rows
        } == {"NVDA", "DELL"}


def test_article_id_prevents_duplicate_storage() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(bind=engine)

    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        first = TickerNews(
            article_id="test-article-1",
            ticker="DELL",
            published_at=now,
            title="Dell Reports Record Results",
            description="",
            publisher="Test",
            article_url="https://example.invalid/article",
            category="Earnings",
            summary="Earnings: Dell Reports Record Results",
            collected_at=now,
        )

        db.add(first)
        db.commit()

        existing = db.scalar(
            select(TickerNews.id).where(
                TickerNews.article_id
                == "test-article-1"
            )
        )

        assert existing is not None

        rows = list(
            db.scalars(
                select(TickerNews).where(
                    TickerNews.article_id
                    == "test-article-1"
                )
            ).all()
        )

        assert len(rows) == 1


if __name__ == "__main__":
    tests = [
        test_news_classification,
        test_direct_company_headline_is_relevant,
        test_etf_noise_is_rejected,
        test_roundup_is_rejected,
        test_incidental_company_mention_is_rejected,
        test_investor_story_is_rejected,
        test_direct_nvidia_headline_is_relevant,
        test_summary_is_compact,
        test_same_article_can_be_stored_for_multiple_tickers,
        test_article_id_prevents_duplicate_storage,
    ]

    passed = 0

    for test in tests:
        try:
            test()
            print(test.__name__, "PASS")
            passed += 1
        except Exception as exc:
            print(
                test.__name__,
                "FAIL:",
                type(exc).__name__,
                str(exc),
            )
            raise

    print()
    print(f"Passed: {passed}/{len(tests)}")
