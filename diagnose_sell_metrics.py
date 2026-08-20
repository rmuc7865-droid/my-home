#!/usr/bin/env python3
"""Diagnose the numeric inputs behind C4/C5 for one ticker.

Run from the project/container environment where `shared.trading_decisions`
and the monitoring API are available.

Examples:
  python3 diagnose_sell_metrics.py HUT
  python3 diagnose_sell_metrics.py HUT --at '2026-08-17 22:00:00+02:00'

The script uses the same production `evaluate_sell_history()` function as the
Streamlit dashboard, then prints:
  * C4 + the peak selected by decision.max_time and Drop % from that peak
  * C5 + the exact configured lookback and max absolute deviation (Static %)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.trading_decisions import evaluate_sell_history  # noqa: E402

API_URL = os.getenv("MONITOR_API_URL", "http://api:8000")
API_KEY = os.getenv("MONITOR_API_KEY", "CHANGE_ME")
HEADERS = {"X-API-Key": API_KEY}


def load_sell_config() -> dict:
    candidates = [
        Path("/app/server/telegram_notifications.yaml"),
        PROJECT_ROOT / "server" / "telegram_notifications.yaml",
    ]
    for path in candidates:
        if path.exists():
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return cfg.get("sell") or {}
    raise FileNotFoundError("telegram_notifications.yaml not found")


def load_ticker_history(ticker: str) -> pd.DataFrame:
    r = httpx.get(
        f"{API_URL}/api/v1/measurements",
        headers=HEADERS,
        params={"limit": 50000},
        timeout=30,
    )
    r.raise_for_status()

    rows = []
    for record in r.json():
        metadata = record.get("metadata") or {}
        rec_ticker = str(metadata.get("ticker") or record.get("system") or "").upper()
        if rec_ticker != ticker.upper():
            continue
        m = record.get("measurements") or {}
        rows.append(
            {
                "id": record.get("id"),
                "timestamp": pd.to_datetime(record.get("timestamp"), utc=True),
                "close": pd.to_numeric(m.get("close"), errors="coerce"),
                "buy_qty": m.get("buy_qty"),
                "buy_value_eur": m.get("buy_value_eur"),
                "sell_time_seconds": m.get("sell_time_seconds"),
                "sell_time_over_seconds": m.get("sell_time_over_seconds"),
                **m,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"No measurements found for {ticker}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return (
        df.sort_values(["timestamp", "id"])
        .drop_duplicates(subset=["timestamp"], keep="last")
        .dropna(subset=["close"])
        .reset_index(drop=True)
    )


def as_utc(value: str | None, fallback: pd.Timestamp) -> pd.Timestamp:
    if not value:
        return fallback
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Europe/Berlin")
    return ts.tz_convert("UTC")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("ticker", nargs="?", default="HUT")
    p.add_argument(
        "--at",
        help="Snapshot time, e.g. '2026-08-17 22:00:00+02:00'. "
        "If omitted, use the newest measurement.",
    )
    args = p.parse_args()

    cfg = load_sell_config()
    movement_percent = float(cfg.get("movement_percent", 1.1))
    c5_hours = float(cfg.get("c5_hours", 24.0))

    history = load_ticker_history(args.ticker)
    latest_available = history["timestamp"].max()
    requested = as_utc(args.at, latest_available)
    history = history[history["timestamp"] <= requested].copy()
    if history.empty:
        raise RuntimeError("No measurements exist at or before the requested time")

    latest = history.iloc[-1]
    latest_time = pd.Timestamp(latest["timestamp"])
    current_price = float(latest["close"])

    decision = evaluate_sell_history(
        ticker_df=history,
        latest_time=latest_time,
        current_price=current_price,
        movement_percent=movement_percent,
        c5_hours=c5_hours,
    )

    # C4 numeric value: use the exact peak timestamp selected by the production
    # decision function. This avoids choosing an independent dashboard peak.
    drop_pct = None
    peak_price = None
    peak_time = None
    if pd.notna(decision.max_time):
        peak_time = pd.Timestamp(decision.max_time)
        if peak_time.tzinfo is None:
            peak_time = peak_time.tz_localize("UTC")
        else:
            peak_time = peak_time.tz_convert("UTC")

        exact = history[history["timestamp"] == peak_time]
        if exact.empty:
            idx = (history["timestamp"] - peak_time).abs().idxmin()
            peak_row = history.loc[idx]
        else:
            peak_row = exact.iloc[-1]

        peak_price = float(peak_row["close"])
        if peak_price > 0:
            drop_pct = (peak_price - current_price) / peak_price * 100.0

    # C5 numeric value as documented by the dashboard: every sampled price in
    # the full c5_hours window must stay within +/- movement_percent of the
    # current price. Static is therefore the largest absolute deviation from
    # current price in that window.
    c5_start = latest_time - pd.Timedelta(hours=c5_hours)
    c5_rows = history[history["timestamp"] >= c5_start].copy()
    c5_rows["deviation_pct"] = (c5_rows["close"] / current_price - 1.0).abs() * 100.0
    static_pct = float(c5_rows["deviation_pct"].max()) if not c5_rows.empty else None
    worst = c5_rows.loc[c5_rows["deviation_pct"].idxmax()] if not c5_rows.empty else None
    available_hours = (
        (latest_time - c5_rows["timestamp"].min()).total_seconds() / 3600.0
        if not c5_rows.empty else 0.0
    )

    print(f"Ticker:              {args.ticker.upper()}")
    print(f"Snapshot:            {latest_time}")
    print(f"Current close:       {current_price:.8f}")
    print(f"Threshold:           {movement_percent:.4f}%")
    print()
    print("C4 / Drop")
    print(f"  C4:                {decision.c4_satisfied}")
    print(f"  decision.max_time: {decision.max_time}")
    print(f"  Peak time:         {peak_time}")
    print(f"  Peak close:        {peak_price}")
    print(f"  Drop:              {drop_pct:.4f}%" if drop_pct is not None else "  Drop:              n/a")
    print(f"  Test shown:        Drop > {movement_percent:.4f}%")
    print()
    print("C5 / Static")
    print(f"  C5:                {decision.c5_satisfied}")
    print(f"  Configured window: {c5_hours:g} h")
    print(f"  Window start:      {c5_start}")
    print(f"  Oldest sample:     {c5_rows['timestamp'].min() if not c5_rows.empty else None}")
    print(f"  Available span:    {available_hours:.3f} h")
    print(f"  Static:            {static_pct:.4f}%" if static_pct is not None else "  Static:            n/a")
    if worst is not None:
        print(f"  Worst sample time: {worst['timestamp']}")
        print(f"  Worst close:       {float(worst['close']):.8f}")
    print(f"  Test shown:        full {c5_hours:g} h AND Static <= {movement_percent:.4f}%")
    print(f"  last_one_proc_time:{decision.last_one_proc_time}")
    print()
    print(f"ShouldSell:          {decision.should_sell}")


if __name__ == "__main__":
    main()
