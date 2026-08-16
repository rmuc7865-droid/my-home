from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yaml


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("telegram_notifier")


API_URL = os.getenv(
    "MONITOR_API_URL",
    "http://api:8000",
).rstrip("/")

API_KEY = os.getenv(
    "MONITOR_API_KEY",
    "",
)

#TELEGRAM_BOT_TOKEN = os.getenv(
#    "TELEGRAM_BOT_TOKEN",
#    "",
#)

CONFIG_PATH = Path(
    os.getenv(
        "TELEGRAM_NOTIFICATIONS_CONFIG",
        "/app/server/telegram_notifications.yaml",
    )
)

STATE_PATH = Path(
    os.getenv(
        "TELEGRAM_NOTIFICATIONS_STATE",
        "/app/data/telegram_notifier_state.json",
    )
)

ZERO_JSON_PATH = Path(
    os.getenv(
        "ZERO_JSON_PATH",
        "/app/config/zero.json",
    )
)

INSTRUMENTS_JSON_PATH = Path(
    os.getenv(
        "INSTRUMENTS_JSON_PATH",
        "/app/config/instruments.json",
    )
)

WATCHLIST_MEMBERSHIP_PATH = Path(
    os.getenv(
        "WATCHLIST_MEMBERSHIP_PATH",
        "/app/config/watchlist_membership.json",
    )
)

def load_watchlist_membership() -> dict[str, set[str]]:
    if not WATCHLIST_MEMBERSHIP_PATH.exists():
        logger.warning(
            "Watchlist membership file not found: %s",
            WATCHLIST_MEMBERSHIP_PATH,
        )
        return {}

    try:
        payload = json.loads(
            WATCHLIST_MEMBERSHIP_PATH.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        logger.exception(
            "Cannot read watchlist membership from %s",
            WATCHLIST_MEMBERSHIP_PATH,
        )
        return {}

    result = {}

    for ticker, users in payload.items():
        result[str(ticker).upper()] = {
            str(user).lower()
            for user in users
        }

    return result

def load_config() -> dict:
    with CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return yaml.safe_load(handle) or {}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "buy_condition_active": False,
            "last_alert_id": 0,
        }

    try:
        with STATE_PATH.open(
            "r",
            encoding="utf-8",
        ) as handle:
            state = json.load(handle)

        return {
            "buy_condition_active": bool(
                state.get(
                    "buy_condition_active",
                    False,
                )
            ),
            "last_alert_id": int(
                state.get(
                    "last_alert_id",
                    0,
                )
            ),
        }

    except Exception:
        logger.exception(
            "Cannot read notifier state; using defaults"
        )

        return {
            "buy_condition_active": False,
            "last_alert_id": 0,
        }


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = STATE_PATH.with_suffix(".tmp")

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )

    temporary.replace(STATE_PATH)

