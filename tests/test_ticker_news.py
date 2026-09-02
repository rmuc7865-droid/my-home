from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.database import Base, Instrument, TickerNews
from server.ticker_news import (
    cleanup_old_ticker_news,
    article_is_relevant,
    build_summary,
    classify_news,
    headline_is_low_value,
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


def test_low_value_investment_commentary_is_rejected() -> None:
    rejected = (
        "Amazon Doesn't Pay a Dividend and Constantly Dilutes "
        "Shareholders. Here's Why I'd Still Buy and Hold It Forever.",
        "Does ASML's Dominant Position in EUV Signal Further Upside?",
        "Greg Abel Thinks Berkshire's Stock Is Cheap -- "
        "Is He Right? Here's the Math.",
        "Why Is Marvell Stock Falling, and is it a Generational "
        "Buying Opportunity?",
        "Netflix Is Down 46% From Its High. Is This a Once-in-a-Lifetime "
        "Buying Opportunity Before the Stock Goes Parabolic?",
        "Prediction: This Will Be Palantir's Stock Price in a Year "
        "(Hint: It Implies a Big Move)",
        "Why Oracle (ORCL) is Poised to Beat Earnings Estimates Again",
    )

    for title in rejected:
        assert headline_is_low_value(title) is True


def test_legal_solicitation_is_rejected() -> None:
    title = (
        "BlackRock Investor News: If You Have Suffered Losses in "
        "BlackRock, Inc. Mutual Funds, You Are Encouraged to Contact "
        "The Rosen Law Firm About Your Rights"
    )

    assert headline_is_low_value(title) is True


def test_concrete_catalyst_headlines_are_not_low_value() -> None:
    accepted = (
        "Dell Reports Record Results: AI Momentum Remains Robust",
        "LLY's Mounjaro Gets FDA Nod to Cut Cardiovascular Risk "
        "in Diabetes",
        "Skyworks Announces Extension of Expiration Date of Exchange "
        "Offers for Qorvo's Senior Notes due 2029 and 2031",
        "Why Take-Two Interactive Stock Is Sinking Today",
    )

    for title in accepted:
        assert headline_is_low_value(title) is False


def test_headline_category_takes_priority_over_description() -> None:
    assert (
        classify_news(
            "Company Raises Full-Year Guidance",
            "Quarterly earnings and revenue were also discussed.",
        )
        == "Guidance"
    )

    assert (
        classify_news(
            "Mounjaro Gets FDA Approval",
            "Revenue could increase following the decision.",
        )
        == "Regulatory"
    )


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


def test_production_news_quality_cases() -> None:
    cases = [
        (
            _instrument("ARM", "Arm Holdings plc"),
            {
                "tickers": ["ARM"],
                "title": "Why Arm Holdings Stock Fell on Tuesday",
                "description": (
                    "Arm Holdings stock fell after a regulatory filing "
                    "revealed CFO Jason Child sold 10,400 shares."
                ),
            },
            True,
        ),
        (
            _instrument("BLK", "BlackRock Inc."),
            {
                "tickers": ["BLK"],
                "title": (
                    "BlackRock (BLK) Declines More Than Market: "
                    "Some Information for Investors"
                ),
                "description": (
                    "BlackRock declined with the broader market. "
                    "Upcoming earnings estimates call for EPS growth."
                ),
            },
            False,
        ),
        (
            _instrument("CRWD", "CrowdStrike Holdings Inc."),
            {
                "tickers": ["DKS", "OKTA", "CRWD"],
                "title": (
                    "DKS, OKTA, and CRWD: "
                    "3 Trending Stocks Making Big Moves"
                ),
                "description": (
                    "Three stocks attracted investor attention."
                ),
            },
            False,
        ),
        (
            _instrument("NVDA", "NVIDIA Corporation"),
            {
                "tickers": ["NVDA"],
                "title": (
                    "ASUS Showcases ProArt PCs Powered by NVIDIA "
                    "RTX Spark at IFA 2026"
                ),
                "description": "ASUS announced new computers.",
            },
            False,
        ),
        (
            _instrument("ORCL", "Oracle Corporation"),
            {
                "tickers": ["ORCL"],
                "title": (
                    "Here's Why Oracle (ORCL) Fell More Than "
                    "Broader Market"
                ),
                "description": (
                    "Oracle declined 1.15%. The company is expected "
                    "to report EPS and revenue growth."
                ),
            },
            False,
        ),
        (
            _instrument("QBTS", "D-Wave Quantum Inc."),
            {
                "tickers": ["QBTS"],
                "title": (
                    "D-Wave Quantum's Massive Upside Meets a "
                    "High-Stakes Valuation Reality"
                ),
                "description": (
                    "The stock has a demanding valuation."
                ),
            },
            False,
        ),
        (
            _instrument("TSLA", "Tesla Inc."),
            {
                "tickers": ["TSLA"],
                "title": (
                    "Why Tesla Stock Stepped on the Gas "
                    "Monday Morning"
                ),
                "description": (
                    "Tesla stock surged after CEO Elon Musk posted "
                    "about SpaceX manufacturing gas turbine blades."
                ),
            },
            False,
        ),
        (
            _instrument(
                "TTWO",
                "Take-Two Interactive Software Inc.",
            ),
            {
                "tickers": ["TTWO"],
                "title": (
                    "Why Take-Two Interactive Stock Is "
                    "Sinking Today"
                ),
                "description": (
                    "Take-Two stock fell amid leaks of "
                    "Grand Theft Auto VI footage."
                ),
            },
            True,
        ),
    ]

    for instrument, article, expected in cases:
        actual = article_is_relevant(article, instrument)
        assert actual is expected, (
            f"{instrument.ticker}: "
            f"expected={expected}, actual={actual}"
        )


def test_ticker_news_retention() -> None:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import sessionmaker

    from server.database import Base, TickerNews

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    TestSession = sessionmaker(bind=engine)
    db = TestSession()

    now = datetime(
        2026,
        9,
        2,
        12,
        0,
        tzinfo=timezone.utc,
    )

    def add_news(
        article_id: str,
        age_days: int,
    ) -> None:
        db.add(
            TickerNews(
                article_id=article_id,
                ticker="AAPL",
                published_at=now - timedelta(days=age_days),
                title=f"Test article {article_id}",
                description="Retention test",
                publisher="Test",
                article_url=f"https://example.com/{article_id}",
                category="Other",
                summary="Other: retention test",
                collected_at=now,
            )
        )

    # 8 days old -> delete
    add_news("old-8", 8)

    # Exactly 7 days old -> keep because cleanup uses < cutoff
    add_news("boundary-7", 7)

    # 6 days old -> keep
    add_news("recent-6", 6)

    db.commit()

    try:
        deleted = cleanup_old_ticker_news(
            db,
            now=now,
            retention_days=7,
        )

        assert deleted == 1, deleted

        remaining = set(
            db.scalars(
                select(TickerNews.article_id)
            ).all()
        )

        assert remaining == {
            "boundary-7",
            "recent-6",
        }, remaining

        count = db.scalar(
            select(func.count()).select_from(TickerNews)
        )

        assert count == 2, count

    finally:
        db.close()
        engine.dispose()


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
        test_low_value_investment_commentary_is_rejected,
        test_legal_solicitation_is_rejected,
        test_concrete_catalyst_headlines_are_not_low_value,
        test_headline_category_takes_priority_over_description,
        test_same_article_can_be_stored_for_multiple_tickers,
        test_article_id_prevents_duplicate_storage,
        test_production_news_quality_cases,
        test_ticker_news_retention,
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
