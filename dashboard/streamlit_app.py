from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import math
import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
import altair as alt
import yaml

from shared.trading_decisions import (
    evaluate_sell_history,
    format_duration,
    trading_time_window_start,
    trading_window_info,
)

API_URL = os.getenv("MONITOR_API_URL", "http://api:8000")
API_KEY = os.getenv("MONITOR_API_KEY", "CHANGE_ME")
HEADERS = {"X-API-Key": API_KEY}
LOCAL_TIMEZONE = "Europe/Berlin"

st.set_page_config(page_title="Home Monitor", page_icon="🏠", layout="wide")

@st.cache_data(ttl=300)
def load_alerts_cached():
    return api_get(
        "/api/v1/alerts",
        {
            "limit": 500,
        },
    )

@st.cache_data(ttl=300)
def load_instrument_metadata_cached():
    """Load instrument metadata used for informational dashboard columns."""
    try:
        rows = api_get(
            "/api/v1/instruments",
            {
                "active": False,
            },
        )
    except Exception:
        return {}

    if not isinstance(rows, list):
        return {}

    result = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        ticker = str(row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue

        isin = str(row.get("ISIN") or "").strip()
        source = str(row.get("Source") or "").strip().upper()

        last_dividend = str(
            row.get("LastDividend") or ""
        ).strip()

        next_dividend = str(
            row.get("ExpNextDividend") or ""
        ).strip()

        try:
            dividend_eur = float(
                row.get("DividendEUR")
            )
        except (TypeError, ValueError):
            dividend_eur = None

        result[ticker] = {
            "ISIN": isin or "—",
            "Gainer": source == "AUTO_GAINER",
            "LastDividend": last_dividend,
            "ExpNextDividend": next_dividend,
            "DividendEUR": dividend_eur,
            "DividendType": str(
                row.get("DividendType") or "-"
            ).strip() or "-",
        }

    return result


@st.cache_data(ttl=300)
def load_ticker_news_cached():
    """Load recent stored ticker news without making trading pages depend on it."""
    try:
        rows = api_get(
            "/api/v1/instruments/news",
            {
                "days": 7,
            },
        )
    except Exception:
        return []

    return rows if isinstance(rows, list) else []


def latest_ticker_news_map(rows) -> dict[str, str]:
    """Return the newest stored news summary for each ticker."""
    result: dict[str, str] = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue

        ticker = str(row.get("Ticker") or "").strip().upper()
        summary = str(row.get("Summary") or "").strip()

        if not ticker or not summary or ticker in result:
            continue

        result[ticker] = summary

    return result


@st.cache_data(ttl=30)
def load_simulation_payload_cached():
    return api_get(
        "/api/v1/simulation",
        {
            "days": 0,
            "include_open": True,
        },
    )


@st.cache_data(ttl=30)
def load_simulation_cached():
    payload = load_simulation_payload_cached()

    if isinstance(payload, dict):
        return (
            payload.get("trades")
            or payload.get("rows")
            or payload.get("items")
            or []
        )

    return payload or []

def api_get(path: str, params: dict | None = None):
    response = httpx.get(f"{API_URL}{path}", headers=HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=300)
def load_measurement_df_cached():
    measurements = api_get(
        "/api/v1/measurements",
        {
            "limit": 50000,
            "compact": True,
        },
    )

    measurement_rows = []

    for record in measurements:
        # Compact API responses already contain the dashboard fields flattened
        # into each row. Keep support for the original nested representation so
        # the loader remains backward-compatible during deployments/rollbacks.
        if "measurements" not in record:
            # Keep raw timestamp strings while building the row list.
            # Convert complete columns once below for much better performance.
            measurement_rows.append(dict(record))
            continue

        metadata = (
            record.get("metadata")
            or {}
        )

        ticker = metadata.get(
            "ticker"
        )

        if not ticker:
            ticker = record["system"]

        asset_type = metadata.get(
            "asset_type"
        )

        if not asset_type:
            if record["system"] == "crypto":
                asset_type = "crypto"
            elif record["system"] == "polygon":
                asset_type = "stock"
            else:
                asset_type = "other"

        base = {
            "id": record["id"],
            "system": record["system"],
            "device": record["device"],
            "ticker": ticker,
            "asset_type": asset_type,
            "timestamp": record["timestamp"],
            "received_at": record.get("received_at"),
            "eur_usd": metadata.get(
                "eur_usd"
            ),
        }

        measurement_rows.append(
            {
                **base,
                **record["measurements"],
            }
        )

    result = pd.DataFrame(
        measurement_rows
    )

    if not result.empty:
        if "timestamp" in result.columns:
            result["timestamp"] = pd.to_datetime(
                result["timestamp"],
                utc=True,
                errors="coerce",
                format="mixed",
            )

        if "received_at" in result.columns:
            result["received_at"] = pd.to_datetime(
                result["received_at"],
                utc=True,
                errors="coerce",
                format="mixed",
            )

    return result

def api_post(path: str):
    response = httpx.post(f"{API_URL}{path}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


def api_post_json(path: str, payload: dict):
    response = httpx.post(
        f"{API_URL}{path}",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def api_post_file(
    path: str,
    *,
    filename: str,
    content: bytes,
    content_type: str | None = None,
):
    response = httpx.post(
        f"{API_URL}{path}",
        headers=HEADERS,
        files={
            "file": (
                filename,
                content,
                content_type or "application/octet-stream",
            )
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


page = st.sidebar.radio(
    "Page",
    [
        "Zero-Trading",
        "Last Data",
        "Historical Data",
        "Sim-Trading",
        "Trading Efficiency",
        "System Health",
        "Alerts",
        "Jira",
        "Settings",
        "Logs",
    ],
)

refresh_requested = st.sidebar.button(
    "Refresh now",
    use_container_width=True,
)

if refresh_requested:
    # Clear before loading dashboard data. This avoids the previous behavior
    # where a button click first loaded the old cache, cleared it, and then
    # triggered a second complete rerun/load.
    st.cache_data.clear()


try:
    df = load_measurement_df_cached()
    alerts = load_alerts_cached()

except Exception as exc:
    st.error(
        f"Cannot reach monitoring API: {exc}"
    )
    st.stop()

alerts_df = pd.DataFrame(alerts)

def load_ticker_names() -> dict[str, str]:
    path = Path("/app/config/zero.json")

    if not path.exists():
        return {}

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}

    names = {}

    if isinstance(payload, list):
        rows = payload

    elif isinstance(payload, dict):
        rows = (
            payload.get("tickers")
            or payload.get("instruments")
            or payload.get("rows")
            or []
        )

    else:
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        ticker = str(
            row.get("ticker")
            or row.get("Ticker")
            or ""
        ).strip().upper()

        name = str(
            row.get("name")
            or row.get("Name")
            or ticker
        ).strip()

        if ticker:
            names[ticker] = name or ticker

    return names

TICKER_NAMES = load_ticker_names()


def _to_utc_timestamp(value):
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return parsed if pd.notna(parsed) else pd.NaT


def get_simulation_timestamp(payload, market_df: pd.DataFrame):
    """Return API simulation timestamp when supplied, otherwise latest collector receipt."""
    if isinstance(payload, dict):
        for key in (
            "simulationTimestamp",
            "simulation_timestamp",
            "simulation_time",
            "as_of",
            "generated_at",
        ):
            if payload.get(key) is not None:
                parsed = _to_utc_timestamp(payload.get(key))
                if pd.notna(parsed):
                    return parsed, key

    if not market_df.empty and "received_at" in market_df.columns:
        fallback = pd.to_datetime(
            market_df["received_at"], utc=True, errors="coerce"
        ).max()
        if pd.notna(fallback):
            return fallback, "latest received_at fallback"

    return pd.Timestamp.now(tz="UTC"), "current time fallback"


def discover_imported_asset_csvs():
    """Find likely imported asset CSV files and return asset metadata plus used paths."""
    candidates = []

    for env_name in (
        "ASSETS_CSV",
        "ASSET_CSV",
        "ZERO_ASSETS_CSV",
        "ZERO_CSV",
        "TICKERS_CSV",
    ):
        raw = os.getenv(env_name)
        if raw:
            candidates.append(Path(raw))

    search_dirs = [
        Path("/app/config"),
        PROJECT_ROOT / "config",
        PROJECT_ROOT,
        Path(__file__).resolve().parent,
    ]
    for directory in search_dirs:
        if directory.exists():
            candidates.extend(sorted(directory.glob("*.csv")))

    # Keep order while removing duplicate paths.
    unique_candidates = []
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except Exception:
            key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)

    asset_frames = []
    used_paths = []
    ticker_columns = (
        "Ticker", "ticker", "TICKER",
        "Symbol", "symbol", "SYMBOL",
    )
    name_columns = (
        "Name", "name", "NAME",
        "TickerName", "tickerName", "ticker_name",
    )
    isin_columns = (
        "ISIN", "Isin", "isin",
    )

    for path in unique_candidates:
        if not path.is_file():
            continue
        try:
            imported = pd.read_csv(path)
        except Exception:
            continue

        ticker_column = next(
            (column for column in ticker_columns if column in imported.columns),
            None,
        )
        if ticker_column is None:
            continue

        name_column = next(
            (column for column in name_columns if column in imported.columns),
            None,
        )
        isin_column = next(
            (column for column in isin_columns if column in imported.columns),
            None,
        )

        assets = pd.DataFrame({
            "Ticker": imported[ticker_column].astype("string").str.strip().str.upper(),
            "Name": (
                imported[name_column].astype("string").str.strip()
                if name_column else pd.Series(pd.NA, index=imported.index, dtype="string")
            ),
            "ISIN": (
                imported[isin_column].astype("string").str.strip()
                if isin_column else pd.Series(pd.NA, index=imported.index, dtype="string")
            ),
        })
        assets = assets[assets["Ticker"].notna() & assets["Ticker"].ne("")]
        if assets.empty:
            continue

        asset_frames.append(assets)
        used_paths.append(str(path))

    if not asset_frames:
        return pd.DataFrame(columns=["Ticker", "Name", "ISIN"]), used_paths

    all_assets = pd.concat(asset_frames, ignore_index=True)
    all_assets = all_assets.drop_duplicates(subset=["Ticker"], keep="first")
    return all_assets.sort_values("Ticker").reset_index(drop=True), used_paths


def tracked_tickers_at(market_df: pd.DataFrame, snapshot_time):
    """Match Live Overview's 30-minute tracked-asset definition at a historical time."""
    if market_df.empty or pd.isna(snapshot_time):
        return set()

    work = market_df.copy()
    work["received_at"] = pd.to_datetime(
        work["received_at"], utc=True, errors="coerce"
    )
    snapshot = _to_utc_timestamp(snapshot_time)
    work = work[
        work["received_at"].notna()
        & (work["received_at"] <= snapshot)
        & (work["received_at"] >= snapshot - pd.Timedelta(minutes=30))
    ]
    return set(
        work["ticker"].dropna().astype(str).str.strip().str.upper()
    )


def simulation_state_at(simulation_df: pd.DataFrame, snapshot_time):
    """Count ticker state using each ticker's latest BUY record at the snapshot."""
    if simulation_df.empty or pd.isna(snapshot_time):
        return 0, 0

    snapshot = _to_utc_timestamp(snapshot_time)
    work = simulation_df.copy()
    work["BuyTime"] = pd.to_datetime(work.get("BuyTime"), utc=True, errors="coerce")
    work["SellTime"] = pd.to_datetime(work.get("SellTime"), utc=True, errors="coerce")
    work["Ticker"] = work.get("Ticker", pd.Series(index=work.index, dtype="object")).astype(str).str.upper()
    work = work[work["BuyTime"].notna() & (work["BuyTime"] <= snapshot)]
    if work.empty:
        return 0, 0

    latest = (
        work.sort_values(["Ticker", "BuyTime"])
        .groupby("Ticker", as_index=False)
        .tail(1)
    )
    is_open = latest["SellTime"].isna() | (latest["SellTime"] > snapshot)
    return int(is_open.sum()), int((~is_open).sum())


def closeb_over_two_at(market_df: pd.DataFrame, snapshot_time):
    """Rebuild the Live Overview as known at a collection snapshot and count CloseB > 2%."""
    if market_df.empty or pd.isna(snapshot_time):
        return 0

    snapshot = _to_utc_timestamp(snapshot_time)
    known = market_df.copy()
    known["received_at"] = pd.to_datetime(
        known["received_at"], utc=True, errors="coerce"
    )
    known = known[known["received_at"].notna() & (known["received_at"] <= snapshot)]
    if known.empty:
        return 0

    active = tracked_tickers_at(known, snapshot)
    if not active:
        return 0
    known = known[
        known["ticker"].astype(str).str.upper().isin(active)
    ]
    overview = build_live_overview(known)
    if overview.empty or "CloseB" not in overview.columns:
        return 0
    values = pd.to_numeric(overview["CloseB"], errors="coerce")
    return int((values > 2.0).sum())


def previous_simulator_day_bounds(now_local=None):
    """Previous simulator day is 03:00 local time to 03:00 local time."""
    if now_local is None:
        now_local = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    else:
        now_local = pd.Timestamp(now_local).tz_convert(LOCAL_TIMEZONE)

    today_three = now_local.normalize() + pd.Timedelta(hours=3)
    end_local = today_three if now_local >= today_three else today_three - pd.Timedelta(days=1)
    start_local = end_local - pd.Timedelta(days=1)
    return start_local.tz_convert("UTC"), end_local.tz_convert("UTC")


def fmt_local_datetime(value, fmt="%Y-%m-%d %H:%M:%S %Z"):
    parsed = _to_utc_timestamp(value)
    if pd.isna(parsed):
        return "—"
    return parsed.tz_convert(LOCAL_TIMEZONE).strftime(fmt)


def add_market_data_count(
    result: pd.DataFrame,
    measurements_df: pd.DataFrame,
    *,
    day_start_hour: int = 0,
    output_column: str = "MarketData",
) -> pd.DataFrame:
    if result.empty or measurements_df.empty:
        result[output_column] = 0
        return result

    work = measurements_df[
        measurements_df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

    work["timestamp"] = pd.to_datetime(
        work["timestamp"],
        utc=True,
    )

    local_timestamp = work["timestamp"].dt.tz_convert(
        LOCAL_TIMEZONE
    )

    # Count relative to the newest market-data timestamp, not the wall clock.
    # This keeps the operating day stable when the newest available bar is
    # before 03:00 local time. Example: newest data 01:45 belongs to the
    # operating day that started at 03:00 on the previous calendar day.
    reference_local = local_timestamp.max()

    day_start_local = (
        reference_local.normalize()
        + pd.Timedelta(hours=day_start_hour)
    )
    if reference_local < day_start_local:
        day_start_local -= pd.Timedelta(days=1)

    if day_start_hour == 0:
        work = work[
            local_timestamp.dt.date == reference_local.date()
        ].copy()
    else:
        work = work[
            (local_timestamp >= day_start_local)
            & (local_timestamp <= reference_local)
        ].copy()

    counts = (
        work.groupby("ticker")["timestamp"]
        .nunique()
    )

    result[output_column] = (
        result["Ticker"]
        .map(counts)
        .fillna(0)
        .astype(int)
    )

    return result


def trading_config_path() -> Path:
    primary = Path("/app/server/telegram_notifications.yaml")
    if primary.exists():
        return primary
    return Path(__file__).resolve().parents[1] / "server" / "telegram_notifications.yaml"


def load_trading_config() -> dict:
    path = trading_config_path()
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_trading_config(config: dict) -> tuple[Path, Path]:
    """Atomically persist dashboard-editable settings and keep a timestamped backup."""
    path = trading_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Settings file does not exist: {path}")
    if not os.access(path, os.W_OK):
        raise PermissionError(f"Settings file is not writable by the dashboard: {path}")

    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    return path, backup


def _valid_hhmm(value: str) -> bool:
    try:
        datetime.strptime(str(value), "%H:%M")
        return True
    except Exception:
        return False


def _valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(str(value))
        return True
    except Exception:
        return False


def watchlist_target_path() -> Path:
    """Return the collector/watchlist file configured by environment, or a safe default."""
    for name in ("ASSETS_CSV", "ASSET_CSV", "ZERO_ASSETS_CSV", "ZERO_CSV", "TICKERS_CSV"):
        raw = os.getenv(name)
        if raw:
            return Path(raw)
    return Path("/app/config/watchlist.csv")


def validate_watchlist_csv(uploaded_file) -> tuple[pd.DataFrame, bytes]:
    raw = uploaded_file.getvalue()
    frame = pd.read_csv(io.BytesIO(raw))
    ticker_column = next(
        (c for c in ("Ticker", "ticker", "TICKER", "Symbol", "symbol", "SYMBOL") if c in frame.columns),
        None,
    )
    if ticker_column is None:
        raise ValueError("CSV must contain a Ticker or Symbol column.")
    tickers = frame[ticker_column].astype("string").str.strip()
    if tickers.isna().any() or tickers.eq("").any():
        raise ValueError("Ticker/ Symbol values must not be empty.")
    normalized = tickers.str.upper()
    if normalized.duplicated().any():
        duplicates = sorted(normalized[normalized.duplicated(keep=False)].unique().tolist())
        raise ValueError("Duplicate tickers are not allowed: " + ", ".join(duplicates[:20]))
    if len(frame) > 5000:
        raise ValueError("Watchlist contains more than 5,000 rows; please upload a smaller list.")
    return frame, raw


def save_watchlist_bytes(raw: bytes) -> tuple[Path, Path | None]:
    target = watchlist_target_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not os.access(target, os.W_OK):
        raise PermissionError(f"Watchlist is not writable by the dashboard: {target}")
    if not target.exists() and not os.access(target.parent, os.W_OK):
        raise PermissionError(f"Watchlist directory is not writable by the dashboard: {target.parent}")

    backup = None
    if target.exists():
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.bak-{stamp}")
        backup.write_bytes(target.read_bytes())

    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, target)
    return target, backup


def load_market_regions() -> dict[str, str]:
    path = Path("/app/config/instruments.json")
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / "config" / "instruments.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result = {}
    for row in rows if isinstance(rows, list) else []:
        ticker = str(row.get("Ticker") or row.get("ticker") or "").strip().upper()
        region = str(row.get("MarketRegion") or "").strip().upper()
        isin = str(row.get("ISIN") or "").strip().upper()
        if not region:
            if isin.startswith("US"):
                region = "US"
            elif isin.startswith("DE"):
                region = "DE"
        if ticker and region:
            result[ticker] = region
    return result


def load_configured_ticker_sources() -> dict[str, set[str]]:
    # Map configured ticker symbols to their collector source/system.
    project_root = Path(__file__).resolve().parents[1]

    ticker_files = {
        "tickers.json": "polygon",
        "crypto_tickers.json": "crypto",
        "international_tickers.json": "international",
    }

    result: dict[str, set[str]] = {}

    for file_name, source_name in ticker_files.items():
        candidate = Path("/app/config") / file_name
        if not candidate.exists():
            candidate = project_root / "config" / file_name
        if not candidate.exists() or not candidate.is_file():
            continue

        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(payload, dict):
            rows = payload.get("tickers") or payload.get("symbols") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        for row in rows:
            if isinstance(row, str):
                ticker = row
            elif isinstance(row, dict):
                ticker = (
                    row.get("ticker")
                    or row.get("Ticker")
                    or row.get("zero_ticker")
                    or row.get("symbol")
                    or row.get("Symbol")
                    or ""
                )
            else:
                ticker = ""

            ticker = str(ticker).strip().upper()
            if ticker:
                result.setdefault(ticker, set()).add(source_name)

    return result


TRADING_CONFIG = load_trading_config()
TRADING_WINDOWS = TRADING_CONFIG.get("trading_windows") or {}
MARKET_REGIONS = load_market_regions()
CONFIGURED_TICKER_SOURCES = load_configured_ticker_sources()
BUY_CONFIG = TRADING_CONFIG.get("buy") or {}
BUY_MIN_CLOSEB_COUNT = int(BUY_CONFIG.get("minimum_closeb_count", BUY_CONFIG.get("minimum_closeb_ge2_count", 6)))
BUY_MIN_CLOSEB_PERCENT = float(BUY_CONFIG.get("minimum_closeb_percent", 2.0))
BUY_MAX_OPEN_TICKERS = max(1, int(BUY_CONFIG.get("max_open_tickers", 10)))
SELL_CONFIG = TRADING_CONFIG.get("sell") or {}

# Display-only market phases used by Last Data. They intentionally do not
# change C1/C3 or the configured BUY/SELL windows. Times are in each market's
# local timezone. Optional trading_phases entries in telegram_notifications.yaml
# can override these defaults without changing the dashboard code.
DEFAULT_TRADING_PHASES = {
    "US": {
        "timezone": "America/New_York",
        "pre_start": "04:00",
        "opening_start": "09:30",
        "opening_end": "16:00",
        "post_end": "20:00",
    },
    "DE": {
        "timezone": "Europe/Berlin",
        "pre_start": "08:00",
        "opening_start": "09:00",
        "opening_end": "17:30",
        "post_end": "22:00",
    },
    "CRYPTO": {
        "timezone": "UTC",
        "pre_start": "00:00",
        "opening_start": "00:00",
        "opening_end": "23:59",
        "post_end": "23:59",
    },
}
TRADING_PHASES = TRADING_CONFIG.get("trading_phases") or {}


def c5_phase_config(market_region: str | None) -> dict:
    phase_config = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    phase_config.update(TRADING_PHASES.get(market_region) or {})
    return phase_config


def dashboard_status_summary(measurements_df: pd.DataFrame, alerts_table: pd.DataFrame) -> dict:
    """Return global Healthy and Market indicator states used beside the title."""
    result = {
        "healthy": False,
        "market": False,
        "collectors_ok": False,
        "settings_ok": False,
        "alerts_ok": False,
        "collector_bad": [],
        "market_bad": [],
        "market_stale": [],
        "market_offline": [],
        "market_state": "OFFLINE",
        "open_alerts": 0,
    }

    # The simulator in this dashboard uses these loaded configuration sections.
    result["settings_ok"] = bool(TRADING_WINDOWS and BUY_CONFIG and SELL_CONFIG)

    if alerts_table.empty:
        open_alerts = 0
    elif "acknowledged" in alerts_table.columns:
        acknowledged = alerts_table["acknowledged"].fillna(False).astype(bool)
        open_alerts = int((~acknowledged).sum())
    else:
        open_alerts = int(len(alerts_table))

    result["open_alerts"] = open_alerts
    result["alerts_ok"] = open_alerts == 0

    if measurements_df.empty or "system" not in measurements_df.columns:
        return result

    work = measurements_df.copy()
    work["timestamp"] = pd.to_datetime(work.get("timestamp"), utc=True, errors="coerce")
    work["received_at"] = pd.to_datetime(work.get("received_at"), utc=True, errors="coerce")

    now = pd.Timestamp.now(tz="UTC")
    latest = (
        work.groupby("system")
        .agg(
            last_market_time=("timestamp", "max"),
            last_received_time=("received_at", "max"),
        )
        .reset_index()
    )

    # Keep every known system in the global status calculation. A system
    # with old data must become STALE/OFFLINE rather than disappear, otherwise
    # Healthy/Market can incorrectly become green on weekends or outages.
    if latest.empty:
        return result

    latest["collector_age_minutes"] = (
        (now - latest["last_received_time"]).dt.total_seconds() / 60
    )
    latest["market_age_minutes"] = (
        (now - latest["last_market_time"]).dt.total_seconds() / 60
    )

    latest["CollectorStatus"] = latest["collector_age_minutes"].apply(
        lambda age: "OK" if pd.notna(age) and age <= 30 else (
            "STALE" if pd.notna(age) and age <= 120 else "OFFLINE"
        )
    )
    latest["MarketStatus"] = latest["market_age_minutes"].apply(
        lambda age: "OK" if pd.notna(age) and age <= 30 else (
            "STALE" if pd.notna(age) and age <= 120 else "OFFLINE"
        )
    )

    # Crypto and Massive are intentionally excluded from the dashboard
    # Healthy/Market indicators.
    #
    # Massive may still be shown as STALE/OFFLINE on the System Health page,
    # but its provider/system status must not control the two global badges.
    excluded_status_systems = {"crypto", "massive"}
    status_latest = latest[
        ~latest["system"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(excluded_status_systems)
    ].copy()

    result["collector_bad"] = status_latest.loc[
        status_latest["CollectorStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["market_bad"] = status_latest.loc[
        status_latest["MarketStatus"] != "OK", "system"
    ].astype(str).tolist()
    result["market_stale"] = status_latest.loc[
        status_latest["MarketStatus"] == "STALE", "system"
    ].astype(str).tolist()
    result["market_offline"] = status_latest.loc[
        status_latest["MarketStatus"] == "OFFLINE", "system"
    ].astype(str).tolist()

    result["collectors_ok"] = not result["collector_bad"]
    result["market"] = not result["market_bad"]

    if result["market_offline"]:
        result["market_state"] = "OFFLINE"
    elif result["market_stale"]:
        result["market_state"] = "STALE"
    elif not status_latest.empty:
        result["market_state"] = "OK"
    result["healthy"] = (
        result["collectors_ok"]
        and result["settings_ok"]
        and result["alerts_ok"]
    )
    return result


def render_dashboard_title(measurements_df: pd.DataFrame, alerts_table: pd.DataFrame) -> None:
    status = dashboard_status_summary(measurements_df, alerts_table)
    healthy_color = "#21c55d" if status["healthy"] else "#ef4444"

    market_color = {
        "OK": "#21c55d",
        "STALE": "#f59e0b",
        "OFFLINE": "#ef4444",
    }.get(status.get("market_state"), "#ef4444")

    healthy_parts = []
    if not status["collectors_ok"]:
        healthy_parts.append(
            "Collectors not OK: " + (", ".join(status["collector_bad"]) or "none reporting")
        )
    if not status["settings_ok"]:
        healthy_parts.append("Simulator settings/configuration are incomplete")
    if not status["alerts_ok"]:
        healthy_parts.append(f"Open alerts: {status['open_alerts']}")
    healthy_tip = "Healthy" if not healthy_parts else "; ".join(healthy_parts)

    if status.get("market_state") == "OK":
        market_tip = "All relevant systems have MarketStatus OK"
    elif status.get("market_state") == "STALE":
        market_tip = (
            "MarketStatus STALE: "
            + (", ".join(status["market_stale"]) or "unknown system")
        )
    else:
        market_tip = (
            "MarketStatus OFFLINE: "
            + (", ".join(status["market_offline"]) or ", ".join(status["market_bad"]) or "no relevant systems")
        )

    html = f"""
        <div style="display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin-bottom:0.5rem;">
          <h1 style="margin:0;">🏠 Home Monitor</h1>
          <div title="{healthy_tip}" style="display:flex;align-items:center;gap:7px;font-size:1.05rem;font-weight:600;">
            <span style="width:14px;height:14px;border-radius:50%;background:{healthy_color};display:inline-block;box-shadow:0 0 0 2px rgba(128,128,128,0.18);"></span>
            <span>Healthy</span>
          </div>
          <div title="{market_tip}" style="display:flex;align-items:center;gap:7px;font-size:1.05rem;font-weight:600;">
            <span style="width:14px;height:14px;border-radius:50%;background:{market_color};display:inline-block;box-shadow:0 0 0 2px rgba(128,128,128,0.18);"></span>
            <span>Market</span>
          </div>
        </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def market_region_for_ticker(ticker: str, asset_type: str) -> str | None:
    asset_type_normalized = str(asset_type).strip().lower()
    if asset_type_normalized == "crypto":
        return "CRYPTO"

    configured_region = MARKET_REGIONS.get(str(ticker).upper())
    if configured_region:
        return configured_region

    # Current stock collector is Polygon/Massive US equities. Some tickers
    # are not present in the instrument-region mapping; treating them as
    # Closed gives a false phase. Explicit mappings still win.
    if asset_type_normalized == "stock":
        return "US"

    return None


def _hhmm_to_minutes(value: str) -> int:
    hour_text, minute_text = str(value).strip().split(":", 1)
    return int(hour_text) * 60 + int(minute_text)


def historical_trading_window_start(
    latest_time,
    trading_duration: pd.Timedelta,
    market_region: str | None,
):
    """Return the UTC start after counting only configured trading-session time.

    This is used for the short Historical Data ranges (2h/6h/12h). Time
    between ``post_end`` and the next ``pre_start`` is skipped, as are
    configured weekends and closed dates. A session whose ``post_end`` is
    earlier than/equal to ``pre_start`` is treated as crossing midnight.
    """
    latest = pd.to_datetime(latest_time, utc=True, errors="coerce")
    if pd.isna(latest):
        return pd.NaT

    remaining = pd.Timedelta(trading_duration)
    if remaining <= pd.Timedelta(0):
        return latest

    market_config = TRADING_WINDOWS.get(market_region) or {}
    phase_config = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    phase_config.update(TRADING_PHASES.get(market_region) or {})

    if not phase_config:
        return latest - remaining

    tz_name = str(
        phase_config.get("timezone")
        or market_config.get("timezone")
        or "UTC"
    )
    market_tz = ZoneInfo(tz_name)
    pre_start_minute = _hhmm_to_minutes(phase_config.get("pre_start", "00:00"))
    post_end_minute = _hhmm_to_minutes(phase_config.get("post_end", "23:59"))

    raw_weekdays = market_config.get("open_weekdays")
    allowed_weekdays = (
        {"mon", "tue", "wed", "thu", "fri"}
        if raw_weekdays is None
        else {str(value).strip().lower()[:3] for value in raw_weekdays}
    )
    closed_dates = {
        str(value)
        for value in (market_config.get("closed_dates") or [])
    }

    cursor = latest
    session_date = cursor.tz_convert(market_tz).date()

    # A 12-hour window normally needs at most a few sessions. Keep a generous
    # bound so unusual holiday configurations cannot create an endless loop.
    for _ in range(370):
        day_start = pd.Timestamp(session_date, tz=market_tz)
        weekday = day_start.strftime("%a").lower()[:3]
        date_key = day_start.strftime("%Y-%m-%d")

        if weekday in allowed_weekdays and date_key not in closed_dates:
            session_start_local = day_start + pd.Timedelta(minutes=pre_start_minute)
            session_end_local = day_start + pd.Timedelta(minutes=post_end_minute)
            if post_end_minute <= pre_start_minute:
                session_end_local += pd.Timedelta(days=1)

            session_start = session_start_local.tz_convert("UTC")
            session_end = session_end_local.tz_convert("UTC")
            usable_end = min(cursor, session_end)

            if usable_end > session_start:
                usable = usable_end - session_start
                if remaining <= usable:
                    return usable_end - remaining
                remaining -= usable

            cursor = min(cursor, session_start)

        session_date -= pd.Timedelta(days=1)

    # Defensive fallback for a pathological configuration with no open days.
    return latest - trading_duration


def _format_hhmm_duration(delta: pd.Timedelta | None) -> str:
    if delta is None or pd.isna(delta) or delta < pd.Timedelta(0):
        return "-"
    total_minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def effective_trading_duration(
    start_time,
    end_time,
    market_region: str | None,
) -> pd.Timedelta:
    """Elapsed market-open time between two UTC timestamps.

    Closed overnight intervals, weekends, and configured closed dates do not
    contribute. This is the duration basis used by Last Data's DropDur2% and
    ChangeDur2% values. Crypto stays continuous.
    """
    start = pd.to_datetime(start_time, utc=True, errors="coerce")
    end = pd.to_datetime(end_time, utc=True, errors="coerce")
    if pd.isna(start) or pd.isna(end) or end <= start:
        return pd.Timedelta(0)

    if market_region == "CRYPTO":
        return end - start

    market_config = TRADING_WINDOWS.get(market_region) or {}
    phase_config = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    phase_config.update(TRADING_PHASES.get(market_region) or {})
    if not phase_config:
        return end - start

    tz_name = str(
        phase_config.get("timezone")
        or market_config.get("timezone")
        or "UTC"
    )
    market_tz = ZoneInfo(tz_name)
    pre_start_minute = _hhmm_to_minutes(phase_config.get("pre_start", "00:00"))
    post_end_minute = _hhmm_to_minutes(phase_config.get("post_end", "23:59"))

    first_date = start.tz_convert(market_tz).date() - pd.Timedelta(days=1)
    last_date = end.tz_convert(market_tz).date()
    total = pd.Timedelta(0)

    session_date = first_date
    while session_date <= last_date:
        if hasattr(session_date, "date"):
            session_date = session_date.date()

        if _allowed_market_date(session_date, market_config):
            day_start = pd.Timestamp(session_date, tz=market_tz)
            session_start_local = day_start + pd.Timedelta(minutes=pre_start_minute)
            session_end_local = day_start + pd.Timedelta(minutes=post_end_minute)
            if post_end_minute <= pre_start_minute:
                session_end_local += pd.Timedelta(days=1)

            session_start = session_start_local.tz_convert("UTC")
            session_end = session_end_local.tz_convert("UTC")
            overlap_start = max(start, session_start)
            overlap_end = min(end, session_end)
            if overlap_end > overlap_start:
                total += overlap_end - overlap_start

        session_date = session_date + pd.Timedelta(days=1)

    return total


def historical_market_rangebreaks(
    start_time,
    end_time,
    market_region: str | None,
) -> list[dict]:
    """Plotly range breaks for every closed market interval in a chart span."""
    start = pd.to_datetime(start_time, utc=True, errors="coerce")
    end = pd.to_datetime(end_time, utc=True, errors="coerce")
    if (
        pd.isna(start)
        or pd.isna(end)
        or end <= start
        or market_region == "CRYPTO"
    ):
        return []

    market_config = TRADING_WINDOWS.get(market_region) or {}
    phase_config = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    phase_config.update(TRADING_PHASES.get(market_region) or {})
    if not phase_config:
        return []

    tz_name = str(
        phase_config.get("timezone")
        or market_config.get("timezone")
        or "UTC"
    )
    market_tz = ZoneInfo(tz_name)
    pre_start_minute = _hhmm_to_minutes(phase_config.get("pre_start", "00:00"))
    post_end_minute = _hhmm_to_minutes(phase_config.get("post_end", "23:59"))

    sessions = []
    session_date = start.tz_convert(market_tz).date() - pd.Timedelta(days=1)
    final_date = end.tz_convert(market_tz).date() + pd.Timedelta(days=1)

    while session_date <= final_date:
        if hasattr(session_date, "date"):
            session_date = session_date.date()

        if _allowed_market_date(session_date, market_config):
            day_start = pd.Timestamp(session_date, tz=market_tz)
            session_start_local = day_start + pd.Timedelta(minutes=pre_start_minute)
            session_end_local = day_start + pd.Timedelta(minutes=post_end_minute)
            if post_end_minute <= pre_start_minute:
                session_end_local += pd.Timedelta(days=1)

            session_start = session_start_local.tz_convert("UTC")
            session_end = session_end_local.tz_convert("UTC")
            if session_end > start and session_start < end:
                sessions.append((max(start, session_start), min(end, session_end)))

        session_date = session_date + pd.Timedelta(days=1)

    sessions.sort(key=lambda item: item[0])
    breaks = []
    cursor = start
    for session_start, session_end in sessions:
        if session_start > cursor:
            gap_start = cursor
            gap_end = min(session_start, end)
            if gap_end > gap_start:
                breaks.append((gap_start, gap_end))
        cursor = max(cursor, session_end)
        if cursor >= end:
            break

    if cursor < end:
        breaks.append((cursor, end))

    result = []
    for gap_start, gap_end in breaks:
        gap_ms = int((gap_end - gap_start).total_seconds() * 1000)
        if gap_ms <= 0:
            continue
        result.append(
            {
                "values": [gap_start.tz_convert(LOCAL_TIMEZONE).to_pydatetime()],
                "dvalue": gap_ms,
            }
        )
    return result


def _allowed_market_date(local_date, market_config: dict | None) -> bool:
    if not market_config or not bool(market_config.get("enabled", True)):
        return False
    weekdays = market_config.get("open_weekdays")
    if weekdays:
        allowed = {str(value).strip().lower()[:3] for value in weekdays}
        if local_date.strftime("%a").lower()[:3] not in allowed:
            return False
    closed = {str(value) for value in (market_config.get("closed_dates") or [])}
    return local_date.isoformat() not in closed


def market_phase_info(timestamp, market_region: str | None) -> tuple[str, str, str]:
    """Return Phase, WaitToTrade, WaitToOpening for one LastCollect time."""
    if not market_region:
        return "Closed", "-", "-"

    market_config = TRADING_WINDOWS.get(market_region) or {}
    phase_config = dict(DEFAULT_TRADING_PHASES.get(market_region) or {})
    phase_config.update(TRADING_PHASES.get(market_region) or {})
    if not phase_config:
        return "Closed", "-", "-"

    tz_name = str(phase_config.get("timezone") or market_config.get("timezone") or "UTC")
    ts_utc = pd.Timestamp(timestamp)
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.tz_localize("UTC")
    else:
        ts_utc = ts_utc.tz_convert("UTC")
    local_ts = ts_utc.tz_convert(tz_name)

    pre_start = _hhmm_to_minutes(phase_config.get("pre_start", "00:00"))
    opening_start = _hhmm_to_minutes(phase_config.get("opening_start", "00:00"))
    opening_end = _hhmm_to_minutes(phase_config.get("opening_end", "23:59"))
    post_end = _hhmm_to_minutes(phase_config.get("post_end", "23:59"))
    minute_of_day = local_ts.hour * 60 + local_ts.minute
    allowed_today = _allowed_market_date(local_ts.date(), market_config)

    phase = "Closed"
    if allowed_today:
        if pre_start <= minute_of_day < opening_start:
            phase = "Pre-Trading"
        elif opening_start <= minute_of_day < opening_end:
            phase = "Opening"
        elif opening_end <= minute_of_day < post_end:
            phase = "Post-Trading"

    def next_start(target_minutes: int, zero_phases: set[str]) -> str:
        if phase in zero_phases:
            return "00:00"
        base_date = local_ts.date()
        for day_offset in range(0, 15):
            candidate_date = base_date + pd.Timedelta(days=day_offset)
            # pd.Timedelta added to date can return date-like Timestamp; normalize.
            if hasattr(candidate_date, "date"):
                candidate_date = candidate_date.date()
            if not _allowed_market_date(candidate_date, market_config):
                continue
            candidate = pd.Timestamp(candidate_date, tz=tz_name) + pd.Timedelta(minutes=target_minutes)
            if candidate > local_ts:
                return _format_hhmm_duration(candidate - local_ts)
        return "-"

    wait_to_trade = next_start(
        pre_start, {"Pre-Trading", "Opening", "Post-Trading"}
    )
    wait_to_opening = next_start(opening_start, {"Opening"})
    return phase, wait_to_trade, wait_to_opening


def movement_durations(
    ticker_df: pd.DataFrame,
    latest_time,
    current_price,
    movement_percent: float,
    market_region: str | None,
) -> tuple[str, str]:
    """Trading-time durations backwards from LastCollect using the current price band."""
    if pd.isna(current_price) or float(current_price) <= 0:
        return "-", "-"

    history = ticker_df[ticker_df["timestamp"] <= latest_time].copy()
    history["_close_num"] = pd.to_numeric(history["close"], errors="coerce")
    history = history.dropna(subset=["_close_num", "timestamp"]).sort_values("timestamp")
    if history.empty:
        return "-", "-"

    current = float(current_price)
    fraction = float(movement_percent) / 100.0
    upper = current * (1.0 + fraction)
    lower = current * (1.0 - fraction)
    last_ts = pd.Timestamp(latest_time)

    # DropDuration = time since the most recent sample that was still more than
    # movement_percent above LastPrice. From the following sample through
    # LastCollect, price has not been higher than that threshold.
    above = history[history["_close_num"] > upper]
    drop_duration = "-"
    if not above.empty:
        drop_duration = _format_hhmm_duration(
            effective_trading_duration(
                above.iloc[-1]["timestamp"],
                last_ts,
                market_region,
            )
        )

    # StaticDuration = time since the most recent sample outside the symmetric
    # +/- movement_percent band around LastPrice. From the following sample
    # through LastCollect, price has remained inside the band.
    outside = history[(history["_close_num"] < lower) | (history["_close_num"] > upper)]
    static_duration = "-"
    if not outside.empty:
        static_duration = _format_hhmm_duration(
            effective_trading_duration(
                outside.iloc[-1]["timestamp"],
                last_ts,
                market_region,
            )
        )

    return drop_duration, static_duration


def format_local_timestamp(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return (
        pd.to_datetime(value, utc=True)
        .tz_convert(LOCAL_TIMEZONE)
        .strftime("%Y-%m-%d %H:%M")
    )


def build_trade_analysis(
    measurements_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    reference_time,
    period,
) -> pd.DataFrame:
    # Build Effective Trading counters on a common 15-minute timeline.
    if measurements_df.empty:
        return pd.DataFrame()

    reference_time = pd.to_datetime(reference_time, utc=True, errors="coerce")
    if pd.isna(reference_time):
        return pd.DataFrame()

    start_time = reference_time - period
    tolerance = pd.Timedelta(minutes=30)
    history_start = start_time - pd.Timedelta(hours=2) - tolerance

    wanted_columns = ["ticker", "timestamp", "close", "id", "asset_type", "eur_usd"]
    available_columns = [c for c in wanted_columns if c in measurements_df.columns]
    market = measurements_df[
        measurements_df["asset_type"].isin(["stock", "crypto"])
    ][available_columns].copy()

    if market.empty:
        return pd.DataFrame()
    if "eur_usd" not in market.columns:
        market["eur_usd"] = pd.NA
    if "asset_type" not in market.columns:
        market["asset_type"] = "stock"
    if "id" not in market.columns:
        market["id"] = range(len(market))

    market["timestamp"] = pd.to_datetime(market["timestamp"], utc=True, errors="coerce")
    market["close"] = pd.to_numeric(market["close"], errors="coerce")
    market["eur_usd"] = pd.to_numeric(market["eur_usd"], errors="coerce")
    market = market[
        market["timestamp"].notna()
        & market["ticker"].notna()
        & market["close"].notna()
        & (market["timestamp"] >= history_start)
        & (market["timestamp"] <= reference_time)
    ].copy()
    if market.empty:
        return pd.DataFrame()

    market["ticker"] = market["ticker"].astype(str).str.strip().str.upper()
    market["timepoint"] = market["timestamp"].dt.round("15min")
    current = (
        market[market["timepoint"] >= start_time]
        .sort_values(["ticker", "timepoint", "timestamp", "id"])
        .drop_duplicates(subset=["ticker", "timepoint"], keep="last")
        .copy()
    )
    if current.empty:
        return pd.DataFrame()

    all_timepoints = pd.date_range(
        start=start_time.ceil("15min"),
        end=reference_time.floor("15min"),
        freq="15min",
        tz="UTC",
    )
    if len(all_timepoints) == 0:
        return pd.DataFrame()

    closeb_parts = []
    for ticker, ticker_current in current.groupby("ticker", sort=False):
        history = market[market["ticker"] == ticker][["timestamp", "close"]].sort_values("timestamp")
        if history.empty:
            continue
        work = ticker_current[["ticker", "timepoint", "close"]].copy()
        work = work.rename(columns={"close": "current_price"})
        work["baseline_target"] = work["timepoint"] - pd.Timedelta(hours=2)
        work = work.sort_values("baseline_target")
        hist = history.rename(columns={"timestamp": "baseline_timestamp", "close": "baseline_price"}).sort_values("baseline_timestamp")

        backward = pd.merge_asof(
            work,
            hist,
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="backward",
            tolerance=tolerance,
        ).rename(columns={"baseline_timestamp": "backward_timestamp", "baseline_price": "backward_price"})
        forward = pd.merge_asof(
            work,
            hist,
            left_on="baseline_target",
            right_on="baseline_timestamp",
            direction="forward",
            tolerance=tolerance,
        ).rename(columns={"baseline_timestamp": "forward_timestamp", "baseline_price": "forward_price"})

        matched = backward[["ticker", "timepoint", "current_price", "baseline_target", "backward_timestamp", "backward_price"]].copy()
        matched["forward_timestamp"] = pd.to_datetime(forward["forward_timestamp"].reset_index(drop=True), utc=True, errors="coerce")
        matched["forward_price"] = forward["forward_price"].reset_index(drop=True)
        for column in ["baseline_target", "backward_timestamp", "forward_timestamp"]:
            matched[column] = pd.to_datetime(matched[column], utc=True, errors="coerce")

        backward_distance = (matched["baseline_target"] - matched["backward_timestamp"]).abs()
        forward_distance = (matched["forward_timestamp"] - matched["baseline_target"]).abs()
        use_backward = matched["backward_timestamp"].notna() & (
            matched["forward_timestamp"].isna() | (backward_distance <= forward_distance)
        )
        matched["baseline_price"] = matched["forward_price"]
        matched.loc[use_backward, "baseline_price"] = matched.loc[use_backward, "backward_price"]
        matched = matched[matched["baseline_price"].notna() & (matched["baseline_price"] > 0)].copy()
        if matched.empty:
            continue
        matched["CloseB"] = (matched["current_price"] / matched["baseline_price"] - 1.0) * 100.0
        closeb_parts.append(matched[["ticker", "timepoint", "CloseB"]])

    closeb_df = pd.concat(closeb_parts, ignore_index=True) if closeb_parts else pd.DataFrame(columns=["ticker", "timepoint", "CloseB"])

    current["PriceEUR"] = pd.NA
    stock_mask = (
        current["asset_type"].astype(str).str.lower().eq("stock")
        & current["close"].notna()
        & current["eur_usd"].notna()
        & (current["eur_usd"] > 0)
    )
    current.loc[stock_mask, "PriceEUR"] = current.loc[stock_mask, "close"] / current.loc[stock_mask, "eur_usd"]
    crypto_mask = current["asset_type"].astype(str).str.lower().eq("crypto") & current["close"].notna()
    current.loc[crypto_mask, "PriceEUR"] = current.loc[crypto_mask, "close"]
    # Use the latest price known at or before every 15-minute timepoint.
    # Do not require an exact bar at the timepoint: stock markets can be closed
    # for many hours/weekends while simulator positions remain OPEN.
    price_columns = [
        c
        for c in ["ticker", "timestamp", "close", "asset_type", "eur_usd"]
        if c in measurements_df.columns
    ]
    price_history = measurements_df[
        measurements_df["asset_type"].isin(["stock", "crypto"])
    ][price_columns].copy()

    price_history["timestamp"] = pd.to_datetime(
        price_history["timestamp"], utc=True, errors="coerce"
    )
    price_history["close"] = pd.to_numeric(
        price_history["close"], errors="coerce"
    )
    if "eur_usd" not in price_history.columns:
        price_history["eur_usd"] = pd.NA
    price_history["eur_usd"] = pd.to_numeric(
        price_history["eur_usd"], errors="coerce"
    )
    price_history["ticker"] = (
        price_history["ticker"].astype(str).str.strip().str.upper()
    )
    price_history = price_history[
        price_history["timestamp"].notna()
        & price_history["close"].notna()
        & (price_history["timestamp"] <= reference_time)
    ].copy()

    # Match Sim-Trading / Last Data: stored stock and crypto closes are
    # USD-denominated, so use EUR/USD when it is available.
    price_history["PriceEUR"] = price_history["close"]
    fx_mask = (
        price_history["eur_usd"].notna()
        & (price_history["eur_usd"] > 0)
    )
    price_history.loc[fx_mask, "PriceEUR"] = (
        price_history.loc[fx_mask, "close"]
        / price_history.loc[fx_mask, "eur_usd"]
    )

    current_price_lookup = {}
    timeline = pd.DataFrame({"timepoint": all_timepoints})

    for ticker, ticker_prices in price_history.groupby("ticker", sort=False):
        ticker_prices = (
            ticker_prices[["timestamp", "PriceEUR"]]
            .dropna(subset=["timestamp", "PriceEUR"])
            .sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")
            .rename(columns={"timestamp": "price_timestamp"})
        )
        if ticker_prices.empty:
            continue

        matched_prices = pd.merge_asof(
            timeline,
            ticker_prices,
            left_on="timepoint",
            right_on="price_timestamp",
            direction="backward",
        )

        for row in matched_prices.itertuples(index=False):
            if pd.notna(row.PriceEUR):
                current_price_lookup[
                    (ticker, row.timepoint)
                ] = row.PriceEUR

    trades = trades_df.copy()
    if trades.empty:
        trades = pd.DataFrame(columns=["Ticker", "BuyTime", "SellTime", "BuyPriceEUR"])
    else:
        for column in ["BuyTime", "SellTime"]:
            if column not in trades.columns:
                trades[column] = pd.NaT
            trades[column] = pd.to_datetime(trades[column], utc=True, errors="coerce")
        if "Ticker" not in trades.columns:
            trades["Ticker"] = ""
        trades["Ticker"] = trades["Ticker"].astype(str).str.strip().str.upper()
        if "BuyPriceEUR" not in trades.columns:
            trades["BuyPriceEUR"] = pd.NA
        trades["BuyPriceEUR"] = pd.to_numeric(trades["BuyPriceEUR"], errors="coerce")

    buy_map = {tp: set() for tp in all_timepoints}
    sell_map = {tp: set() for tp in all_timepoints}
    for _, trade in trades.iterrows():
        ticker = str(trade.get("Ticker") or "").strip().upper()
        buy_time = trade.get("BuyTime")
        sell_time = trade.get("SellTime")
        if ticker and pd.notna(buy_time):
            buy_point = pd.Timestamp(buy_time).round("15min")
            if buy_point in buy_map:
                buy_map[buy_point].add(ticker)
        if ticker and pd.notna(sell_time):
            sell_point = pd.Timestamp(sell_time).round("15min")
            if sell_point in sell_map:
                sell_map[sell_point].add(ticker)

    result_rows = []
    for timepoint in all_timepoints:
        closeb_at_time = closeb_df[closeb_df["timepoint"] == timepoint]
        close2h_ge_2 = set(
            closeb_at_time.loc[
                closeb_at_time["CloseB"] >= 2.0,
                "ticker",
            ].astype(str)
        )

        for threshold in [2.0, 1.0]:
            tickers = sorted(
                set(
                    closeb_at_time.loc[
                        closeb_at_time["CloseB"] >= threshold,
                        "ticker",
                    ].astype(str)
                )
            )
            series_name = (
                "Close2h >= 2%"
                if threshold == 2.0
                else f"CloseB >= {threshold:g}%"
            )
            result_rows.append({
                "Time": timepoint,
                "Series": series_name,
                "Count": len(tickers),
                "Tickers": ", ".join(tickers) if tickers else "—",
            })

        open_tickers = set()
        profitable_open_tickers = set()
        closed_tickers = set()

        for ticker, ticker_trades in trades.groupby("Ticker", sort=False):
            ticker = str(ticker or "").strip().upper()
            if not ticker:
                continue

            eligible = ticker_trades[
                ticker_trades["BuyTime"].notna()
                & (ticker_trades["BuyTime"] <= timepoint)
            ].sort_values("BuyTime")

            if eligible.empty:
                continue

            latest_trade = eligible.iloc[-1]
            buy_time = latest_trade.get("BuyTime")
            sell_time = latest_trade.get("SellTime")
            buy_price = latest_trade.get("BuyPriceEUR")

            is_open = (
                pd.notna(buy_time)
                and timepoint >= buy_time
                and (pd.isna(sell_time) or timepoint < sell_time)
            )

            if is_open:
                open_tickers.add(ticker)
                current_price = current_price_lookup.get((ticker, timepoint))
                if (
                    pd.notna(buy_price)
                    and float(buy_price) > 0
                    and pd.notna(current_price)
                    and (
                        (float(current_price) / float(buy_price) - 1.0) * 100.0
                        >= 2.0
                    )
                ):
                    profitable_open_tickers.add(ticker)
            elif pd.notna(sell_time) and timepoint >= sell_time:
                closed_tickers.add(ticker)

        loss_open_tickers = open_tickers - profitable_open_tickers
        closed_close2h_ge_2 = closed_tickers & close2h_ge_2

        open_sorted = sorted(open_tickers)
        profit_sorted = sorted(profitable_open_tickers)
        loss_sorted = sorted(loss_open_tickers)
        closed_close2h_sorted = sorted(closed_close2h_ge_2)
        buy_sorted = sorted(buy_map.get(timepoint, set()))
        sell_sorted = sorted(sell_map.get(timepoint, set()))

        for series, tickers in [
            ("OPEN", open_sorted),
            ("OPEN & Profit", profit_sorted),
            ("OPEN & Loss", loss_sorted),
            ("CLOSED", sorted(closed_tickers)),
            ("CLOSED & Close2h>=2%", closed_close2h_sorted),
            ("BUY", buy_sorted),
            ("SELL", sell_sorted),
        ]:
            result_rows.append({
                "Time": timepoint,
                "Series": series,
                "Count": len(tickers),
                "Tickers": ", ".join(tickers) if tickers else "—",
            })

    return pd.DataFrame(result_rows)


@st.cache_data(ttl=300)
def build_trade_analysis_cached(
    _measurements_df: pd.DataFrame,
    _trades_df: pd.DataFrame,
    reference_time,
    period,
) -> pd.DataFrame:
    return build_trade_analysis(
        measurements_df=_measurements_df,
        trades_df=_trades_df,
        reference_time=reference_time,
        period=period,
    )

def build_live_overview(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()

    rows = []

    for ticker, ticker_df in data.groupby("ticker"):
        ticker_df = (
            ticker_df
            .sort_values(["timestamp", "id"])
            .drop_duplicates(
                subset=["timestamp"],
                keep="last",
            )
        )

        latest = ticker_df.iloc[-1]
        latest_time = latest["timestamp"]
        baseline_time = latest_time - pd.Timedelta(hours=2)

        price = latest.get("close")
        eur_usd = latest.get("eur_usd")

        sell_time_seconds = latest.get(
            "sell_time_seconds"
        )

        sell_time_over_seconds = latest.get(
            "sell_time_over_seconds"
        )

        buy_qty = None
        buy_value_eur = None
        price_eur = None

        if (
            pd.notna(price)
            and pd.notna(eur_usd)
            and float(price) > 0
            and float(eur_usd) > 0
        ):
            # Both stock and X:BTCUSD closes are USD-denominated in the stored
            # market records. LastPrice is therefore known at LastCollect when
            # the same record also contains the EUR/USD reference rate.
            price_eur = float(price) / float(eur_usd)

            if latest["asset_type"] == "stock":
                buy_qty = math.ceil(10000.0 / price_eur)
                buy_value_eur = buy_qty * price_eur

        row = {
            "Ticker": ticker,
            "TickerName": TICKER_NAMES.get(
                str(ticker).upper(),
                ticker,
            ),
            "Type": latest["asset_type"],
            "_timestamp": latest_time,
            #"Time": latest_time.strftime("%H:%M"),
            "Time": latest_time.tz_convert(
                 LOCAL_TIMEZONE
            ).strftime("%H:%M"),
            "Price": price_eur,
            "BuyQty": buy_qty,
            "BuyValueEUR": buy_value_eur,
            "SellTime": sell_time_seconds,
            "SellTimeOver": sell_time_over_seconds,
            "OpenB": None,
            "LowB": None,
            "HighB": None,
            "CloseB": None,
            "MarketRegion": market_region_for_ticker(ticker, latest["asset_type"]),            
            "CanBuy": False,
            "C1": False,
            "C2": False,
            "BuyInfo": "",

            "ShouldSell": False,
            "C4": False,
            "C5": False,
            "C4Check": "—",
            "C5Check": "—",
            "CanSellNow": False,
            "BoughtBefore": None,
            "SellTiming": "",
            "SellInfo": "",
            # Informational percentages exposed on the Advisor page.
            # These do not change the existing C4/C5 / ShouldSell decisions.
            "Drop": None,
            "Static": None,
        }

        market_config = TRADING_WINDOWS.get(row["MarketRegion"]) if row["MarketRegion"] else None
        if pd.notna(price) and float(price) > 0:
            decision = evaluate_sell_history(
                ticker_df=ticker_df,
                latest_time=latest_time,
                current_price=float(price),
                movement_percent=float(SELL_CONFIG.get("movement_percent", 1.1)),
                c5_hours=float(SELL_CONFIG.get("c5_hours", 24.0)),
                market_region=row["MarketRegion"],
                market_config=market_config,
                phase_config=c5_phase_config(row["MarketRegion"]),
            )
            row["ShouldSell"] = decision.should_sell
            row["C4"] = bool(decision.c4_satisfied)
            row["C5"] = bool(decision.c5_satisfied)
            row["BoughtBefore"] = decision.bought_before

            movement_percent = float(
                SELL_CONFIG.get("movement_percent", 1.1)
            )
            c5_hours = float(
                SELL_CONFIG.get("c5_hours", 24.0)
            )

            if (
                decision.max_time is not None
                and decision.max_price is not None
                and float(decision.max_price) > 0
            ):
                c4_drop = (
                    (float(decision.max_price) - float(price))
                    / float(decision.max_price)
                    * 100.0
                )
                row["C4Check"] = (
                    f"{'TRUE' if decision.c4_satisfied else 'FALSE'}: "
                    f"drop {c4_drop:.2f}% from peak "
                    f"{format_local_timestamp(decision.max_time)}; "
                    f"threshold > {movement_percent:.2f}%"
                )

            c5_start = trading_time_window_start(
                latest_time,
                c5_hours,
                market_region=row["MarketRegion"],
                market_config=market_config,
                phase_config=c5_phase_config(row["MarketRegion"]),
            )
            if decision.last_one_proc_time is not None:
                c5_last_outside = format_local_timestamp(
                    decision.last_one_proc_time
                )
            else:
                c5_last_outside = "none"

            row["C5Check"] = (
                f"{'TRUE' if decision.c5_satisfied else 'FALSE'}: "
                f"{c5_hours:g}h window from "
                f"{format_local_timestamp(c5_start)} to "
                f"{format_local_timestamp(latest_time)}; "
                f"last outside +/-{movement_percent:.2f}%: "
                f"{c5_last_outside}"
            )

            # Advisor numeric metrics for the same C4/C5 evaluation.
            # Drop is anchored to decision.max_time, i.e. the exact peak time
            # selected by the shared sell-decision logic. Static is the maximum
            # absolute price deviation from the current price inside the exact
            # C5 lookback window.
            history_for_sell = ticker_df[
                ticker_df["timestamp"] <= latest_time
            ].copy()
            history_for_sell["_close_num"] = pd.to_numeric(
                history_for_sell["close"], errors="coerce"
            )
            history_for_sell = history_for_sell.dropna(subset=["_close_num"])

            if pd.notna(decision.max_time) and float(price) > 0:
                try:
                    max_time_ts = pd.Timestamp(decision.max_time)
                    if max_time_ts.tzinfo is None:
                        max_time_ts = max_time_ts.tz_localize("UTC")
                    else:
                        max_time_ts = max_time_ts.tz_convert("UTC")

                    at_peak = history_for_sell[
                        history_for_sell["timestamp"] == max_time_ts
                    ]
                    if at_peak.empty and not history_for_sell.empty:
                        nearest_idx = (
                            history_for_sell["timestamp"] - max_time_ts
                        ).abs().idxmin()
                        peak_price = history_for_sell.loc[nearest_idx, "_close_num"]
                    else:
                        peak_price = at_peak.iloc[-1]["_close_num"]

                    if pd.notna(peak_price) and float(peak_price) > 0:
                        row["Drop"] = max(
                            0.0,
                            (float(peak_price) - float(price))
                            / float(peak_price)
                            * 100.0,
                        )
                except Exception:
                    pass

            c5_hours = float(SELL_CONFIG.get("c5_hours", 24.0))
            static_start = trading_time_window_start(
                latest_time,
                c5_hours,
                market_region=row["MarketRegion"],
                market_config=market_config,
                phase_config=c5_phase_config(row["MarketRegion"]),
            )
            static_history = history_for_sell[
                history_for_sell["timestamp"] >= static_start
            ]
            if not static_history.empty and float(price) > 0:
                deviations = (
                    (static_history["_close_num"] / float(price) - 1.0).abs()
                    * 100.0
                )
                if not deviations.empty:
                    row["Static"] = float(deviations.max())

            if market_config:
                sell_window = trading_window_info(
                    pd.Timestamp.now(tz="UTC"),
                    market_config,
                    "sell",
                )
                row["CanSellNow"] = sell_window.is_open
                if sell_window.is_open:
                    row["SellTiming"] = f"RemainingTime={format_duration(sell_window.remaining_time)}"
                else:
                    row["SellTiming"] = f"FirstNextSellTime={format_local_timestamp(sell_window.first_next_time)}"
            row["SellInfo"] = (
                f"C4={decision.c4_satisfied} (> {float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% drop from peak; MaxTime={format_local_timestamp(decision.max_time)}); "
                f"C5={decision.c5_satisfied} (full {float(SELL_CONFIG.get('c5_hours', 24.0)):g}h within +/-{float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% of current price; LastOneProcTime={format_local_timestamp(decision.last_one_proc_time)}); "
                f"{row['SellTiming']}"
            )

        candidate_rows = ticker_df[
            (
                ticker_df["timestamp"]
                >= baseline_time - pd.Timedelta(minutes=30)
            )
            &
            (
                ticker_df["timestamp"]
                <= baseline_time + pd.Timedelta(minutes=30)
            )
        ].copy()

        if candidate_rows.empty:
            rows.append(row)
            continue

        candidate_rows["baseline_distance"] = (
            candidate_rows["timestamp"] - baseline_time
        ).abs()

        baseline = (
            candidate_rows
            .sort_values(
                ["baseline_distance", "timestamp"],
                ascending=[True, True],
            )
            .iloc[0]
        )

        baseline_age = (
             baseline_time - baseline["timestamp"]
        ).total_seconds()
        if baseline_age > 1800:
            rows.append(row)
            continue

        baseline_close = baseline.get("close")

        if pd.isna(baseline_close) or baseline_close == 0:
            rows.append(row)
            continue

        window = ticker_df[
            (ticker_df["timestamp"] >= baseline_time)
            & (ticker_df["timestamp"] <= latest_time)
        ]

        row["OpenB"] = 0.0

        if "high" in window.columns:
            high_value = pd.to_numeric(
                window["high"],
                errors="coerce",
            ).max()

            if pd.notna(high_value):
                row["HighB"] = (
                    high_value / baseline_close - 1
                ) * 100

        if "low" in window.columns:
            low_value = pd.to_numeric(
                window["low"],
                errors="coerce",
            ).min()

            if pd.notna(low_value):
                row["LowB"] = (
                    low_value / baseline_close - 1
                ) * 100

        if pd.notna(price):
            row["CloseB"] = (
                price / baseline_close - 1
            ) * 100

        rows.append(row)

    result = pd.DataFrame(rows)

    if not result.empty:
        closeb_ge2_count = int(
            (pd.to_numeric(result["CloseB"], errors="coerce") >= BUY_MIN_CLOSEB_PERCENT).sum()
        )
        c2_breadth_satisfied = closeb_ge2_count >= BUY_MIN_CLOSEB_COUNT
        for index, current in result.iterrows():
            current_closeb = pd.to_numeric(
                current.get("CloseB"),
                errors="coerce",
            )
            c2_satisfied = bool(
                c2_breadth_satisfied
                and pd.notna(current_closeb)
                and float(current_closeb) >= BUY_MIN_CLOSEB_PERCENT
            )
            market_region = current.get("MarketRegion")
            market_config = TRADING_WINDOWS.get(market_region) if market_region else None
            c1_satisfied = False
            remaining_text = "—"
            if market_config:
                buy_window = trading_window_info(
                    current["_timestamp"],
                    market_config,
                    "buy",
                )
                c1_satisfied = buy_window.is_open
                if buy_window.is_open:
                    remaining_text = format_duration(buy_window.remaining_time)
            result.at[index, "C1"] = bool(c1_satisfied)
            result.at[index, "C2"] = bool(c2_satisfied)
            result.at[index, "CanBuy"] = bool(c1_satisfied and c2_satisfied)
            result.at[index, "BuyInfo"] = (
                f"C1={c1_satisfied} (RemainingTime={remaining_text}); "
                f"C2={c2_satisfied} (CloseB>={BUY_MIN_CLOSEB_PERCENT:g}%: {closeb_ge2_count}/{BUY_MIN_CLOSEB_COUNT})"
            )

        result = result.sort_values(
            by=["_timestamp", "CloseB"],
            ascending=[False, False],
            na_position="last",
        )
    return result

@st.cache_data(ttl=300)
def build_live_overview_cached(
    _data: pd.DataFrame,
    data_version,
    cache_scope: str = "default",
) -> pd.DataFrame:
    return build_live_overview(
        _data
    )

render_dashboard_title(df, alerts_df)

local_now = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
st.sidebar.caption(
    f"Last loaded: "
    f"{local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
)

def format_newest_data(frame: pd.DataFrame, column: str = "timestamp") -> str:
    """Format the newest timestamp for page summary rows in local time."""
    if frame is None or frame.empty or column not in frame.columns:
        return "—"
    newest = pd.to_datetime(frame[column], utc=True, errors="coerce").max()
    if pd.isna(newest):
        return "—"
    return newest.tz_convert(LOCAL_TIMEZONE).strftime("%H:%M")

def count_market_assets(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty or "ticker" not in frame.columns:
        return 0
    market = frame
    if "asset_type" in market.columns:
        market = market[market["asset_type"].isin(["stock", "crypto"])]
    return int(market["ticker"].dropna().astype(str).nunique())

def count_open_alerts(alerts_table: pd.DataFrame) -> int:
    if alerts_table is None or alerts_table.empty or "acknowledged" not in alerts_table.columns:
        return 0
    acknowledged = alerts_table["acknowledged"].fillna(False).astype(bool)
    return int((~acknowledged).sum())

if page == "Zero-Trading":
    st.header("Zero-Trading")

    # News is informational only. Failure to load it must never prevent
    # Zero-Trading or any trading-rule calculation from working.
    zero_news_map = latest_ticker_news_map(
        load_ticker_news_cached()
    )
    zero_instrument_metadata = load_instrument_metadata_cached()
    market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    zero_summary_placeholder = st.empty()

    def render_zero_summary(buy_assets: int = 0, sell_assets: int = 0) -> None:
        with zero_summary_placeholder.container():
            counter_cols = st.columns(4)
            counter_cols[0].metric("Newest Data", format_newest_data(market_df))
            counter_cols[1].metric("Max Assets", BUY_MAX_OPEN_TICKERS)
            counter_cols[2].metric("Buy Assets", int(buy_assets))
            counter_cols[3].metric("Sell Assets", int(sell_assets))

    render_zero_summary()

    if market_df.empty:
        st.info("No measurements received yet.")
    else:
        market_df["timestamp"] = pd.to_datetime(market_df["timestamp"], utc=True, errors="coerce")
        market_df["received_at"] = pd.to_datetime(market_df["received_at"], utc=True, errors="coerce")

        newest_received = market_df["received_at"].max()
        latest_received = market_df.groupby("ticker")["received_at"].max()

        # Keep all stored tickers available for SELL evaluation. C4/C5/C6 must
        # not disappear just because a source is stale (for example over a
        # weekend). C2/BUY, however, uses only the same non-stale ticker
        # universe as Sim-Trading so old CloseB values cannot satisfy breadth.
        active_tickers = latest_received.index
        active_df = market_df[
            market_df["ticker"].isin(active_tickers)
        ].copy()

        advisor_data_version = (
            active_df["timestamp"].max(),
            active_df["id"].max(),
        )
        advisor_live = build_live_overview_cached(
            active_df,
            advisor_data_version,
            "zero-trading-advisor",
        )

        if advisor_live.empty:
            st.info("No active assets available.")
        else:
            newest_market_data = active_df["timestamp"].max()

            latest_market_rows = (
                active_df.sort_values(["timestamp", "id"])
                .drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")
            )
            c2_stale_tickers = set()
            for ticker in active_tickers:
                if ticker not in latest_market_rows.index:
                    c2_stale_tickers.add(ticker)
                    continue

                latest_row = latest_market_rows.loc[ticker]
                latest_bar_time = pd.to_datetime(
                    latest_row.get("timestamp"),
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(latest_bar_time):
                    c2_stale_tickers.add(ticker)
                    continue

                asset_type = str(
                    latest_row.get("asset_type") or ""
                ).strip().lower()
                maximum_bar_age = (
                    pd.Timedelta(minutes=60)
                    if asset_type == "crypto"
                    else pd.Timedelta(hours=72)
                )
                if newest_market_data - latest_bar_time > maximum_bar_age:
                    c2_stale_tickers.add(ticker)

            c2_active_tickers = set(active_tickers) - c2_stale_tickers
            c2_active_df = active_df[
                active_df["ticker"].isin(c2_active_tickers)
            ].copy()
            c2_live = (
                build_live_overview_cached(
                    c2_active_df,
                    (
                        c2_active_df["timestamp"].max(),
                        c2_active_df["id"].max(),
                    ),
                    "zero-trading-c2",
                )
                if not c2_active_df.empty
                else pd.DataFrame()
            )

            if not c2_live.empty and "Ticker" in c2_live.columns:
                c2_closeb_numeric = pd.to_numeric(
                    c2_live.get("CloseB"),
                    errors="coerce",
                )
                c2_group_count = int(
                    (c2_closeb_numeric >= BUY_MIN_CLOSEB_PERCENT).sum()
                )
                c2_map = (
                    c2_live
                    .dropna(subset=["Ticker"])
                    .assign(
                        _TickerKey=lambda frame: (
                            frame["Ticker"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )
                    )
                    .drop_duplicates(subset=["_TickerKey"], keep="first")
                    .set_index("_TickerKey")["C2"]
                    .fillna(False)
                    .astype(bool)
                    .to_dict()
                )
            else:
                c2_group_count = 0
                c2_map = {}

            advisor_live["C2"] = (
                advisor_live["Ticker"]
                .astype(str)
                .str.strip()
                .str.upper()
                .map(lambda ticker: bool(c2_map.get(ticker, False)))
            )

            try:
                zero_sim_payload = load_simulation_payload_cached()
                if isinstance(zero_sim_payload, dict):
                    zero_sim_rows = (
                        zero_sim_payload.get("trades")
                        or zero_sim_payload.get("rows")
                        or zero_sim_payload.get("items")
                        or []
                    )
                else:
                    zero_sim_rows = zero_sim_payload or []

                zero_open_tickers = set()
                zero_open_buy_times = {}
                zero_open_trades = {}
                for trade in zero_sim_rows:
                    ticker_value = str(trade.get("Ticker") or trade.get("ticker") or "").strip().upper()
                    status_value = str(trade.get("Status") or trade.get("status") or "").strip().upper()
                    sell_time_value = trade.get("SellTime") if "SellTime" in trade else trade.get("sell_time")
                    is_open_trade = status_value == "OPEN" or (not status_value and not sell_time_value)
                    if ticker_value and is_open_trade:
                        zero_open_tickers.add(ticker_value)
                        raw_buy_time = trade.get("BuyTime") if "BuyTime" in trade else trade.get("buy_time")
                        parsed_buy_time = pd.to_datetime(
                            raw_buy_time,
                            utc=True,
                            errors="coerce",
                        )
                        if pd.notna(parsed_buy_time):
                            previous = zero_open_buy_times.get(ticker_value)
                            if previous is None or parsed_buy_time > previous:
                                zero_open_buy_times[ticker_value] = parsed_buy_time
                                zero_open_trades[ticker_value] = trade
                zero_open_count = len(zero_open_tickers)
            except Exception:
                zero_open_count = 0
                zero_open_tickers = set()
                zero_open_buy_times = {}
                zero_open_trades = {}

            movement_threshold = float(
                SELL_CONFIG.get("movement_percent", 1.1)
            )
            c5_hours = float(
                SELL_CONFIG.get("c5_hours", 24.0)
            )
            c6_min_gain_percent = float(
                SELL_CONFIG.get("c6_min_gain_percent", 2.0)
            )
            c7_max_gain_percent = float(
                SELL_CONFIG.get("c7_max_gain_percent", 5.0)
            )
            c6_close_minutes = float(
                SELL_CONFIG.get("c6_close_minutes", 30.0)
            )
            c6_enabled = bool(
                SELL_CONFIG.get("c6_enabled", True)
            )
            zero_details = bool(
                SELL_CONFIG.get("details", True)
            )

            advisor_live["C6"] = False
            advisor_live["C7"] = False
            advisor_live["C6Check"] = "—"
            advisor_live["C7Check"] = "—"

            for idx, row in advisor_live.iterrows():
                ticker_value = str(
                    row.get("Ticker") or ""
                ).strip().upper()
                init_time_latest = zero_open_buy_times.get(ticker_value)
                if init_time_latest is None:
                    continue

                ticker_history = (
                    active_df[
                        active_df["ticker"].astype(str).str.upper()
                        == ticker_value
                    ]
                    .sort_values(["timestamp", "id"])
                    .drop_duplicates(
                        subset=["timestamp"],
                        keep="last",
                    )
                    .copy()
                )
                if ticker_history.empty:
                    continue

                latest_row = ticker_history.iloc[-1]
                latest_time = pd.to_datetime(
                    latest_row.get("timestamp"),
                    utc=True,
                    errors="coerce",
                )
                current_price = pd.to_numeric(
                    latest_row.get("close"),
                    errors="coerce",
                )
                if (
                    pd.isna(latest_time)
                    or pd.isna(current_price)
                    or float(current_price) <= 0
                ):
                    continue

                market_region = row.get("MarketRegion")
                market_config = (
                    TRADING_WINDOWS.get(market_region)
                    if market_region
                    else None
                )
                decision = evaluate_sell_history(
                    ticker_df=ticker_history,
                    latest_time=latest_time,
                    current_price=float(current_price),
                    movement_percent=movement_threshold,
                    c5_hours=c5_hours,
                    init_time=init_time_latest,
                    market_region=market_region,
                    market_config=market_config,
                    phase_config=c5_phase_config(market_region),
                )

                c6_reference_price = None
                c6_reference_time = None
                c6_reference_source = None
                c6_gain_percent = None
                c6_remaining_minutes = None

                open_trade = zero_open_trades.get(ticker_value, {})
                buy_price = pd.to_numeric(
                    open_trade.get("BuyPrice"),
                    errors="coerce",
                )
                if pd.isna(buy_price):
                    buy_price = pd.to_numeric(
                        open_trade.get("InitPrice"),
                        errors="coerce",
                    )

                if pd.notna(buy_price) and float(buy_price) > 0:
                    c6_reference_price = float(buy_price)
                    c6_reference_time = init_time_latest
                    c6_reference_source = "InitPriceLatest"

                if c6_reference_price is not None and c6_reference_price > 0:
                    c6_gain_percent = (
                        float(current_price) / c6_reference_price - 1.0
                    ) * 100.0

                window = None
                if market_config:
                    action_time = pd.Timestamp.now(tz="UTC")
                    window = trading_window_info(
                        action_time,
                        market_config,
                        "sell",
                    )
                    regular_close_value = market_config.get("regular_close")
                    if window.is_open and regular_close_value:
                        try:
                            market_timezone = str(
                                market_config.get("timezone") or "UTC"
                            )
                            action_local = action_time.tz_convert(
                                market_timezone
                            )
                            regular_close_local = pd.Timestamp(
                                f"{action_local.date()} {regular_close_value}",
                                tz=market_timezone,
                            )
                            c6_remaining_minutes = (
                                regular_close_local - action_local
                            ).total_seconds() / 60.0
                        except Exception:
                            c6_remaining_minutes = None

                c6_satisfied = bool(
                    c6_enabled
                    and not decision.c4_satisfied
                    and not decision.c5_satisfied
                    and window is not None
                    and window.is_open
                    and c6_remaining_minutes is not None
                    and 0.0 <= c6_remaining_minutes <= c6_close_minutes
                    and c6_gain_percent is not None
                    and c6_gain_percent < c6_min_gain_percent
                )

                c7_satisfied = bool(
                    not decision.c4_satisfied
                    and not decision.c5_satisfied
                    and window is not None
                    and window.is_open
                    and c6_remaining_minutes is not None
                    and 0.0 <= c6_remaining_minutes <= c6_close_minutes
                    and c6_gain_percent is not None
                    and c6_gain_percent > c7_max_gain_percent
                )

                advisor_live.at[idx, "ShouldSell"] = bool(
                    decision.should_sell or c6_satisfied or c7_satisfied
                )
                advisor_live.at[idx, "C4"] = bool(
                    decision.c4_satisfied
                )
                advisor_live.at[idx, "C5"] = bool(
                    decision.c5_satisfied
                )
                advisor_live.at[idx, "C6"] = c6_satisfied
                advisor_live.at[idx, "C7"] = c7_satisfied
                advisor_live.at[idx, "BoughtBefore"] = (
                    decision.bought_before
                )
                advisor_live.at[idx, "C6Check"] = (
                    f"{'TRUE' if c6_satisfied else 'FALSE'}: "
                    f"gain "
                    f"{c6_gain_percent:+.2f}%"
                    if c6_gain_percent is not None
                    else f"{'TRUE' if c6_satisfied else 'FALSE'}: gain unavailable"
                )
                if c6_gain_percent is not None:
                    advisor_live.at[idx, "C6Check"] += (
                        f"; threshold < {c6_min_gain_percent:.2f}%; "
                        f"reference={c6_reference_source or '—'} "
                        f"{format_local_timestamp(c6_reference_time)}; "
                        f"remaining={c6_remaining_minutes:.1f}m"
                        if c6_remaining_minutes is not None
                        else f"; threshold < {c6_min_gain_percent:.2f}%; remaining=—"
                    )

                advisor_live.at[idx, "C7Check"] = (
                    f"{'TRUE' if c7_satisfied else 'FALSE'}: "
                    f"gain {c6_gain_percent:+.2f}%"
                    if c6_gain_percent is not None
                    else f"{'TRUE' if c7_satisfied else 'FALSE'}: gain unavailable"
                )
                if c6_gain_percent is not None:
                    advisor_live.at[idx, "C7Check"] += (
                        f"; threshold > {c7_max_gain_percent:.2f}%; "
                        f"reference={c6_reference_source or '—'} "
                        f"{format_local_timestamp(c6_reference_time)}; "
                        f"remaining={c6_remaining_minutes:.1f}m"
                        if c6_remaining_minutes is not None
                        else f"; threshold > {c7_max_gain_percent:.2f}%; remaining=—"
                    )

                if (
                    decision.max_time is not None
                    and decision.max_price is not None
                    and float(decision.max_price) > 0
                ):
                    c4_drop = (
                        (float(decision.max_price) - float(current_price))
                        / float(decision.max_price)
                        * 100.0
                    )
                    advisor_live.at[idx, "C4Check"] = (
                        f"{'TRUE' if decision.c4_satisfied else 'FALSE'}: "
                        f"drop {c4_drop:.2f}% from peak "
                        f"{format_local_timestamp(decision.max_time)} "
                        f"since InitTimeLatest "
                        f"{format_local_timestamp(init_time_latest)}; "
                        f"threshold > {movement_threshold:.2f}%"
                    )

                c5_start = max(
                    init_time_latest,
                    trading_time_window_start(
                        latest_time,
                        c5_hours,
                        market_region=market_region,
                        market_config=market_config,
                        phase_config=c5_phase_config(market_region),
                    ),
                )
                last_outside = (
                    format_local_timestamp(decision.last_one_proc_time)
                    if decision.last_one_proc_time is not None
                    else "none"
                )
                advisor_live.at[idx, "C5Check"] = (
                    f"{'TRUE' if decision.c5_satisfied else 'FALSE'}: "
                    f"window from {format_local_timestamp(c5_start)} "
                    f"to {format_local_timestamp(latest_time)}; "
                    f"last outside +/-{movement_threshold:.2f}%: "
                    f"{last_outside}"
                )

            # Build one current row per ticker. Current condition values are
            # evaluated at that ticker's own latest collected market timestamp.
            # Details add the latest actual simulator state transition (Buy/Sell)
            # in the active 03:00 -> 03:00 local accounting day.
            def _zero_timedelta_text(value):
                if value is None:
                    return "—"

                total_minutes = None
                if isinstance(value, str):
                    raw = value.strip()
                    if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                        return "—"
                    parts = raw.split(":")
                    if (
                        len(parts) == 2
                        and parts[0].isdigit()
                        and parts[1].isdigit()
                    ):
                        total_hours = int(parts[0])
                        minute_part = int(parts[1])
                        if 0 <= minute_part < 60:
                            total_minutes = total_hours * 60 + minute_part
                    if total_minutes is None:
                        try:
                            delta = pd.to_timedelta(raw)
                        except Exception:
                            return raw
                        if pd.isna(delta):
                            return "—"
                        total_minutes = max(
                            0,
                            int(round(delta.total_seconds() / 60.0)),
                        )
                else:
                    try:
                        delta = pd.to_timedelta(value)
                    except Exception:
                        return "—"
                    if pd.isna(delta):
                        return "—"
                    total_minutes = max(
                        0,
                        int(round(delta.total_seconds() / 60.0)),
                    )

                days, remainder = divmod(total_minutes, 24 * 60)
                hours, minutes = divmod(remainder, 60)
                return (
                    f"{days}d {hours:02d}:{minutes:02d}"
                    if days > 0
                    else f"{hours:02d}:{minutes:02d}"
                )

            def _zero_wait_is_zero(value):
                if value is None:
                    return False
                try:
                    delta = pd.to_timedelta(value)
                    if pd.isna(delta):
                        return False
                    return abs(delta.total_seconds()) < 30
                except Exception:
                    return str(value).strip().lower() in {
                        "0", "0s", "00:00", "0:00", "0d 00:00",
                        "0 days 00:00:00",
                    }

            def _zero_local_timestamp(value):
                parsed = pd.to_datetime(value, utc=True, errors="coerce")
                if pd.isna(parsed):
                    return "—"
                return parsed.tz_convert(LOCAL_TIMEZONE).strftime("%H:%M")

            def _zero_interval_metrics(ticker, last_time, start_time):
                last_time = pd.to_datetime(last_time, utc=True, errors="coerce")
                start_time = pd.to_datetime(start_time, utc=True, errors="coerce")
                if pd.isna(last_time) or pd.isna(start_time):
                    return (None, None)
                source = market_df[
                    market_df["ticker"].astype(str).str.upper() == str(ticker).upper()
                ].copy()
                if source.empty:
                    return (None, None)
                source = source[
                    (source["timestamp"] >= start_time)
                    & (source["timestamp"] <= last_time)
                ].copy()
                if source.empty:
                    return (None, None)
                source["_CloseNumeric"] = pd.to_numeric(
                    source["close"], errors="coerce"
                )
                source = source[
                    source["_CloseNumeric"].notna()
                    & (source["_CloseNumeric"] > 0)
                ].copy()
                if source.empty:
                    return (None, None)
                sort_cols = (
                    ["timestamp", "id"]
                    if "id" in source.columns
                    else ["timestamp"]
                )
                source = (
                    source.sort_values(sort_cols)
                    .drop_duplicates(subset=["timestamp"], keep="last")
                )
                last_price = float(source.iloc[-1]["_CloseNumeric"])
                peak_price = float(source["_CloseNumeric"].max())
                if last_price <= 0 or peak_price <= 0:
                    return (None, None)
                drop_percent = max(
                    0.0,
                    ((peak_price - last_price) / peak_price) * 100.0,
                )
                change_percent = float(
                    (((source["_CloseNumeric"] / last_price) - 1.0).abs().max())
                    * 100.0
                )
                return (drop_percent, change_percent)

            def _zero_trade_value(trade, *keys):
                for key in keys:
                    if key in trade and trade.get(key) not in (None, ""):
                        return trade.get(key)
                return None

            def _zero_raw_buy_price(trade):
                """Return the simulator buy price in the ticker's market currency.

                Zero-Trading compares this value with the raw market ``close``.
                BuyPriceEUR must never be used here because that mixes EUR with
                USD/other market-currency prices and can create false double-digit
                ProfitInitTimeLatest / C6 / C7 percentages.
                """
                if trade is None:
                    return None
                for key in ["BuyPrice", "InitPrice"]:
                    if key in trade.index:
                        candidate = pd.to_numeric(trade.get(key), errors="coerce")
                        if pd.notna(candidate) and float(candidate) > 0:
                            return float(candidate)
                return None

            # Normalize simulator trades once so state-at-time and the latest
            # action in the accounting day can be derived deterministically.
            zero_sim_df = pd.DataFrame(zero_sim_rows)
            if zero_sim_df.empty:
                zero_sim_df = pd.DataFrame(
                    columns=[
                        "Ticker", "BuyTime", "SellTime", "SellReason",
                        "BuyPriceEUR", "BuyPrice", "InitPrice",
                    ]
                )
            if "Ticker" not in zero_sim_df.columns:
                if "ticker" in zero_sim_df.columns:
                    zero_sim_df["Ticker"] = zero_sim_df["ticker"]
                else:
                    zero_sim_df["Ticker"] = ""
            zero_sim_df["Ticker"] = (
                zero_sim_df["Ticker"].astype(str).str.strip().str.upper()
            )
            for canonical, fallback in [
                ("BuyTime", "buy_time"),
                ("SellTime", "sell_time"),
            ]:
                if canonical not in zero_sim_df.columns:
                    zero_sim_df[canonical] = (
                        zero_sim_df[fallback]
                        if fallback in zero_sim_df.columns
                        else pd.NaT
                    )
                zero_sim_df[canonical] = pd.to_datetime(
                    zero_sim_df[canonical], utc=True, errors="coerce"
                )

            def _zero_latest_trade_at(ticker, when):
                when = pd.to_datetime(when, utc=True, errors="coerce")
                if pd.isna(when) or zero_sim_df.empty:
                    return None
                trades = zero_sim_df[
                    (zero_sim_df["Ticker"] == str(ticker).upper())
                    & zero_sim_df["BuyTime"].notna()
                    & (zero_sim_df["BuyTime"] <= when)
                ].copy()
                if trades.empty:
                    return None
                return trades.sort_values("BuyTime").iloc[-1]

            def _zero_is_open_at(ticker, when):
                trade = _zero_latest_trade_at(ticker, when)
                if trade is None:
                    return False
                sell_time = pd.to_datetime(
                    trade.get("SellTime"), utc=True, errors="coerce"
                )
                when = pd.to_datetime(when, utc=True, errors="coerce")
                return pd.isna(sell_time) or sell_time > when

            def _zero_market_row_at(ticker, when):
                when = pd.to_datetime(when, utc=True, errors="coerce")
                if pd.isna(when):
                    return None
                source = active_df[
                    (active_df["ticker"].astype(str).str.upper() == str(ticker).upper())
                    & (active_df["timestamp"] <= when)
                ].copy()
                if source.empty:
                    return None
                sort_cols = (
                    ["timestamp", "id"]
                    if "id" in source.columns
                    else ["timestamp"]
                )
                return source.sort_values(sort_cols).iloc[-1]

            def _zero_open_count_at(when):
                if zero_sim_df.empty:
                    return 0
                count = 0
                for ticker in zero_sim_df["Ticker"].dropna().unique():
                    if ticker and _zero_is_open_at(ticker, when):
                        count += 1
                return count

            advisor = advisor_live.copy()
            if advisor.empty:
                st.info("No active assets available.")
            else:
                advisor["_LastClose2hRaw"] = pd.to_numeric(
                    advisor.get("CloseB"), errors="coerce"
                )
                advisor["_LastSellingRaw"] = pd.to_numeric(
                    advisor.get("SellTime"), errors="coerce"
                )
                # Keep the original timezone-aware market timestamp for all
                # Zero-Trading calculations.  ``Time`` is only a display string
                # (already converted to Europe/Berlin as HH:MM); parsing it as
                # UTC would shift LastTime and all wait/condition calculations.
                advisor["_LastCollectRaw"] = (
                    pd.to_datetime(advisor["_timestamp"], utc=True, errors="coerce")
                    if "_timestamp" in advisor.columns
                    else pd.NaT
                )

                # Recalculate C4/C5/C6/C7 and waits at each ticker's LastTime.
                # InitTimeLatest is the latest simulator BuyTime that is still
                # OPEN at that exact timestamp.
                current_c4 = {}
                current_c5 = {}
                current_c6 = {}
                current_c7 = {}
                current_wait_trade = {}
                current_wait_opening = {}
                current_init = {}
                current_open = {}
                current_sell_window_open = {}
                current_buy_window_open = {}
                current_profit_init = {}
                current_sim_action_time = {}
                current_buy_ts = {}
                current_buy_c2 = {}
                current_c2_snapshot_cache = {}

                def _zero_current_c2_at(action_time, ticker):
                    action_time = pd.to_datetime(action_time, utc=True, errors="coerce")
                    if pd.isna(action_time):
                        return (0, False)
                    cache_key = pd.Timestamp(action_time).isoformat()
                    if cache_key not in current_c2_snapshot_cache:
                        snapshot_source = active_df[
                            active_df["timestamp"] <= action_time
                        ].copy()
                        if snapshot_source.empty:
                            current_c2_snapshot_cache[cache_key] = (0, {})
                        else:
                            snapshot_live = build_live_overview(snapshot_source)
                            if snapshot_live.empty or "Ticker" not in snapshot_live.columns:
                                current_c2_snapshot_cache[cache_key] = (0, {})
                            else:
                                closeb = pd.to_numeric(
                                    snapshot_live.get("CloseB"), errors="coerce"
                                )
                                count = int(
                                    (closeb >= BUY_MIN_CLOSEB_PERCENT).sum()
                                )
                                snapshot_map = (
                                    snapshot_live
                                    .assign(
                                        _TickerKey=lambda frame: (
                                            frame["Ticker"].astype(str).str.strip().str.upper()
                                        ),
                                        _CloseBNumeric=closeb,
                                    )
                                    .drop_duplicates(subset=["_TickerKey"], keep="first")
                                    .set_index("_TickerKey")["_CloseBNumeric"]
                                    .to_dict()
                                )
                                current_c2_snapshot_cache[cache_key] = (count, snapshot_map)
                    count, closeb_map = current_c2_snapshot_cache[cache_key]
                    ticker_closeb = pd.to_numeric(
                        closeb_map.get(str(ticker).upper()), errors="coerce"
                    )
                    c2_value = bool(
                        count >= BUY_MIN_CLOSEB_COUNT
                        and pd.notna(ticker_closeb)
                        and float(ticker_closeb) >= BUY_MIN_CLOSEB_PERCENT
                    )
                    return (count, c2_value)

                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    last_time = pd.to_datetime(
                        row.get("_LastCollectRaw"), utc=True, errors="coerce"
                    )
                    market_row = _zero_market_row_at(ticker, last_time)
                    asset_type = market_row.get("asset_type") if market_row is not None else None
                    region = market_region_for_ticker(ticker, asset_type)
                    market_config = TRADING_WINDOWS.get(region) if region else None

                    if market_config and pd.notna(last_time):
                        phase_info = market_phase_info(last_time, region)
                        current_wait_trade[idx] = (
                            phase_info[1] if len(phase_info) > 1 else None
                        )
                        current_wait_opening[idx] = (
                            phase_info[2] if len(phase_info) > 2 else None
                        )
                        current_sell_window_open[idx] = bool(
                            trading_window_info(last_time, market_config, "sell").is_open
                        )
                        current_buy_window_open[idx] = bool(
                            trading_window_info(last_time, market_config, "buy").is_open
                        )
                    else:
                        current_wait_trade[idx] = None
                        current_wait_opening[idx] = None
                        current_sell_window_open[idx] = False
                        current_buy_window_open[idx] = False

                    trade = _zero_latest_trade_at(ticker, last_time)
                    is_open = _zero_is_open_at(ticker, last_time)
                    current_open[idx] = is_open
                    init_time = (
                        pd.to_datetime(trade.get("BuyTime"), utc=True, errors="coerce")
                        if trade is not None and is_open
                        else pd.NaT
                    )
                    current_init[idx] = init_time
                    sim_action_time = pd.NaT
                    if trade is not None:
                        buy_time_value = pd.to_datetime(
                            trade.get("BuyTime"), utc=True, errors="coerce"
                        )
                        sell_time_value = pd.to_datetime(
                            trade.get("SellTime"), utc=True, errors="coerce"
                        )
                        if pd.notna(sell_time_value) and sell_time_value <= last_time:
                            sim_action_time = sell_time_value
                        elif pd.notna(buy_time_value) and buy_time_value <= last_time:
                            sim_action_time = buy_time_value
                    current_sim_action_time[idx] = sim_action_time

                    c4 = False
                    c5 = False
                    c6 = False
                    c7 = False
                    profit_init = None
                    if is_open and pd.notna(init_time) and market_row is not None:
                        ticker_history = active_df[
                            (active_df["ticker"].astype(str).str.upper() == ticker)
                            & (active_df["timestamp"] <= last_time)
                        ].copy()
                        if not ticker_history.empty:
                            sort_cols = (
                                ["timestamp", "id"]
                                if "id" in ticker_history.columns
                                else ["timestamp"]
                            )
                            ticker_history = (
                                ticker_history.sort_values(sort_cols)
                                .drop_duplicates(subset=["timestamp"], keep="last")
                            )
                            current_price = pd.to_numeric(
                                ticker_history.iloc[-1].get("close"), errors="coerce"
                            )
                            if pd.notna(current_price) and float(current_price) > 0:
                                decision = evaluate_sell_history(
                                    ticker_df=ticker_history,
                                    latest_time=last_time,
                                    current_price=float(current_price),
                                    movement_percent=movement_threshold,
                                    c5_hours=c5_hours,
                                    init_time=init_time,
                                    market_region=region,
                                    market_config=market_config,
                                    phase_config=c5_phase_config(region),
                                )
                                c4 = bool(decision.c4_satisfied)
                                c5 = bool(decision.c5_satisfied)

                                reference_price = _zero_raw_buy_price(trade)
                                if reference_price is not None:
                                    profit_init = (
                                        float(current_price) / reference_price - 1.0
                                    ) * 100.0

                                remaining_minutes = None
                                if market_config and current_sell_window_open[idx]:
                                    regular_close_value = market_config.get("regular_close")
                                    if regular_close_value:
                                        try:
                                            market_timezone = str(
                                                market_config.get("timezone") or "UTC"
                                            )
                                            action_local = last_time.tz_convert(market_timezone)
                                            regular_close_local = pd.Timestamp(
                                                f"{action_local.date()} {regular_close_value}",
                                                tz=market_timezone,
                                            )
                                            remaining_minutes = (
                                                regular_close_local - action_local
                                            ).total_seconds() / 60.0
                                        except Exception:
                                            remaining_minutes = None

                                c6 = bool(
                                    c6_enabled
                                    and not c4
                                    and not c5
                                    and current_sell_window_open[idx]
                                    and remaining_minutes is not None
                                    and 0.0 <= remaining_minutes <= c6_close_minutes
                                    and profit_init is not None
                                    and profit_init < c6_min_gain_percent
                                )
                                c7 = bool(
                                    not c4
                                    and not c5
                                    and current_sell_window_open[idx]
                                    and remaining_minutes is not None
                                    and 0.0 <= remaining_minutes <= c6_close_minutes
                                    and profit_init is not None
                                    and profit_init > c7_max_gain_percent
                                )

                    current_c4[idx] = c4
                    current_c5[idx] = c5
                    current_c6[idx] = c6
                    current_c7[idx] = c7
                    current_profit_init[idx] = profit_init
                    buy_ts_value, buy_c2_value = _zero_current_c2_at(
                        last_time, ticker
                    )
                    current_buy_ts[idx] = buy_ts_value
                    current_buy_c2[idx] = buy_c2_value

                advisor["C4"] = pd.Series(current_c4).reindex(advisor.index).fillna(False).astype(bool)
                advisor["C5"] = pd.Series(current_c5).reindex(advisor.index).fillna(False).astype(bool)
                advisor["C6"] = pd.Series(current_c6).reindex(advisor.index).fillna(False).astype(bool)
                advisor["C7"] = pd.Series(current_c7).reindex(advisor.index).fillna(False).astype(bool)
                advisor["_WaitToTradeRaw"] = pd.Series(current_wait_trade).reindex(advisor.index)
                advisor["_WaitToOpeningRaw"] = pd.Series(current_wait_opening).reindex(advisor.index)
                advisor["_InitTimeLatestRaw"] = pd.to_datetime(
                    pd.Series(current_init).reindex(advisor.index),
                    utc=True,
                    errors="coerce",
                )
                advisor["_IsOpenAtLastTime"] = pd.Series(current_open).reindex(advisor.index).fillna(False).astype(bool)
                advisor["_SellWindowOpen"] = pd.Series(current_sell_window_open).reindex(advisor.index).fillna(False).astype(bool)
                advisor["_BuyWindowOpen"] = pd.Series(current_buy_window_open).reindex(advisor.index).fillna(False).astype(bool)
                advisor["_ProfitInitTimeLatestRaw"] = pd.to_numeric(
                    pd.Series(current_profit_init).reindex(advisor.index), errors="coerce"
                )
                advisor["_SimActionTimeRaw"] = pd.to_datetime(
                    pd.Series(current_sim_action_time).reindex(advisor.index),
                    utc=True,
                    errors="coerce",
                )

                # C2 at each ticker's own LastTime. BuyTs is the number of
                # tickers with Close2h >= the configured threshold at that time.
                advisor["BuyTs"] = pd.to_numeric(
                    pd.Series(current_buy_ts).reindex(advisor.index), errors="coerce"
                ).fillna(0).astype(int)
                advisor["BuyC2"] = (
                    pd.Series(current_buy_c2)
                    .reindex(advisor.index)
                    .fillna(False)
                    .astype(bool)
                )
                advisor["SellC4"] = advisor["C4"]
                advisor["SellC5"] = advisor["C5"]
                advisor["SellC6"] = advisor["C6"]
                advisor["SellC7"] = advisor["C7"]

                sell_signal = (
                    advisor["SellC4"]
                    | advisor["SellC5"]
                    | advisor["SellC6"]
                    | advisor["SellC7"]
                )
                advisor["ToSell"] = (
                    advisor["_IsOpenAtLastTime"]
                    & advisor["_SellWindowOpen"]
                    & sell_signal
                )

                # A ticker can be bought only when it is not already OPEN, C2 is
                # true, the buy window is open, and it survives the same up-to-6
                # Close2h ranking used by the simulator/advisor. Portfolio slots
                # are applied at the newest available state.
                buy_pool = (
                    ~advisor["_IsOpenAtLastTime"]
                    & advisor["BuyC2"]
                    & advisor["_BuyWindowOpen"]
                    & ~advisor["ToSell"]
                )
                advisor["ToBuy"] = False
                buy_candidates = advisor.loc[buy_pool].copy()
                if not buy_candidates.empty:
                    buy_candidates["_BuyRankClose"] = pd.to_numeric(
                        buy_candidates["_LastClose2hRaw"], errors="coerce"
                    )
                    available_slots = max(
                        0,
                        int(BUY_MAX_OPEN_TICKERS) - _zero_open_count_at(newest_market_data),
                    )
                    buy_limit = min(6, available_slots)
                    if buy_limit > 0:
                        allowed = buy_candidates.sort_values(
                            ["_BuyRankClose", "Ticker"],
                            ascending=[False, True],
                            na_position="last",
                        ).head(buy_limit).index
                        advisor.loc[allowed, "ToBuy"] = True

                # Current display metrics. DropInitTimeLast is the signed
                # price change from the simulator's raw buy price to LastPrice.
                # DropMaxLast is the signed drawdown from the highest close since
                # SimInitTime to LastPrice.
                drop24 = {}
                drop_init_last = {}
                drop_max_last = {}
                change24 = {}
                change_init = {}
                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    last_time = row.get("_LastCollectRaw")
                    init_time = row.get("_InitTimeLatestRaw")
                    if pd.notna(last_time):
                        d24, c24 = _zero_interval_metrics(
                            ticker,
                            last_time,
                            last_time - pd.Timedelta(hours=24),
                        )
                    else:
                        d24, c24 = (None, None)

                    init_change = None
                    max_drop = None
                    ci = None
                    if pd.notna(last_time) and pd.notna(init_time) and init_time <= last_time:
                        max_drop_magnitude, ci = _zero_interval_metrics(
                            ticker, last_time, init_time
                        )
                        if max_drop_magnitude is not None:
                            max_drop = -float(max_drop_magnitude)
                        market_row = _zero_market_row_at(ticker, last_time)
                        last_price = (
                            pd.to_numeric(market_row.get("close"), errors="coerce")
                            if market_row is not None
                            else pd.NA
                        )
                        trade = _zero_latest_trade_at(ticker, last_time)
                        init_price = _zero_raw_buy_price(trade)
                        if (
                            pd.notna(last_price)
                            and float(last_price) > 0
                            and init_price is not None
                        ):
                            init_change = (
                                float(last_price) / init_price - 1.0
                            ) * 100.0

                    drop24[idx] = d24
                    drop_init_last[idx] = init_change
                    drop_max_last[idx] = max_drop
                    change24[idx] = c24
                    change_init[idx] = ci

                advisor["_Drop24hRaw"] = pd.Series(drop24).reindex(advisor.index)
                advisor["_DropInitTimeLastRaw"] = pd.Series(drop_init_last).reindex(advisor.index)
                advisor["_DropMaxLastRaw"] = pd.Series(drop_max_last).reindex(advisor.index)
                advisor["_Change24hRaw"] = pd.Series(change24).reindex(advisor.index)
                advisor["_ChangeInitTimeLatestRaw"] = pd.Series(change_init).reindex(advisor.index)

                advisor["WaitToTrade"] = advisor["_WaitToTradeRaw"].map(_zero_timedelta_text)
                advisor["WaitToOpening"] = advisor["_WaitToOpeningRaw"].map(_zero_timedelta_text)
                if "BuyQty" in advisor.columns:
                    advisor["Qty"] = pd.to_numeric(advisor["BuyQty"], errors="coerce")
                elif "Qty" not in advisor.columns:
                    advisor["Qty"] = pd.NA
                advisor["Qty"] = advisor["Qty"].map(
                    lambda value: str(int(round(float(value)))) if pd.notna(value) else "-"
                )
                advisor["LastTime"] = advisor["_LastCollectRaw"].map(_zero_local_timestamp)
                advisor["SimInitTime"] = advisor["_InitTimeLatestRaw"].map(_zero_local_timestamp)
                advisor["SimActionTime"] = advisor["_SimActionTimeRaw"].map(_zero_local_timestamp)
                advisor["LastSelling"] = advisor["_LastSellingRaw"].map(
                    lambda value: f"{int(round(float(value)))}s" if pd.notna(value) else "—"
                )
                advisor["LastTops"] = int(c2_group_count)
                advisor["LastClose2h"] = advisor["_LastClose2hRaw"].map(
                    lambda value: f"{float(value):+.2f}%" if pd.notna(value) else "—"
                )

                def _zero_percent(value):
                    return f"{float(value):.2f}%" if pd.notna(value) else "—"

                def _zero_signed_percent(value):
                    return f"{float(value):.2f}%" if pd.notna(value) else "—"

                advisor["Drop24h"] = advisor["_Drop24hRaw"].map(_zero_percent)
                advisor["DropInitTimeLast"] = advisor["_DropInitTimeLastRaw"].map(_zero_signed_percent)
                advisor["DropMaxLast"] = advisor["_DropMaxLastRaw"].map(_zero_signed_percent)
                advisor["Change24h"] = advisor["_Change24hRaw"].map(_zero_percent)
                advisor["ChangeInitTimeLatest"] = advisor["_ChangeInitTimeLatestRaw"].map(_zero_percent)
                advisor["WeakInitTimeLatest"] = advisor["_ProfitInitTimeLatestRaw"].map(
                    lambda value: f"{float(value):+.2f}%" if pd.notna(value) else "—"
                )
                advisor["BuyPriceDiff"] = advisor["_ProfitInitTimeLatestRaw"].map(
                    lambda value: f"{float(value):+.2f}%" if pd.notna(value) else "—"
                )

                # Current 03:00 -> 03:00 accounting day.
                now_local = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
                accounting_day = now_local.normalize()
                if now_local.hour < 3:
                    accounting_day = accounting_day - pd.Timedelta(days=1)
                accounting_start_local = accounting_day + pd.Timedelta(hours=3)
                accounting_end_local = accounting_start_local + pd.Timedelta(days=1)
                accounting_start_utc = accounting_start_local.tz_convert("UTC")
                accounting_end_utc = accounting_end_local.tz_convert("UTC")

                # Build the latest actual simulator transition per ticker in the
                # accounting day. A Sell transition is ignored for MANUAL_RESET.
                sim_events = []
                for _, trade in zero_sim_df.iterrows():
                    ticker = str(trade.get("Ticker") or "").strip().upper()
                    if not ticker:
                        continue
                    buy_time = pd.to_datetime(
                        trade.get("BuyTime"), utc=True, errors="coerce"
                    )
                    sell_time = pd.to_datetime(
                        trade.get("SellTime"), utc=True, errors="coerce"
                    )
                    if (
                        pd.notna(buy_time)
                        and accounting_start_utc <= buy_time < accounting_end_utc
                    ):
                        sim_events.append({
                            "Ticker": ticker,
                            "ActionTime": buy_time,
                            "Action": "Buy",
                            "Trade": trade,
                        })
                    sell_reason = str(trade.get("SellReason") or "").strip().upper()
                    if (
                        pd.notna(sell_time)
                        and accounting_start_utc <= sell_time < accounting_end_utc
                        and sell_reason != "MANUAL_RESET"
                    ):
                        sim_events.append({
                            "Ticker": ticker,
                            "ActionTime": sell_time,
                            "Action": "Sell",
                            "Trade": trade,
                        })

                latest_event_by_ticker = {}
                for event in sim_events:
                    previous = latest_event_by_ticker.get(event["Ticker"])
                    if previous is None or event["ActionTime"] > previous["ActionTime"]:
                        latest_event_by_ticker[event["Ticker"]] = event

                snapshot_cache = {}

                def _zero_snapshot_live(action_time):
                    key = pd.Timestamp(action_time).isoformat()
                    if key in snapshot_cache:
                        return snapshot_cache[key]
                    snapshot_source = active_df[
                        active_df["timestamp"] <= action_time
                    ].copy()
                    if snapshot_source.empty:
                        result = (pd.DataFrame(), 0)
                    else:
                        live = build_live_overview(snapshot_source)
                        if live.empty or "Ticker" not in live.columns:
                            result = (live, 0)
                        else:
                            closeb = pd.to_numeric(live.get("CloseB"), errors="coerce")
                            # SimBuyTs intentionally uses > threshold, per table definition.
                            count = int((closeb > BUY_MIN_CLOSEB_PERCENT).sum())
                            result = (live, count)
                    snapshot_cache[key] = result
                    return result

                def _zero_sell_conditions_at(ticker, action_time, init_time, trade):
                    result = {"C4": False, "C5": False, "C6": False, "C7": False}
                    action_time = pd.to_datetime(action_time, utc=True, errors="coerce")
                    init_time = pd.to_datetime(init_time, utc=True, errors="coerce")
                    if pd.isna(action_time) or pd.isna(init_time):
                        return result
                    history = active_df[
                        (active_df["ticker"].astype(str).str.upper() == ticker)
                        & (active_df["timestamp"] <= action_time)
                    ].copy()
                    if history.empty:
                        return result
                    sort_cols = ["timestamp", "id"] if "id" in history.columns else ["timestamp"]
                    history = history.sort_values(sort_cols).drop_duplicates(
                        subset=["timestamp"], keep="last"
                    )
                    current_price = pd.to_numeric(
                        history.iloc[-1].get("close"), errors="coerce"
                    )
                    if pd.isna(current_price) or float(current_price) <= 0:
                        return result
                    latest_market = history.iloc[-1]
                    region = market_region_for_ticker(
                        ticker, latest_market.get("asset_type")
                    )
                    market_config = TRADING_WINDOWS.get(region) if region else None
                    decision = evaluate_sell_history(
                        ticker_df=history,
                        latest_time=action_time,
                        current_price=float(current_price),
                        movement_percent=movement_threshold,
                        c5_hours=c5_hours,
                        init_time=init_time,
                        market_region=region,
                        market_config=market_config,
                        phase_config=c5_phase_config(region),
                    )
                    result["C4"] = bool(decision.c4_satisfied)
                    result["C5"] = bool(decision.c5_satisfied)

                    reference_price = _zero_raw_buy_price(trade)
                    gain = None
                    if reference_price is not None:
                        gain = (float(current_price) / reference_price - 1.0) * 100.0

                    remaining_minutes = None
                    sell_open = False
                    if market_config:
                        sell_open = bool(
                            trading_window_info(action_time, market_config, "sell").is_open
                        )
                        regular_close_value = market_config.get("regular_close")
                        if sell_open and regular_close_value:
                            try:
                                market_timezone = str(market_config.get("timezone") or "UTC")
                                action_local = action_time.tz_convert(market_timezone)
                                regular_close_local = pd.Timestamp(
                                    f"{action_local.date()} {regular_close_value}",
                                    tz=market_timezone,
                                )
                                remaining_minutes = (
                                    regular_close_local - action_local
                                ).total_seconds() / 60.0
                            except Exception:
                                remaining_minutes = None
                    result["C6"] = bool(
                        c6_enabled
                        and not result["C4"]
                        and not result["C5"]
                        and sell_open
                        and remaining_minutes is not None
                        and 0.0 <= remaining_minutes <= c6_close_minutes
                        and gain is not None
                        and gain < c6_min_gain_percent
                    )
                    result["C7"] = bool(
                        not result["C4"]
                        and not result["C5"]
                        and sell_open
                        and remaining_minutes is not None
                        and 0.0 <= remaining_minutes <= c6_close_minutes
                        and gain is not None
                        and gain > c7_max_gain_percent
                    )
                    return result

                # Default Details values.
                for column, default in {
                    "SimLastActionTime": "—",
                    "SimLastAction": "—",
                    "SimLastInit": "—",
                    "SimReason": "—",
                    "SimBuyTs": pd.NA,
                    "SimBuyC2": False,
                    "SimSellC4": False,
                    "SimSellC5": False,
                    "SimSellC6": False,
                    "SimSellC7": False,
                    "SimWaitToOpening": False,
                }.items():
                    advisor[column] = default

                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    event = latest_event_by_ticker.get(ticker)
                    if event is None:
                        continue
                    action_time = event["ActionTime"]
                    action = event["Action"]
                    trade = event["Trade"]
                    snapshot_live, snapshot_count = _zero_snapshot_live(action_time)
                    snapshot_row = None
                    if not snapshot_live.empty and "Ticker" in snapshot_live.columns:
                        matches = snapshot_live[
                            snapshot_live["Ticker"].astype(str).str.strip().str.upper() == ticker
                        ]
                        if not matches.empty:
                            snapshot_row = matches.iloc[0]

                    sim_c2 = False
                    if snapshot_row is not None:
                        closeb = pd.to_numeric(snapshot_row.get("CloseB"), errors="coerce")
                        sim_c2 = bool(
                            snapshot_count >= BUY_MIN_CLOSEB_COUNT
                            and pd.notna(closeb)
                            and float(closeb) >= BUY_MIN_CLOSEB_PERCENT
                        )

                    init_time = pd.to_datetime(
                        trade.get("BuyTime"), utc=True, errors="coerce"
                    )
                    sell_conditions = _zero_sell_conditions_at(
                        ticker, action_time, init_time, trade
                    )

                    market_row = _zero_market_row_at(ticker, action_time)
                    wait_opening = None
                    last_collect_at_action = pd.NaT
                    if market_row is not None:
                        last_collect_at_action = pd.to_datetime(
                            market_row.get("timestamp"), utc=True, errors="coerce"
                        )
                        region = market_region_for_ticker(
                            ticker, market_row.get("asset_type")
                        )
                        if region:
                            phase_info = market_phase_info(action_time, region)
                            wait_opening = phase_info[2] if len(phase_info) > 2 else None

                    reason = "Buy signal" if action == "Buy" else str(
                        trade.get("SellReason") or "Sell signal"
                    ).strip()
                    sim_init = (
                        init_time if action == "Sell" else last_collect_at_action
                    )

                    advisor.at[idx, "SimLastActionTime"] = _zero_local_timestamp(action_time)
                    advisor.at[idx, "SimLastAction"] = action
                    advisor.at[idx, "SimLastInit"] = _zero_local_timestamp(sim_init)
                    advisor.at[idx, "SimReason"] = reason or "—"
                    advisor.at[idx, "SimBuyTs"] = int(snapshot_count)
                    advisor.at[idx, "SimBuyC2"] = bool(sim_c2)
                    advisor.at[idx, "SimSellC4"] = bool(sell_conditions["C4"])
                    advisor.at[idx, "SimSellC5"] = bool(sell_conditions["C5"])
                    advisor.at[idx, "SimSellC6"] = bool(sell_conditions["C6"])
                    advisor.at[idx, "SimSellC7"] = bool(sell_conditions["C7"])
                    advisor.at[idx, "SimWaitToOpening"] = _zero_wait_is_zero(wait_opening)

                # Zero-Trading is a fresh validation layer between the latest
                # simulator signal and the user's delayed manual ZERO execution.
                # Re-evaluate the action at each ticker's LastTime without requiring
                # the simulator to still be in the pre-action OPEN/CLOSED state.
                fresh_sell_conditions = {}
                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    event = latest_event_by_ticker.get(ticker)
                    if event is None or str(event.get("Action") or "").lower() != "sell":
                        continue
                    trade = event.get("Trade")
                    buy_time = (
                        pd.to_datetime(trade.get("BuyTime"), utc=True, errors="coerce")
                        if trade is not None
                        else pd.NaT
                    )
                    last_time = pd.to_datetime(
                        row.get("_LastCollectRaw"), utc=True, errors="coerce"
                    )
                    fresh_sell_conditions[idx] = _zero_sell_conditions_at(
                        ticker, last_time, buy_time, trade
                    )

                # Candidate BUY validations are based on the latest simulator BUY
                # signal, current C2/current trading window, and the current top-6
                # Close2h ranking. Simulator OPEN state is not a blocker because it
                # is the natural consequence of the signal being validated.
                buy_signal_indices = []
                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    event = latest_event_by_ticker.get(ticker)
                    if event is not None and str(event.get("Action") or "").lower() == "buy":
                        buy_signal_indices.append(idx)

                buy_validation_pool = advisor.loc[buy_signal_indices].copy()
                if not buy_validation_pool.empty:
                    buy_validation_pool = buy_validation_pool[
                        buy_validation_pool["BuyC2"]
                        & buy_validation_pool["_BuyWindowOpen"]
                    ].copy()
                    buy_validation_pool["_BuyRankClose"] = pd.to_numeric(
                        buy_validation_pool["_LastClose2hRaw"], errors="coerce"
                    )
                    buy_rank_allowed = set(
                        buy_validation_pool.sort_values(
                            ["_BuyRankClose", "Ticker"],
                            ascending=[False, True],
                            na_position="last",
                        ).head(6).index
                    )
                else:
                    buy_rank_allowed = set()

                advisor["ToBuy"] = False
                advisor["ToSell"] = False
                missing_by_index = {}
                open_count_now = _zero_open_count_at(newest_market_data)

                for idx, row in advisor.iterrows():
                    ticker = str(row.get("Ticker") or "").strip().upper()
                    event = latest_event_by_ticker.get(ticker)
                    sim_action = (
                        str(event.get("Action") or "").strip().lower()
                        if event is not None
                        else ""
                    )
                    missing = []

                    if sim_action == "buy":
                        if not bool(row.get("BuyC2", False)):
                            missing.append("C2")
                        if not bool(row.get("_BuyWindowOpen", False)):
                            missing.append("Trade")
                        if bool(row.get("BuyC2", False)) and bool(row.get("_BuyWindowOpen", False)):
                            if idx not in buy_rank_allowed:
                                missing.append("Rank")

                            # Exclude this ticker from the simulator open count: its
                            # OPEN state is caused by the very BUY signal being
                            # validated for delayed execution in ZERO.
                            open_without_this_signal = open_count_now - (
                                1 if bool(row.get("_IsOpenAtLastTime", False)) else 0
                            )
                            if open_without_this_signal >= int(BUY_MAX_OPEN_TICKERS):
                                missing.append("Slot")

                        advisor.at[idx, "ToBuy"] = not missing

                    elif sim_action == "sell":
                        if not bool(row.get("_SellWindowOpen", False)):
                            missing.append("Trade")
                        conditions = fresh_sell_conditions.get(
                            idx, {"C4": False, "C5": False, "C6": False, "C7": False}
                        )
                        if not any(bool(conditions.get(key, False)) for key in ("C4", "C5", "C6", "C7")):
                            missing.append("C4/C5/C6/C7")
                        advisor.at[idx, "ToSell"] = not missing

                    else:
                        missing.append("SimAction")

                    missing_by_index[idx] = ", ".join(missing) if missing else "-"

                advisor["Missing"] = (
                    pd.Series(missing_by_index).reindex(advisor.index).fillna("-")
                )

                # Informational context only. This column is deliberately
                # added after all trading-condition calculations so news cannot
                # influence ToBuy, ToSell, C2, C4, C5, C6, or C7.
                zero_ticker_keys = (
                    advisor["Ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                advisor["ISIN"] = zero_ticker_keys.map(
                    lambda ticker: zero_instrument_metadata.get(
                        ticker,
                        {},
                    ).get("ISIN", "—")
                )

                advisor["News"] = (
                    zero_ticker_keys
                    .map(zero_news_map)
                    .fillna("—")
                )

                # Keep current opportunities, tickers that are currently OPEN in
                # Sim-Trading, and/or tickers with an actual simulator transition
                # in the current accounting day. This keeps overnight OPEN positions
                # visible in Zero-Trading even when they currently have neither a BUY
                # nor a SELL signal.
                ticker_keys = advisor["Ticker"].astype(str).str.strip().str.upper()
                has_sim_action = ticker_keys.isin(latest_event_by_ticker.keys())
                is_currently_open = ticker_keys.isin(zero_open_tickers)
                advisor = advisor[
                    advisor["ToSell"]
                    | advisor["ToBuy"]
                    | has_sim_action
                    | is_currently_open
                ].copy()

                buy_asset_count = 0
                sell_asset_count = 0

                if advisor.empty:
                    st.success(
                        "No current Buy/Sell opportunities, currently OPEN simulator positions, "
                        "or simulator actions in the active 03:00–03:00 accounting day."
                    )
                else:
                    # Required ordering: ToSell, ToBuy, LastClose2h.
                    advisor = advisor.sort_values(
                        by=["ToSell", "ToBuy", "_LastClose2hRaw", "Ticker"],
                        ascending=[False, False, False, True],
                        na_position="last",
                    )

                    requested_columns = [
                        "LastTime",
                        "ToSell",
                        "ToBuy",
                        "Missing",
                        "Ticker",
                        "TickerName",
                        "ISIN",
                        "News",
                        "WaitToTrade",
                        "WaitToOpening",
                        "LastSelling",
                        "BuyTs",
                        "LastClose2h",
                        "DropInitTimeLast",
                        "DropMaxLast",
                        "ChangeInitTimeLatest",
                        "BuyPriceDiff",
                        # Simulator state/condition columns are grouped after
                        # BuyPriceDiff for easier action review.
                        "SimInitTime",
                        "Qty",
                        "SimActionTime",
                        "BuyC2",
                        "SellC4",
                        "SellC5",
                        "SellC6",
                        "SellC7",
                    ]
                    if zero_details:
                        requested_columns.extend([
                            "SimLastActionTime",
                            "SimLastAction",
                            "SimLastInit",
                            "SimReason",
                            "SimBuyTs",
                            "SimBuyC2",
                            "SimSellC4",
                            "SimSellC5",
                            "SimSellC6",
                            "SimSellC7",
                        ])

                    for column in requested_columns:
                        if column not in advisor.columns:
                            advisor[column] = "—"
                    display = advisor[requested_columns].copy()

                    raw_lookup = advisor[[
                        "_WaitToTradeRaw",
                        "_WaitToOpeningRaw",
                        "_LastSellingRaw",
                        "_LastClose2hRaw",
                        "_Drop24hRaw",
                        "_DropInitTimeLastRaw",
                        "_DropMaxLastRaw",
                        "_Change24hRaw",
                        "_ChangeInitTimeLatestRaw",
                        "_ProfitInitTimeLatestRaw",
                        "BuyTs",
                        "SellC6",
                        "SellC7",
                    ]].loc[display.index].copy()

                    def _zero_row_style(row):
                        raw = raw_lookup.loc[row.name]
                        styles = ["" for _ in row.index]

                        def bold(column):
                            if column in row.index:
                                styles[row.index.get_loc(column)] = "font-weight: 700;"

                        if _zero_wait_is_zero(raw.get("_WaitToTradeRaw")):
                            bold("WaitToTrade")
                        if _zero_wait_is_zero(raw.get("_WaitToOpeningRaw")):
                            bold("WaitToOpening")
                        last_selling = pd.to_numeric(
                            raw.get("_LastSellingRaw"), errors="coerce"
                        )
                        if pd.notna(last_selling) and float(last_selling) < 120.0:
                            bold("LastSelling")
                        buy_ts_value = pd.to_numeric(raw.get("BuyTs"), errors="coerce")
                        if (
                            pd.notna(buy_ts_value)
                            and int(buy_ts_value) >= int(BUY_MIN_CLOSEB_COUNT)
                        ):
                            bold("BuyTs")
                        last_close = pd.to_numeric(
                            raw.get("_LastClose2hRaw"), errors="coerce"
                        )
                        if pd.notna(last_close) and float(last_close) > 2.0:
                            bold("LastClose2h")
                        drop_init = pd.to_numeric(
                            raw.get("_DropInitTimeLastRaw"), errors="coerce"
                        )
                        if pd.notna(drop_init) and float(drop_init) < -movement_threshold:
                            bold("DropInitTimeLast")
                        drop_max = pd.to_numeric(
                            raw.get("_DropMaxLastRaw"), errors="coerce"
                        )
                        if pd.notna(drop_max) and abs(float(drop_max)) > movement_threshold:
                            bold("DropMaxLast")
                        for display_column, raw_column in [
                            ("Change24h", "_Change24hRaw"),
                            ("ChangeInitTimeLatest", "_ChangeInitTimeLatestRaw"),
                        ]:
                            numeric = pd.to_numeric(raw.get(raw_column), errors="coerce")
                            if pd.notna(numeric) and float(numeric) < movement_threshold:
                                bold(display_column)
                        if bool(raw.get("SellC6", False)) or bool(raw.get("SellC7", False)):
                            bold("BuyPriceDiff")
                        return styles

                    styled_display = display.style.apply(_zero_row_style, axis=1)

                    buy_asset_count = int(display["ToBuy"].fillna(False).sum())
                    sell_asset_count = int(display["ToSell"].fillna(False).sum())

                    st.dataframe(
                        styled_display,
                        use_container_width=True,
                        hide_index=True,
                    )

                newest_display = pd.to_datetime(
                    newest_market_data,
                    utc=True,
                    errors="coerce",
                )
                newest_display = (
                    newest_display
                    .tz_convert(LOCAL_TIMEZONE)
                    .strftime("%H:%M")
                    if pd.notna(newest_display)
                    else "—"
                )

                render_zero_summary(buy_asset_count, sell_asset_count)

        st.caption(
            "Zero-Trading is a fresh validation layer between the simulator signal and the user's "
            "manual ZERO execution. Because the user may execute the simulator action 5–60 minutes "
            "later, the relevant conditions are re-evaluated at each ticker's LastTime. For the "
            "latest simulator Buy, ToBuy=True means the BUY signal is still valid now; Missing shows "
            "what changed or is currently missing (C2, Trade, Rank and/or Slot). For the latest "
            "simulator Sell, ToSell=True means the SELL signal is still valid now; Missing shows "
            "Trade and/or C4/C5/C6/C7 when the SELL is no longer validated. '-' means the latest "
            "simulator action is still valid for manual ZERO execution. Simulator OPEN/CLOSED state "
            "itself is not treated as a missing condition, because it is the consequence of the "
            "simulator action being re-validated. Rows are sorted by ToSell, then ToBuy, then "
            "LastClose2h, with actionable/high values first. WaitToTrade is 00:00 throughout "
            "Pre-Trading, Opening and Post-Trading; WaitToOpening is 00:00 only during the Opening period."
        )

        st.caption(
            "News shows the newest stored relevant news item for the ticker as "
            "Category: Text. Category is derived from the article headline; Text is the "
            "article description (or headline if no description is available), shortened "
            "to 180 characters. It represents one article, not a combination of multiple "
            "news items, and does not affect trading rules."
        )

        st.caption(
            "Parameters: SimInitTime is the current OPEN simulator position's BuyTime. "
            "SimActionTime has the same meaning as on Sim-Trading: the latest simulator transition "
            "for the ticker (BuyTime while OPEN, SellTime when the latest trade is CLOSED). "
            "BuyTs is the number of tickers whose Close2h is greater than or equal to "
            f"the configured C2 threshold ({BUY_MIN_CLOSEB_PERCENT:g}%) at LastTime; BuyC2 is the "
            f"C2 result and requires at least {BUY_MIN_CLOSEB_COUNT} qualifying tickers. SellC4 is "
            f"the drop-from-peak condition using the {float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% "
            f"movement threshold. SellC5 is the static-price condition over "
            f"{float(SELL_CONFIG.get('c5_hours', 24.0)):g} trading hours. SellC6 is the near-close weak-position "
            f"exit within {float(SELL_CONFIG.get('c6_close_minutes', 30.0)):g} minutes of regular close "
            f"when gain is below {float(SELL_CONFIG.get('c6_min_gain_percent', 2.0)):.2f}%. SellC7 uses "
            f"the same close window and is true when gain is above "
            f"{float(SELL_CONFIG.get('c7_max_gain_percent', 5.0)):.2f}%."
        )

        st.caption(
            "LastSelling is the estimated liquidity time to sell; LastClose2h is the approximately "
            "two-hour CloseB. DropInitTimeLast is the signed percentage change from the OPEN simulator "
            "buy price to LastPrice. DropMaxLast is the signed drawdown from the maximum price after "
            "SimInitTime to LastPrice and is bold when its magnitude exceeds the configured C4/C5 "
            "movement threshold. ChangeInitTimeLatest uses SimInitTime through LastTime. "
            "BuyPriceDiff is the percentage difference between the simulator buy price and LastPrice: "
            "(LastPrice - BuyPrice) / BuyPrice × 100. Positive values are gains and negative values "
            "are losses. Both prices use the ticker's raw market currency, so EUR conversion cannot "
            "distort the percentage. WaitToTrade and WaitToOpening are "
            "evaluated at each ticker's LastTime."
        )

        if bool(SELL_CONFIG.get("details", True)):
            st.caption(
                "Details is enabled: SimLastActionTime and SimLastAction show the latest actual simulator "
                "CLOSED->OPEN Buy or OPEN->CLOSED Sell transition in the active 03:00-03:00 accounting day. "
                "SimLastInit and SimReason describe that action. SimBuyTs, SimBuyC2 and SimSellC4-C7 "
                "are reconstructed at SimLastActionTime. SimBuyTs intentionally counts "
                "Close2h values strictly greater than the C2 threshold, as defined for the simulator details."
            )

        st.subheader("Steps to buy a ticker")
        st.markdown(
            "1. Check **ToBuy == True**.\n"
            "2. Review **BuyTs**, **BuyC2** and **LastClose2h**.\n"
            "3. Confirm **WaitToTrade == 00:00** and **LastSelling** is acceptable.\n"
            "4. Buy **Qty** in the ZERO app if you want to follow the simulator signal."
        )

        st.subheader("Steps to sell a ticker")
        st.markdown(
            "1. Check **ToSell == True**.\n"
            "2. Review **SellC4**, **SellC5**, **SellC6** and **SellC7**.\n"
            "3. Confirm **WaitToTrade == 00:00** and **LastSelling** is acceptable.\n"
            "4. Sell the ticker in the ZERO app if you want to follow the simulator signal."
        )

elif page == "Last Data":
    st.header("Last Data")

    # News is informational only and must not influence trading calculations.
    last_data_news_map = latest_ticker_news_map(
        load_ticker_news_cached()
    )

    last_data_instrument_metadata = load_instrument_metadata_cached()

    market_df = df[
        df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

    last_data_summary_placeholder = st.empty()
    with last_data_summary_placeholder.container():
        summary_cols = st.columns(4)
        summary_cols[0].metric("Newest Data", format_newest_data(market_df))
        summary_cols[1].metric("Assets with records", 0)
        summary_cols[2].metric("Assets", count_market_assets(market_df))
        summary_cols[3].metric("Alerts", count_open_alerts(alerts_df))

    if df.empty:
        st.info("No measurements received yet.")

    else:
        open_alerts = (
            0
            if alerts_df.empty
            else int(
                (~alerts_df["acknowledged"]).sum()
            )
        )

        # Determine currently tracked assets using collection time, not
        # market-bar time. This keeps delayed feeds active as long as the
        # collector is still receiving them.
        newest_received = market_df["received_at"].max()
        latest_received = market_df.groupby("ticker")["received_at"].max()

        # Last Data keeps historical rows visible. Actionable state is based
        # on SOURCE health, not individual bar freshness: a temporarily delayed
        # ticker stays current while its collector source is alive, but all
        # tickers belonging only to a disabled/stale source become historical.
        source_latest_received = (
            market_df.groupby("system")["received_at"].max()
            if "system" in market_df.columns
            else pd.Series(dtype="datetime64[ns, UTC]")
        )
        # Ticker visibility must not depend on whether its source has produced
        # data in the last 30 minutes. Stock sources are naturally quiet while
        # their markets are closed (for example on weekends).
        currently_collected_tickers = {
            str(ticker)
            for ticker in latest_received.index
        }

        # A healthy source alone is not enough: a specific ticker can still
        # have an unreasonably old market bar. Keep normal overnight/weekend
        # stock gaps, but suppress clearly stale multi-day stock records.
        #
        # Crypto trades continuously, so a much tighter freshness limit is
        # appropriate there.
        latest_market_rows = (
            market_df.sort_values(["timestamp", "id"])
            .drop_duplicates(subset=["ticker"], keep="last")
            .set_index("ticker")
        )
        market_reference_time = pd.to_datetime(
            market_df["timestamp"].max(), utc=True
        )

        stale_tickers = set()

        for ticker in currently_collected_tickers:
            if ticker not in latest_market_rows.index:
                stale_tickers.add(ticker)
                continue

            latest_row = latest_market_rows.loc[ticker]
            latest_bar_time = pd.to_datetime(
                latest_row.get("timestamp"), utc=True
            )

            if pd.isna(latest_bar_time):
                stale_tickers.add(ticker)
                continue

            asset_type = str(
                latest_row.get("asset_type") or ""
            ).strip().lower()

            if asset_type == "crypto":
                maximum_bar_age = pd.Timedelta(minutes=60)
            else:
                maximum_bar_age = pd.Timedelta(hours=72)

            if market_reference_time - latest_bar_time > maximum_bar_age:
                stale_tickers.add(ticker)

        currently_collected_tickers -= stale_tickers

        active_tickers = latest_received.index
        active_df = market_df.copy()

        live = build_live_overview_cached(
            active_df,
            (
                active_df["timestamp"].max(),
                active_df["id"].max(),
            ),
            "last-data",
        )
        live = add_market_data_count(
            live,
            market_df,
            day_start_hour=3,
            output_column="Records",
        )

        newest_market_data = active_df["timestamp"].max()

        # These two metrics describe exactly the rows shown in the Last Data
        # table below. ``Assets`` is the total number of ticker rows, including
        # historical rows with no records in the current 03:00-03:00 day.
        # ``Assets with records`` is the subset whose displayed DayRecs value
        # (the Records column before the display rename) is greater than zero.
        assets = int(len(live))
        assets_with_records = (
            int(
                pd.to_numeric(
                    live["Records"], errors="coerce"
                ).fillna(0).gt(0).sum()
            )
            if "Records" in live.columns
            else 0
        )

        with last_data_summary_placeholder.container():
            cols = st.columns(4)
            cols[0].metric("Newest Data", format_newest_data(active_df))
            cols[1].metric("Assets with records", assets_with_records)
            cols[2].metric("Assets", assets)
            cols[3].metric("Alerts", open_alerts)

        if live.empty:
            st.info("No active assets available.")

        else:
            # Last Data is the complete per-ticker trading-data view. Keep one
            # row for every ticker regardless of its current BUY/SELL decision.
            relevant = live.copy()

            if relevant.empty:
                st.info("No ticker data is currently available.")
            else:
                relevant["_CloseBSort"] = pd.to_numeric(
                    relevant["CloseB"], errors="coerce"
                )
                relevant = relevant.sort_values(
                    by="_CloseBSort",
                    ascending=False,
                    na_position="last",
                )

                relevant = relevant.rename(
                    columns={
                        "Time": "LastCollect",
                        "Price": "LastPrice",
                        "SellTime": "SellingTime",
                    }
                )

                def format_selling_time(row):
                    value = row.get("SellingTime")
                    if pd.notna(value):
                        return f"{int(value)}s"
                    over = row.get("SellTimeOver")
                    if pd.notna(over):
                        return f">{int(over)}s"
                    # Keep unknown rather than carrying a liquidity estimate
                    # from another collection time. Crypto currently normally
                    # lands here because no one-second SellTime is collected.
                    return "—"

                relevant["SellingTime"] = relevant.apply(format_selling_time, axis=1)
                relevant["_LastSellingRaw"] = pd.to_numeric(
                    relevant["SellingTime"].astype(str).str.extract(r"(\d+)", expand=False),
                    errors="coerce",
                )

                # For crypto, build_live_overview may not populate Price because
                # the stock-specific EUR conversion path is not available.
                # Use the close from the exact latest crypto bar and the latest
                # known EUR/USD reference rate at or before that bar.
                crypto_last_price_eur = {}
                if "eur_usd" in active_df.columns:
                    fx_history = active_df[["timestamp", "eur_usd"]].copy()
                    fx_history["eur_usd"] = pd.to_numeric(
                        fx_history["eur_usd"], errors="coerce"
                    )
                    fx_history = fx_history[
                        fx_history["eur_usd"].notna()
                        & (fx_history["eur_usd"] > 0)
                    ].sort_values("timestamp")
                else:
                    fx_history = pd.DataFrame(columns=["timestamp", "eur_usd"])

                for ticker in relevant["Ticker"].astype(str):
                    source = active_df[
                        active_df["ticker"].astype(str) == ticker
                    ].copy()
                    if source.empty:
                        continue

                    source = source.sort_values(["timestamp", "id"]).drop_duplicates(
                        subset=["timestamp"], keep="last"
                    )
                    latest_source = source.iloc[-1]

                    if str(latest_source.get("asset_type", "")).lower() != "crypto":
                        continue

                    close_value = pd.to_numeric(
                        pd.Series([latest_source.get("close")]),
                        errors="coerce",
                    ).iloc[0]
                    if pd.isna(close_value) or float(close_value) <= 0:
                        continue

                    fx_candidates = fx_history[
                        fx_history["timestamp"] <= latest_source["timestamp"]
                    ]
                    if fx_candidates.empty:
                        continue

                    eur_usd = float(fx_candidates.iloc[-1]["eur_usd"])
                    if eur_usd > 0:
                        crypto_last_price_eur[ticker] = float(close_value) / eur_usd

                last_price_numeric = pd.to_numeric(
                    relevant["LastPrice"], errors="coerce"
                )
                ticker_series = relevant["Ticker"].astype(str)
                for row_index, ticker in ticker_series.items():
                    crypto_price = crypto_last_price_eur.get(ticker)
                    if crypto_price is not None:
                        last_price_numeric.at[row_index] = crypto_price

                relevant["LastPrice"] = last_price_numeric.map(
                    lambda value: f"€{value:.2f}" if pd.notna(value) else "—"
                )

                relevant["_Close2hRaw"] = pd.to_numeric(
                    relevant["CloseB"], errors="coerce"
                )

                relevant["OpenB"] = pd.to_numeric(
                    relevant["OpenB"], errors="coerce"
                ).map(
                    lambda value: "0%"
                    if pd.notna(value) and abs(float(value)) < 0.0000001
                    else (f"{value:+.2f}%" if pd.notna(value) else "—")
                )

                for column in ["LowB", "HighB", "CloseB"]:
                    relevant[column] = pd.to_numeric(
                        relevant[column], errors="coerce"
                    ).map(
                        lambda value: f"{value:+.2f}%" if pd.notna(value) else "—"
                    )

                movement_percent = float(SELL_CONFIG.get("movement_percent", 1.1))
                phase_values = {}
                duration_values = {}
                last_collect_values = {}

                newest_market_local_date = newest_market_data.tz_convert(
                    LOCAL_TIMEZONE
                ).date()

                for ticker in relevant["Ticker"].astype(str):
                    source = active_df[active_df["ticker"].astype(str) == ticker].copy()
                    if source.empty:
                        continue
                    source = source.sort_values(["timestamp", "id"]).drop_duplicates(
                        subset=["timestamp"], keep="last"
                    )
                    latest_source = source.iloc[-1]

                    latest_local = pd.to_datetime(
                        latest_source["timestamp"], utc=True
                    ).tz_convert(LOCAL_TIMEZONE)
                    if latest_local.date() == newest_market_local_date:
                        last_collect_values[ticker] = latest_local.strftime("%H:%M")
                    else:
                        last_collect_values[ticker] = latest_local.strftime("%d.%m %H:%M")

                    # Historical/inactive tickers remain visible in Last Data,
                    # but a current trading phase/wait value would be misleading.
                    if ticker not in currently_collected_tickers:
                        phase_values[ticker] = ("-", "-", "-")
                        duration_values[ticker] = ("-", "-")
                        continue

                    region = market_region_for_ticker(ticker, latest_source.get("asset_type"))

                    # Phase describes the ticker's latest available market bar,
                    # while the wait columns use the dashboard's newest market time.
                    bar_phase_info = market_phase_info(
                        latest_source["timestamp"], region
                    )
                    current_wait_info = market_phase_info(
                        newest_market_data, region
                    )
                    phase_values[ticker] = (
                        bar_phase_info[0],
                        current_wait_info[1],
                        current_wait_info[2],
                    )

                    duration_values[ticker] = movement_durations(
                        source,
                        latest_source["timestamp"],
                        latest_source.get("close"),
                        movement_percent,
                        region,
                    )

                relevant["Phase"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: phase_values.get(ticker, ("Closed", "-", "-"))[0]
                )
                relevant["_WaitToTradeRaw"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: phase_values.get(ticker, ("Closed", "-", "-"))[1]
                )
                relevant["_WaitToOpeningRaw"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: phase_values.get(ticker, ("Closed", "-", "-"))[2]
                )
                relevant["_DropDurRaw"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: duration_values.get(ticker, ("-", "-"))[0]
                )
                relevant["_ChangeDurRaw"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: duration_values.get(ticker, ("-", "-"))[1]
                )

                def _last_data_duration_text(value):
                    if value is None:
                        return "—"

                    total_minutes = None

                    if isinstance(value, str):
                        raw = value.strip()
                        if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                            return "—"

                        parts = raw.split(":")
                        if (
                            len(parts) == 2
                            and parts[0].isdigit()
                            and parts[1].isdigit()
                        ):
                            total_hours = int(parts[0])
                            minute_part = int(parts[1])
                            if 0 <= minute_part < 60:
                                total_minutes = total_hours * 60 + minute_part

                        if total_minutes is None:
                            try:
                                delta = pd.to_timedelta(raw)
                            except Exception:
                                return raw
                            if pd.isna(delta):
                                return "—"
                            total_minutes = max(
                                0,
                                int(round(delta.total_seconds() / 60.0)),
                            )
                    else:
                        try:
                            delta = pd.to_timedelta(value)
                        except Exception:
                            return "—"
                        if pd.isna(delta):
                            return "—"
                        total_minutes = max(
                            0,
                            int(round(delta.total_seconds() / 60.0)),
                        )

                    days, remainder = divmod(total_minutes, 24 * 60)
                    hours, minutes = divmod(remainder, 60)
                    return (
                        f"{days}d {hours:02d}:{minutes:02d}"
                        if days > 0
                        else f"{hours:02d}:{minutes:02d}"
                    )

                def _last_data_wait_is_zero(value):
                    if value is None:
                        return False

                    if isinstance(value, str):
                        raw = value.strip()
                        if raw in {"0", "0s", "00:00", "0:00", "0d 00:00"}:
                            return True

                        parts = raw.split(":")
                        if (
                            len(parts) == 2
                            and parts[0].isdigit()
                            and parts[1].isdigit()
                        ):
                            return int(parts[0]) == 0 and int(parts[1]) == 0

                    try:
                        delta = pd.to_timedelta(value)
                    except Exception:
                        return False

                    return pd.notna(delta) and abs(delta.total_seconds()) < 30

                relevant["WaitToTrade"] = relevant["_WaitToTradeRaw"].map(
                    _last_data_duration_text
                )
                relevant["WaitToOpening"] = relevant["_WaitToOpeningRaw"].map(
                    _last_data_duration_text
                )
                def _trading_duration_minutes(value):
                    if value is None:
                        return float("nan")
                    raw = str(value).strip()
                    if not raw or raw in {"-", "—", "None", "nan", "NaT"}:
                        return float("nan")
                    parts = raw.split(":")
                    if (
                        len(parts) == 2
                        and parts[0].isdigit()
                        and parts[1].isdigit()
                    ):
                        minute_part = int(parts[1])
                        if 0 <= minute_part < 60:
                            return float(int(parts[0]) * 60 + minute_part)
                    return float("nan")

                def _format_trading_minutes(value):
                    if value is None or pd.isna(value):
                        return "—"
                    total_minutes = max(0, int(round(float(value))))
                    hours, minutes = divmod(total_minutes, 60)
                    return f"{hours:02d}:{minutes:02d}"

                # Keep the underlying values numeric (minutes) so Streamlit
                # sorts these columns numerically while the Styler displays
                # total trading hours as HH:MM, e.g. 34:30.
                relevant["DropDuration"] = relevant["_DropDurRaw"].map(
                    _trading_duration_minutes
                )
                relevant["StaticDuration"] = relevant["_ChangeDurRaw"].map(
                    _trading_duration_minutes
                )

                relevant["LastCollect"] = relevant["Ticker"].astype(str).map(
                    lambda ticker: last_collect_values.get(ticker, "—")
                )

                # Keep every ticker with stored market data visible in Last
                # Data. Whether a ticker is currently actionable is represented
                # by Phase / WaitToTrade / WaitToOpening and system status; it
                # must not be hidden merely because its source is quiet.

                relevant = relevant.rename(
                    columns={
                        "SellingTime": "LastSelling",
                        "Records": "DayRecs",
                        "OpenB": "Open2h",
                        "LowB": "Low2h",
                        "HighB": "High2h",
                        "CloseB": "Close2h",
                        "DropDuration": "DropDur2%",
                        "StaticDuration": "ChangeDur2%",
                    }
                )

                # Instrument origin metadata is display-only. AUTO_GAINER means
                # the ticker was proposed by automatic gainer discovery rather
                # than originating from a manually imported watching list.
                last_data_ticker_keys = (
                    relevant["Ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                relevant["ISIN"] = last_data_ticker_keys.map(
                    lambda ticker: last_data_instrument_metadata.get(
                        ticker,
                        {},
                    ).get("ISIN", "—")
                )

                relevant["Gainer"] = last_data_ticker_keys.map(
                    lambda ticker: bool(
                        last_data_instrument_metadata.get(
                            ticker,
                            {},
                        ).get("Gainer", False)
                    )
                )

                def _last_data_dividend_date(
                    ticker,
                    field,
                ):
                    value = (
                        last_data_instrument_metadata
                        .get(ticker, {})
                        .get(field)
                    )

                    if not value:
                        return "-"

                    parsed = pd.to_datetime(
                        value,
                        errors="coerce",
                    )

                    if pd.isna(parsed):
                        return "-"

                    return parsed.strftime(
                        "%d.%m.%Y"
                    )

                relevant["News"] = (
                    last_data_ticker_keys
                    .map(last_data_news_map)
                    .fillna("—")
                )

                relevant["LastDividend"] = (
                    last_data_ticker_keys.map(
                        lambda ticker:
                        _last_data_dividend_date(
                            ticker,
                            "LastDividend",
                        )
                    )
                )

                relevant["ExpNextDividend"] = (
                    last_data_ticker_keys.map(
                        lambda ticker:
                        _last_data_dividend_date(
                            ticker,
                            "ExpNextDividend",
                        )
                    )
                )

                def _last_data_dividend_amount(
                    ticker,
                ):
                    value = (
                        last_data_instrument_metadata
                        .get(ticker, {})
                        .get("DividendEUR")
                    )

                    try:
                        amount = float(value)
                    except (TypeError, ValueError):
                        return "-"

                    if not math.isfinite(amount):
                        return "-"

                    return f"€{amount:.2f}"

                relevant["Dividend"] = (
                    last_data_ticker_keys.map(
                        _last_data_dividend_amount
                    )
                )

                relevant["DividendType"] = (
                    last_data_ticker_keys.map(
                        lambda ticker: str(
                            last_data_instrument_metadata
                            .get(ticker, {})
                            .get(
                                "DividendType",
                                "-",
                            )
                            or "-"
                        )
                    )
                )

                # DayRecs is a display value. Keep it as text so Streamlit
                # follows the table's left alignment instead of right-aligning
                # it as a numeric column.
                relevant["DayRecs"] = relevant["DayRecs"].map(
                    lambda value: str(int(value)) if pd.notna(value) else "—"
                )

                live_columns = [
                    "Ticker",
                    "TickerName",
                    "ISIN",
                    "Gainer",
                    "News",
                ]

                if bool(
                    SELL_CONFIG.get(
                        "details",
                        True,
                    )
                ):
                    live_columns.extend(
                        [
                            "LastDividend",
                        ]
                    )

                live_columns.extend(
                    [
                        "ExpNextDividend",
                    ]
                )

                if bool(
                    SELL_CONFIG.get(
                        "details",
                        True,
                    )
                ):
                    live_columns.extend(
                        [
                            "Dividend",
                            "DividendType",
                        ]
                    )

                live_columns.extend([
                    "DayRecs",
                    "LastCollect",
                    "LastPrice",
                    "Open2h",
                    "Low2h",
                    "High2h",
                    "Close2h",
                    "DropDur2%",
                    "ChangeDur2%",
                    "Phase",
                    "LastSelling",
                    "WaitToTrade",
                    "WaitToOpening",
                ])

                display = relevant[
                    [column for column in live_columns if column in relevant.columns]
                ].copy()

                raw_lookup = relevant[
                    [
                        "_Close2hRaw",
                        "_LastSellingRaw",
                        "_WaitToTradeRaw",
                        "_WaitToOpeningRaw",
                    ]
                ].loc[display.index].copy()

                def _last_data_row_style(row):
                    raw = raw_lookup.loc[row.name]
                    styles = ["text-align: left;" for _ in row.index]

                    def bold(column):
                        if column in row.index:
                            styles[row.index.get_loc(column)] = (
                                "font-weight: 700; text-align: left;"
                            )

                    close2h = pd.to_numeric(
                        raw.get("_Close2hRaw"),
                        errors="coerce",
                    )
                    if pd.notna(close2h) and abs(float(close2h)) >= 2.0:
                        bold("Close2h")

                    last_selling = pd.to_numeric(
                        raw.get("_LastSellingRaw"),
                        errors="coerce",
                    )
                    if pd.notna(last_selling) and float(last_selling) < 120.0:
                        bold("LastSelling")

                    if _last_data_wait_is_zero(raw.get("_WaitToTradeRaw")):
                        bold("WaitToTrade")

                    if _last_data_wait_is_zero(raw.get("_WaitToOpeningRaw")):
                        bold("WaitToOpening")

                    return styles

                display = (
                    display.style
                    .set_properties(**{"text-align": "left"})
                    .set_table_styles(
                        [
                            {
                                "selector": "th",
                                "props": [("text-align", "left")],
                            }
                        ]
                    )
                    .format(
                        {
                            "DropDur2%": _format_trading_minutes,
                            "ChangeDur2%": _format_trading_minutes,
                        },
                        na_rep="—",
                    )
                    .apply(_last_data_row_style, axis=1)
                )

                st.dataframe(
                    display,
                    use_container_width=True,
                    hide_index=True,
                    height=563,  # header + approximately 15 ticker rows
                )

        st.caption(
            "News shows the newest stored relevant news item for the ticker as "
            "Category: Text. Category is derived from the article headline; Text is the "
            "article description (or headline if no description is available), shortened "
            "to 180 characters. It represents one article, not a combination of multiple "
            "news items, and does not affect trading rules."
        )

        st.subheader("Steps to analyse tickers")
        st.markdown(
            "1. Is **Close2h** very high or very low, is **DropDur2%** short, "
            "or is **ChangeDur2%** long?\n"
            "2. Is now the trading time?\n"
            "3. Is **LastSelling** short?\n"
            "4. If all are yes, review this ticker on the **Zero-Trading** page."
        )

        st.caption(
            "Last Data shows one row for every ticker, regardless of its current BUY/SELL "
            "decision. Rows are ordered by "
            "Close2h from highest to lowest. LastCollect is the timestamp of the latest market "
            "bar; when it is from an earlier date, LastCollect also shows DD.MM. LastPrice is "
            "its estimated EUR price. DayRecs counts unique market bars from 03:00 Europe/Berlin, "
            "which is the operating-day boundary for this view. The table shows only currently "
            "actionable tickers: tickers from inactive collector sources and tickers with stale "
            "market bars are filtered out. Crypto bars older than 60 minutes and non-crypto bars "
            "older than 72 hours are treated as stale."
        )
        st.caption(
            "Open2h, Low2h, High2h and Close2h describe the approximately two-hour price block "
            "ending at LastCollect. Values are percentage changes from the baseline price about "
            "two hours earlier: Open2h is the block start, Low2h the minimum, High2h the maximum, "
            "and Close2h the latest price relative to that baseline."
        )
        st.caption(
            "LastSelling is the estimated time needed to sell a EUR 10,000 position using "
            "recent one-second market turnover and the configured participation assumption. "
            "DropDur2% is the shortest trading-time period ending at LastCollect during which price "
            "did not exceed LastPrice by more than the configured C4/C5 movement threshold. "
            "ChangeDur2% is the shortest trading-time period ending at LastCollect during which price "
            "stayed inside the configured +/- movement-threshold band around LastPrice. "
            "WaitToTrade and WaitToOpening are calculated from Newest data and show the remaining time to the relevant trading phase."
        )
        st.caption(
            "C1 checks whether the configured BUY trading window is open at the market-data time. "
            f"C2 is true when at least {BUY_MIN_CLOSEB_COUNT} active tickers have CloseB >= "
            f"{BUY_MIN_CLOSEB_PERCENT:g}%. C4 is true after a drop of more than "
            f"{float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% from the sampled peak. "
            f"C5 is true after a full {float(SELL_CONFIG.get('c5_hours', 24.0)):g} trading hours when "
            f"every sampled price stays within +/-{float(SELL_CONFIG.get('movement_percent', 1.1)):.2f}% "
            "of the current price. ShouldSell = C4 or C5."
        )

elif page == "Alerts":
    st.header("Alerts")
    alert_columns = [
        "id",
        "created_at",
        "severity",
        "system",
        "rule_name",
        "actual_value",
        "acknowledged",
    ]

    alerts_work = alerts_df.copy()

    # Keep the expected schema even when there are currently zero rows.
    for column in alert_columns:
        if column not in alerts_work.columns:
            if column == "acknowledged":
                alerts_work[column] = pd.Series(dtype="bool")
            elif column == "id":
                alerts_work[column] = pd.Series(dtype="Int64")
            else:
                alerts_work[column] = pd.Series(dtype="object")

    alerts_work["created_at"] = pd.to_datetime(
        alerts_work["created_at"],
        utc=True,
        errors="coerce",
    )

    alerts_work["acknowledged"] = (
        alerts_work["acknowledged"]
        .fillna(False)
        .astype(bool)
    )

    alert_summary_cols = st.columns(4)
    alert_summary_cols[0].metric("Newest Data", format_newest_data(df))
    alert_summary_cols[1].metric("Alerts", len(alerts_work))
    alert_summary_cols[2].metric("Unacknowledged", int((~alerts_work["acknowledged"]).sum()))
    alert_summary_cols[3].metric("Acknowledged", int(alerts_work["acknowledged"].sum()))

    show_open = st.toggle(
        "Only unacknowledged",
        value=True,
    )

    shown = (
        alerts_work[~alerts_work["acknowledged"]].copy()
        if show_open
        else alerts_work.copy()
    )

    if shown.empty:
        st.success(
            "No unacknowledged alerts."
            if show_open
            else "No alerts recorded."
        )

    st.dataframe(
        shown[alert_columns],
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Columns: id = unique alert identifier; created_at = time the alert was created; "
        "severity = importance/urgency; system = collector or subsystem that raised the alert; "
        "rule_name = rule or condition that triggered it; actual_value = measured value/details "
        "that caused the rule to fire; acknowledged = whether the alert has already been reviewed."
    )

    st.caption(
        "Alert types are represented mainly by rule_name and severity. Typical types include "
        "collector/data-freshness alerts when a source stops reporting or becomes stale; "
        "market-data alerts when the latest market observation is too old; threshold/rule alerts "
        "when a configured condition is exceeded; and system/configuration alerts when a service, "
        "setting, or expected data source is unavailable. The exact rule_name identifies the "
        "specific condition that fired."
    )

    if not alerts_work.empty:
        alert_id = st.number_input(
            "Alert ID to acknowledge",
            min_value=1,
            step=1,
        )
        if st.button("Acknowledge alert"):
            try:
                api_post(
                    f"/api/v1/alerts/{int(alert_id)}/acknowledge"
                )
                st.success("Alert acknowledged.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

elif page == "Jira":
    st.header("Jira")

    st.caption(
        "Jira issues are loaded only when Refresh Jira is pressed."
    )

    if "jira_issues" not in st.session_state:
        st.session_state["jira_issues"] = None

    if st.button("Refresh Jira", key="jira_refresh"):
        try:
            st.session_state["jira_issues"] = api_get(
                "/api/v1/jira/issues",
                {"limit": 100},
            )
            st.success("Jira issues refreshed.")
        except Exception as exc:
            st.error(f"Unable to retrieve Jira issues: {exc}")

    jira_issues = st.session_state.get("jira_issues")

    if jira_issues is None:
        st.info(
            "Press Refresh Jira to load the current issues "
            "from project HM."
        )
    else:
        jira_df = pd.DataFrame(jira_issues)

        jira_columns = [
            "Key",
            "Summary",
            "Status",
            "IssueType",
            "Created",
            "Updated",
        ]

        for column in jira_columns:
            if column not in jira_df.columns:
                jira_df[column] = ""

        jira_df = jira_df[jira_columns]

        for column in ("Created", "Updated"):
            jira_times = pd.to_datetime(
                jira_df[column],
                utc=True,
                errors="coerce",
            )
            jira_df[column] = jira_times.dt.tz_convert(
                LOCAL_TIMEZONE
            ).dt.strftime("%d.%m %H:%M")
            jira_df[column] = jira_df[column].fillna("—")

        jira_df = jira_df.rename(
            columns={"IssueType": "Issue Type"}
        )

        st.dataframe(
            jira_df,
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Create Jira Issue")

    with st.form(
        "jira_create_issue_form",
        clear_on_submit=True,
    ):
        jira_summary = st.text_input(
            "Summary",
            max_chars=255,
        )

        jira_description = st.text_area(
            "Description",
            height=180,
        )

        jira_attachment = st.file_uploader(
            "Attachment (optional, max. 20 MB)",
            key="jira_attachment",
        )

        jira_submit = st.form_submit_button(
            "Create Issue",
            type="primary",
        )

    if jira_submit:
        summary = str(jira_summary or "").strip()
        description = str(jira_description or "").strip()

        if not summary:
            st.error("Summary is required.")
        else:
            try:
                created_issue = api_post_json(
                    "/api/v1/jira/issues",
                    {
                        "summary": summary,
                        "description": description,
                    },
                )

                issue_key = str(
                    created_issue.get("key") or ""
                ).strip()

                if not issue_key:
                    raise RuntimeError(
                        "Jira did not return an issue key."
                    )

                attachment_error = None

                if (
                    jira_attachment is not None
                    and jira_attachment.size > 20 * 1024 * 1024
                ):
                    attachment_error = RuntimeError(
                        "Attachment exceeds the 20 MB limit."
                    )

                if (
                    jira_attachment is not None
                    and attachment_error is None
                ):
                    try:
                        api_post_file(
                            (
                                "/api/v1/jira/issues/"
                                f"{issue_key}/attachment"
                            ),
                            filename=jira_attachment.name,
                            content=jira_attachment.getvalue(),
                            content_type=jira_attachment.type,
                        )
                    except Exception as exc:
                        attachment_error = exc

                try:
                    st.session_state["jira_issues"] = api_get(
                        "/api/v1/jira/issues",
                        {"limit": 100},
                    )
                except Exception:
                    pass

                if attachment_error is None:
                    st.success(
                        f"Jira issue {issue_key} created successfully."
                    )
                else:
                    st.warning(
                        f"Jira issue {issue_key} was created, "
                        "but the attachment could not be uploaded: "
                        f"{attachment_error}"
                    )

            except Exception as exc:
                st.error(
                    f"Unable to create Jira issue: {exc}"
                )

elif page == "Settings":
    editable_config = load_trading_config()
    editable_windows = editable_config.setdefault("trading_windows", {})
    editable_buy = editable_config.setdefault("buy", {})
    editable_sell = editable_config.setdefault("sell", {})

    last_change = (
        editable_config.get("dashboard", {}).get("last_rule_change", {})
        if isinstance(editable_config.get("dashboard", {}), dict)
        else {}
    )
    last_changes_display = "—"
    if isinstance(last_change, dict):
        last_change_time = pd.to_datetime(last_change.get("time"), utc=True, errors="coerce")
        last_change_description = str(last_change.get("description") or "").strip()
        if pd.notna(last_change_time) and last_change_description:
            last_changes_display = (
                f"{last_change_time.tz_convert(LOCAL_TIMEZONE).strftime('%d.%m %H:%M')} · "
                f"{last_change_description}"
            )

    st.header("Settings")
    settings_summary_cols = st.columns([1, 1, 3])
    settings_summary_cols[0].metric("Newest Data", format_newest_data(df))
    settings_summary_cols[1].metric("Assets", count_market_assets(df))
    settings_summary_cols[2].metric("Last changes", last_changes_display)

    config_path = trading_config_path()
    st.caption(f"Active configuration file: {config_path}")

    if st.session_state.pop("settings_saved", False):
        st.success("Settings saved. The dashboard has reloaded and is now using the modified values.")
    if st.session_state.pop("watchlist_saved", False):
        st.success("Watchlist saved. It will be available to a collector that reads the configured watchlist path at its next collection cycle.")

    st.subheader("Rule-based parameters")
    st.caption(
        "Rule meaning: C1 checks whether the configured BUY market window is open. "
        "C2 checks whether enough active tickers meet the configured CloseB threshold. "
        "C4 becomes true after price has dropped by more than the configured movement threshold "
        "from the sampled peak. C5 becomes true only after the price has remained inside the "
        "configured +/- movement threshold around the current price for the entire configured "
        "static-price window. The setting stored internally as c5_hours is simply the length of "
        "that C5 window, in trading hours. Closed overnight periods, weekends, and configured market holidays do not count. This dashboard version has no C3 calculation. "
        "SELL windows control when a sell can be executed (CanSellNow/SellTiming); they do not "
        "change the C4 or C5 calculations themselves."
    )

    with st.form("rule_settings_form"):
        buy_col1, buy_col2 = st.columns(2)
        with buy_col1:
            new_closeb_count = st.number_input(
                "C2: minimum number of tickers",
                min_value=1,
                max_value=5000,
                value=int(editable_buy.get("minimum_closeb_count", editable_buy.get("minimum_closeb_ge2_count", 6))),
                step=1,
                help="C2 is true only when at least this many active tickers meet the CloseB percentage threshold.",
            )
        with buy_col2:
            new_closeb_percent = st.number_input(
                "C2: CloseB threshold (%)",
                min_value=0.01,
                max_value=100.0,
                value=float(editable_buy.get("minimum_closeb_percent", 2.0)),
                step=0.1,
                format="%.2f",
            )

        new_max_open_tickers = st.number_input(
            "Portfolio: maximum OPEN tickers",
            min_value=1,
            max_value=5000,
            value=int(editable_buy.get("max_open_tickers", 10)),
            step=1,
            help=(
                "Hard cap for simultaneously OPEN simulated positions. "
                "If 8 are OPEN and the maximum is 10, at most 2 new BUYs "
                "can be created in the next BUY batch."
            ),
        )

        sell_col1, sell_col2 = st.columns(2)
        with sell_col1:
            new_movement_percent = st.number_input(
                "C4/C5: movement threshold (%)",
                min_value=0.01,
                max_value=100.0,
                value=float(editable_sell.get("movement_percent", 1.1)),
                step=0.1,
                format="%.2f",
                help="C4 requires a drop greater than this percentage. C5 requires all samples to remain within +/- this percentage of current price.",
            )
        with sell_col2:
            new_c5_hours = st.number_input(
                "C5: static-price window (hours)",
                min_value=0.25,
                max_value=720.0,
                value=float(editable_sell.get("c5_hours", 24.0)),
                step=0.25,
                format="%.2f",
                help=(
                    "Internally this setting is named c5_hours. It is the minimum continuous "
                    "time period for which all sampled prices must remain within +/- the "
                    "configured C4/C5 movement threshold around the current price before C5 "
                    "becomes true. Example: with 24 trading hours and a 1.1% threshold, C5 is true only "
                    "after a full 24 hours in which every sampled price stayed within +/-1.1% "
                    "of the current price."
                ),
            )

        c6_col1, c7_col1, details_col = st.columns(3)
        with c6_col1:
            new_c6_min_gain_percent = st.number_input(
                "C6: minimum gain (%)",
                min_value=-100.0,
                max_value=1000.0,
                value=float(editable_sell.get("c6_min_gain_percent", 2.0)),
                step=0.1,
                format="%.2f",
                help=(
                    "C6 may trigger near the regular market close when C4 and C5 are false "
                    "and the gain versus the C6 reference price is below this percentage."
                ),
            )
        with c7_col1:
            new_c7_max_gain_percent = st.number_input(
                "C7: maximum gain (%)",
                min_value=-100.0,
                max_value=1000.0,
                value=float(editable_sell.get("c7_max_gain_percent", 5.0)),
                step=0.1,
                format="%.2f",
                help=(
                    "C7 may trigger near the regular market close when C4 and C5 are false "
                    "and the gain versus the same C6 reference price is above this percentage. "
                    "The goal is to lock in a strong same-day gain and reduce overnight risk."
                ),
            )
        with details_col:
            new_details = st.checkbox(
                "Details",
                value=bool(editable_sell.get("details", True)),
                help=(
                    "Show SimLastActionTime, SimLastAction, SimLastInit, SimReason and the SimBuy/SimSell snapshot columns on Zero-Trading."
                ),
            )

        st.caption(
            "Parameter names: movement_percent = the percentage threshold shared by C4 and C5; "
            "c5_hours = the C5 static-price window length; c6_min_gain_percent = the lower "
            "end-of-day gain threshold; c7_max_gain_percent = the upper end-of-day gain threshold; "
            "Details controls the simulator detail columns on Zero-Trading and Sim-Trading."
        )

        st.markdown("**Market calendars and trading windows (C1 / CanSellNow)**")
        market_values = {}
        weekday_options = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        for region, cfg in sorted(editable_windows.items()):
            if not isinstance(cfg, dict):
                continue
            st.markdown(f"##### {region}")
            c1, c2, c3 = st.columns(3)
            with c1:
                enabled = st.checkbox(
                    "Market enabled",
                    value=bool(cfg.get("enabled", True)),
                    key=f"settings_enabled_{region}",
                )
                tz_value = st.text_input(
                    "Timezone",
                    value=str(cfg.get("timezone", "UTC")),
                    key=f"settings_tz_{region}",
                    help="Must be a valid IANA timezone, for example Europe/Berlin or America/New_York.",
                )
            with c2:
                buy_start = st.text_input(
                    "BUY start (HH:MM)",
                    value=str(cfg.get("buy_start", "00:00")),
                    key=f"settings_buy_start_{region}",
                )
                buy_end = st.text_input(
                    "BUY end (HH:MM)",
                    value=str(cfg.get("buy_end", "23:59")),
                    key=f"settings_buy_end_{region}",
                )
            with c3:
                sell_start = st.text_input(
                    "SELL start (HH:MM)",
                    value=str(cfg.get("sell_start", "00:00")),
                    key=f"settings_sell_start_{region}",
                )
                sell_end = st.text_input(
                    "SELL end (HH:MM)",
                    value=str(cfg.get("sell_end", "23:59")),
                    key=f"settings_sell_end_{region}",
                )

            raw_weekdays = cfg.get("open_weekdays")
            current_weekdays = weekday_options if raw_weekdays is None else [str(v).strip().lower()[:3] for v in raw_weekdays]
            selected_weekdays = st.multiselect(
                "Open weekdays",
                options=weekday_options,
                default=[d for d in current_weekdays if d in weekday_options],
                key=f"settings_weekdays_{region}",
            )
            closed_dates_text = st.text_input(
                "Closed dates (YYYY-MM-DD, comma separated)",
                value=", ".join(str(v) for v in (cfg.get("closed_dates") or [])),
                key=f"settings_closed_{region}",
            )
            market_values[region] = {
                "enabled": enabled,
                "timezone": tz_value.strip(),
                "buy_start": buy_start.strip(),
                "buy_end": buy_end.strip(),
                "sell_start": sell_start.strip(),
                "sell_end": sell_end.strip(),
                "open_weekdays": selected_weekdays,
                "closed_dates_text": closed_dates_text,
            }

        save_rules = st.form_submit_button("Save rule-based settings", type="primary")

    if save_rules:
        errors = []
        if new_closeb_count < 1:
            errors.append("C2 minimum ticker count must be at least 1.")
        if new_max_open_tickers < 1:
            errors.append("Portfolio maximum OPEN tickers must be at least 1.")
        if not (0 < new_closeb_percent <= 100):
            errors.append("C2 CloseB threshold must be greater than 0 and at most 100%.")
        if not (0 < new_movement_percent <= 100):
            errors.append("C4/C5 movement threshold must be greater than 0 and at most 100%.")
        if not (0.25 <= new_c5_hours <= 720):
            errors.append("C5 static-price window must be between 0.25 and 720 hours.")
        if not (-100.0 <= new_c6_min_gain_percent <= 1000.0):
            errors.append("C6 minimum gain must be between -100% and 1000%.")
        if not (-100.0 <= new_c7_max_gain_percent <= 1000.0):
            errors.append("C7 maximum gain must be between -100% and 1000%.")
        if new_c7_max_gain_percent <= new_c6_min_gain_percent:
            errors.append(
                "C7 maximum gain must be greater than the C6 minimum gain."
            )

        for region, values in market_values.items():
            if not _valid_timezone(values["timezone"]):
                errors.append(f"{region}: invalid timezone {values['timezone']!r}.")
            for label in ("buy_start", "buy_end", "sell_start", "sell_end"):
                if not _valid_hhmm(values[label]):
                    errors.append(f"{region}: {label} must use HH:MM (24-hour) format.")
            if values["enabled"] and not values["open_weekdays"]:
                errors.append(f"{region}: select at least one open weekday while the market is enabled.")
            closed_dates = []
            for raw_date in [part.strip() for part in values["closed_dates_text"].split(",") if part.strip()]:
                try:
                    datetime.strptime(raw_date, "%Y-%m-%d")
                    closed_dates.append(raw_date)
                except Exception:
                    errors.append(f"{region}: invalid closed date {raw_date!r}; use YYYY-MM-DD.")
            values["closed_dates"] = closed_dates

        if errors:
            for error in errors:
                st.error(error)
        else:
            changes = []

            def _record_setting_change(label, old_value, new_value, formatter=str):
                if old_value != new_value:
                    changes.append(
                        f"{label} {formatter(old_value)}→{formatter(new_value)}"
                    )

            _record_setting_change(
                "C2 count",
                int(editable_buy.get("minimum_closeb_count", editable_buy.get("minimum_closeb_ge2_count", 6))),
                int(new_closeb_count),
            )
            _record_setting_change(
                "C2 threshold",
                float(editable_buy.get("minimum_closeb_percent", 2.0)),
                float(new_closeb_percent),
                lambda value: f"{float(value):.2f}%",
            )
            _record_setting_change(
                "Max OPEN",
                int(editable_buy.get("max_open_tickers", 10)),
                int(new_max_open_tickers),
            )
            _record_setting_change(
                "C4/C5 threshold",
                float(editable_sell.get("movement_percent", 1.1)),
                float(new_movement_percent),
                lambda value: f"{float(value):.2f}%",
            )
            _record_setting_change(
                "C5 window",
                float(editable_sell.get("c5_hours", 24.0)),
                float(new_c5_hours),
                lambda value: f"{float(value):g}h",
            )
            _record_setting_change(
                "C6 gain",
                float(editable_sell.get("c6_min_gain_percent", 2.0)),
                float(new_c6_min_gain_percent),
                lambda value: f"{float(value):.2f}%",
            )
            _record_setting_change(
                "C7 gain",
                float(editable_sell.get("c7_max_gain_percent", 5.0)),
                float(new_c7_max_gain_percent),
                lambda value: f"{float(value):.2f}%",
            )
            _record_setting_change(
                "Details",
                bool(editable_sell.get("details", True)),
                bool(new_details),
                lambda value: "on" if value else "off",
            )

            for region, values in market_values.items():
                old_cfg = editable_windows.get(region, {})
                market_fields = (
                    ("enabled", "enabled"),
                    ("timezone", "timezone"),
                    ("buy_start", "BUY start"),
                    ("buy_end", "BUY end"),
                    ("sell_start", "SELL start"),
                    ("sell_end", "SELL end"),
                    ("open_weekdays", "weekdays"),
                    ("closed_dates", "closed dates"),
                )
                for field, label in market_fields:
                    old_value = old_cfg.get(field)
                    new_value = values.get(field)
                    if field in ("open_weekdays", "closed_dates"):
                        old_value = list(old_value or [])
                        new_value = list(new_value or [])
                    _record_setting_change(
                        f"{region} {label}",
                        old_value,
                        new_value,
                        lambda value: ",".join(value) if isinstance(value, list) else str(value),
                    )

            if changes:
                short_changes = "; ".join(changes[:3])
                if len(changes) > 3:
                    short_changes += f"; +{len(changes) - 3} more"
            else:
                short_changes = "No values changed"

            dashboard_meta = editable_config.setdefault("dashboard", {})
            dashboard_meta["last_rule_change"] = {
                "time": pd.Timestamp.now(tz="UTC").isoformat(),
                "description": short_changes,
            }

            editable_buy["minimum_closeb_count"] = int(new_closeb_count)
            editable_buy.pop("minimum_closeb_ge2_count", None)
            editable_buy["minimum_closeb_percent"] = float(new_closeb_percent)
            editable_buy["max_open_tickers"] = int(new_max_open_tickers)
            editable_sell["movement_percent"] = float(new_movement_percent)
            editable_sell["c5_hours"] = float(new_c5_hours)
            editable_sell["c6_min_gain_percent"] = float(new_c6_min_gain_percent)
            editable_sell["c7_max_gain_percent"] = float(new_c7_max_gain_percent)
            editable_sell["details"] = bool(new_details)
            for region, values in market_values.items():
                cfg = editable_windows.setdefault(region, {})
                cfg["enabled"] = bool(values["enabled"])
                cfg["timezone"] = values["timezone"]
                cfg["buy_start"] = values["buy_start"]
                cfg["buy_end"] = values["buy_end"]
                cfg["sell_start"] = values["sell_start"]
                cfg["sell_end"] = values["sell_end"]
                cfg["open_weekdays"] = values["open_weekdays"]
                cfg["closed_dates"] = values["closed_dates"]
            try:
                saved_path, backup_path = save_trading_config(editable_config)
                st.cache_data.clear()
                st.session_state["settings_saved"] = True
                st.session_state["settings_backup"] = str(backup_path)
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save settings: {exc}")

    backup_path = st.session_state.pop("settings_backup", None)
    if backup_path:
        st.caption(f"Previous configuration backup: {backup_path}")

    st.divider()
    st.subheader("Simulator synchronization")

    st.caption(
        "Use this after you have manually cleared positions in ZERO. "
        "It administratively closes all currently OPEN simulator positions "
        "with SellReason=MANUAL_RESET. No normal simulator SELL, Telegram SELL, "
        "SellPrice or simulated profit/loss is created."
    )

    try:
        manual_reset_open_payload = api_get(
            "/api/v1/simulation/open-tickers"
        )
        manual_reset_open_tickers = (
            manual_reset_open_payload.get("tickers", [])
            if isinstance(manual_reset_open_payload, dict)
            else []
        )
    except Exception as exc:
        manual_reset_open_tickers = []
        st.warning(
            f"Cannot determine currently OPEN simulator positions: {exc}"
        )

    st.write(
        f"Currently OPEN simulator positions: "
        f"**{len(manual_reset_open_tickers)}**"
    )

    if manual_reset_open_tickers:
        st.caption(
            "Tickers: "
            + ", ".join(
                sorted(
                    str(ticker)
                    for ticker in manual_reset_open_tickers
                )
            )
        )

    manual_reset_confirm = st.checkbox(
        "I confirm that these positions should be removed from "
        "the simulator's OPEN portfolio.",
        key="manual_simulator_reset_confirm",
    )

    if st.button(
        "Reset all OPEN simulator positions",
        disabled=(
            not manual_reset_confirm
            or not manual_reset_open_tickers
        ),
        type="secondary",
        key="manual_simulator_reset_button",
    ):
        try:
            reset_result = api_post(
                "/api/v1/simulation/reset-open"
            )

            load_simulation_payload_cached.clear()
            load_simulation_cached.clear()

            st.session_state[
                "manual_simulator_reset_result"
            ] = reset_result

            st.rerun()

        except Exception as exc:
            st.error(
                f"Could not reset OPEN simulator positions: {exc}"
            )

    manual_reset_result = st.session_state.pop(
        "manual_simulator_reset_result",
        None,
    )

    if isinstance(manual_reset_result, dict):
        reset_count = int(
            manual_reset_result.get("closed_count", 0)
            or 0
        )

        st.success(
            f"Manual simulator synchronization completed: "
            f"{reset_count} OPEN position(s) moved to CLOSED "
            f"with SellReason=MANUAL_RESET."
        )

    st.divider()
    st.subheader("System parameters")
    st.caption(
        "Only system parameters already present in a top-level 'system' section are offered for editing, and only "
        "conservative scalar types are supported. API address/key and code/cache constants are intentionally not editable here."
    )
    system_cfg = editable_config.get("system")
    if not isinstance(system_cfg, dict) or not system_cfg:
        st.info("No safely editable top-level 'system' parameters were found in the active YAML configuration.")
    else:
        safe_system = {}
        for key, value in system_cfg.items():
            key_l = str(key).lower()
            if isinstance(value, bool):
                safe_system[key] = ("bool", value)
            elif isinstance(value, int) and any(token in key_l for token in ("interval", "timeout", "minutes", "seconds", "hours", "days", "limit", "retention")):
                safe_system[key] = ("int", value)
            elif isinstance(value, float) and any(token in key_l for token in ("interval", "timeout", "minutes", "seconds", "hours", "days", "limit")):
                safe_system[key] = ("float", value)
            elif key_l in ("log_level", "timezone") and isinstance(value, str):
                safe_system[key] = (key_l, value)

        if not safe_system:
            st.info("The 'system' section exists, but none of its values match the conservative editable allow-list.")
        else:
            with st.form("system_settings_form"):
                system_values = {}
                for key, (kind, value) in safe_system.items():
                    if kind == "bool":
                        system_values[key] = st.checkbox(str(key), value=bool(value), key=f"sys_{key}")
                    elif kind == "int":
                        system_values[key] = st.number_input(str(key), min_value=0, max_value=1000000, value=int(value), step=1, key=f"sys_{key}")
                    elif kind == "float":
                        system_values[key] = st.number_input(str(key), min_value=0.0, max_value=1000000.0, value=float(value), step=0.25, key=f"sys_{key}")
                    elif kind == "log_level":
                        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
                        current = str(value).upper()
                        system_values[key] = st.selectbox(str(key), levels, index=levels.index(current) if current in levels else 1, key=f"sys_{key}")
                    elif kind == "timezone":
                        system_values[key] = st.text_input(str(key), value=str(value), key=f"sys_{key}")
                save_system = st.form_submit_button("Save system settings")
            if save_system:
                errors = []
                for key, (kind, _) in safe_system.items():
                    if kind == "timezone" and not _valid_timezone(system_values[key]):
                        errors.append(f"{key}: invalid IANA timezone.")
                    if kind in ("int", "float") and float(system_values[key]) < 0:
                        errors.append(f"{key}: value must be non-negative.")
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    for key, value in system_values.items():
                        editable_config["system"][key] = value
                    try:
                        saved_path, backup_path = save_trading_config(editable_config)
                        st.cache_data.clear()
                        st.session_state["settings_saved"] = True
                        st.session_state["settings_backup"] = str(backup_path)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not save system settings: {exc}")

    st.divider()
    st.subheader("Watchlist upload")
    target = watchlist_target_path()
    st.caption(f"Configured watchlist target: {target}")
    uploaded_watchlist = st.file_uploader("Upload <name>.csv", type=["csv"], key="settings_watchlist")
    if uploaded_watchlist is not None:
        try:
            preview_df, raw_watchlist = validate_watchlist_csv(uploaded_watchlist)
            st.dataframe(preview_df.head(25), use_container_width=True, hide_index=True)
            st.caption(f"{len(preview_df)} ticker rows validated.")
            if st.button("Activate uploaded watchlist", type="primary"):
                saved_target, backup_target = save_watchlist_bytes(raw_watchlist)
                st.cache_data.clear()
                st.session_state["watchlist_saved"] = True
                st.session_state["watchlist_path"] = str(saved_target)
                if backup_target is not None:
                    st.session_state["watchlist_backup"] = str(backup_target)
                st.rerun()
        except Exception as exc:
            st.error(f"Invalid watchlist: {exc}")

    saved_watchlist_path = st.session_state.pop("watchlist_path", None)
    saved_watchlist_backup = st.session_state.pop("watchlist_backup", None)
    if saved_watchlist_path:
        st.caption(f"Active watchlist file: {saved_watchlist_path}")
    if saved_watchlist_backup:
        st.caption(f"Previous watchlist backup: {saved_watchlist_backup}")
    st.warning(
        "The dashboard can atomically replace the configured watchlist file, but this codebase exposes no collector reload API. "
        "Next-15-minute-slot activation therefore depends on the collector reading this same path on each cycle. If your collector "
        "loads the watchlist only at startup or from another path, it must be configured/restarted separately."
    )

elif page == "Logs":
    st.header("Logs")
    logs_market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    logs_summary_placeholder = st.empty()

    def _logs_left_table(frame: pd.DataFrame):
        """Left-align values and headers in Logs tables."""
        return (
            frame.style
            .set_properties(**{"text-align": "left"})
            .set_table_styles(
                [
                    {
                        "selector": "th",
                        "props": [("text-align", "left")],
                    }
                ]
            )
        )

    market_df = df[
        df["asset_type"].isin(["stock", "crypto"])
    ].copy()
    market_df["received_at"] = pd.to_datetime(
        market_df["received_at"], utc=True, errors="coerce"
    )
    market_df["timestamp"] = pd.to_datetime(
        market_df["timestamp"], utc=True, errors="coerce"
    )

    try:
        simulation_payload = load_simulation_payload_cached()
        if isinstance(simulation_payload, dict):
            simulation_rows = (
                simulation_payload.get("trades")
                or simulation_payload.get("rows")
                or simulation_payload.get("items")
                or []
            )
        else:
            simulation_rows = simulation_payload or []
        simulation_df = pd.DataFrame(simulation_rows)
    except Exception as exc:
        simulation_payload = {}
        simulation_df = pd.DataFrame()
        st.warning(f"Cannot load Simulation data for Logs: {exc}")

    simulation_timestamp, simulation_timestamp_source = get_simulation_timestamp(
        simulation_payload,
        market_df,
    )

    # 1. Sim-Trading Results
    st.subheader("1. Sim-Trading Results")

    sim_results = simulation_df.copy()

    if not sim_results.empty:
        if "Ticker" not in sim_results.columns:
            sim_results["Ticker"] = ""

        sim_results["Ticker"] = (
            sim_results["Ticker"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        for column in ["BuyTime", "SellTime"]:
            if column not in sim_results.columns:
                sim_results[column] = pd.NaT
            sim_results[column] = pd.to_datetime(
                sim_results[column],
                utc=True,
                errors="coerce",
            )

        # DiffSellPrice is the simulator's percentage profit/loss for a
        # completed trade. Prefer the stored RelativeDifference value,
        # otherwise calculate it from sell/buy EUR prices where possible.
        if "RelativeDifference" in sim_results.columns:
            sim_results["DiffSellPrice"] = pd.to_numeric(
                sim_results["RelativeDifference"],
                errors="coerce",
            )
        elif "DiffSellPrice" in sim_results.columns:
            sim_results["DiffSellPrice"] = pd.to_numeric(
                sim_results["DiffSellPrice"],
                errors="coerce",
            )
        else:
            buy_price_column = next(
                (
                    column
                    for column in ["BuyPriceEUR", "InitPrice", "BuyPrice"]
                    if column in sim_results.columns
                ),
                None,
            )
            sell_price_column = next(
                (
                    column
                    for column in ["SellPriceEUR", "SellPrice"]
                    if column in sim_results.columns
                ),
                None,
            )

            sim_results["DiffSellPrice"] = float("nan")
            if buy_price_column and sell_price_column:
                buy_price = pd.to_numeric(
                    sim_results[buy_price_column],
                    errors="coerce",
                )
                sell_price = pd.to_numeric(
                    sim_results[sell_price_column],
                    errors="coerce",
                )
                valid_price = (
                    buy_price.notna()
                    & sell_price.notna()
                    & buy_price.ne(0)
                )
                sim_results.loc[
                    valid_price,
                    "DiffSellPrice",
                ] = (
                    (
                        sell_price.loc[valid_price]
                        / buy_price.loc[valid_price]
                        - 1.0
                    )
                    * 100.0
                )

    valid_collection_times = market_df["timestamp"].dropna()
    last_collected_time = (
        valid_collection_times.max()
        if not valid_collection_times.empty
        else pd.NaT
    )

    st.markdown("**Days of the last week**")

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fr"]

    # Use the same EUR InitPrice that is presented by Sim-Trading whenever
    # available. The ZERO Advisor calculates Qty as the smallest whole number
    # of shares covering EUR 10,000, so reconstruct the same Qty for historical
    # simulator trades. DiffSellPrice is stored in percentage points, therefore
    # monetary P/L is:
    #
    #     (DiffSellPrice / 100) * Qty * InitPrice
    #
    # MANUAL_RESET is an administrative simulator synchronization and is
    # deliberately not treated as a simulator SELL.
    if not sim_results.empty:
        if "BuyPriceEUR" in sim_results.columns:
            sim_results["_LogsInitPrice"] = pd.to_numeric(
                sim_results["BuyPriceEUR"],
                errors="coerce",
            )
        elif "BuyPrice" in sim_results.columns:
            sim_results["_LogsInitPrice"] = pd.to_numeric(
                sim_results["BuyPrice"],
                errors="coerce",
            )
        else:
            sim_results["_LogsInitPrice"] = float("nan")

        sim_results["_LogsQty"] = sim_results["_LogsInitPrice"].map(
            lambda price: (
                math.ceil(10000.0 / float(price))
                if pd.notna(price) and float(price) > 0
                else float("nan")
            )
        )

        sim_results["_LogsInputEUR"] = (
            sim_results["_LogsQty"]
            * sim_results["_LogsInitPrice"]
        )

        sim_results["_LogsDiffSellPrice"] = pd.to_numeric(
            sim_results.get(
                "DiffSellPrice",
                pd.Series(index=sim_results.index, dtype="float64"),
            ),
            errors="coerce",
        )

        sim_results["_LogsProfitEUR"] = (
            sim_results["_LogsDiffSellPrice"]
            / 100.0
            * sim_results["_LogsQty"]
            * sim_results["_LogsInitPrice"]
        )

        sim_results["_LogsSellReason"] = (
            sim_results.get(
                "SellReason",
                pd.Series(index=sim_results.index, dtype="object"),
            )
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

    def _logs_sell_summary(day_start_local):
        """Return simulator SELL count and EUR profit for 03:00 -> 03:00."""

        accounting_start_local = (
            day_start_local.normalize()
            + pd.Timedelta(hours=3)
        )
        accounting_end_local = (
            accounting_start_local
            + pd.Timedelta(days=1)
        )

        accounting_start_utc = accounting_start_local.tz_convert("UTC")
        accounting_end_utc = accounting_end_local.tz_convert("UTC")

        if sim_results.empty:
            return 0, 0.0

        sell_mask = (
            sim_results["SellTime"].notna()
            & (sim_results["SellTime"] >= accounting_start_utc)
            & (sim_results["SellTime"] < accounting_end_utc)
            & sim_results["_LogsSellReason"].ne("MANUAL_RESET")
        )

        sell_assets = int(sell_mask.sum())

        profit_eur = float(
            sim_results.loc[
                sell_mask,
                "_LogsProfitEUR",
            ]
            .fillna(0.0)
            .sum()
        )

        return sell_assets, profit_eur

    if pd.isna(last_collected_time):
        day_rows = [
            {
                "Day": day_name,
                "Date": "—",
                "Bought Assets": 0,
                "Sold Assets": 0,
                "Max Open Assets": 0,
                "Open Assets EOB": 0,
                "Input": "€0",
                "ProfitOnInput": "+0.00%",
                "Profit": "€0.00",
                "Loss": "€0.00",
                "Result": "€0.00",
            }
            for day_name in day_names
        ]

        st.info(
            "No collected market-data timestamp is available; "
            "the daily Sim-Trading summary is shown with zeros."
        )

    else:
        last_local = pd.Timestamp(
            last_collected_time
        ).tz_convert(
            LOCAL_TIMEZONE
        )

        # Build the five most recent Monday-Friday simulator accounting days,
        # newest first, anchored to the current local operating day rather than
        # the date of the latest market-data row. This keeps today's row visible
        # even when there have not been any SELLs (or even any fresh market rows)
        # yet today. Before 03:00, the preceding calendar day is still the active
        # 03:00 -> 03:00 accounting day.
        now_local = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
        current_accounting_day = now_local.normalize()
        if now_local.hour < 3:
            current_accounting_day = (
                current_accounting_day - pd.Timedelta(days=1)
            )

        recent_days = []
        candidate_day = current_accounting_day

        while len(recent_days) < 5:
            if candidate_day.weekday() < 5:
                recent_days.append(candidate_day)
            candidate_day = candidate_day - pd.Timedelta(days=1)

        day_rows = []

        for day_start_local in recent_days:
            day_name = day_start_local.strftime("%a")

            # All daily simulator statistics use the same accounting day:
            # 03:00 local time -> 03:00 local time on the following day.
            accounting_start_local = (
                day_start_local.normalize()
                + pd.Timedelta(hours=3)
            )
            accounting_end_local = (
                accounting_start_local
                + pd.Timedelta(days=1)
            )

            # The newest day may still be incomplete. Transaction counts and
            # realised percentages are therefore capped at the latest available
            # market-data timestamp. Open Assets EOB is only final once the
            # 03:00 end-of-day boundary has been reached.
            effective_end_local = min(
                accounting_end_local,
                last_local,
            )

            bought_assets = 0
            sold_assets = 0
            max_open_assets = 0
            open_assets_eob = 0
            input_eur = 0.0
            profit_on_input = 0.0
            profit_eur = 0.0
            loss_eur = 0.0

            accounting_start_utc = accounting_start_local.tz_convert("UTC")
            accounting_end_utc = accounting_end_local.tz_convert("UTC")
            effective_end_utc = effective_end_local.tz_convert("UTC")

            if (
                not sim_results.empty
                and effective_end_local > accounting_start_local
            ):
                buy_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] >= accounting_start_utc)
                    & (sim_results["BuyTime"] < effective_end_utc)
                )
                bought_assets = int(buy_mask.sum())

                sell_mask = (
                    sim_results["SellTime"].notna()
                    & (sim_results["SellTime"] >= accounting_start_utc)
                    & (sim_results["SellTime"] < effective_end_utc)
                    & sim_results["_LogsSellReason"].ne("MANUAL_RESET")
                )
                sold_assets = int(sell_mask.sum())

                sold_diff = pd.to_numeric(
                    sim_results.loc[
                        sell_mask,
                        "_LogsDiffSellPrice",
                    ],
                    errors="coerce",
                ).dropna()

                sold_profit_eur = pd.to_numeric(
                    sim_results.loc[
                        sell_mask,
                        "_LogsProfitEUR",
                    ],
                    errors="coerce",
                )

                profit_eur = float(
                    sold_profit_eur[
                        sim_results.loc[sell_mask, "_LogsDiffSellPrice"] > 0
                    ].fillna(0.0).sum()
                )
                loss_eur = float(
                    sold_profit_eur[
                        sim_results.loc[sell_mask, "_LogsDiffSellPrice"] < 0
                    ].fillna(0.0).sum()
                )

            # Calculate the maximum number of simultaneously OPEN positions
            # during the accounting day. Start with the positions already open
            # at 03:00, then process each BUY as +1 and each normal SELL as -1.
            #
            # BUY events are processed before SELL events when timestamps are
            # identical, so the maximum reflects all positions existing at that
            # instant.
            if not sim_results.empty and effective_end_utc > accounting_start_utc:
                open_at_start_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] < accounting_start_utc)
                    & (
                        sim_results["SellTime"].isna()
                        | (sim_results["SellTime"] >= accounting_start_utc)
                    )
                )
                open_trade_indexes = set(
                    sim_results.index[open_at_start_mask].tolist()
                )
                max_open_trade_indexes = set(open_trade_indexes)
                running_open_assets = len(open_trade_indexes)
                max_open_assets = running_open_assets

                buy_events = sim_results.loc[
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] >= accounting_start_utc)
                    & (sim_results["BuyTime"] < effective_end_utc),
                    ["BuyTime"],
                ].copy()
                buy_events["TradeIndex"] = buy_events.index
                buy_events["EventTime"] = buy_events["BuyTime"]
                buy_events["Delta"] = 1
                buy_events["EventOrder"] = 0

                sell_events = sim_results.loc[
                    sim_results["SellTime"].notna()
                    & (sim_results["SellTime"] >= accounting_start_utc)
                    & (sim_results["SellTime"] < effective_end_utc),
                    ["SellTime"],
                ].copy()
                sell_events["TradeIndex"] = sell_events.index
                sell_events["EventTime"] = sell_events["SellTime"]
                sell_events["Delta"] = -1
                sell_events["EventOrder"] = 1

                open_events = pd.concat(
                    [
                        buy_events[
                            ["TradeIndex", "EventTime", "Delta", "EventOrder"]
                        ],
                        sell_events[
                            ["TradeIndex", "EventTime", "Delta", "EventOrder"]
                        ],
                    ],
                    ignore_index=True,
                ).sort_values(
                    ["EventTime", "EventOrder"],
                    ascending=[True, True],
                )

                for _, open_event in open_events.iterrows():
                    trade_index = open_event["TradeIndex"]
                    if int(open_event["Delta"]) > 0:
                        open_trade_indexes.add(trade_index)
                    else:
                        open_trade_indexes.discard(trade_index)

                    running_open_assets = len(open_trade_indexes)
                    if running_open_assets > max_open_assets:
                        max_open_assets = running_open_assets
                        max_open_trade_indexes = set(open_trade_indexes)

                input_eur = float(
                    pd.to_numeric(
                        sim_results.loc[
                            list(max_open_trade_indexes),
                            "_LogsInputEUR",
                        ],
                        errors="coerce",
                    ).fillna(0.0).sum()
                )

                if max_open_assets > 0:
                    profit_on_input = float(sold_diff.sum()) / max_open_assets

            # Count positions that are still OPEN exactly at the 03:00 EOB
            # boundary. A trade is open at EOB when it was bought before the
            # boundary and has either not been sold yet or was sold afterwards.
            #
            # For the current incomplete accounting day, show the current open
            # count at the latest available timestamp instead of pretending the
            # future 03:00 EOB state is already known.
            state_time_utc = (
                accounting_end_utc
                if last_local >= accounting_end_local
                else effective_end_utc
            )

            if not sim_results.empty and state_time_utc > accounting_start_utc:
                open_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] < state_time_utc)
                    & (
                        sim_results["SellTime"].isna()
                        | (sim_results["SellTime"] >= state_time_utc)
                    )
                )
                open_assets_eob = int(open_mask.sum())

            result_eur = profit_eur + loss_eur

            day_rows.append(
                {
                    "Day": day_name,
                    "Date": day_start_local.strftime("%d.%m.%Y"),
                    "Bought Assets": bought_assets,
                    "Sold Assets": sold_assets,
                    "Max Open Assets": max_open_assets,
                    "Open Assets EOB": open_assets_eob,
                    "Input": f"€{input_eur:.0f}",
                    "ProfitOnInput": f"{profit_on_input:+.2f}%",
                    "Profit": f"€{profit_eur:.2f}",
                    "Loss": f"€{loss_eur:.2f}",
                    "Result": f"€{result_eur:.2f}",
                }
            )

        st.caption(
            "Five most recent Monday-Friday simulator accounting days, newest first, "
            "including the current operating day even when it has no SELLs yet. "
            "Each day runs from 03:00 local time until 03:00 on the following day; "
            "the current row is calculated only through the latest available data time. "
            "Bought Assets and Sold Assets are transaction counts. Max Open Assets "
            "is the maximum number of simultaneously open positions during the day. "
            "Open Assets EOB is the number of positions still open at the 03:00 "
            "end-of-day boundary. Input is the sum of Qty × InitPrice for the "
            "positions that make up Max Open Assets. ProfitOnInput is the sum of "
            "DiffSellPrice values for normal SELLs divided by Max Open Assets. "
            "Profit and Loss are realised EUR amounts: (DiffSellPrice / 100) × Qty "
            "× InitPrice for positive and negative sold positions respectively. "
            "Result = Profit + Loss."
        )

    days_df = pd.DataFrame(
        day_rows,
        columns=[
            "Day",
            "Date",
            "Bought Assets",
            "Sold Assets",
            "Max Open Assets",
            "Open Assets EOB",
            "Input",
            "ProfitOnInput",
            "Profit",
            "Loss",
            "Result",
        ],
    )

    days_display_df = days_df.copy()

    for column in days_display_df.columns:
        days_display_df[column] = days_display_df[column].map(
            lambda value: "—"
            if pd.isna(value)
            else str(value)
        )

    st.dataframe(
        _logs_left_table(days_display_df),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Weeks of the last year**")

    week_rows = []

    if pd.isna(last_collected_time):
        st.info(
            "No collected market-data timestamp is available for the "
            "52-week Sim-Trading summary."
        )

    else:
        last_local = pd.Timestamp(
            last_collected_time
        ).tz_convert(
            LOCAL_TIMEZONE
        )

        current_week_monday = (
            last_local.normalize()
            - pd.Timedelta(days=last_local.weekday())
        )

        # Show the current ISO week first, followed by the previous
        # 51 weeks in descending order. The current week may be incomplete.
        first_week_monday = (
            current_week_monday
            - pd.Timedelta(weeks=51)
        )

        for week_index in reversed(range(52)):
            week_start_local = (
                first_week_monday
                + pd.Timedelta(weeks=week_index)
            )

            week_end_local = (
                week_start_local
                + pd.Timedelta(days=7)
            )

            effective_week_end_local = min(
                week_end_local,
                last_local,
            )

            week_start_utc = week_start_local.tz_convert("UTC")
            effective_week_end_utc = effective_week_end_local.tz_convert("UTC")

            iso = week_start_local.isocalendar()
            week_number = int(iso.week)

            # Weekly totals are calculated as the sum of the corresponding
            # Monday-Friday 03:00 -> 03:00 simulator accounting days.
            bought_assets = 0
            sold_assets = 0
            profit_eur = 0.0
            loss_eur = 0.0

            for week_day_offset in range(5):
                accounting_day = (
                    week_start_local
                    + pd.Timedelta(days=week_day_offset)
                )

                accounting_start_local = (
                    accounting_day.normalize()
                    + pd.Timedelta(hours=3)
                )
                accounting_end_local = (
                    accounting_start_local
                    + pd.Timedelta(days=1)
                )

                if accounting_start_local >= last_local:
                    continue

                effective_day_end_local = min(
                    accounting_end_local,
                    last_local,
                )

                if (
                    sim_results.empty
                    or effective_day_end_local <= accounting_start_local
                ):
                    continue

                accounting_start_utc = accounting_start_local.tz_convert("UTC")
                effective_day_end_utc = effective_day_end_local.tz_convert("UTC")

                buy_mask = (
                    sim_results["BuyTime"].notna()
                    & (sim_results["BuyTime"] >= accounting_start_utc)
                    & (sim_results["BuyTime"] < effective_day_end_utc)
                )
                bought_assets += int(buy_mask.sum())

                sell_mask = (
                    sim_results["SellTime"].notna()
                    & (sim_results["SellTime"] >= accounting_start_utc)
                    & (sim_results["SellTime"] < effective_day_end_utc)
                    & sim_results["_LogsSellReason"].ne("MANUAL_RESET")
                )
                sold_assets += int(sell_mask.sum())

                sold_profit_eur = pd.to_numeric(
                    sim_results.loc[
                        sell_mask,
                        "_LogsProfitEUR",
                    ],
                    errors="coerce",
                )
                sold_diff = pd.to_numeric(
                    sim_results.loc[
                        sell_mask,
                        "_LogsDiffSellPrice",
                    ],
                    errors="coerce",
                )

                # Use exactly the same daily Profit/Loss definition as the
                # daily table, then sum those daily EUR values into the week.
                profit_eur += float(
                    sold_profit_eur[sold_diff > 0].fillna(0.0).sum()
                )
                loss_eur += float(
                    sold_profit_eur[sold_diff < 0].fillna(0.0).sum()
                )

            result_eur = profit_eur + loss_eur

            normal_display_week_end = (
                week_start_local
                + pd.Timedelta(days=4)
            )

            display_week_end = min(
                normal_display_week_end,
                last_local.normalize(),
            )

            week_rows.append(
                {
                    "Week": week_number,
                    "Dates": (
                        f"{week_start_local.strftime('%d.%m.%Y')}-"
                        f"{display_week_end.strftime('%d.%m.%Y')}"
                    ),
                    "Bought Assets": bought_assets,
                    "Sold Assets": sold_assets,
                    "Profit": f"€{profit_eur:.2f}",
                    "Loss": f"€{loss_eur:.2f}",
                    "Result": f"€{result_eur:.2f}",
                }
            )

    weeks_df = pd.DataFrame(
        week_rows,
        columns=[
            "Week",
            "Dates",
            "Bought Assets",
            "Sold Assets",
            "Profit",
            "Loss",
            "Result",
        ],
    )

    weeks_display_df = weeks_df.copy()

    for column in weeks_display_df.columns:
        weeks_display_df[column] = weeks_display_df[column].map(
            lambda value: "—"
            if pd.isna(value)
            else str(value)
        )

    st.dataframe(
        _logs_left_table(weeks_display_df),
        use_container_width=True,
        hide_index=True,
    )

    curr_day_profit = (
        str(days_df.iloc[0]["Result"])
        if not days_df.empty and "Result" in days_df.columns
        else "—"
    )
    curr_week_profit = (
        str(weeks_df.iloc[0]["Result"])
        if not weeks_df.empty and "Result" in weeks_df.columns
        else "—"
    )
    with logs_summary_placeholder.container():
        logs_summary_cols = st.columns(6)
        logs_summary_cols[0].metric(
            "Newest Data",
            format_newest_data(logs_market_df),
        )
        logs_summary_cols[1].metric("CurrDayProfit", curr_day_profit)
        logs_summary_cols[2].metric("CurrWeekProfit", curr_week_profit)
        logs_summary_cols[3].metric(
            "Assets",
            count_market_assets(logs_market_df),
        )
        logs_summary_cols[4].metric("Measurements", len(logs_market_df))
        logs_summary_cols[5].metric(
            "Alerts",
            count_open_alerts(alerts_df),
        )

    st.caption(
        "Sim-Trading Results: Date uses DD.MM.YYYY. Buy Assets keeps the "
        "calendar-day/calendar-week BUY transaction count. Sell Assets uses "
        "simulator SELL transactions in the accounting day from 03:00 local "
        "time until 03:00 local time on the following day. MANUAL_RESET "
        "positions are administrative closes and are excluded from Sell Assets "
        "and Profit. Profit is expressed in EUR and is calculated for each "
        "normal sold position as InitPrice × DiffSellPrice / 100, where "
        "DiffSellPrice is the percentage shown on Sim-Trading and InitPrice "
        "uses the corresponding EUR buy/init price. Daily Profit is the sum "
        "of those position profits. Weekly Profit, Loss and Result are the sums "
        "of the corresponding daily EUR values from the same Monday-Friday "
        "03:00–03:00 accounting days. The weekly "
        "table contains the previous 52 ISO calendar weeks plus the current "
        "week; the current-week row can therefore still be partial."
    )

    # 2. Imported assets that were not tracked at simulationTimestamp.
    st.subheader("2. Non-tracked imported assets")
    st.caption(
        "simulationTimestamp: "
        f"{fmt_local_datetime(simulation_timestamp, '%Y-%m-%d %H:%M:%S')} "
        f"({simulation_timestamp_source})"
    )

    imported_assets, imported_csv_paths = discover_imported_asset_csvs()
    imported_tickers = set(imported_assets["Ticker"].dropna().astype(str))
    tracked_at_simulation = tracked_tickers_at(
        market_df,
        simulation_timestamp,
    )
    non_tracked_assets = imported_assets[
        ~imported_assets["Ticker"].isin(tracked_at_simulation)
    ].copy()

    if imported_csv_paths:
        st.caption("Imported asset CSV source(s): " + ", ".join(imported_csv_paths))
    else:
        st.warning(
            "No imported asset CSV with a Ticker/Symbol column was found. "
            "Set ASSETS_CSV (or ASSET_CSV) to the production <name>.csv path "
            "if it is stored outside /app/config or the project directories."
        )

    asset_cols = st.columns(3)
    asset_cols[0].metric("ImportedTickers", len(imported_tickers))
    asset_cols[1].metric("TrackedTickersLastTime", len(tracked_at_simulation))
    asset_cols[2].metric("NonTrackedTickersLastTime", len(non_tracked_assets))

    if not non_tracked_assets.empty:
        st.dataframe(
            _logs_left_table(
                non_tracked_assets[["Ticker", "Name", "ISIN"]]
            ),
            use_container_width=True,
            hide_index=True,
        )
    elif imported_tickers:
        st.success("All imported assets were tracked at simulationTimestamp.")

    # 3. Previous simulator day statistics (03:00 -> 03:00 Europe/Berlin).
    st.subheader("3. Previous simulator day")
    day_start, day_end = previous_simulator_day_bounds()
    st.caption(
        f"Window: {fmt_local_datetime(day_start, '%Y-%m-%d %H:%M:%S')} to "
        f"{fmt_local_datetime(day_end, '%Y-%m-%d %H:%M:%S')}"
    )

    day_receipts = market_df[
        market_df["received_at"].notna()
        & (market_df["received_at"] >= day_start)
        & (market_df["received_at"] < day_end)
    ]["received_at"]

    first_collection = day_receipts.min() if not day_receipts.empty else pd.NaT
    last_collection = day_receipts.max() if not day_receipts.empty else pd.NaT

    if simulation_df.empty:
        start_open = start_closed = end_open = end_closed = 0
        bought_count = sold_count = 0
    else:
        sim_work = simulation_df.copy()
        sim_work["BuyTime"] = pd.to_datetime(
            sim_work.get("BuyTime"), utc=True, errors="coerce"
        )
        sim_work["SellTime"] = pd.to_datetime(
            sim_work.get("SellTime"), utc=True, errors="coerce"
        )
        sim_work["Ticker"] = (
            sim_work.get("Ticker", pd.Series(index=sim_work.index, dtype="object"))
            .astype(str).str.upper()
        )

        start_open, start_closed = simulation_state_at(
            sim_work,
            first_collection,
        )
        end_open, end_closed = simulation_state_at(
            sim_work,
            last_collection,
        )
        bought_count = int(
            sim_work.loc[
                sim_work["BuyTime"].notna()
                & (sim_work["BuyTime"] >= day_start)
                & (sim_work["BuyTime"] < day_end),
                "Ticker",
            ].nunique()
        )
        sold_count = int(
            sim_work.loc[
                sim_work["SellTime"].notna()
                & (sim_work["SellTime"] >= day_start)
                & (sim_work["SellTime"] < day_end),
                "Ticker",
            ].nunique()
        )

    start_closeb2 = closeb_over_two_at(market_df, first_collection)
    end_closeb2 = closeb_over_two_at(market_df, last_collection)

    stats_rows = [
        {
            "Point": "Start of day",
            "CollectionTime": fmt_local_datetime(
                first_collection, "%Y-%m-%d %H:%M:%S"
            ),
            "OpenTickers": start_open,
            "ClosedTickers": start_closed,
            "CloseB > 2%": start_closeb2,
            "BoughtTickers": "—",
            "SoldTickers": "—",
        },
        {
            "Point": "During day",
            "CollectionTime": (
                f"{fmt_local_datetime(day_start, '%Y-%m-%d %H:%M:%S')} – "
                f"{fmt_local_datetime(day_end, '%Y-%m-%d %H:%M:%S')}"
            ),
            "OpenTickers": "—",
            "ClosedTickers": "—",
            "CloseB > 2%": "—",
            "BoughtTickers": bought_count,
            "SoldTickers": sold_count,
        },
        {
            "Point": "End of day",
            "CollectionTime": fmt_local_datetime(
                last_collection, "%Y-%m-%d %H:%M:%S"
            ),
            "OpenTickers": end_open,
            "ClosedTickers": end_closed,
            "CloseB > 2%": end_closeb2,
            "BoughtTickers": "—",
            "SoldTickers": "—",
        },
    ]
    st.dataframe(
        _logs_left_table(
            pd.DataFrame(stats_rows)
        ),
        use_container_width=True,
        hide_index=True,
    )
    if pd.isna(first_collection) or pd.isna(last_collection):
        st.info("No collector measurements were found for the complete previous 03:00–03:00 window.")

    # 4. Timing of the newest 15-minute market-data block.
    st.subheader("4. Latest 15-minute data collection")
    valid_market = market_df[
        market_df["timestamp"].notna() & market_df["received_at"].notna()
    ].copy()

    if valid_market.empty:
        st.info("No market collection timing data is available.")
    else:
        newest_market_data = valid_market["timestamp"].max()
        newest_rows = valid_market[
            valid_market["timestamp"] == newest_market_data
        ]
        collection_started = newest_rows["received_at"].min()
        collection_finished = newest_rows["received_at"].max()
        dashboard_available = collection_finished
        block_end = newest_market_data + pd.Timedelta(minutes=15)

        timing_rows = pd.DataFrame(
            [
                {
                    "Event": "Collector started",
                    "Time": fmt_local_datetime(
                        collection_started, "%Y-%m-%d %H:%M:%S"
                    ),
                    "Details": (
                        "15-min delayed block "
                        f"{fmt_local_datetime(newest_market_data, '%H:%M')}–"
                        f"{fmt_local_datetime(block_end, '%H:%M')}"
                    ),
                },
                {
                    "Event": "Collector finished",
                    "Time": fmt_local_datetime(
                        collection_finished, "%Y-%m-%d %H:%M:%S"
                    ),
                    "Details": f"{len(newest_rows)} asset measurement(s) received",
                },
                {
                    "Event": "Dashboard data available",
                    "Time": fmt_local_datetime(
                        dashboard_available, "%Y-%m-%d %H:%M:%S"
                    ),
                    "Details": (
                        "Refresh can show Newest data = "
                        f"{fmt_local_datetime(newest_market_data, '%H:%M')}"
                    ),
                },
            ]
        )
        st.dataframe(
            timing_rows,
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Collector start/finish are the earliest/latest received_at values "
            "for the newest market-data timestamp across stock/crypto measurements."
        )

    # 4. Measurements moved here from Live Overview.
    st.subheader("4. Measurements")
    st.metric("Mesurements in local database", len(market_df))

elif page == "Historical Data":
    st.header("Historical Data")
    trends_df = df[
        df["asset_type"].isin(
            ["stock", "crypto"]
        )
    ].copy()

    historical_summary_placeholder = st.empty()

    def render_historical_summary(
        from_to: str = "—",
        points: int = 0,
    ) -> None:
        with historical_summary_placeholder.container():
            historical_summary_cols = st.columns(4)
            historical_summary_cols[0].metric(
                "Newest Data",
                format_newest_data(trends_df),
            )
            historical_summary_cols[1].metric(
                "Assets",
                count_market_assets(trends_df),
            )
            historical_summary_cols[2].metric(
                "From - To",
                from_to,
            )
            historical_summary_cols[3].metric(
                "Points",
                int(points),
            )

    render_historical_summary()

    if trends_df.empty:
        st.info("No historical data available.")

    else:
        historical = trends_df.copy()
        data_version = (
            historical["timestamp"].max()
        )

        data_version = (
            historical["timestamp"].max()
        )

        ranking_df = build_live_overview_cached(
            historical,
            data_version,
            "historical-data",
        )

        if not ranking_df.empty:
            ranked_assets = ranking_df["Ticker"].tolist()
        else:
            ranked_assets = sorted(
                historical["ticker"]
                .dropna()
                .astype(str)
                .unique()
            )

        available_assets = sorted(
            historical["ticker"]
            .dropna()
            .astype(str)
            .unique()
        )

        if not available_assets:
            st.info("No assets available.")

        else:
            control_cols = st.columns(4)

            #
            # Current Simulation OPEN positions
            #
            open_position_tickers = []
            open_buy_times = {}

            try:
                simulation_trades = (
                    load_simulation_cached()
                )

                for trade in simulation_trades:
                    if trade.get("SellTime"):
                        continue

                    ticker = str(
                        trade.get("Ticker") or ""
                    ).strip().upper()

                    buy_time = pd.to_datetime(
                        trade.get("BuyTime"),
                        utc=True,
                        errors="coerce",
                    )

                    if ticker:
                        open_position_tickers.append(
                            ticker
                        )

                        if pd.notna(buy_time):
                            open_buy_times[
                                ticker
                            ] = buy_time

                open_position_tickers = sorted(
                    set(open_position_tickers)
                )

            except Exception as exc:
                st.warning(
                    "Cannot load Simulation OPEN positions: "
                    f"{exc}"
                )

            asset_mode = control_cols[0].selectbox(
                "Assets",
                [
                    "Top K",
                    "OPEN positions",
                    "OPEN positive positions",
                    "OPEN negative positions",
                    "Single",
                    "All",
                ],
                index=1,
                key="historical_data_assets_v4",
            )

            if asset_mode == "Single":
                selected_assets = [
                    st.selectbox(
                        "Asset",
                        ranked_assets,
                    )
                ]

            elif asset_mode == "Top K":
                k = st.number_input(
                        "Number of tickers",
                        min_value=1,
                        max_value=len(ranked_assets),
                        value=min(10, len(ranked_assets)),
                        step=1,
                )

                selected_assets = ranked_assets[:int(k)]
                st.caption(
                    "Selected from the current Live Overview ranking: "
                    + ", ".join(selected_assets)
                )

            elif asset_mode == "OPEN positions":
                selected_assets = [
                    ticker
                    for ticker
                    in open_position_tickers
                    if ticker
                    in available_assets
                ]
                if selected_assets:
                    st.caption(
                        "Current Simulation OPEN positions: "
                        + ", ".join(
                            selected_assets
                        )
                    )
                else:
                    st.info(
                        "There are currently no OPEN "
                        "Simulation positions with "
                        "Historical Data."
                    )

            elif asset_mode == "OPEN positive positions":
                # Start with all currently OPEN positions. The final positive
                # filter is applied after the selected Range/Metric has been
                # converted into the chart's Relative % values.
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if ticker in available_assets
                ]

            elif asset_mode == "OPEN negative positions":
                # Start with all currently OPEN positions. The final negative
                # filter is applied after the selected Range/Metric has been
                # converted into the chart's Relative % values.
                selected_assets = [
                    ticker
                    for ticker in open_position_tickers
                    if ticker in available_assets
                ]

            elif asset_mode == "All":
                selected_assets = ranked_assets
                st.caption(
                    f"Selected all {len(selected_assets)} tracked assets."
                )

            else:
                selected_assets = ranked_assets[:int(
                    min(10, len(ranked_assets))
                )]

            st.caption(
                "Asset filters: OPEN positions = currently open simulator trades. "
                "For OPEN positive/negative positions, the dashboard first builds "
                "the selected Historical Data chart and then classifies each OPEN "
                "ticker by its last plotted Relative % node: > 0% = positive, "
                "< 0% = negative, and exactly 0% belongs to neither group."
            )

            asset_df = (
                historical[
                    historical["ticker"].isin(selected_assets)
                ]
            #asset_df = (
            #    historical[
            #        historical["ticker"] == asset
            #    ]
                .sort_values(["timestamp", "id"])
                .drop_duplicates(
                    subset=["ticker", "timestamp"],
                    keep="last",
                )
                .copy()
            )

            excluded_numeric = {
                "id",
                "sell_time_seconds",
                "sell_time_over_seconds",
                "eur_usd",
            }

            preferred_metrics = [
                "close",
                "open",
                "high",
                "low",
                "vwap",
                "volume",
                "transactions",
            ]

            numeric_columns = [
                column
                for column in asset_df.select_dtypes(
                    include="number"
                ).columns
                if column not in excluded_numeric
            ]

            metric_options = [
                metric
                for metric in preferred_metrics
                if metric in numeric_columns
            ]

            metric_options += [
                metric
                for metric in numeric_columns
                if metric not in metric_options
            ]

            if not metric_options:
                st.info(
                    "This asset has no numeric measurements "
                    "available to chart."
                )

            else:
                default_metric_index = (
                    metric_options.index("close")
                    if "close" in metric_options
                    else 0
                )

                metric = control_cols[1].selectbox(
                    "Metric",
                    metric_options,
                    index=default_metric_index,
                )

                range_label = control_cols[2].selectbox(
                    "Range",
                    [
                        "2h",
                        "6h",
                        "12h",
                        "1d",
                        "2d",
                        "7d",
                        "All",
                    ],
                    index=3,
                )

                norm = control_cols[3].radio(
                    "Norm",
                    ["Relative", "Absolute"],
                    horizontal=True,
                )

                range_map = {
                    "2h": pd.Timedelta(hours=2),
                    "6h": pd.Timedelta(hours=6),
                    "12h": pd.Timedelta(hours=12),
                    "1d": pd.Timedelta(days=1),
                    "2d": pd.Timedelta(days=2),
                    "7d": pd.Timedelta(days=7),
                }

                chart_columns = [
                    "ticker",
                    "timestamp",
                    metric,
                ]

                if "eur_usd" in asset_df.columns:
                    chart_columns.append("eur_usd")

                chart_df = asset_df[
                    chart_columns
                ].copy()

                chart_df[metric] = pd.to_numeric(
                    chart_df[metric],
                    errors="coerce",
                )
                if "eur_usd" in chart_df.columns:
                    chart_df["eur_usd"] = pd.to_numeric(
                        chart_df["eur_usd"],
                        errors="coerce",
                    )

                chart_df = chart_df.dropna(
                    subset=[metric]
                )

                if chart_df.empty:
                    st.info(
                        "No values available for the selected "
                        "asset and metric."
                    )

                else:
                    latest_time = chart_df[
                        "timestamp"
                    ].max()

                    # Resolve the market calendar once for both trading-time
                    # range selection and closed-time compression on the x-axis.
                    if asset_mode == "Single" and selected_assets:
                        selected_ticker = str(selected_assets[0]).strip().upper()
                        ticker_rows = asset_df[
                            asset_df["ticker"].astype(str).str.upper().eq(selected_ticker)
                        ]
                        asset_type = (
                            str(ticker_rows["asset_type"].iloc[-1])
                            if not ticker_rows.empty and "asset_type" in ticker_rows.columns
                            else "stock"
                        )
                        historical_market_region = market_region_for_ticker(
                            selected_ticker,
                            asset_type,
                        )
                    else:
                        # Historical Data phase bands currently use the US/Polygon
                        # calendar for multi-asset stock charts.
                        historical_market_region = "US"

                    selected_range_start = None
                    if range_label != "All":
                        if range_label in {"2h", "6h", "12h"}:
                            start_time = historical_trading_window_start(
                                latest_time,
                                range_map[range_label],
                                historical_market_region,
                            )
                        else:
                            start_time = latest_time - range_map[range_label]

                        selected_range_start = start_time
                        chart_df = chart_df[
                            chart_df["timestamp"] >= start_time
                        ].copy()

                    if chart_df.empty:
                        st.info(
                            "No measurements available in "
                            "the selected range."
                        )

                    else:
                        chart_df["Local Time"] = (
                            chart_df["timestamp"]
                            .dt.tz_convert(
                                LOCAL_TIMEZONE
                            )
                        )

                    y_column = metric
                    y_label = metric

                    price_metrics = {
                        "open",
                        "high",
                        "low",
                        "close",
                        "vwap",
                    }

                    if (
                        norm == "Absolute"
                        and metric in price_metrics
                        and "eur_usd" in chart_df.columns
                    ):
                        chart_df["Value EUR"] = (
                            chart_df[metric]
                            / chart_df["eur_usd"]
                        )

                        y_column = "Value EUR"
                        y_label = f"{metric} (EUR)"

                    elif norm == "Relative":
                        chart_df["Relative %"] = (
                            chart_df
                            .groupby("ticker")[metric]
                            .transform(
                                lambda series:
                                (
                                    series / series.iloc[0] - 1
                                ) * 100
                                if len(series) > 0
                                and pd.notna(series.iloc[0])
                                and series.iloc[0] != 0
                                else float("nan")
                            )
                        )

                        y_column = "Relative %"
                        y_label = f"{metric} change (%)"

                    #
                    # OPEN positive/negative filtering is based on the final
                    # Relative % node that will actually be plotted for each
                    # ticker in the currently selected range and metric.
                    #
                    if asset_mode in {
                        "OPEN positive positions",
                        "OPEN negative positions",
                    }:
                        relative_for_filter = chart_df.copy()

                        # The requested filters are explicitly defined by the
                        # Relative node, regardless of whether the user selected
                        # Absolute for the visible y-axis.
                        relative_for_filter["_FilterRelative"] = (
                            relative_for_filter
                            .groupby("ticker")[metric]
                            .transform(
                                lambda series:
                                (
                                    series / series.iloc[0] - 1
                                ) * 100
                                if len(series) > 0
                                and pd.notna(series.iloc[0])
                                and series.iloc[0] != 0
                                else float("nan")
                            )
                        )

                        last_relative = (
                            relative_for_filter
                            .sort_values(["ticker", "timestamp"])
                            .groupby("ticker", as_index=False)
                            .tail(1)
                            .set_index("ticker")["_FilterRelative"]
                        )

                        if asset_mode == "OPEN positive positions":
                            matching_tickers = set(
                                last_relative[
                                    last_relative > 0
                                ].index.astype(str)
                            )
                            filter_description = (
                                "last plotted Relative % node > 0%"
                            )
                        else:
                            matching_tickers = set(
                                last_relative[
                                    last_relative < 0
                                ].index.astype(str)
                            )
                            filter_description = (
                                "last plotted Relative % node < 0%"
                            )

                        selected_assets = [
                            ticker
                            for ticker in selected_assets
                            if str(ticker) in matching_tickers
                        ]

                        chart_df = chart_df[
                            chart_df["ticker"]
                            .astype(str)
                            .isin(selected_assets)
                        ].copy()

                        if selected_assets:
                            st.caption(
                                f"{asset_mode}: "
                                + ", ".join(selected_assets)
                                + f" ({filter_description})."
                            )
                        else:
                            st.info(
                                "No currently OPEN Simulation positions "
                                f"have {filter_description} for the selected "
                                "metric and range."
                            )

                    #
                    # Load ALL historical Simulation trades so every chart
                    # timepoint can be classified by the simulator state that
                    # applied at that time.
                    #
                    historical_trade_intervals = []
                    historical_buy_points = {}
                    historical_sell_points = {}

                    try:
                        simulation_rows = load_simulation_cached()
                        simulation_df = pd.DataFrame(
                            simulation_rows
                        )

                        if not simulation_df.empty:
                            for column in [
                                "BuyTime",
                                "SellTime",
                            ]:
                                if column not in simulation_df.columns:
                                    simulation_df[column] = pd.NaT

                                simulation_df[column] = pd.to_datetime(
                                    simulation_df[column],
                                    utc=True,
                                    errors="coerce",
                                )

                            if "Ticker" not in simulation_df.columns:
                                simulation_df["Ticker"] = ""

                            simulation_df["Ticker"] = (
                                simulation_df["Ticker"]
                                .astype(str)
                                .str.strip()
                                .str.upper()
                            )

                            simulation_df = simulation_df[
                                simulation_df["Ticker"].ne("")
                                & simulation_df["BuyTime"].notna()
                            ].copy()

                            simulation_df = simulation_df.sort_values(
                                ["Ticker", "BuyTime"]
                            )

                            for ticker, ticker_trades in (
                                simulation_df.groupby(
                                    "Ticker",
                                    sort=False,
                                )
                            ):
                                ticker_trades = ticker_trades.sort_values(
                                    "BuyTime"
                                )

                                for _, trade in ticker_trades.iterrows():
                                    buy_time = trade.get("BuyTime")
                                    sell_time = trade.get("SellTime")

                                    historical_trade_intervals.append(
                                        (
                                            ticker,
                                            buy_time,
                                            sell_time,
                                            str(
                                                trade.get("SellReason") or ""
                                            ).strip().upper(),
                                        )
                                    )

                                    # Attach each simulator transition to exactly
                                    # one plotted market-data node.  Using a 15-minute
                                    # bucket as the lookup key caused every chart point
                                    # inside that bucket to inherit Action=Buy (for
                                    # example 15:45, 16:00 and 16:15 in sparse/irregular
                                    # data).  Instead select the latest actual plotted
                                    # timestamp at or before the simulator event.
                                    ticker_chart_times = chart_df.loc[
                                        chart_df["ticker"]
                                        .astype(str)
                                        .str.upper()
                                        .eq(ticker),
                                        "timestamp",
                                    ]

                                    buy_candidates = ticker_chart_times[
                                        ticker_chart_times <= buy_time
                                    ]
                                    if not buy_candidates.empty:
                                        buy_point = buy_candidates.max()
                                        historical_buy_points[(ticker, buy_point)] = {
                                            "buy_time": buy_time,
                                            "sell_time": sell_time,
                                        }

                                    if pd.notna(sell_time):
                                        sell_candidates = ticker_chart_times[
                                            ticker_chart_times <= sell_time
                                        ]
                                        if not sell_candidates.empty:
                                            sell_open_point = sell_candidates.max()
                                            historical_sell_points[
                                                (ticker, sell_open_point)
                                            ] = {
                                                "buy_time": buy_time,
                                                "sell_time": sell_time,
                                                "sell_reason": str(
                                                    trade.get("SellReason") or ""
                                                ).strip().upper(),
                                            }

                    except Exception as exc:
                        st.warning(
                            "Cannot load historical Simulation trades for "
                            f"chart highlighting: {exc}"
                        )

                    #
                    # Market-phase background bands. Use exactly the same
                    # US phase configuration and colors as Resources.
                    #
                    historical_open_intervals = []
                    historical_prepost_intervals = []

                    polygon_config = TRADING_WINDOWS.get("US") or {}

                    if (
                        polygon_config.get("enabled", True)
                        and not chart_df.empty
                    ):
                        phase_config = dict(
                            DEFAULT_TRADING_PHASES.get("US") or {}
                        )
                        phase_config.update(
                            TRADING_PHASES.get("US") or {}
                        )

                        polygon_timezone = str(
                            phase_config.get("timezone")
                            or polygon_config.get(
                                "timezone",
                                "America/New_York",
                            )
                        )
                        polygon_tz = ZoneInfo(
                            polygon_timezone
                        )

                        def _historical_minutes_from_hhmm(
                            value,
                            default,
                        ):
                            raw = str(value or default)
                            hour_text, minute_text = raw.split(
                                ":",
                                1,
                            )
                            return (
                                int(hour_text) * 60
                                + int(minute_text)
                            )

                        pre_start_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get("pre_start"),
                                "04:00",
                            )
                        )
                        opening_start_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get(
                                    "opening_start"
                                ),
                                "09:30",
                            )
                        )
                        opening_end_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get(
                                    "opening_end"
                                ),
                                "16:00",
                            )
                        )
                        post_end_minute = (
                            _historical_minutes_from_hhmm(
                                phase_config.get("post_end"),
                                "20:00",
                            )
                        )

                        raw_weekdays = polygon_config.get(
                            "open_weekdays"
                        )
                        allowed_weekdays = (
                            {
                                "mon",
                                "tue",
                                "wed",
                                "thu",
                                "fri",
                            }
                            if raw_weekdays is None
                            else {
                                str(value)
                                .strip()
                                .lower()[:3]
                                for value in raw_weekdays
                            }
                        )

                        closed_dates = {
                            str(value)
                            for value in (
                                polygon_config.get(
                                    "closed_dates"
                                )
                                or []
                            )
                        }

                        historical_start = pd.to_datetime(
                            chart_df["timestamp"].min(),
                            utc=True,
                            errors="coerce",
                        )
                        historical_end = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )

                        if (
                            pd.notna(historical_start)
                            and pd.notna(historical_end)
                        ):
                            local_start = (
                                historical_start
                                .tz_convert(polygon_tz)
                                .normalize()
                            )
                            local_end = (
                                historical_end
                                .tz_convert(polygon_tz)
                                .normalize()
                            )

                            for local_day in pd.date_range(
                                start=local_start,
                                end=local_end,
                                freq="D",
                            ):
                                weekday_key = (
                                    local_day
                                    .strftime("%a")
                                    .lower()[:3]
                                )
                                date_key = (
                                    local_day
                                    .strftime("%Y-%m-%d")
                                )

                                if (
                                    weekday_key
                                    not in allowed_weekdays
                                    or date_key
                                    in closed_dates
                                ):
                                    continue

                                pre_start = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=pre_start_minute
                                    )
                                )
                                opening_start = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=opening_start_minute
                                    )
                                )
                                opening_end = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=opening_end_minute
                                    )
                                )
                                post_end = (
                                    local_day
                                    + pd.Timedelta(
                                        minutes=post_end_minute
                                    )
                                )

                                if pre_start < opening_start:
                                    historical_prepost_intervals.append(
                                        (
                                            pre_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            opening_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Pre-Trading",
                                        )
                                    )

                                if opening_start < opening_end:
                                    historical_open_intervals.append(
                                        (
                                            opening_start.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            opening_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Opening",
                                        )
                                    )

                                if opening_end < post_end:
                                    historical_prepost_intervals.append(
                                        (
                                            opening_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            post_end.tz_convert(
                                                LOCAL_TIMEZONE
                                            ),
                                            "Post-Trading",
                                        )
                                    )

                    # The date axis hides closed market intervals, but each ticker
                    # remains a continuous line across the compressed session break.
                    # This makes multi-ticker charts easy to follow from the final
                    # post-trading point to the next pre-trading point while still
                    # removing the irrelevant overnight/weekend space from the axis.
                    plot_df = chart_df.copy()

                    figure = px.line(
                        plot_df,
                        x="Local Time",
                        y=y_column,
                        color="ticker",
                        markers=True,
                        title=(
                            f"{metric} — " 
                            f"{len(selected_assets)} asset(s) "
                            f"({range_label}, {norm})"
                        ),
                    )
                    # Draw phase bands behind the ticker lines.
                    # Same colors/opacities as the Resources diagram.
                    for phase_start, phase_end, phase_name in (
                        historical_prepost_intervals
                    ):
                        figure.add_vrect(
                            x0=phase_start,
                            x1=phase_end,
                            fillcolor="#f4a261",
                            opacity=0.16,
                            layer="below",
                            line_width=0,
                        )

                    for phase_start, phase_end, phase_name in (
                        historical_open_intervals
                    ):
                        figure.add_vrect(
                            x0=phase_start,
                            x1=phase_end,
                            fillcolor="#c6dbef",
                            opacity=0.12,
                            layer="below",
                            line_width=0,
                        )

                    #
                    # Highlight every historical point at which the ticker
                    # was in simulator status OPEN. SellTime itself is not
                    # OPEN; intervals therefore use [BuyTime, SellTime).
                    #
                    open_points_parts = []

                    for (
                        ticker,
                        buy_time,
                        sell_time,
                        sell_reason,
                    ) in historical_trade_intervals:
                        mask = (
                            chart_df["ticker"]
                            .astype(str)
                            .str.upper()
                            .eq(ticker)
                            & (
                                chart_df["timestamp"]
                                >= pd.Timestamp(buy_time).floor("15min")
                            )
                        )

                        if pd.notna(sell_time):
                            mask = (
                                mask
                                & (
                                    chart_df["timestamp"]
                                    < sell_time
                                )
                            )

                        ticker_points = chart_df[
                            mask
                        ].copy()

                        if not ticker_points.empty:
                            ticker_points[
                                "_OpenTicker"
                            ] = ticker

                            # Preserve the exact simulator interval responsible
                            # for this OPEN point.
                            ticker_points["_TradeBuyTime"] = buy_time
                            ticker_points["_TradeSellTime"] = sell_time
                            ticker_points["_TradeSellReason"] = sell_reason

                            open_points_parts.append(
                                ticker_points
                            )

                    if open_points_parts:
                        open_points = pd.concat(
                            open_points_parts,
                            ignore_index=True,
                        )

                        # Avoid duplicate overlay markers if malformed or
                        # overlapping simulator records exist.
                        open_points = (
                            open_points
                            .sort_values(
                                ["ticker", "timestamp"]
                            )
                            .drop_duplicates(
                                subset=[
                                    "ticker",
                                    "timestamp",
                                ],
                                keep="last",
                            )
                        )

                        #
                        # Every simulator BUY/init point is drawn as a triangle.
                        # All other OPEN timepoints use larger circles.
                        #
                        # Keep the exact plotted timestamp for transition lookup.
                        # A simulator BUY/SELL must label only one chart node.
                        open_points["_ActionTime"] = open_points["timestamp"]

                        def _historical_close2h_reason(ticker, point_time):
                            source = historical[
                                historical["ticker"].astype(str).str.upper().eq(
                                    str(ticker).upper()
                                )
                            ].copy()
                            if source.empty:
                                return "Close2h = —"

                            source["timestamp"] = pd.to_datetime(
                                source["timestamp"], utc=True, errors="coerce"
                            )
                            source["_CloseNumeric"] = pd.to_numeric(
                                source["close"], errors="coerce"
                            )
                            source = source[
                                source["timestamp"].notna()
                                & source["_CloseNumeric"].notna()
                                & (source["_CloseNumeric"] > 0)
                                & (source["timestamp"] <= point_time)
                            ].sort_values("timestamp")

                            if source.empty:
                                return "Close2h = —"

                            latest_price = float(source.iloc[-1]["_CloseNumeric"])
                            target = point_time - pd.Timedelta(
                                hours=float(BUY_CONFIG.get("baseline_hours", 2))
                            )
                            tolerance = pd.Timedelta(
                                minutes=int(
                                    BUY_CONFIG.get("baseline_tolerance_minutes", 30)
                                )
                            )
                            candidates = source[
                                (source["timestamp"] - target).abs() <= tolerance
                            ].copy()
                            if candidates.empty:
                                return "Close2h = —"

                            candidates["_Distance"] = (
                                candidates["timestamp"] - target
                            ).abs()
                            baseline_price = float(
                                candidates.sort_values(
                                    ["_Distance", "timestamp"]
                                ).iloc[0]["_CloseNumeric"]
                            )
                            if baseline_price <= 0:
                                return "Close2h = —"

                            close2h = (
                                latest_price / baseline_price - 1.0
                            ) * 100.0
                            return f"Close2h = {close2h:.2f}%"

                        def _historical_sell_reason(
                            ticker,
                            buy_time,
                            point_time,
                            sell_reason,
                        ):
                            source = historical[
                                historical["ticker"].astype(str).str.upper().eq(
                                    str(ticker).upper()
                                )
                            ].copy()
                            if source.empty:
                                return "—"

                            source["timestamp"] = pd.to_datetime(
                                source["timestamp"], utc=True, errors="coerce"
                            )
                            source["_CloseNumeric"] = pd.to_numeric(
                                source["close"], errors="coerce"
                            )
                            source = source[
                                source["timestamp"].notna()
                                & source["_CloseNumeric"].notna()
                                & (source["_CloseNumeric"] > 0)
                                & (source["timestamp"] >= buy_time)
                                & (source["timestamp"] <= point_time)
                            ].sort_values("timestamp")

                            if source.empty:
                                return "—"

                            last_price = float(source.iloc[-1]["_CloseNumeric"])
                            peak_price = float(source["_CloseNumeric"].max())
                            drop_value = (
                                ((peak_price - last_price) / peak_price) * 100.0
                                if peak_price > 0
                                else float("nan")
                            )
                            change_value = float(
                                (((source["_CloseNumeric"] / last_price) - 1.0)
                                 .abs().max()) * 100.0
                            )

                            reason = str(sell_reason or "").upper()
                            if "C4" in reason:
                                return f"DropInitTimeLatest = {drop_value:.2f}%"
                            if "C5" in reason:
                                return f"ChangeInitTimeLatest = {change_value:.2f}%"

                            if drop_value >= change_value:
                                return f"DropInitTimeLatest = {drop_value:.2f}%"
                            return f"ChangeInitTimeLatest = {change_value:.2f}%"

                        def _historical_open_action(row):
                            key = (
                                str(row["ticker"]).upper(),
                                row["_ActionTime"],
                            )

                            # Both BUY and SELL are attached to the preceding
                            # 15-minute chart point. Prefer BUY if transition
                            # markers ever coincide at the same plotted point.
                            if key in historical_buy_points:
                                return "Buy"
                            if key in historical_sell_points:
                                return "Sell"
                            return "-"

                        def _historical_open_reason(row):
                            action = _historical_open_action(row)
                            key = (
                                str(row["ticker"]).upper(),
                                row["_ActionTime"],
                            )

                            if action == "Buy":
                                return _historical_close2h_reason(
                                    row["ticker"],
                                    row["timestamp"],
                                )

                            if action == "Sell":
                                meta = historical_sell_points.get(key) or {}
                                return _historical_sell_reason(
                                    row["ticker"],
                                    meta.get("buy_time"),
                                    row["timestamp"],
                                    meta.get("sell_reason"),
                                )

                            return "-"

                        open_points["_Action"] = open_points.apply(
                            _historical_open_action, axis=1
                        )
                        open_points["_Reason"] = open_points.apply(
                            _historical_open_reason, axis=1
                        )

                        open_points["_IsBuyPoint"] = (
                            open_points["_Action"].eq("Buy")
                        )

                        regular_open_points = open_points[
                            ~open_points["_IsBuyPoint"]
                        ].copy()

                        buy_points = open_points[
                            open_points["_IsBuyPoint"]
                        ].copy()

                        if not regular_open_points.empty:
                            figure.add_scatter(
                                x=regular_open_points[
                                    "Local Time"
                                ],
                                y=regular_open_points[
                                    y_column
                                ],
                                mode="markers",
                                marker={
                                    "color": "green",
                                    "size": 10,
                                    "symbol": "circle",
                                    "line": {
                                        "width": 2,
                                    },
                                },
                                name="Simulator OPEN",
                                customdata=regular_open_points[
                                    ["ticker", "_Action", "_Reason"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>Action: %{customdata[1]}"
                                    "<br>Reason: %{customdata[2]}"
                                    "<extra></extra>"
                                ),
                            )

                        if not buy_points.empty:
                            figure.add_scatter(
                                x=buy_points[
                                    "Local Time"
                                ],
                                y=buy_points[
                                    y_column
                                ],
                                mode="markers",
                                marker={
                                    "color": "green",
                                    "size": 13,
                                    "symbol": "triangle-up",
                                    "line": {
                                        "width": 2,
                                    },
                                },
                                name="Simulator BUY / Init",
                                customdata=buy_points[
                                    ["ticker", "_Action", "_Reason"]
                                ],
                                hovertemplate=(
                                    "<b>%{customdata[0]}</b>"
                                    "<br>Simulator status: OPEN"
                                    "<br>Action: %{customdata[1]}"
                                    "<br>Reason: %{customdata[2]}"
                                    "<extra></extra>"
                                ),
                            )

                    # Keep market-phase rectangles from expanding the visible
                    # x-axis beyond the selected Historical Data range. For 2h/6h/12h
                    # the start can be much earlier in wall-clock time because the
                    # overnight non-trading gap is intentionally skipped.
                    historical_axis_after = {
                        "2h": pd.Timedelta(hours=0.5),
                        "6h": pd.Timedelta(hours=1),
                        "12h": pd.Timedelta(hours=2),
                        "1d": pd.Timedelta(hours=4),
                        "2d": pd.Timedelta(hours=8),
                        "7d": pd.Timedelta(days=1, hours=4),
                    }

                    if (
                        range_label in historical_axis_after
                        and not chart_df.empty
                    ):
                        visible_last = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )

                        if pd.notna(visible_last):
                            if selected_range_start is not None and pd.notna(selected_range_start):
                                xaxis_start = pd.to_datetime(
                                    selected_range_start,
                                    utc=True,
                                ).tz_convert(LOCAL_TIMEZONE)
                            else:
                                xaxis_start = pd.to_datetime(
                                    chart_df["timestamp"].min(),
                                    utc=True,
                                ).tz_convert(LOCAL_TIMEZONE)

                            xaxis_end = (
                                visible_last + historical_axis_after[range_label]
                            ).tz_convert(LOCAL_TIMEZONE)

                            figure.update_xaxes(
                                range=[
                                    xaxis_start,
                                    xaxis_end,
                                ]
                            )

                    # Remove closed periods from the visual x-axis entirely.
                    # Friday's final post-trading node and Monday's first pre-trading
                    # node therefore appear next to each other and remain connected
                    # by that ticker's line. The line represents continuity between
                    # observed market points, not elapsed closed-market time.
                    if not chart_df.empty:
                        break_start = (
                            pd.to_datetime(selected_range_start, utc=True, errors="coerce")
                            if selected_range_start is not None
                            else pd.to_datetime(chart_df["timestamp"].min(), utc=True, errors="coerce")
                        )
                        break_end = pd.to_datetime(
                            chart_df["timestamp"].max(),
                            utc=True,
                            errors="coerce",
                        )
                        market_rangebreaks = historical_market_rangebreaks(
                            break_start,
                            break_end,
                            historical_market_region,
                        )
                        if market_rangebreaks:
                            figure.update_xaxes(rangebreaks=market_rangebreaks)

                    figure.update_layout(
                        xaxis_title="Trading time (closed periods hidden)",
                        yaxis_title=y_label,
                        # Use point-specific hover. With ``x unified`` Plotly also
                        # shows the nearest point from sparse overlay traces. That
                        # caused a BUY triangle at 16:00 to appear in the tooltip
                        # while hovering the ordinary 15:45 market-data node.
                        # ``closest`` keeps Action/Reason attached to the exact
                        # marker being hovered.
                        hovermode="closest",
                    )

                    st.plotly_chart(
                        figure,
                        use_container_width=True,
                    )

                    st.caption(
                        "Market phases: background bands use the same US/Polygon phase "
                        "configuration as Resources. Opening is light blue; Pre-Trading "
                        "and Post-Trading share the orange background. Weekdays, closed "
                        "dates, phase times, and timezone come from the current settings. "
                        "For the 2h, 6h, and 12h ranges, only effective trading time is counted; "
                        "overnight closures, weekends, and configured closed dates are skipped. "
                        "Closed intervals are also compressed out of the x-axis, and the price line "
                        "is broken at each session boundary so separate sessions are not connected. "
                        "The x-axis therefore starts "
                        "at the calculated trading-time boundary and keeps a small amount of "
                        "context after the latest point."
                    )

                    st.caption(
                        "Historical Data node meaning: the normal line markers are market-data "
                        "observations. Larger green circular nodes mark every displayed 15-minute "
                        "timepoint during which that ticker had simulator status OPEN. A green "
                        "triangle marks every simulator BUY/init node, including the first buy and "
                        "all later re-buys. BUY times are mapped to the chart node at or immediately "
                        "before the simulator BuyTime. "
                        "SellTime itself is treated as CLOSED, so OPEN highlighting ends before "
                        "the SellTime point."
                    )

                    first_time = chart_df[
                        "Local Time"
                    ].iloc[0]

                    last_time = chart_df[
                        "Local Time"
                    ].iloc[-1]

                    newest_historical = pd.to_datetime(
                        historical["timestamp"].max(),
                        utc=True,
                        errors="coerce",
                    )
                    newest_historical_text = (
                        newest_historical
                        .tz_convert(LOCAL_TIMEZONE)
                        .strftime("%H:%M %Z")
                        if pd.notna(newest_historical)
                        else "—"
                    )

                    render_historical_summary(
                        from_to=(
                            first_time.strftime("%d.%m %H:%M")
                            + " - "
                            + last_time.strftime("%d.%m %H:%M")
                        ),
                        points=len(chart_df),
                    )

                    st.subheader(
                        "Steps to analyse the ticker trading periods"
                    )
                    st.markdown(
                        "1. Are the start and end time points of the OPEN ticker "
                        "proposed by the simulator as expected?\n"
                        "2. If not, review the ticker in the **Historical Data** and "
                        "**Last Data** pages to understand the behavior and update "
                        "the simulator rules that decide when to buy and sell the ticker."
                    )

elif page == "Sim-Trading":
    st.header("Sim-Trading")

    # News is informational only. Failure to load it must never prevent
    # Sim-Trading or any trading-rule calculation from working.
    sim_news_map = latest_ticker_news_map(
        load_ticker_news_cached()
    )
    sim_market_for_summary = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    sim_summary_placeholder = st.empty()

    def render_sim_summary(
        open_count: int = 0,
        day_max_open_count: int = 0,
    ) -> None:
        available_slots = max(0, BUY_MAX_OPEN_TICKERS - int(open_count))
        with sim_summary_placeholder.container():
            portfolio_cols = st.columns(3)
            portfolio_cols[0].metric(
                "Newest Data",
                format_newest_data(sim_market_for_summary),
            )
            portfolio_cols[1].metric(
                "LastOPENTickers / DayMaxOPENTickers",
                f"{int(open_count)}/{int(day_max_open_count)}",
            )
            portfolio_cols[2].metric(
                "AvailableBUYTickers",
                available_slots,
            )

    render_sim_summary()

    st.caption(
        "Current simulator trading state for every active market ticker. "
        "OPEN means the latest simulated trade is still open; CLOSED means "
        "the latest simulated trade has been sold; NO TRADE means no "
        "simulation BUY has been recorded for that ticker."
    )

    try:
        simulation_rows = load_simulation_cached()
    except Exception as exc:
        st.error(f"Cannot load simulation data: {exc}")
    else:
        simulation_df = pd.DataFrame(simulation_rows)

        market_df = df[
            df["asset_type"].isin(["stock", "crypto"])
        ].copy()

        if market_df.empty:
            st.info("No market ticker data are available.")
        else:
            market_df["timestamp"] = pd.to_datetime(
                market_df["timestamp"],
                utc=True,
                errors="coerce",
            )
            market_df["received_at"] = pd.to_datetime(
                market_df["received_at"],
                utc=True,
                errors="coerce",
            )

            newest_received = market_df["received_at"].max()
            latest_received = market_df.groupby("ticker")["received_at"].max()

            source_latest_received = (
                market_df.groupby("system")["received_at"].max()
                if "system" in market_df.columns
                else pd.Series(dtype="datetime64[ns, UTC]")
            )
            # Keep configured/known tickers visible even when their source is
            # quiet because the market is closed.
            configured_now = {
                str(ticker)
                for ticker in latest_received.index
            }

            latest_market_rows = (
                market_df.sort_values(["timestamp", "id"])
                .drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")
            )

            market_reference_time = pd.to_datetime(
                market_df["timestamp"].max(),
                utc=True,
            )

            stale_tickers = set()
            for ticker in configured_now:
                if ticker not in latest_market_rows.index:
                    stale_tickers.add(ticker)
                    continue

                latest_row = latest_market_rows.loc[ticker]
                latest_bar_time = pd.to_datetime(
                    latest_row.get("timestamp"),
                    utc=True,
                    errors="coerce",
                )

                if pd.isna(latest_bar_time):
                    stale_tickers.add(ticker)
                    continue

                asset_type = str(
                    latest_row.get("asset_type") or ""
                ).strip().lower()

                maximum_bar_age = (
                    pd.Timedelta(minutes=60)
                    if asset_type == "crypto"
                    else pd.Timedelta(hours=72)
                )

                if market_reference_time - latest_bar_time > maximum_bar_age:
                    stale_tickers.add(ticker)

            active_tickers = sorted(configured_now - stale_tickers)

            active_market_df = market_df[
                market_df["ticker"].astype(str).isin(active_tickers)
            ].copy()

            live_now = (
                build_live_overview_cached(
                    active_market_df,
                    (
                        active_market_df["timestamp"].max(),
                        active_market_df["id"].max(),
                    ),
                    "sim-trading",
                )
                if not active_market_df.empty
                else pd.DataFrame()
            )

            current_price_map = {}
            if not live_now.empty and "Ticker" in live_now.columns:
                current_price_map = (
                    live_now
                    .dropna(subset=["Ticker"])
                    .drop_duplicates(subset=["Ticker"], keep="first")
                    .set_index("Ticker")["Price"]
                    .to_dict()
                )

            last_time_map = (
                active_market_df
                .sort_values(["timestamp", "id"])
                .drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")["timestamp"]
                .to_dict()
                if not active_market_df.empty
                else {}
            )

            if simulation_df.empty:
                latest_sim = pd.DataFrame(columns=[
                    "Ticker",
                    "TickerName",
                    "BuyTime",
                    "BuyPriceEUR",
                    "SellTime",
                    "SellPriceEUR",
                    "RelativeDifference",
                    "SellReason",
                    "Status",
                ])
            else:
                for column in ["BuyTime", "SellTime"]:
                    if column not in simulation_df.columns:
                        simulation_df[column] = pd.NaT
                    simulation_df[column] = pd.to_datetime(
                        simulation_df[column],
                        utc=True,
                        errors="coerce",
                    )

                for column in [
                    "BuyPriceEUR",
                    "SellPriceEUR",
                    "RelativeDifference",
                ]:
                    if column not in simulation_df.columns:
                        simulation_df[column] = pd.NA
                    simulation_df[column] = pd.to_numeric(
                        simulation_df[column],
                        errors="coerce",
                    )

                if "Ticker" not in simulation_df.columns:
                    simulation_df["Ticker"] = ""

                simulation_df["Ticker"] = (
                    simulation_df["Ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                latest_sim = (
                    simulation_df
                    .sort_values(["Ticker", "BuyTime"], na_position="first")
                    .groupby("Ticker", as_index=False)
                    .tail(1)
                    .copy()
                )

            shown = pd.DataFrame({"Ticker": active_tickers})

            ticker_name_map = dict(TICKER_NAMES)
            if not latest_sim.empty and "TickerName" in latest_sim.columns:
                sim_name_map = (
                    latest_sim
                    .dropna(subset=["Ticker"])
                    .drop_duplicates(subset=["Ticker"], keep="last")
                    .set_index("Ticker")["TickerName"]
                    .to_dict()
                )
                for ticker, name in sim_name_map.items():
                    if pd.notna(name) and str(name).strip():
                        ticker_name_map[str(ticker)] = str(name).strip()

            shown["TickerName"] = shown["Ticker"].map(
                lambda ticker: ticker_name_map.get(ticker, ticker)
            )

            if not latest_sim.empty:
                merge_columns = [
                    column
                    for column in [
                        "Ticker",
                        "BuyTime",
                        "BuyPriceEUR",
                        "SellTime",
                        "SellPriceEUR",
                        "RelativeDifference",
                        "SellReason",
                        "Status",
                    ]
                    if column in latest_sim.columns
                ]
                shown = shown.merge(
                    latest_sim[merge_columns],
                    on="Ticker",
                    how="left",
                )

            for column, default in {
                "BuyTime": pd.NaT,
                "BuyPriceEUR": pd.NA,
                "SellTime": pd.NaT,
                "SellPriceEUR": pd.NA,
                "RelativeDifference": pd.NA,
                "SellReason": pd.NA,
                "Status": pd.NA,
            }.items():
                if column not in shown.columns:
                    shown[column] = default

            shown["LastTimeRaw"] = shown["Ticker"].map(last_time_map)
            shown["LastPriceRaw"] = shown["Ticker"].map(current_price_map)

            shown["BuyPriceEUR"] = pd.to_numeric(
                shown["BuyPriceEUR"], errors="coerce"
            )
            shown["SellPriceEUR"] = pd.to_numeric(
                shown["SellPriceEUR"], errors="coerce"
            )
            shown["LastPriceRaw"] = pd.to_numeric(
                shown["LastPriceRaw"], errors="coerce"
            )

            shown["SimStatus"] = shown["Status"].map(
                lambda value:
                str(value).strip().upper()
                if pd.notna(value) and str(value).strip()
                else "NO TRADE"
            )

            current_open_count = int((shown["SimStatus"] == "OPEN").sum())

            # Maximum simultaneously OPEN simulator positions during the active
            # local 03:00 -> 03:00 accounting day. Positions already OPEN at
            # 03:00 form the starting count; BUYs add one and SELLs subtract one.
            now_local = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
            sim_accounting_day = now_local.normalize()
            if now_local.hour < 3:
                sim_accounting_day = sim_accounting_day - pd.Timedelta(days=1)
            sim_day_start_utc = (
                sim_accounting_day + pd.Timedelta(hours=3)
            ).tz_convert("UTC")
            sim_day_end_utc = sim_day_start_utc + pd.Timedelta(days=1)
            sim_effective_end_utc = min(
                sim_day_end_utc,
                pd.Timestamp.now(tz="UTC"),
            )

            day_max_open_count = 0
            if not simulation_df.empty:
                open_at_start = (
                    simulation_df["BuyTime"].notna()
                    & (simulation_df["BuyTime"] < sim_day_start_utc)
                    & (
                        simulation_df["SellTime"].isna()
                        | (simulation_df["SellTime"] >= sim_day_start_utc)
                    )
                )
                running_open_count = int(open_at_start.sum())
                day_max_open_count = running_open_count

                buy_events = simulation_df.loc[
                    simulation_df["BuyTime"].notna()
                    & (simulation_df["BuyTime"] >= sim_day_start_utc)
                    & (simulation_df["BuyTime"] <= sim_effective_end_utc),
                    ["BuyTime"],
                ].copy()
                buy_events["EventTime"] = buy_events["BuyTime"]
                buy_events["Delta"] = 1
                buy_events["EventOrder"] = 0

                sell_events = simulation_df.loc[
                    simulation_df["SellTime"].notna()
                    & (simulation_df["SellTime"] >= sim_day_start_utc)
                    & (simulation_df["SellTime"] <= sim_effective_end_utc),
                    ["SellTime"],
                ].copy()
                sell_events["EventTime"] = sell_events["SellTime"]
                sell_events["Delta"] = -1
                sell_events["EventOrder"] = 1

                sim_day_events = pd.concat(
                    [
                        buy_events[["EventTime", "Delta", "EventOrder"]],
                        sell_events[["EventTime", "Delta", "EventOrder"]],
                    ],
                    ignore_index=True,
                ).sort_values(
                    ["EventTime", "EventOrder"],
                    ascending=[True, True],
                )

                for _, sim_event in sim_day_events.iterrows():
                    running_open_count += int(sim_event["Delta"])
                    day_max_open_count = max(
                        day_max_open_count,
                        running_open_count,
                    )

            render_sim_summary(current_open_count, day_max_open_count)

            def sim_trade_reason(row):
                status = str(row.get("SimStatus") or "").strip().upper()

                if status == "OPEN":
                    # The simulation API currently persists SellReason but no
                    # equivalent BuyReason. "Buy signal" therefore describes
                    # the actual transition without inventing a non-persisted
                    # trigger.
                    return "Buy signal"

                if status == "CLOSED":
                    sell_reason = str(
                        row.get("SellReason") or ""
                    ).strip().upper()

                    if sell_reason in {"C4+C5", "C5+C4"}:
                        return "Drop + static"
                    if sell_reason == "C4":
                        return "Price drop"
                    if sell_reason == "C5":
                        return "Price static"
                    if sell_reason:
                        # Keep unknown backend reason concise.
                        return sell_reason[:24]
                    return "Sell signal"

                return "—"

            shown["SimReason"] = shown.apply(
                sim_trade_reason,
                axis=1,
            )

            # Show the current trading-condition values used by Zero-Trading.
            # C2 is calculated explicitly from each ticker's latest available
            # CloseB so a ticker does not become C2=False merely because its
            # newest bar is older than the newest global market timestamp.
            # C4/C5 start with the live-overview evaluation. For currently OPEN
            # simulated positions, C4/C5 are recalculated from the latest
            # simulated BuyTime and C6/C7 are evaluated with the same
            # end-of-day rules used on the Zero-Trading page.
            for condition in ["C2", "C4", "C5", "C6", "C7"]:
                shown[condition] = False
            shown["_SimBoughtBefore"] = pd.NA
            shown["_SimCoreShouldSell"] = False
            shown["_SimSellWindowOpen"] = False
            shown["_SimSellDataReady"] = False
            shown["_SimBoughtBefore"] = pd.NA
            shown["_SimCoreShouldSell"] = False
            shown["_SimSellWindowOpen"] = False
            shown["_SimSellDataReady"] = False

            if not live_now.empty and "Ticker" in live_now.columns:
                condition_live = (
                    live_now
                    .dropna(subset=["Ticker"])
                    .assign(
                        _TickerKey=lambda frame: (
                            frame["Ticker"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )
                    )
                    .drop_duplicates(subset=["_TickerKey"], keep="first")
                    .set_index("_TickerKey")
                )

                # C2 is a two-stage condition:
                # 1) market breadth must meet BUY_MIN_CLOSEB_COUNT; and
                # 2) the individual ticker's latest CloseB must meet the
                #    configured BUY_MIN_CLOSEB_PERCENT threshold.
                if "CloseB" in condition_live.columns:
                    latest_closeb = pd.to_numeric(
                        condition_live["CloseB"],
                        errors="coerce",
                    )
                    c2_qualifying_count = int(
                        (latest_closeb >= BUY_MIN_CLOSEB_PERCENT).sum()
                    )
                    c2_breadth_satisfied = (
                        c2_qualifying_count >= BUY_MIN_CLOSEB_COUNT
                    )
                    c2_map = (
                        c2_breadth_satisfied
                        & (latest_closeb >= BUY_MIN_CLOSEB_PERCENT)
                    ).fillna(False).astype(bool).to_dict()
                    shown["C2"] = (
                        shown["Ticker"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .map(lambda ticker: bool(c2_map.get(ticker, False)))
                    )

                for condition in ["C4", "C5"]:
                    if condition in condition_live.columns:
                        condition_map = (
                            condition_live[condition]
                            .fillna(False)
                            .astype(bool)
                            .to_dict()
                        )
                        shown[condition] = (
                            shown["Ticker"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                            .map(
                                lambda ticker, values=condition_map:
                                bool(values.get(ticker, False))
                            )
                        )

            sim_latest_trade_map = {}
            if not latest_sim.empty and "Ticker" in latest_sim.columns:
                sim_latest_trade_map = (
                    latest_sim
                    .dropna(subset=["Ticker"])
                    .drop_duplicates(subset=["Ticker"], keep="last")
                    .set_index("Ticker")
                    .to_dict(orient="index")
                )

            sim_movement_threshold = float(
                SELL_CONFIG.get("movement_percent", 1.1)
            )
            sim_c5_hours = float(SELL_CONFIG.get("c5_hours", 24.0))
            sim_c6_min_gain_percent = float(
                SELL_CONFIG.get("c6_min_gain_percent", 2.0)
            )
            sim_c7_max_gain_percent = float(
                SELL_CONFIG.get("c7_max_gain_percent", 5.0)
            )
            sim_c6_close_minutes = float(
                SELL_CONFIG.get("c6_close_minutes", 30.0)
            )
            sim_c6_enabled = bool(SELL_CONFIG.get("c6_enabled", True))

            for idx, sim_row in shown.iterrows():
                if str(sim_row.get("SimStatus") or "").strip().upper() != "OPEN":
                    continue

                ticker_value = str(sim_row.get("Ticker") or "").strip().upper()
                init_time_latest = pd.to_datetime(
                    sim_row.get("BuyTime"),
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(init_time_latest):
                    continue

                ticker_history = (
                    active_market_df[
                        active_market_df["ticker"].astype(str).str.upper()
                        == ticker_value
                    ]
                    .sort_values(["timestamp", "id"])
                    .drop_duplicates(subset=["timestamp"], keep="last")
                    .copy()
                )
                if ticker_history.empty:
                    continue

                latest_row = ticker_history.iloc[-1]
                latest_time = pd.to_datetime(
                    latest_row.get("timestamp"),
                    utc=True,
                    errors="coerce",
                )
                current_price = pd.to_numeric(
                    latest_row.get("close"),
                    errors="coerce",
                )
                if (
                    pd.isna(latest_time)
                    or pd.isna(current_price)
                    or float(current_price) <= 0
                ):
                    continue

                market_region = market_region_for_ticker(
                    ticker_value,
                    latest_row.get("asset_type"),
                )
                market_config = (
                    TRADING_WINDOWS.get(market_region)
                    if market_region
                    else None
                )
                decision = evaluate_sell_history(
                    ticker_df=ticker_history,
                    latest_time=latest_time,
                    current_price=float(current_price),
                    movement_percent=sim_movement_threshold,
                    c5_hours=sim_c5_hours,
                    init_time=init_time_latest,
                    market_region=market_region,
                    market_config=market_config,
                    phase_config=c5_phase_config(market_region),
                )
                shown.at[idx, "_SimBoughtBefore"] = decision.bought_before
                shown.at[idx, "_SimCoreShouldSell"] = bool(decision.should_sell)
                shown.at[idx, "_SimSellDataReady"] = True
                shown.at[idx, "C4"] = bool(decision.c4_satisfied)
                shown.at[idx, "C5"] = bool(decision.c5_satisfied)

                open_trade = sim_latest_trade_map.get(ticker_value, {})
                c6_reference_price = pd.to_numeric(
                    open_trade.get("BuyPrice"),
                    errors="coerce",
                )
                if pd.isna(c6_reference_price):
                    c6_reference_price = pd.to_numeric(
                        open_trade.get("InitPrice"),
                        errors="coerce",
                    )

                c6_gain_percent = None
                if (
                    pd.notna(c6_reference_price)
                    and float(c6_reference_price) > 0
                ):
                    c6_gain_percent = (
                        float(current_price) / float(c6_reference_price) - 1.0
                    ) * 100.0

                sell_window = None
                c6_remaining_minutes = None
                if market_config:
                    action_time = pd.Timestamp.now(tz="UTC")
                    sell_window = trading_window_info(
                        action_time,
                        market_config,
                        "sell",
                    )
                    regular_close_value = market_config.get("regular_close")
                    if sell_window.is_open and regular_close_value:
                        try:
                            market_timezone = str(
                                market_config.get("timezone") or "UTC"
                            )
                            action_local = action_time.tz_convert(market_timezone)
                            regular_close_local = pd.Timestamp(
                                f"{action_local.date()} {regular_close_value}",
                                tz=market_timezone,
                            )
                            c6_remaining_minutes = (
                                regular_close_local - action_local
                            ).total_seconds() / 60.0
                        except Exception:
                            c6_remaining_minutes = None

                shown.at[idx, "_SimSellWindowOpen"] = bool(
                    sell_window is not None and sell_window.is_open
                )

                shown.at[idx, "C6"] = bool(
                    sim_c6_enabled
                    and not decision.c4_satisfied
                    and not decision.c5_satisfied
                    and sell_window is not None
                    and sell_window.is_open
                    and c6_remaining_minutes is not None
                    and 0.0 <= c6_remaining_minutes <= sim_c6_close_minutes
                    and c6_gain_percent is not None
                    and c6_gain_percent < sim_c6_min_gain_percent
                )

                shown.at[idx, "C7"] = bool(
                    not decision.c4_satisfied
                    and not decision.c5_satisfied
                    and sell_window is not None
                    and sell_window.is_open
                    and c6_remaining_minutes is not None
                    and 0.0 <= c6_remaining_minutes <= sim_c6_close_minutes
                    and c6_gain_percent is not None
                    and c6_gain_percent > sim_c7_max_gain_percent
                )

            for condition in ["C2", "C4", "C5", "C6", "C7"]:
                shown[condition] = shown[condition].map(
                    lambda value: bool(value) if pd.notna(value) else False
                )

            def _sim_current_window_open(ticker, action):
                ticker_key = str(ticker or "").strip().upper()
                if not ticker_key:
                    return False

                ticker_rows = active_market_df[
                    active_market_df["ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    == ticker_key
                ]
                if ticker_rows.empty:
                    return False

                latest_market_row = (
                    ticker_rows
                    .sort_values(["timestamp", "id"])
                    .iloc[-1]
                )

                market_region = market_region_for_ticker(
                    ticker_key,
                    latest_market_row.get("asset_type"),
                )
                market_config = (
                    TRADING_WINDOWS.get(market_region)
                    if market_region
                    else None
                )
                if not market_config:
                    return False

                window = trading_window_info(
                    pd.Timestamp.now(tz="UTC"),
                    market_config,
                    action,
                )
                return bool(window.is_open)

            def _sim_needed(row):
                status = str(
                    row.get("SimStatus") or ""
                ).strip().upper()

                ticker = row.get("Ticker")

                if status == "OPEN":
                    if not bool(row.get("_SimSellDataReady", False)):
                        return "Latest market data"

                    if not bool(row.get("_SimSellWindowOpen", False)):
                        return "SELL window to open"

                    c4 = bool(row.get("C4", False))
                    c5 = bool(row.get("C5", False))
                    c6 = bool(row.get("C6", False))
                    c7 = bool(row.get("C7", False))

                    if c6 or c7:
                        return "Ready to SELL"

                    if c4 or c5:
                        bought_before = row.get("_SimBoughtBefore")

                        if (
                            pd.notna(bought_before)
                            and not bool(bought_before)
                        ):
                            return "Buy must predate signal"

                        if bool(row.get("_SimCoreShouldSell", False)):
                            return "Ready to SELL"

                        return "SELL eligibility"

                    return "C4/C5/C6/C7 signal"

                if status in {"CLOSED", "NO TRADE"}:
                    if not bool(row.get("C2", False)):
                        return "C2 buy signal"

                    if not _sim_current_window_open(ticker, "buy"):
                        return "BUY window to open"

                    if current_open_count >= BUY_MAX_OPEN_TICKERS:
                        return "Free portfolio slot"

                    return "Ready to BUY"

                return "—"

            shown["Needed"] = shown.apply(
                _sim_needed,
                axis=1,
            )

            # Informational context only. Add news after C2-C7 and Needed
            # have already been calculated so it cannot influence simulator
            # state or any BUY/SELL decision.
            shown["News"] = (
                shown["Ticker"]
                .astype(str)
                .str.strip()
                .str.upper()
                .map(sim_news_map)
                .fillna("—")
            )

            shown["DiffSellPriceRaw"] = pd.to_numeric(
                shown["RelativeDifference"],
                errors="coerce",
            )

            shown["DiffLastPriceRaw"] = pd.NA
            valid_last_price = (
                shown["BuyPriceEUR"].notna()
                & shown["LastPriceRaw"].notna()
                & (shown["BuyPriceEUR"] > 0)
            )
            shown.loc[
                valid_last_price,
                "DiffLastPriceRaw",
            ] = (
                (
                    shown.loc[valid_last_price, "LastPriceRaw"]
                    / shown.loc[valid_last_price, "BuyPriceEUR"]
                )
                - 1.0
            ) * 100.0

            def _sim_market_region(ticker):
                ticker_key = str(ticker or "").strip().upper()
                ticker_rows = active_market_df[
                    active_market_df["ticker"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    == ticker_key
                ]
                if ticker_rows.empty:
                    return None
                latest_market_row = (
                    ticker_rows
                    .sort_values(["timestamp", "id"])
                    .iloc[-1]
                )
                return market_region_for_ticker(
                    ticker_key,
                    latest_market_row.get("asset_type"),
                )

            def _sim_trading_duration(row, end_column):
                start_time = pd.to_datetime(
                    row.get("BuyTime"), utc=True, errors="coerce"
                )
                end_time = pd.to_datetime(
                    row.get(end_column), utc=True, errors="coerce"
                )
                if pd.isna(start_time) or pd.isna(end_time):
                    return pd.NaT
                return effective_trading_duration(
                    start_time,
                    end_time,
                    _sim_market_region(row.get("Ticker")),
                )

            # Trading-time durations: overnight market closures, weekends and
            # configured market holidays do not count. This makes the simulator
            # efficiency columns directly comparable with C5/Last Data durations.
            shown["DiffSellTimeRaw"] = shown.apply(
                lambda row: _sim_trading_duration(row, "SellTime"), axis=1
            )
            shown["DiffSellTime"] = shown["DiffSellTimeRaw"].map(
                _format_hhmm_duration
            )

            shown["LastTimeRaw"] = pd.to_datetime(
                shown["LastTimeRaw"],
                utc=True,
                errors="coerce",
            )
            shown["DiffLastTimeRaw"] = shown.apply(
                lambda row: _sim_trading_duration(row, "LastTimeRaw"), axis=1
            )
            shown["DiffLastTime"] = shown["DiffLastTimeRaw"].map(
                _format_hhmm_duration
            )

            def format_local_time(value):
                value = pd.to_datetime(
                    value,
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(value):
                    return "—"
                return value.tz_convert(
                    LOCAL_TIMEZONE
                ).strftime("%Y-%m-%d %H:%M")

            shown["InitTime"] = shown["BuyTime"].map(format_local_time)
            shown["SellTimeDisplay"] = shown["SellTime"].map(
                format_local_time
            )
            # Latest simulator action for the ticker. OPEN means its latest
            # simulator action is BUY; CLOSED means its latest action is SELL.
            shown["SimAction"] = shown["SimStatus"].map(
                lambda status: (
                    "BUY"
                    if str(status).strip().upper() == "OPEN"
                    else (
                        "SELL"
                        if str(status).strip().upper() == "CLOSED"
                        else "—"
                    )
                )
            )
            shown["SimActionTimeRaw"] = shown["BuyTime"]
            closed_action_mask = shown["SimStatus"] == "CLOSED"
            shown.loc[closed_action_mask, "SimActionTimeRaw"] = shown.loc[
                closed_action_mask, "SellTime"
            ]
            no_trade_action_mask = ~shown["SimStatus"].isin(["OPEN", "CLOSED"])
            shown.loc[no_trade_action_mask, "SimActionTimeRaw"] = pd.NaT
            shown["SimActionTime"] = shown["SimActionTimeRaw"].map(
                format_local_time
            )

            def format_eur(value):
                return f"€{float(value):.2f}" if pd.notna(value) else "—"

            def format_percent(value):
                return f"{float(value):+.2f}%" if pd.notna(value) else "—"

            shown["InitPrice"] = shown["BuyPriceEUR"].map(format_eur)
            shown["SellPrice"] = shown["SellPriceEUR"].map(format_eur)
            shown["LastPrice"] = shown["LastPriceRaw"].map(format_eur)
            shown["DiffSellPrice%"] = shown["DiffSellPriceRaw"].map(
                format_percent
            )

            # Match the ZERO Advisor / Logs position sizing: Qty is the
            # smallest whole number of shares covering EUR 10,000. The new
            # DiffSellPrice column answers "what would the position gain/loss
            # be if sold at the latest price?" and therefore deliberately uses
            # LastPrice rather than the historical SellPrice.
            shown["_SimQty"] = shown["BuyPriceEUR"].map(
                lambda price: (
                    math.ceil(10000.0 / float(price))
                    if pd.notna(price) and float(price) > 0
                    else float("nan")
                )
            )
            shown["DiffSellPriceEURRaw"] = (
                (shown["LastPriceRaw"] - shown["BuyPriceEUR"])
                * shown["_SimQty"]
            )
            shown["DiffSellPrice"] = shown.apply(
                lambda row: (
                    format_eur(row.get("DiffSellPriceEURRaw"))
                    if str(row.get("SimStatus") or "").strip().upper() == "CLOSED"
                    else "—"
                ),
                axis=1,
            )
            shown["DiffLastPrice"] = shown["DiffLastPriceRaw"].map(
                format_percent
            )

            status_order = {
                "OPEN": 0,
                "CLOSED": 1,
                "NO TRADE": 2,
            }
            shown["_StatusSort"] = shown["SimStatus"].map(
                lambda value: status_order.get(value, 99)
            )
            shown["_DiffSellPriceSort"] = pd.to_numeric(
                shown["DiffSellPriceRaw"],
                errors="coerce",
            )
            shown["_DiffLastPriceSort"] = pd.to_numeric(
                shown["DiffLastPriceRaw"],
                errors="coerce",
            )

            shown = shown.sort_values(
                by=[
                    "_StatusSort",
                    "SimActionTimeRaw",
                    "Ticker",
                ],
                ascending=[True, False, True],
                na_position="last",
            )

            display_columns = [
                "SimActionTime",
                "SimAction",
                "SimReason",
                "SimStatus",
                "Ticker",
                "TickerName",
                "News",
                "InitTime",
                "InitPrice",
                "SellTimeDisplay",
                "SellPrice",
                "DiffSellTime",
                "DiffSellPrice%",
                "DiffSellPrice",
                "LastPrice",
                "DiffLastTime",
                "DiffLastPrice",
            ]

            sim_details = bool(SELL_CONFIG.get("details", True))
            if sim_details:
                display_columns.extend([
                    "Needed",
                    "C2",
                    "C4",
                    "C5",
                    "C6",
                    "C7",
                ])

            display = shown[display_columns].rename(
                columns={
                    "SellTimeDisplay": "SellTime",
                    "Needed": "LastNeeded",
                    "C2": "LastC2",
                    "C4": "LastC4",
                    "C5": "LastC5",
                    "C6": "LastC6",
                    "C7": "LastC7",
                }
            )

            raw_lookup = shown[
                [
                    "SimStatus",
                    "DiffLastPriceRaw",
                    "DiffLastTimeRaw",
                ]
            ].loc[display.index].copy()

            def _sim_trading_row_style(row):
                raw = raw_lookup.loc[row.name]
                styles = ["" for _ in row.index]
                is_open = str(raw.get("SimStatus") or "").strip().upper() == "OPEN"

                def bold(column):
                    if column in row.index:
                        styles[row.index.get_loc(column)] = "font-weight: 700;"

                if is_open:
                    bold("SimStatus")

                    diff_last_price = pd.to_numeric(
                        raw.get("DiffLastPriceRaw"),
                        errors="coerce",
                    )
                    if (
                        pd.notna(diff_last_price)
                        and abs(float(diff_last_price)) >= 2.0
                    ):
                        bold("DiffLastPrice")

                    diff_last_time = raw.get("DiffLastTimeRaw")
                    if (
                        pd.notna(diff_last_time)
                        and diff_last_time >= pd.Timedelta(hours=48)
                    ):
                        bold("DiffLastTime")

                return styles

            styled_display = display.style.apply(
                _sim_trading_row_style,
                axis=1,
            )

            st.dataframe(
                styled_display,
                use_container_width=True,
                hide_index=True,
                height=563,  # header + approximately 15 ticker rows
            )

            st.caption(
                "News shows the newest stored relevant news item for the ticker as "
                "Category: Text. Category is derived from the article headline; Text is the "
                "article description (or headline if no description is available), shortened "
                "to 180 characters. It represents one article, not a combination of multiple "
                "news items, and does not affect trading rules."
            )

            st.subheader("Steps to analyse the simulator results")
            st.markdown(
                "1. For **OPEN** tickers, is **DiffLastPrice** very high or very low, "
                "or is **DiffLastTime** very long?\n"
                "2. If yes, review the ticker in the **Historical Data** and "
                "**Last Data** pages to understand why the simulator has not "
                "sold the ticker."
            )

            st.caption(
                "SimActionTime and SimAction show the latest simulator transition "
                "for the ticker: BUY for an OPEN latest trade and SELL for a CLOSED "
                "latest trade. SimReason explains that transition. SimStatus shows "
                "the latest simulator state: OPEN, CLOSED, or NO TRADE. "
                "When Settings > Details is enabled, LastNeeded and LastC2 through "
                "LastC7 show the condition values at the ticker's latest market data. "
                "InitTime and InitPrice are the latest simulated BUY time and EUR price. "
                "SellTime and SellPrice are populated when the latest simulated trade "
                "has been sold."
            )

            st.caption(
                "DiffSellTime = SellTime - InitTime. DiffSellPrice% is the percentage "
                "change from InitPrice to SellPrice. DiffSellPrice is the current EUR "
                "position gain/loss at LastPrice, calculated as (LastPrice - InitPrice) "
                "x Qty, where Qty is the smallest whole-share position covering EUR "
                "10,000. LastTime and LastPrice are the latest collected market-data "
                "time and EUR price. DiffLastTime = LastTime - InitTime. DiffLastPrice "
                "is the percentage change from InitPrice to LastPrice. Elapsed times "
                "use total hours HH:MM. Rows are "
                "sorted first by SimStatus and then by SimActionTime, newest "
                "simulator action first within each status."
            )

elif page == "Trading Efficiency":
    st.header("Trading Efficiency")
    efficiency_market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    efficiency_summary_cols = st.columns(5)
    efficiency_summary_cols[0].metric("Newest Data", format_newest_data(efficiency_market_df))
    efficiency_summary_cols[1].metric("Assets", count_market_assets(efficiency_market_df))
    last_open_profit_metric = efficiency_summary_cols[2].empty()
    last_open_loss_metric = efficiency_summary_cols[3].empty()
    last_close2h_metric = efficiency_summary_cols[4].empty()
    last_open_profit_metric.metric("LastOPENProfit / LastOPEN", "—")
    last_open_loss_metric.metric("LastOPENLoss / LastSell", "—")
    last_close2h_metric.metric("LastClose2h / CLOSED", "—")

    range_label = st.selectbox(
        "Range",
        ["7 days", "1 day", "6 hours", "2 hours"],
        index=1,
        key="effective_trading_range_v3",
    )
    range_map = {
        "7 days": pd.Timedelta(days=7),
        "1 day": pd.Timedelta(days=1),
        "6 hours": pd.Timedelta(hours=6),
        "2 hours": pd.Timedelta(hours=2),
    }

    counter_options = [
        "Close2h >= 2%",
        "CloseB >= 1%",
        "OPEN",
        "OPEN & Profit",
        "OPEN & Loss",
        "CLOSED & Close2h>=2%",
        "BUY",
        "SELL",
    ]
    selected_counters = st.multiselect(
        "Counters",
        counter_options,
        default=["OPEN", "OPEN & Profit"],
    )

    st.caption(
        "Use Counters to compare market breadth with the simulator trading profile. "
        "Close2h >= 2% counts tickers at least 2% above their approximately two-hour "
        "baseline; CloseB >= 1% uses the corresponding 1% threshold. OPEN counts "
        "simulated positions that are open. OPEN & Profit counts OPEN positions whose "
        "DiffLastPrice is at least 2%; OPEN & Loss counts the remaining OPEN "
        "positions. CLOSED & Close2h>=2% counts CLOSED simulator tickers whose "
        "Close2h is at least 2%. BUY and SELL count simulator transactions at each "
        "15-minute timepoint."
    )

    market_df = df[df["asset_type"].isin(["stock", "crypto"])].copy()
    if market_df.empty:
        st.info("No Effective Trading market data available.")
    else:
        reference_time = pd.to_datetime(
            market_df["timestamp"], utc=True, errors="coerce"
        ).max()

        try:
            simulation_rows = load_simulation_cached()
        except Exception as exc:
            st.error(f"Cannot load simulation data: {exc}")
        else:
            simulation_df = pd.DataFrame(simulation_rows)

            # MANUAL_RESET is an administrative synchronization event,
            # not a strategy-generated C4/C5/C6/C7 SELL. Exclude these
            # rows from Trading Efficiency.
            strategy_simulation_df = simulation_df.copy()
            if (
                not strategy_simulation_df.empty
                and "SellReason" in strategy_simulation_df.columns
            ):
                strategy_simulation_df = strategy_simulation_df[
                    strategy_simulation_df["SellReason"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .ne("MANUAL_RESET")
                ].copy()

            selected_period = range_map[range_label]

            full_analysis_df = build_trade_analysis_cached(
                df,
                strategy_simulation_df,
                reference_time,
                selected_period,
            )

            if not full_analysis_df.empty:
                summary_times = pd.to_datetime(
                    full_analysis_df["Time"], utc=True, errors="coerce"
                )
                latest_summary_time = summary_times.max()
                latest_summary = full_analysis_df[
                    summary_times.eq(latest_summary_time)
                ].copy()
                latest_count_map = {
                    str(row["Series"]): int(row["Count"])
                    for _, row in latest_summary.iterrows()
                    if pd.notna(row.get("Count"))
                }
                last_open_profit_metric.metric(
                    "LastOPENProfit / LastOPEN",
                    f"{latest_count_map.get('OPEN & Profit', 0)}/{latest_count_map.get('OPEN', 0)}",
                )
                last_open_loss_metric.metric(
                    "LastOPENLoss / LastSell",
                    f"{latest_count_map.get('OPEN & Loss', 0)}/{latest_count_map.get('SELL', 0)}",
                )
                last_close2h_metric.metric(
                    "LastClose2h / CLOSED",
                    f"{latest_count_map.get('CLOSED & Close2h>=2%', 0)}/{latest_count_map.get('CLOSED', 0)}",
                )

            analysis_start = reference_time - selected_period
            analysis_df = full_analysis_df[
                pd.to_datetime(
                    full_analysis_df["Time"],
                    utc=True,
                    errors="coerce",
                ) >= analysis_start
            ].copy()
            analysis_df = analysis_df[
                analysis_df["Series"].isin(selected_counters)
            ].copy()

            if not selected_counters:
                st.info("Select at least one Counter to display the diagram.")
            elif analysis_df.empty:
                st.info("No Effective Trading data are available for the selected range.")
            else:
                analysis_df["TimeLocal"] = pd.to_datetime(
                    analysis_df["Time"], utc=True, errors="coerce"
                ).dt.tz_convert(LOCAL_TIMEZONE)

                # Keep the first and last plotted nodes at 10% and 90% of the
                # x-axis respectively. Therefore the visible axis span is 125%
                # of the node-to-node span, with 12.5% of that span added on
                # each side.
                first_node_time = analysis_df["TimeLocal"].min()
                last_node_time = analysis_df["TimeLocal"].max()
                if (
                    pd.notna(first_node_time)
                    and pd.notna(last_node_time)
                    and last_node_time > first_node_time
                ):
                    node_span = last_node_time - first_node_time
                    axis_padding = node_span / 8
                else:
                    axis_padding = selected_period / 8
                axis_domain_start = first_node_time - axis_padding
                axis_domain_end = last_node_time + axis_padding
                # Altair scale domains accept only UTC-aware or naive datetimes.
                # Keep the plotted data in Europe/Berlin local time, but express
                # the domain endpoints as the equivalent UTC instants.
                shared_time_scale = alt.Scale(
                    domain=[
                        axis_domain_start.tz_convert("UTC").to_pydatetime(),
                        axis_domain_end.tz_convert("UTC").to_pydatetime(),
                    ]
                )

                polygon_config = TRADING_WINDOWS.get("US") or {}
                polygon_open_intervals = []
                polygon_prepost_intervals = []

                if polygon_config.get("enabled", True):
                    phase_config = dict(
                        DEFAULT_TRADING_PHASES.get("US") or {}
                    )
                    phase_config.update(
                        TRADING_PHASES.get("US") or {}
                    )

                    polygon_timezone = str(
                        phase_config.get("timezone")
                        or polygon_config.get("timezone", "America/New_York")
                    )
                    polygon_tz = ZoneInfo(polygon_timezone)

                    def _minutes_from_hhmm(value, default):
                        raw = str(value or default)
                        hour_text, minute_text = raw.split(":", 1)
                        return int(hour_text) * 60 + int(minute_text)

                    pre_start_minute = _minutes_from_hhmm(
                        phase_config.get("pre_start"),
                        "04:00",
                    )
                    opening_start_minute = _minutes_from_hhmm(
                        phase_config.get("opening_start"),
                        "09:30",
                    )
                    opening_end_minute = _minutes_from_hhmm(
                        phase_config.get("opening_end"),
                        "16:00",
                    )
                    post_end_minute = _minutes_from_hhmm(
                        phase_config.get("post_end"),
                        "20:00",
                    )

                    raw_weekdays = polygon_config.get("open_weekdays")
                    allowed_weekdays = (
                        {"mon", "tue", "wed", "thu", "fri"}
                        if raw_weekdays is None
                        else {
                            str(value).strip().lower()[:3]
                            for value in raw_weekdays
                        }
                    )

                    closed_dates = {
                        str(value)
                        for value in (
                            polygon_config.get("closed_dates")
                            or []
                        )
                    }

                    local_start = analysis_start.tz_convert(polygon_tz).normalize()
                    local_end = reference_time.tz_convert(polygon_tz).normalize()

                    for local_day in pd.date_range(
                        start=local_start,
                        end=local_end,
                        freq="D",
                    ):
                        weekday_key = local_day.strftime("%a").lower()[:3]
                        date_key = local_day.strftime("%Y-%m-%d")

                        if (
                            weekday_key not in allowed_weekdays
                            or date_key in closed_dates
                        ):
                            continue

                        pre_start = local_day + pd.Timedelta(minutes=pre_start_minute)
                        opening_start = local_day + pd.Timedelta(minutes=opening_start_minute)
                        opening_end = local_day + pd.Timedelta(minutes=opening_end_minute)
                        post_end = local_day + pd.Timedelta(minutes=post_end_minute)

                        if pre_start < opening_start:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": pre_start.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": opening_start.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Pre-Trading",
                                }
                            )

                        if opening_start < opening_end:
                            polygon_open_intervals.append(
                                {
                                    "StartLocal": opening_start.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": opening_end.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Opening",
                                }
                            )

                        if opening_end < post_end:
                            polygon_prepost_intervals.append(
                                {
                                    "StartLocal": opening_end.tz_convert(LOCAL_TIMEZONE),
                                    "EndLocal": post_end.tz_convert(LOCAL_TIMEZONE),
                                    "Phase": "Post-Trading",
                                }
                            )

                base = alt.Chart(analysis_df).encode(
                    x=alt.X(
                        "TimeLocal:T",
                        title="Local time",
                        scale=shared_time_scale,
                        axis=alt.Axis(
                            format="%H:%M",
                            labelAngle=0,
                            labelExpr=(
                                "hours(datum.value) == 0 && minutes(datum.value) == 0 "
                                "? timeFormat(datum.value, '%d %b') "
                                ": timeFormat(datum.value, '%H:%M')"
                            ),
                        ),
                    ),
                    y=alt.Y(
                        "Count:Q",
                        title="Number of tickers",
                        scale=alt.Scale(zero=True),
                        axis=alt.Axis(format="d", tickMinStep=1),
                    ),
                    color=alt.Color("Series:N", title="Counter", sort=counter_options),
                )
                lines = base.mark_line()
                points = base.mark_circle(size=55).encode(
                    tooltip=[
                        alt.Tooltip("TimeLocal:T", title="Time", format="%Y-%m-%d %H:%M"),
                        alt.Tooltip("Series:N", title="Counter"),
                        alt.Tooltip("Count:Q", title="Count"),
                        alt.Tooltip("Tickers:N", title="Tickers"),
                    ]
                )

                layers = []

                if polygon_prepost_intervals:
                    polygon_prepost_df = pd.DataFrame(
                        polygon_prepost_intervals
                    )
                    prepost_bands = (
                        alt.Chart(polygon_prepost_df)
                        .mark_rect(
                            opacity=0.16,
                            color="#f4a261",
                        )
                        .encode(
                            x=alt.X(
                                "StartLocal:T",
                                title=None,
                                scale=shared_time_scale,
                                axis=alt.Axis(
                                    format="%H:%M",
                                    labelAngle=0,
                                ),
                            ),
                            x2="EndLocal:T",
                            tooltip=[
                                alt.Tooltip(
                                    "Phase:N",
                                    title="Phase",
                                ),
                                alt.Tooltip(
                                    "StartLocal:T",
                                    title="Start",
                                    format="%Y-%m-%d %H:%M",
                                ),
                                alt.Tooltip(
                                    "EndLocal:T",
                                    title="End",
                                    format="%Y-%m-%d %H:%M",
                                ),
                            ],
                        )
                    )
                    layers.append(prepost_bands)

                if polygon_open_intervals:
                    polygon_open_df = pd.DataFrame(
                        polygon_open_intervals
                    )
                    open_bands = (
                        alt.Chart(polygon_open_df)
                        .mark_rect(
                            opacity=0.12,
                            color="#c6dbef",
                        )
                        .encode(
                            x=alt.X(
                                "StartLocal:T",
                                title="Time",
                                scale=shared_time_scale,
                                axis=alt.Axis(
                                    format="%H:%M",
                                    labelAngle=0,
                                ),
                            ),
                            x2="EndLocal:T",
                            tooltip=[
                                alt.Tooltip(
                                    "Phase:N",
                                    title="Phase",
                                ),
                                alt.Tooltip(
                                    "StartLocal:T",
                                    title="Start",
                                    format="%Y-%m-%d %H:%M",
                                ),
                                alt.Tooltip(
                                    "EndLocal:T",
                                    title="End",
                                    format="%Y-%m-%d %H:%M",
                                ),
                            ],
                        )
                    )
                    layers.append(open_bands)

                layers.extend([lines, points])
                chart = alt.layer(*layers).properties(height=500).interactive()
                st.altair_chart(chart, use_container_width=True)

                polygon_window_text = "unavailable"
                if polygon_config:
                    polygon_window_text = (
                        f"{polygon_config.get('timezone', 'America/New_York')}: "
                        f"BUY {polygon_config.get('buy_start', '—')}–{polygon_config.get('buy_end', '—')}, "
                        f"SELL {polygon_config.get('sell_start', '—')}–{polygon_config.get('sell_end', '—')}"
                    )

                st.caption(
                    f"Selected range: {range_label}. Each node represents a 15-minute timepoint. "
                    "Shaded vertical bands identify the configured US/Polygon phases. "
                    "Opening uses the light-blue background; Pre-Trading and Post-Trading "
                    "share the second background color. The phase boundaries use the same "
                    "trading_phases configuration as Last Data. "
                    f"Current Polygon settings: {polygon_window_text}."
                )
                st.caption(
                    "Counter definitions: Close2h >= 2% and CloseB >= 1% count tickers whose "
                    "market price is at least that percentage above the approximately two-hour "
                    "baseline. OPEN counts simulator positions active at the timepoint. "
                    "OPEN & Profit counts OPEN positions with DiffLastPrice >= 2%; "
                    "OPEN & Loss counts the remaining OPEN positions. "
                    "CLOSED & Close2h>=2% counts CLOSED simulator tickers with Close2h >= 2%. "
                    "BUY and SELL count simulator buy/sell actions occurring at the timepoint."
                )

                st.subheader(
                    "Steps to analyse the effectiveness of this system"
                )
                st.markdown(
                    "1. Select **OPEN & Loss** and **SELL**. Is the number of OPEN tickers "
                    "with loss high? If yes, decrease **MinProfit** in the Settings page.\n"
                    "2. Select **OPEN** and **OPEN & Profit**. Does the number of OPEN tickers "
                    "vary strongly through time? If yes, decrease **Maximum OPEN tickers** "
                    "in the Settings page.\n"
                    "3. Select **OPEN** and **OPEN & Profit**. Is the number of OPEN tickers "
                    "with profit much smaller than the number of OPEN tickers? If yes, "
                    "decrease the **movement threshold** in the Settings page.\n"
                    "4. Select **CLOSED & Close2h>=2%** and **BUY**. Is the number of bought "
                    "tickers much smaller than the number of CLOSED tickers with "
                    "Close2h >= 2%? If yes, decrease the **CloseB threshold** in the "
                    "Settings page."
                )

elif page == "System Health":
    st.header("System Health")
    system_summary_placeholder = st.empty()

    def render_system_summary(systems: int = 0, collectors_ok: int = 0, markets_ok: int = 0) -> None:
        with system_summary_placeholder.container():
            summary_cols = st.columns(4)
            summary_cols[0].metric("Newest Data", format_newest_data(df))
            summary_cols[1].metric("Systems", int(systems))
            summary_cols[2].metric("Collectors OK", int(collectors_ok))
            summary_cols[3].metric("Markets OK", int(markets_ok))

    render_system_summary(
        df["system"].dropna().astype(str).nunique() if not df.empty and "system" in df.columns else 0
    )

    if df.empty:
        st.warning(
            "No systems have reported data."
        )

    else:
        now = pd.Timestamp.now(
            tz="UTC"
        )

        latest = (
            df.groupby("system")
            .agg(
                last_market_time=(
                    "timestamp",
                    "max",
                ),
                last_received_time=(
                    "received_at",
                    "max",
                ),
            )
            .reset_index()
        )

        latest[
            "collector_age_minutes"
        ] = (
            (
                now
                - latest[
                    "last_received_time"
                ]
            )
            .dt.total_seconds()
            / 60
        )

        latest[
            "market_age_minutes"
        ] = (
            (
                now
                - latest[
                    "last_market_time"
                ]
            )
            .dt.total_seconds()
            / 60
        )

        def health_status(age):
            if pd.isna(age):
                return "UNKNOWN"

            if age <= 30:
                return "OK"

            if age <= 120:
                return "STALE"

            return "OFFLINE"

        latest[
            "CollectorStatus"
        ] = (
            latest[
                "collector_age_minutes"
            ]
            .apply(
                health_status
            )
        )

        latest[
            "MarketStatus"
        ] = (
            latest[
                "market_age_minutes"
            ]
            .apply(
                health_status
            )
        )

        latest[
            "LastReceived"
        ] = (
            latest[
                "last_received_time"
            ]
            .dt.tz_convert(
                LOCAL_TIMEZONE
            )
            .dt.strftime(
                "%Y-%m-%d %H:%M"
            )
        )

        latest[
            "LastMarketData"
        ] = (
            latest[
                "last_market_time"
            ]
            .dt.tz_convert(
                LOCAL_TIMEZONE
            )
            .dt.strftime(
                "%Y-%m-%d %H:%M"
            )
        )
        # System Health must show every system that has ever reported data,
        # including systems whose upstream market is currently closed.
        # Their age is already represented by CollectorStatus/MarketStatus,
        # so do not hide them merely because the last record is older than 24h.

        technology_by_system = {
            "polygon": "REST API",
            "crypto": "REST API",
            "massive": "REST API",
            "international": "REST API",
            "x1": "Mock / simulated",
        }

        data_type_by_system = {
            "polygon": "Stock market",
            "crypto": "Crypto market",
            "massive": "Market data",
            "international": "International market",
            "x1": "Home energy",
        }

        system_key = (
            latest["system"]
            .astype(str)
            .str.strip()
            .str.lower()
        )

        latest["Technology"] = (
            system_key
            .map(technology_by_system)
            .fillna("Unknown")
        )
        latest["Data Type"] = (
            system_key
            .map(data_type_by_system)
            .fillna("Unknown")
        )

        render_system_summary(
            len(latest),
            int((latest["CollectorStatus"] == "OK").sum()),
            int((latest["MarketStatus"] == "OK").sum()),
        )

        display_health = latest[
            [
                "system",
                "Technology",
                "Data Type",
                "CollectorStatus",
                "MarketStatus",
                "LastReceived",
                "LastMarketData",
            ]
        ].copy()

        st.dataframe(
            display_health,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "Status meaning: OK = the latest timestamp is no more than 30 minutes old. "
            "STALE = the latest timestamp is more than 30 minutes but no more than "
            "120 minutes old; the source is delayed and should be treated cautiously. "
            "OFFLINE = the latest timestamp is more than 120 minutes old (or unavailable); "
            "the source should be treated as unavailable until fresh data arrives. "
            "CollectorStatus uses the time the server last received a record. "
            "MarketStatus uses the timestamp of the latest market observation. "
            "Therefore, CollectorStatus can be OK while MarketStatus is STALE/OFFLINE "
            "when the collector/network is working but the upstream provider is not "
            "delivering fresh market bars."
        )

        system_descriptions = {
            "polygon": (
                "Polygon: US stock market-data collector used for the ZERO stock "
                "trading analysis and simulator."
            ),
            "crypto": (
                "Crypto: cryptocurrency market-data collector. It is shown on this "
                "System Health page, but it is intentionally excluded from the "
                "Healthy/Market header indicators."
            ),
            "international": (
                "International: international-market collector. Its status matters "
                "only when that collector is enabled and actively reporting."
            ),
        }

        shown_systems = (
            display_health["system"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )

        system_notes = []
        for system_name in shown_systems:
            normalized = system_name.lower()
            system_notes.append(
                system_descriptions.get(
                    normalized,
                    f"{system_name}: reporting system/source shown in the health table.",
                )
            )

        if system_notes:
            st.caption(
                "Systems: " + " ".join(system_notes)
            )