def load_ticker_market_regions() -> dict[str, str]:
    if not INSTRUMENTS_JSON_PATH.exists():
        logger.warning(
            "Instrument file not found: %s",
            INSTRUMENTS_JSON_PATH,
        )
        return {}

    try:
        rows = json.loads(
            INSTRUMENTS_JSON_PATH.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        logger.exception(
            "Cannot read instrument file: %s",
            INSTRUMENTS_JSON_PATH,
        )
        return {}

    result = {}

    for row in rows:
        ticker = str(
            row.get("Ticker") or ""
        ).strip().upper()

        region = str(
            row.get("MarketRegion") or ""
        ).strip().upper()

        if ticker and region:
            result[ticker] = region

    return result

def market_region_for_row(
    ticker: str,
    row,
    market_regions: dict[str, str],
) -> str | None:
    #
    # Explicit configuration always wins.
    #
    explicit = market_regions.get(
        ticker.upper()
    )

    if explicit:
        return explicit

    asset_type = str(
        row.get("asset_type") or ""
    ).lower()

    system = str(
        row.get("system") or ""
    ).lower()

    if asset_type == "crypto":
        return "CRYPTO"

    #
    # Current Polygon stock collector represents
    # US-market symbols.
    #
    if system == "polygon":
        return "US"

    return None

def load_ticker_names() -> dict[str, str]:
    if not ZERO_JSON_PATH.exists():
        logger.warning(
            "ZERO ticker-name file not found: %s",
            ZERO_JSON_PATH,
        )
        return {}

    try:
        rows = json.loads(
            ZERO_JSON_PATH.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        logger.exception(
            "Cannot read ZERO ticker-name file: %s",
            ZERO_JSON_PATH,
        )
        return {}

    names = {}

    for row in rows:
        ticker = str(
            row.get("Ticker") or ""
        ).strip().upper()

        name = str(
            row.get("Name") or ""
        ).strip()

        if ticker:
            names[ticker] = name

    return names

def api_get(
    client: httpx.Client,
    path: str,
    params: dict | None = None,
):
    response = client.get(
        f"{API_URL}{path}",
        headers={
            "X-API-Key": API_KEY,
        },
        params=params,
    )

    response.raise_for_status()

    return response.json()

def api_post(
    client: httpx.Client,
    path: str,
    payload: dict,
):
    response = client.post(
        f"{API_URL}{path}",
        headers={"X-API-Key": API_KEY},
        json=payload,
    )
    if response.is_error:
        logger.error(
            "API POST %s failed: status=%s body=%s",
            path,
            response.status_code,
            response.text,
        )
    response.raise_for_status()

    return response.json()

def telegram_send(
    client: httpx.Client,
    bot_token: str,
    chat_id: str,
    text: str,
) -> None:
    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    response = client.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )

    response.raise_for_status()

    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(
            f"Telegram rejected message: {payload}"
        )

def measurement_dataframe(
    measurements: list[dict],
) -> pd.DataFrame:
    rows = []

    for record in measurements:
        metadata = record.get("metadata") or {}

        ticker = metadata.get("ticker")

        if not ticker:
            continue

        measurements_payload = (
            record.get("measurements") or {}
        )

        rows.append(
            {
                "id": record["id"],
                "ticker": ticker,
                "system": record["system"],
                "timestamp": pd.to_datetime(
                    record["timestamp"],
                    utc=True,
                ),
                "eur_usd": metadata.get("eur_usd"),
                **measurements_payload,
            }
        )

    return pd.DataFrame(rows)

def send_to_all_recipients(
    client: httpx.Client,
    config: dict,
    text: str,
    ticker: str | None = None,
) -> int:
    recipients = config.get("recipients") or []

    if not recipients:
        raise RuntimeError(
            "No Telegram recipients configured"
        )

    sent_count = 0
    membership = load_watchlist_membership()

    for recipient in recipients:
        token_env = recipient["bot_token_env"]
        bot_token = os.getenv(token_env)

        if not bot_token:
            logger.error(
                "Telegram token missing for recipient %s",
                recipient.get("name", "?"),
            )
            continue

        if ticker:
            allowed_users = membership.get(
                ticker.upper(),
                set(),
            )

            watchlist = str(
                recipient.get("watchlist") or ""
            ).strip().lower()

            if (
                watchlist
                and watchlist not in allowed_users
            ):
                continue

        try:
            telegram_send(
                client=client,
                bot_token=bot_token,
                chat_id=str(recipient["chat_id"]),
                text=text,
            )

            sent_count += 1

            logger.info(
                "Telegram message sent to %s",
                recipient.get("name", "?"),
            )

        except Exception:
            logger.exception(
                "Telegram send failed for %s",
                recipient.get("name", "?"),
            )

    return sent_count

def parse_hhmm(value: str) -> dt_time:
    return dt_time.fromisoformat(
        str(value)
    )

def inside_trading_window(
    timestamp,
    market_config: dict,
    kind: str,
) -> bool:
    timestamp = pd.to_datetime(
        timestamp,
        utc=True,
    )

    timezone_name = str(
        market_config["timezone"]
    )

    local_time = (
        timestamp
        .tz_convert(
            ZoneInfo(timezone_name)
        )
        .time()
    )

    start = parse_hhmm(
        market_config[
            f"{kind}_start"
        ]
    )

    end = parse_hhmm(
        market_config[
            f"{kind}_end"
        ]
    )

    if start <= end:
        return (
            start
            <= local_time
            <= end
        )

    #
    # Also supports a window crossing midnight.
    #
    return (
        local_time >= start
        or local_time <= end
    )

def calculate_latest_highb(
    df: pd.DataFrame,
    baseline_hours: float,
    tolerance_minutes: int,
) -> list[dict]:
    if df.empty:
        return []

    results = []

    for ticker, ticker_df in df.groupby("ticker"):
        if "close" not in ticker_df.columns:
            continue

        if "high" not in ticker_df.columns:
            continue

        ticker_df = (
            ticker_df
            .sort_values(
                ["timestamp", "id"]
            )
            .drop_duplicates(
                subset=["timestamp"],
                keep="last",
            )
            .copy()
        )

        if ticker_df.empty:
            continue

        latest = ticker_df.iloc[-1]

        latest_time = latest["timestamp"]

        baseline_time = (
            latest_time
            - pd.to_timedelta(
                float(baseline_hours),
                unit="h",
            )
        )
        tolerance = pd.to_timedelta(
            int(tolerance_minutes),
            unit="m",
        )

        candidates = ticker_df[
            (
                ticker_df["timestamp"]
                >= baseline_time - tolerance
            )
            &
            (
                ticker_df["timestamp"]
                <= baseline_time + tolerance
            )
        ].copy()

        if candidates.empty:
            continue

        candidates["_distance"] = (
            candidates["timestamp"]
            - baseline_time
        ).abs()

        baseline = (
            candidates
            .sort_values(
                ["_distance", "timestamp"],
                ascending=[True, True],
            )
            .iloc[0]
        )

        baseline_close = pd.to_numeric(
            baseline.get("close"),
            errors="coerce",
        )

        if (
            pd.isna(baseline_close)
            or baseline_close == 0
        ):
            continue

        latest_close = pd.to_numeric(
            latest.get("close"),
            errors="coerce",
        )

        closeb = None

        if (
            pd.notna(latest_close)
            and pd.notna(baseline_close)
            and baseline_close != 0
        ):
            closeb = (
                latest_close / baseline_close - 1
            ) * 100

        # Match the Live Overview behavior:
        # use the window beginning with the
        # selected baseline bar.
        window = ticker_df[
            (
                ticker_df["timestamp"]
                >= baseline["timestamp"]
            )
            &
            (
                ticker_df["timestamp"]
                <= latest_time
            )
        ].copy()

        high_values = pd.to_numeric(
            window["high"],
            errors="coerce",
        )

        highest = high_values.max()

        if pd.isna(highest):
            continue

        highb = (
            highest / baseline_close - 1
        ) * 100

        results.append(
            {
                "ticker": ticker,
                "latest_time": latest_time,
                "highb": float(highb),
                "closeb": (
                    float(closeb)
                    if closeb is not None
                    else None
                ),
                "system": latest.get(
                    "system"
                ),
                "asset_type": latest.get(
                    "asset_type"
                ),
                "sell_time_seconds": (
                    pd.to_numeric(
                        latest.get(
                            "sell_time_seconds"
                        ),
                        errors="coerce",
                    )
                ),
            }
        )

    return results


def newest_local_timestamp(
    timestamps,
) -> str:
    valid = [
        value
        for value in timestamps
        if value is not None
    ]

    if not valid:
        return datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M"
        )

    newest = max(valid)

    if isinstance(newest, pd.Timestamp):
        newest = newest.to_pydatetime()

    return newest.astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )

