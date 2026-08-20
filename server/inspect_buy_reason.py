#!/usr/bin/env python3
"""
Read-only BUY diagnostic for Home Monitor.

Run from the Home Monitor repository, preferably inside the telegram-notifier
container so that MONITOR_API_URL, MONITOR_API_KEY and mounted config paths are
identical to production:

    docker compose exec telegram-notifier \
      python /app/server/inspect_buy_reason.py SWIKS

This script DOES NOT send Telegram messages and DOES NOT write Simulation trades.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys

import httpx
import pandas as pd


def import_notifier():
    try:
        from server import telegram_notifier as n
        return n
    except ImportError:
        import telegram_notifier as n
        return n


def yesno(value: bool) -> str:
    return "PASS" if value else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", help="Ticker to inspect, e.g. SWIKS")
    parser.add_argument(
        "--limit",
        type=int,
        default=50000,
        help="Measurement API row limit (default: 50000)",
    )
    args = parser.parse_args()
    ticker = args.ticker.strip().upper()

    n = import_notifier()

    print("=" * 78)
    print(f"BUY DIAGNOSTIC: {ticker}")
    print("=" * 78)
    print(f"API URL: {n.API_URL}")
    print(f"Config:  {n.CONFIG_PATH}")
    print(f"Membership: {n.WATCHLIST_MEMBERSHIP_PATH}")
    print()

    if not n.API_KEY:
        print("FATAL: MONITOR_API_KEY is not configured in this environment.")
        return 2

    config = n.load_config()
    rule = config.get("buy") or {}
    windows = config.get("trading_windows") or {}

    print("[0] BUY feature")
    buy_enabled = bool(rule.get("enabled", False))
    print(f"    enabled={buy_enabled} -> {yesno(buy_enabled)}")
    if not buy_enabled:
        print("\nFINAL REASON: BUY_DISABLED")
        return 1

    # Detect what the deployed source actually does.
    try:
        buy_source = inspect.getsource(n.evaluate_buy)
    except Exception:
        buy_source = ""

    deployed_highb_gate = (
        'row["highb"] > threshold' in buy_source
        or "row['highb'] > threshold" in buy_source
    )
    deployed_highb_sort = (
        '-row["highb"]' in buy_source
        or "-row['highb']" in buy_source
    )

    print("\n[1] Deployed notifier source")
    print(
        "    HighB candidate gate present: "
        f"{deployed_highb_gate}"
    )
    print(
        "    HighB ranking present:        "
        f"{deployed_highb_sort}"
    )
    if deployed_highb_gate:
        print(
            "    WARNING: this deployed source STILL uses HighB as a BUY condition."
        )

    with httpx.Client(timeout=30) as client:
        try:
            measurements = n.api_get(
                client,
                "/api/v1/measurements",
                {"limit": args.limit},
            )
        except Exception as exc:
            print(f"\nFATAL: cannot read measurements: {exc}")
            return 2

        df = n.measurement_dataframe(measurements)
        rows = n.calculate_latest_highb(
            df=df,
            baseline_hours=int(rule.get("baseline_hours", 2)),
            tolerance_minutes=int(
                rule.get("baseline_tolerance_minutes", 30)
            ),
        )

        by_ticker = {
            str(row.get("ticker", "")).upper(): row
            for row in rows
        }
        row = by_ticker.get(ticker)

        print("\n[2] Measurement / baseline calculation")
        if row is None:
            print(f"    {ticker} not found in calculated BUY rows -> FAIL")
            print(
                "    Possible causes: no recent measurement, missing 2-hour baseline, "
                "or baseline outside tolerance."
            )
            print("\nFINAL REASON: NO_CALCULATED_BUY_ROW")
            return 1

        highb = row.get("highb")
        closeb = row.get("closeb")
        latest_time = row.get("latest_time")
        sell_time = row.get("sell_time_seconds")

        print(f"    latest_time={latest_time}")
        print(f"    HighB={highb!r}")
        print(f"    CloseB={closeb!r}")
        print(f"    SellTime={sell_time!r}")

        minimum_closeb_percent = float(
            rule.get("minimum_closeb_percent", 2.0)
        )
        minimum_closeb_count = int(
            rule.get(
                "minimum_closeb_count",
                rule.get("minimum_closeb_ge2_count", 6),
            )
        )
        closeb_breadth_count = sum(
            1
            for r in rows
            if r.get("closeb") is not None
            and r["closeb"] >= minimum_closeb_percent
        )

        print("\n[3] C2 global breadth")
        c2 = closeb_breadth_count >= minimum_closeb_count
        print(
            f"    CloseB >= {minimum_closeb_percent:.2f}%: "
            f"{closeb_breadth_count} tickers; required={minimum_closeb_count} "
            f"-> {yesno(c2)}"
        )
        if not c2:
            print("\nFINAL REASON: C2_NOT_SATISFIED")
            return 1

        positive_closeb = (
            closeb is not None
            and pd.notna(closeb)
            and float(closeb) > 0
        )
        print("\n[4] Per-ticker CloseB")
        print(f"    CloseB > 0 -> {yesno(positive_closeb)}")
        if not positive_closeb:
            print("\nFINAL REASON: CLOSEB_NOT_POSITIVE")
            return 1

        threshold = float(
            rule.get("highb_threshold_percent", 3.0)
        )
        if deployed_highb_gate:
            highb_ok = (
                highb is not None
                and pd.notna(highb)
                and float(highb) > threshold
            )
            print("\n[5] HighB gate in DEPLOYED source")
            print(
                f"    HighB > {threshold:.2f}% -> {yesno(highb_ok)} "
                f"(HighB={highb!r})"
            )
            if not highb_ok:
                print(
                    "\nFINAL REASON: HIGHB_GATE_STILL_ACTIVE_IN_DEPLOYED_NOTIFIER"
                )
                return 1
        else:
            print("\n[5] HighB gate")
            print("    Not present in deployed evaluate_buy -> PASS")

        try:
            open_payload = n.api_get(
                client,
                "/api/v1/simulation/open-tickers",
            )
        except Exception as exc:
            print(f"\nFATAL: cannot read open simulation tickers: {exc}")
            return 2

        if isinstance(open_payload, dict):
            open_tickers = {
                str(t).upper()
                for t in open_payload.get("tickers", [])
            }
        else:
            open_tickers = {
                str(t).upper()
                for t in (open_payload or [])
            }

        not_open = ticker not in open_tickers
        print("\n[6] Existing simulation position")
        print(f"    already OPEN={not not_open} -> {yesno(not_open)}")
        if not not_open:
            print("\nFINAL REASON: ALREADY_OPEN")
            return 1

        market_regions = n.load_ticker_market_regions()
        market_region = n.market_region_for_row(
            ticker,
            row,
            market_regions,
        )

        print("\n[7] Market mapping")
        print(f"    market_region={market_region!r}")
        if not market_region:
            print("\nFINAL REASON: MARKET_REGION_UNKNOWN")
            return 1

        market_config = windows.get(market_region)
        if not market_config:
            print(
                f"    trading window config for {market_region}: missing -> FAIL"
            )
            print("\nFINAL REASON: TRADING_WINDOW_CONFIG_MISSING")
            return 1

        buy_window = n.trading_window_info(
            row["latest_time"],
            market_config,
            "buy",
        )
        print("\n[8] C1 / BUY trading window")
        print(
            f"    is_open={buy_window.is_open} "
            f"remaining={getattr(buy_window, 'remaining_time', None)} "
            f"-> {yesno(bool(buy_window.is_open))}"
        )
        if not buy_window.is_open:
            print("\nFINAL REASON: OUTSIDE_BUY_WINDOW_OR_MARKET_CLOSED")
            return 1

        max_sell_time = market_config.get(
            "max_buy_sell_time_seconds"
        )
        print("\n[9] Liquidity / SellTime")
        if max_sell_time is None:
            print("    no max_buy_sell_time_seconds configured -> PASS")
        else:
            numeric_sell_time = pd.to_numeric(
                sell_time,
                errors="coerce",
            )
            liquidity_ok = (
                pd.notna(numeric_sell_time)
                and float(numeric_sell_time) <= float(max_sell_time)
            )
            print(
                f"    SellTime={sell_time!r}; max={max_sell_time} "
                f"-> {yesno(liquidity_ok)}"
            )
            if not liquidity_ok:
                print("\nFINAL REASON: LIQUIDITY_SELLTIME_GATE")
                return 1

        # Reconstruct the current deployed candidate set and ranking to detect
        # the global maximum-six exclusion.
        if deployed_highb_gate:
            matching = [
                r for r in rows
                if r.get("highb") is not None
                and r["highb"] > threshold
                and r.get("closeb") is not None
                and r["closeb"] > 0
            ]
        else:
            matching = [
                r for r in rows
                if r.get("closeb") is not None
                and r["closeb"] > 0
            ]

        matching = [
            r for r in matching
            if str(r["ticker"]).upper() not in open_tickers
        ]

        if deployed_highb_sort:
            matching.sort(
                key=lambda r: (-float(r["highb"]), str(r["ticker"]))
            )
        else:
            matching.sort(
                key=lambda r: (
                    -float(r.get("closeb") or float("-inf")),
                    str(r["ticker"]),
                )
            )

        trading_eligible = []
        for candidate in matching:
            candidate_ticker = str(candidate["ticker"]).strip().upper()
            candidate_region = n.market_region_for_row(
                candidate_ticker,
                candidate,
                market_regions,
            )
            if not candidate_region:
                continue
            candidate_cfg = windows.get(candidate_region)
            if not candidate_cfg:
                continue
            candidate_window = n.trading_window_info(
                candidate["latest_time"],
                candidate_cfg,
                "buy",
            )
            if not candidate_window.is_open:
                continue
            candidate_max_sell = candidate_cfg.get(
                "max_buy_sell_time_seconds"
            )
            if candidate_max_sell is not None:
                candidate_sell = pd.to_numeric(
                    candidate.get("sell_time_seconds"),
                    errors="coerce",
                )
                if (
                    pd.isna(candidate_sell)
                    or float(candidate_sell) > float(candidate_max_sell)
                ):
                    continue
            trading_eligible.append(candidate)

        selected = trading_eligible[:6]
        selected_tickers = [
            str(r["ticker"]).upper()
            for r in selected
        ]

        print("\n[10] Global maximum-six selection")
        print(f"    selected={selected_tickers}")
        in_selected = ticker in selected_tickers
        print(f"    {ticker} selected -> {yesno(in_selected)}")
        if not in_selected:
            eligible_tickers = [
                str(r["ticker"]).upper()
                for r in trading_eligible
            ]
            if ticker in eligible_tickers:
                rank = eligible_tickers.index(ticker) + 1
                print(f"    {ticker} eligible rank={rank}; cap=6")
                print("\nFINAL REASON: GLOBAL_TOP6_CAP")
            else:
                print(
                    "    Ticker passed individual checks above but was not found "
                    "in reconstructed trading_eligible; compare notifier source."
                )
                print("\nFINAL REASON: ELIGIBILITY_RECONSTRUCTION_MISMATCH")
            return 1

        membership = n.load_watchlist_membership()
        recipients = config.get("recipients") or []
        interested = []

        for recipient in recipients:
            watchlist = str(
                recipient.get("watchlist") or ""
            ).strip().lower()
            if (
                watchlist
                and watchlist in membership.get(ticker, set())
            ):
                interested.append(recipient)

        print("\n[11] Recipient/watchlist routing")
        print(
            "    ticker memberships="
            f"{sorted(membership.get(ticker, set()))}"
        )
        print(
            "    interested recipients="
            f"{[r.get('name', '?') for r in interested]}"
        )
        if not interested:
            print("\nFINAL REASON: NO_INTERESTED_TELEGRAM_RECIPIENT")
            return 1

        token_ready = []
        token_missing = []
        for recipient in interested:
            env_name = recipient.get("bot_token_env")
            if env_name and os.getenv(env_name):
                token_ready.append(recipient.get("name", "?"))
            else:
                token_missing.append(
                    f"{recipient.get('name', '?')}:{env_name}"
                )

        print("\n[12] Telegram prerequisites (no message is sent)")
        print(f"    token-ready recipients={token_ready}")
        if token_missing:
            print(f"    missing token env={token_missing}")
        if not token_ready:
            print("\nFINAL REASON: TELEGRAM_TOKEN_NOT_AVAILABLE")
            return 1

        ticker_rows = (
            df[df["ticker"] == ticker]
            .sort_values(["timestamp", "id"])
        )
        print("\n[13] Simulation-write prerequisites (no write is made)")
        if ticker_rows.empty:
            print("    no measurements for ticker -> FAIL")
            print("\nFINAL REASON: NO_MEASUREMENT_FOR_SIMULATION_WRITE")
            return 1

        latest_row = ticker_rows.iloc[-1]
        buy_price = pd.to_numeric(
            latest_row.get("close"),
            errors="coerce",
        )
        price_ok = pd.notna(buy_price)
        print(f"    latest close={buy_price!r} -> {yesno(price_ok)}")
        if not price_ok:
            print("\nFINAL REASON: INVALID_BUY_PRICE")
            return 1

        print("\n" + "=" * 78)
        print("ALL READ-ONLY CHECKS PASSED")
        print("=" * 78)
        print(
            "The remaining failure point is runtime delivery/write behavior: "
            "Telegram send exception, or /api/v1/simulation/signals rejecting/"
            "failing the BUY. Inspect telegram-notifier logs around the same cycle."
        )
        print(
            "\nSuggested log command:\n"
            "  docker compose logs --since=15m telegram-notifier | "
            f"grep -Ei '{ticker}|BUY|Telegram|Simulation'"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