def evaluate_buy(
    client: httpx.Client,
    config: dict,
    state: dict,
) -> None:
    rule = config.get("buy") or {}

    if not rule.get("enabled", False):
        state["buy_condition_active"] = False
        return

    measurements = api_get(
        client,
        "/api/v1/measurements",
        {
            "limit": 50000,
        },
    )

    df = measurement_dataframe(
        measurements
    )

    highb_rows = calculate_latest_highb(
        df=df,
        baseline_hours=int(
            rule.get(
                "baseline_hours",
                2,
            )
        ),
        tolerance_minutes=int(
            rule.get(
                "baseline_tolerance_minutes",
                30,
            )
        ),
    )

    closeb_gt0_count = sum(
        1
        for row in highb_rows
        if (
            row.get("closeb") is not None
            and row["closeb"] > 0
        )
    )

    closeb_gt2_count = sum(
        1
        for row in highb_rows
        if (
            row.get("closeb") is not None
            and row["closeb"] > 2
        )
    )

    threshold = float(
        rule.get(
            "highb_threshold_percent",
            3.0,
        )
    )

    matching = [
        row
        for row in highb_rows
        if (
            row["highb"] > threshold
            and row.get("closeb") is not None
            and row["closeb"] > 0
        )
    ]

    ticker_names = load_ticker_names()

    market_regions = (
        load_ticker_market_regions()
    )

    trading_windows = (
        config.get("trading_windows")
        or {}
    )

    matching.sort(
        key=lambda row: (
            -row["highb"],
            row["ticker"],
        )
    )

    # Exclude tickers that already have an open BUY.
    open_payload = api_get(
        client,
        "/api/v1/simulation/open-tickers",
    )

    if isinstance(open_payload, dict):
        open_tickers = {
            str(ticker).upper()
            for ticker in open_payload.get(
                "tickers",
                [],
            )
        }
    else:
        open_tickers = {
            str(ticker).upper()
            for ticker in (open_payload or [])
        }

    excluded_open = [
        row
        for row in matching
        if str(row["ticker"]).upper()
        in open_tickers
    ]

    if excluded_open:
        logger.info(
            "BUY excluded open tickers: %s",
            ", ".join(
                (
                    f"{row['ticker']} "
                    f"HighB={row['highb']:+.2f}% "
                    f"CloseB={row['closeb']:+.2f}%"
                )
                for row in excluded_open
            ),
        )

    eligible = [
        row
        for row in matching
        if row["ticker"] not in open_tickers
    ]

    trading_eligible = []

    for row in eligible:
        ticker = str(
            row["ticker"]
        ).strip().upper()

        market_region = market_region_for_row(
            ticker,
            row,
            market_regions,
        )

        if not market_region:
            logger.warning(
                "BUY skipped %s: "
                "market region unknown",
                ticker,
            )
            continue

        market_config = trading_windows.get(
            market_region
        )

        if not market_config:
            logger.warning(
                "BUY skipped %s: "
                "no trading-window config for %s",
                ticker,
                market_region,
            )
            continue

        if not inside_trading_window(
            row["latest_time"],
            market_config,
            "buy",
        ):
            logger.info(
                "BUY skipped %s: outside %s "
                "preferred BUY window",
                ticker,
                market_region,
            )
            continue

        max_sell_time = market_config.get(
            "max_buy_sell_time_seconds"
        )

        sell_time_seconds = row.get(
            "sell_time_seconds"
        )

        if max_sell_time is not None:
            sell_time_numeric = pd.to_numeric(
                sell_time_seconds,
                errors="coerce",
            )

            if (
                pd.isna(sell_time_numeric)
                or sell_time_numeric
                > float(max_sell_time)
            ):
                logger.info(
                    "BUY skipped %s: SellTime=%r "
                    "exceeds %s seconds",
                    ticker,
                    sell_time_seconds,
                    max_sell_time,
                )
                continue

        trading_eligible.append(row)

    highb_count = len(highb_rows)

    threshold_count = sum(
        1
        for row in highb_rows
        if row["highb"] > threshold
    )

    positive_closeb_count = sum(
        1
        for row in highb_rows
        if (
            row["highb"] > threshold
            and row.get("closeb") is not None
            and row["closeb"] > 0
        )
    )

    open_excluded_count = (
        len(matching)
        - len(eligible)
    )

    logger.info(
        "BUY evaluation: total=%d HighB>%.2f%%=%d "
        "CloseB>0=%d open_excluded=%d "
        "eligible=%d trading_eligible=%d",
        len(highb_rows),
        threshold,
        len([
            row
            for row in highb_rows
            if row["highb"] > threshold
        ]),
        len(matching),
        len(excluded_open),
        len(eligible),
        len(trading_eligible),
    )

    # Global BUY list: maximum 6 tickers.
    selected = trading_eligible[:6]
    #selected = eligible[:6]

    if selected:
        logger.info(
            "BUY selected: %s",
            ", ".join(
                (
                    f"{row['ticker']} "
                    f"HighB={row['highb']:+.2f}% "
                    f"CloseB={row['closeb']:+.2f}%"
                )
                for row in selected
            ),
        )

    if not selected:
        return

    newest_date = newest_local_timestamp(
        [
            row["latest_time"]
            for row in selected
        ]
    )

    membership = load_watchlist_membership()
    recipients = config.get("recipients") or []

    # Tracks which selected tickers were successfully
    # delivered to at least one interested user.
    sent_tickers: set[str] = set()

    for recipient in recipients:
        watchlist = str(
            recipient.get("watchlist") or ""
        ).strip().lower()

        if not watchlist:
            logger.warning(
                "BUY skipped recipient %s: "
                "no watchlist configured",
                recipient.get("name", "?"),
            )
            continue

        recipient_rows = [
            row
            for row in selected
            if watchlist
            in membership.get(
                str(row["ticker"]).upper(),
                set(),
            )
        ]

        if not recipient_rows:
            logger.info(
                "BUY: no selected tickers for %s",
                recipient.get("name", "?"),
            )
            continue

        tickers_text = "\n".join(
            (
                f"{row['ticker']} "
                f"{ticker_names.get(row['ticker'], row['ticker'])} "
                f"{row['closeb']:+.2f}%"
            )
            if row.get("closeb") is not None
            else (
                f"{row['ticker']} "
                f"{ticker_names.get(row['ticker'], row['ticker'])} "
                f"—"
            )
            for row in recipient_rows
        )

        message = (
            f"{newest_date} BUY:\n"
            f"{tickers_text}\n"
            f"{config['dashboard_url']}"
        )

        token_env = recipient["bot_token_env"]
        bot_token = os.getenv(token_env)

        if not bot_token:
            logger.error(
                "Telegram token missing for recipient %s "
                "(environment variable %s)",
                recipient.get("name", "?"),
                token_env,
            )
            continue

        try:
            telegram_send(
                client=client,
                bot_token=bot_token,
                chat_id=str(
                    recipient["chat_id"]
                ),
                text=message,
            )

            for row in recipient_rows:
                sent_tickers.add(
                    str(row["ticker"]).upper()
                )

            logger.info(
                "BUY notification sent to %s "
                "for %d tickers: %s",
                recipient.get("name", "?"),
                len(recipient_rows),
                ", ".join(
                    str(row["ticker"])
                    for row in recipient_rows
                ),
            )

        except Exception:
            logger.exception(
                "BUY Telegram send failed for %s",
                recipient.get("name", "?"),
            )

    if not sent_tickers:
        logger.warning(
            "BUY notification was not delivered "
            "for any selected ticker"
        )
        return

    # Record one Simulation BUY per ticker,
    # even if multiple users received it.
    for row in selected:
        ticker = str(
            row["ticker"]
        ).upper()

        if ticker not in sent_tickers:
            continue

        ticker_rows = (
            df[df["ticker"] == ticker]
            .sort_values(
                ["timestamp", "id"]
            )
        )

        if ticker_rows.empty:
            logger.warning(
                "Cannot record BUY for %s: "
                "no measurements",
                ticker,
            )
            continue

        latest_row = ticker_rows.iloc[-1]

        buy_price = pd.to_numeric(
            latest_row.get("close"),
            errors="coerce",
        )

        if pd.isna(buy_price):
            logger.warning(
                "Cannot record BUY for %s: "
                "invalid close price",
                ticker,
            )
            continue

        buy_time = pd.to_datetime(
            row["latest_time"],
            utc=True,
        )

        buy_eur_usd = eur_usd_near_time(
            ticker_rows,
            buy_time,
        )

        buy_price_eur = None

        if buy_eur_usd is not None:
            buy_price_eur = (
                float(buy_price)
                / float(buy_eur_usd)
            )
        else:
            logger.warning(
                "BUY %s: EUR conversion unavailable "
                "(FX=%r)",
                ticker,
                buy_eur_usd,
            )

        if isinstance(
            buy_time,
            pd.Timestamp,
        ):
            buy_time = (
                buy_time.to_pydatetime()
            )

        try:
            result = api_post(
                client,
                "/api/v1/simulation/signals",
                {
                    "side": "BUY",
                    "ticker": ticker,
                    "ticker_name": (
                        ticker_names.get(
                            ticker,
                            ticker,
                        )
                    ),
                    "timestamp": (
                        buy_time.isoformat()
                    ),
                    "price": float(
                        buy_price
                    ),
                    "price_eur": (
                        float(buy_price_eur)
                        if buy_price_eur is not None
                        else None
                    ),
                    "closeb_gt0_count": (
                        closeb_gt0_count
                    ),
                    "closeb_gt2_count": (
                        closeb_gt2_count
                    ),
                    "telegram_sent": True,
                },
            )

            logger.info(
                "Simulation BUY %s: %s",
                ticker,
                result,
            )

        except Exception:
            logger.exception(
                "Cannot record Simulation BUY "
                "for %s",
                ticker,
            )

    logger.info(
        "BUY cycle delivered %d "
        "unique tickers: %s",
        len(sent_tickers),
        ", ".join(
            sorted(sent_tickers)
        ),
    )

def sell_rule_1_stable_48h(
    ticker_df: pd.DataFrame,
    buy_time,
    buy_price: float,
    latest_time,
) -> bool:
    if ticker_df.empty:
        return False

    buy_time = pd.to_datetime(
        buy_time,
        utc=True,
    )

    latest_time = pd.to_datetime(
        latest_time,
        utc=True,
    )

    # Rule1 cannot trigger before 48 hours
    period_end = (
        buy_time
        + pd.Timedelta(48, unit="h",)
    )

    if latest_time < period_end:
        return False

    work = ticker_df[
        ["timestamp", "close"]
    ].copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"],
        utc=True,
        errors="coerce",
    )

    work["close"] = pd.to_numeric(
        work["close"],
        errors="coerce",
    )

    work = (
        work
        .dropna(subset=["timestamp", "close"])
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
    )

    # Only inspect the first 48h after BUY
    holding = work[
        (work["timestamp"] > buy_time)
        & (work["timestamp"] <= period_end)
    ].copy()

    if holding.empty:
        return False

    first_time = holding["timestamp"].min()
    last_time = holding["timestamp"].max()

    coverage_tolerance = pd.Timedelta(
        30,
        unit="min",
    )

    if first_time > (
        buy_time + coverage_tolerance
    ):
        return False

    if last_time < (
        period_end - coverage_tolerance
    ):
        return False

    lower = float(buy_price) * 0.99
    upper = float(buy_price) * 1.01

    return bool(
        holding["close"]
        .between(
            lower,
            upper,
            inclusive="both",
        )
        .all()
    )

def sell_rule_3_drop_from_peak(
    ticker_df: pd.DataFrame,
    buy_time,
    latest_time,
    current_price: float,
) -> bool:
    if ticker_df.empty:
        return False

    buy_time = pd.to_datetime(
        buy_time,
        utc=True,
    )

    latest_time = pd.to_datetime(
        latest_time,
        utc=True,
    )

    work = ticker_df[
        ["timestamp", "close"]
    ].copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"],
        utc=True,
        errors="coerce",
    )

    work["close"] = pd.to_numeric(
        work["close"],
        errors="coerce",
    )

    work = (
        work
        .dropna(subset=["timestamp", "close"])
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
    )

    # Previous observations strictly after BUY
    # and strictly before the current observation.
    after_buy = work[
        (work["timestamp"] > buy_time)
        & (work["timestamp"] < latest_time)
    ].copy()

    if after_buy.empty:
        return False

    peak_price = float(
        after_buy["close"].max()
    )

    if peak_price <= 0:
        return False

    return (
        float(current_price)
        <= 0.98 * peak_price
    )

def evaluate_sell(
    client: httpx.Client,
    config: dict,
    state: dict,
) -> None:
    rule = config.get("sell") or {}

    if not rule.get("enabled", True):
        return

    simulation_payload = api_get(
        client,
        "/api/v1/simulation",
        {
            "days": 365,
            "include_open": True,
        },
    )

    if isinstance(simulation_payload, dict):
        trades = (
            simulation_payload.get("trades")
            or simulation_payload.get("rows")
            or simulation_payload.get("items")
            or []
        )
    else:
        trades = simulation_payload or []

    open_trades = [
        trade
        for trade in trades
        if not trade.get("SellTime")
    ]

    logger.info(
        "SELL evaluation: %d open trades",
        len(open_trades),
    )

    if not open_trades:
        return

    measurements = api_get(
        client,
        "/api/v1/measurements",
        {
            "limit": 50000,
        },
    )

    df = measurement_dataframe(
        measurements
    )

    logger.info(
        "SELL evaluation: loaded %d measurement rows",
        len(df),
    )

    if df.empty:
        return

    ticker_names = load_ticker_names()

    market_regions = (
        load_ticker_market_regions()
    )

    trading_windows = (
        config.get("trading_windows")
        or {}
    )

    rule2_max_data_age_minutes = int(
        rule.get(
            "rule2_max_data_age_minutes",
            30,
        )
    )

    for trade in open_trades:
        ticker = str(
            trade.get("Ticker") or ""
        ).strip().upper()

        if not ticker:
            continue

        buy_price = pd.to_numeric(
            trade.get("BuyPrice"),
            errors="coerce",
        )

        if pd.isna(buy_price):
            continue

        ticker_rows = (
            df[df["ticker"] == ticker]
            .sort_values(
                ["timestamp", "id"]
            )
            .copy()
        )

        if ticker_rows.empty:
            logger.warning(
                "SELL skipped %s: no measurements",
                ticker,
            )
            continue

        latest = ticker_rows.iloc[-1]

        current_price = pd.to_numeric(
            latest["close"],
            errors="coerce",
        )

        if pd.isna(current_price):
            continue

        current_price = float(current_price)

        latest_time = pd.to_datetime(
            latest["timestamp"],
            utc=True,
        )

        if pd.isna(buy_price) or buy_price <= 0:
            logger.warning(
                "Cannot evaluate SELL for %s: invalid buy_price",
                ticker,
            )
            continue

        ticker_df = (
            df[df["ticker"] == ticker]
            .sort_values(
                ["timestamp", "id"]
            )
            .copy()
        )

        if ticker_df.empty:
            continue

        latest_row = ticker_df.iloc[-1]

        current_price = pd.to_numeric(
            latest_row.get("close"),
            errors="coerce",
        )

        if (
            pd.isna(current_price)
            or current_price <= 0
        ):
            continue

        latest_time = pd.to_datetime(
            latest_row["timestamp"],
            utc=True,
        )

        buy_time = pd.to_datetime(
            trade.get("BuyTime"),
            utc=True,
            errors="coerce",
        )

        if pd.isna(buy_time):
            #logger.warning(
            #    "SELL skipped %s: invalid BuyTime %r",
            #    ticker,
            #    trade.get("BuyTime"),
            #)
            continue

        if latest_time <= buy_time:
            logger.info(
                "SELL skipped %s: latest market time %s "
                "is not after BuyTime %s",
                ticker,
                latest_time,
                buy_time,
            )
            continue

        #
        # SELL Rule 1:
        # after at least 48 hours, sell if all observed
        # prices during the first 48 hours after BuyTime
        # stayed within +/-1% of BuyPrice.
        #
        rule1_triggered = (
            sell_rule_1_stable_48h(
                ticker_df=ticker_df,
                buy_time=buy_time,
                buy_price=float(buy_price),
                latest_time=latest_time,
            )
        )

        #
        # SELL Rule 2:
        # current price <= 98% of BuyPrice.
        #
        rule2_triggered = (
            float(current_price)
            <= 0.98 * float(buy_price)
        )

        #
        # SELL Rule 3:
        # current price <= 98% of the highest
        # previously observed price after BuyTime.
        #
        rule3_triggered = (
            sell_rule_3_drop_from_peak(
                ticker_df=ticker_df,
                buy_time=buy_time,
                latest_time=latest_time,
                current_price=float(current_price),
            )
        )

        inside_sell_window = False

        market_region = market_region_for_row(
            ticker,
            latest_row,
            market_regions,
        )

        market_config = (
            trading_windows.get(
                market_region
            )
            if market_region
            else None
        )

        if market_config:
            inside_sell_window = (
                inside_trading_window(
                    latest_time,
                    market_config,
                    "sell",
                )
            )

        if not market_config:
            logger.warning(
                "SELL %s: market region/config unavailable "
                "(region=%r)",
                ticker,
                market_region,
            )

        normal_exit_triggered = (
            rule1_triggered
            or rule3_triggered
        )

        if (
            normal_exit_triggered
            and not rule2_triggered
            and not inside_sell_window
        ):
            logger.info(
                "SELL deferred %s: Rule1/Rule3 "
                "outside %s preferred SELL window",
                ticker,
                market_region or "UNKNOWN",
            )
            continue

        if (
            rule2_triggered
            and not inside_sell_window
        ):
            now_utc = pd.Timestamp.now(
                tz="UTC"
            )

            data_age = (
                now_utc - latest_time
            )

            max_age = pd.Timedelta(
                rule2_max_data_age_minutes,
                unit="min",
            )

            if data_age > max_age:
                logger.info(
                    "SELL deferred %s: Rule2 triggered "
                    "outside preferred window but market "
                    "data age %s exceeds %d minutes",
                    ticker,
                    data_age,
                    rule2_max_data_age_minutes,
                )
                continue

            logger.warning(
                "SELL %s: Rule2 triggered outside "
                "%s preferred SELL window; using fresh "
                "market data age=%s",
                ticker,
                market_region or "UNKNOWN",
                data_age,
            )

        buy_eur_usd = eur_usd_near_time(
            ticker_rows,
            buy_time,
        )

        sell_eur_usd = eur_usd_near_time(
            ticker_rows,
            latest_time,
        )

        buy_price_eur = None
        sell_price_eur = None
        absolute_difference_eur = None

        if (
            buy_eur_usd is not None
            and sell_eur_usd is not None
        ):
            buy_price_eur = (
                float(buy_price)
                / buy_eur_usd
            )

            sell_price_eur = (
                float(current_price)
                / sell_eur_usd
            )

            absolute_difference_eur = (
                sell_price_eur
                - buy_price_eur
            )
        else:
            logger.warning(
                "SELL %s: EUR conversion unavailable "
                "(buy FX=%r sell FX=%r)",
                ticker,
                buy_eur_usd,
                sell_eur_usd,
            )

        logger.info(
            "SELL check %s: buy=%.4f current=%.4f "
            "relative=%+.2f%% "
            "rule1=%s rule2=%s rule3=%s",
            ticker,
            float(buy_price),
            float(current_price),
            (
                float(current_price)
                / float(buy_price)
                - 1.0
            ) * 100.0,
            rule1_triggered,
            rule2_triggered,
            rule3_triggered,
        )

        if not (
            rule1_triggered
            or rule2_triggered
            or rule3_triggered
        ):
            continue

        #
        # Rule1 and Rule3 are normal exits and should
        # wait for the preferred market SELL window.
        #
        normal_exit_triggered = (
            rule1_triggered
            or rule3_triggered
        )

        if (
            normal_exit_triggered
            and not rule2_triggered
            and not inside_sell_window
        ):
            logger.info(
                "SELL deferred %s: Rule1/Rule3 "
                "outside %s preferred SELL window",
                ticker,
                market_region or "UNKNOWN",
            )
            continue

        sell_reasons = []

        if rule1_triggered:
            sell_reasons.append("Rule1")

        if rule2_triggered:
            sell_reasons.append("Rule2")

        if rule3_triggered:
            sell_reasons.append("Rule3")

        sell_reason = "+".join(
            sell_reasons
        )

        relative_difference = (
            float(current_price)
            / float(buy_price)
            - 1.0
        ) * 100.0

        stored_name = str(
            trade.get("TickerName") or ""
        ).strip()

        ticker_name = ticker_names.get(
            ticker,
            stored_name or ticker,
        )

        if stored_name and stored_name != ticker:
            ticker_name = stored_name

        date_text = (
            latest_time
            .tz_convert("Europe/Berlin")
            .strftime("%Y-%m-%d %H:%M")
        )

        message = (
            f"{date_text} SELL "
            f"{ticker} "
            f"{ticker_name} "
            f"{relative_difference:+.2f}% "
            f"{sell_reason}\n"
            f"{config['dashboard_url']}"
        )

        sent_count = send_to_all_recipients(
            client,
            config,
            message,
            ticker=ticker,
        )

        if sent_count <= 0:
            logger.warning(
                "SELL notification not sent for %s",
                ticker,
            )
            continue

        try:
            result = api_post(
                client,
                "/api/v1/simulation/signals",
                {
                    "side": "SELL",
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                    "timestamp": (
                        latest_time.to_pydatetime()
                        .isoformat()
                    ),
                    "price": float(
                        current_price
                    ),
                    "price_eur": (
                        float(sell_price_eur)
                        if sell_price_eur is not None
                        else None
                    ),
                    "buy_price_eur": (
                        float(buy_price_eur)
                        if buy_price_eur is not None
                        else None
                    ),
                    "sell_reason": sell_reason,
                    "absolute_difference_eur": (
                        float(absolute_difference_eur)
                        if absolute_difference_eur is not None
                        else None
                    ),
                    "telegram_sent": True,
                },
            )

            logger.info(
                "Simulation SELL %s: %s "
                "(rule1=%s rule2=%s rule3=%s absolute_eur=%s)",
                ticker,
                result,
                rule1_triggered,
                rule2_triggered,
                rule3_triggered,
                (
                    f"{absolute_difference_eur:+.2f}"
                    if absolute_difference_eur is not None
                    else "n/a"
                ),
            )

        except Exception:
            logger.exception(
                "Cannot record Simulation SELL for %s",
                ticker,
            )

def alert_text(alert: dict) -> str:
    explicit = (
        alert.get("text")
        or alert.get("message")
        or alert.get("description")
    )

    if explicit:
        return str(explicit)

    parts = []

    severity = alert.get("severity")
    rule_name = alert.get("rule_name")
    actual_value = alert.get("actual_value")
    system = alert.get("system")

    if severity:
        parts.append(
            str(severity).upper()
        )

    if rule_name:
        parts.append(str(rule_name))

    if system:
        parts.append(
            f"[{system}]"
        )

    if actual_value is not None:
        parts.append(
            f"value={actual_value}"
        )

    return " ".join(parts) or "New alert"


def evaluate_alerts(
    client: httpx.Client,
    config: dict,
    state: dict,
) -> None:
    rule = config.get("alerts") or {}

    if not rule.get("enabled", False):
        return

    alerts = api_get(
        client,
        "/api/v1/alerts",
        {
            "limit": 500,
        },
    )

    if not alerts:
        return

    last_alert_id = int(
        state.get(
            "last_alert_id",
            0,
        )
    )

    new_alerts = sorted(
        [
            alert
            for alert in alerts
            if int(alert["id"])
            > last_alert_id
        ],
        key=lambda alert: int(
            alert["id"]
        ),
    )

    for alert in new_alerts:
        created_at = pd.to_datetime(
            alert.get("created_at"),
            utc=True,
        )

        date_text = (
            created_at
            .tz_convert("Europe/Berlin")
            .strftime("%Y-%m-%d %H:%M")
        )

        message = (
            f"{date_text} ALERT: "
            f"{alert_text(alert)} "
            f"{config['dashboard_url']}"
        )

        sent_count = send_to_all_recipients(
            client,
            config,
            message,
        )

        if sent_count > 0:
            logger.info(
                "ALERT notification sent for alert %s",








                alert["id"],
            )

            # Advance only after at least one successful send.
            state["last_alert_id"] = int(
                alert["id"]
            )

            save_state(state)

        else:
            logger.warning(
                "ALERT notification was not sent for alert %s",
                alert["id"],
            )

            # Do not advance last_alert_id.
            # This allows the notifier to try again next cycle.
            break

def eur_usd_near_time(
    ticker_rows: pd.DataFrame,
    target_time,
):
    if (
        ticker_rows.empty
        or "eur_usd" not in ticker_rows.columns
    ):
        return None

    work = ticker_rows[
        ["timestamp", "eur_usd"]
    ].copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"],
        utc=True,
        errors="coerce",
    )

    work["eur_usd"] = pd.to_numeric(
        work["eur_usd"],
        errors="coerce",
    )

    work = work.dropna(
        subset=["timestamp", "eur_usd"]
    )

    work = work[
        work["eur_usd"] > 0
    ]

    if work.empty:
        return None

    target_time = pd.to_datetime(
        target_time,
        utc=True,
    )

    work["distance"] = (
        work["timestamp"]
        - target_time
    ).abs()

    row = (
        work
        .sort_values(
            ["distance", "timestamp"]
        )
        .iloc[0]
    )

    # Don't use a wildly unrelated FX observation.
    if row["distance"] > pd.Timedelta(
        30,
        unit="min",
    ):
        return None

    return float(
        row["eur_usd"]
    )

def run_once() -> None:
    if not API_KEY:
        raise RuntimeError(
            "MONITOR_API_KEY is not configured"
        )

    #if not TELEGRAM_BOT_TOKEN:
    #    raise RuntimeError(
    #        "TELEGRAM_BOT_TOKEN is not configured"
    #    )

    config = load_config()
    state = load_state()

    with httpx.Client(
        timeout=30,
    ) as client:
        evaluate_sell(
            client,
            config,
            state,
        )

        evaluate_buy(
            client,
            config,
            state,
        )

        evaluate_alerts(
            client,
            config,
            state,
        )

    save_state(state)


def main() -> None:
    config = load_config()

    poll_seconds = int(
        config.get(
            "poll_seconds",
            60,
        )
    )

    logger.info(
        "Telegram notifier started; polling every %d seconds",
        poll_seconds,
    )

    while True:
        try:
            run_once()

        except Exception:
            logger.exception(
                "Telegram notification cycle failed"
            )

        time.sleep(
            max(
                poll_seconds,
                10,
            )
        )


if __name__ == "__main__":
    main()
