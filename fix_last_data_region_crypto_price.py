#!/usr/bin/env python3
from pathlib import Path

path = Path("dashboard/streamlit_app.py")
text = path.read_text(encoding="utf-8")

old_region = '''def market_region_for_ticker(ticker: str, asset_type: str) -> str | None:
    if str(asset_type).lower() == "crypto":
        return "CRYPTO"
    return MARKET_REGIONS.get(str(ticker).upper())
'''

new_region = '''def market_region_for_ticker(ticker: str, asset_type: str) -> str | None:
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
'''

if old_region not in text:
    raise SystemExit(
        "ERROR: market_region_for_ticker block did not match current file; "
        "no changes were written."
    )
text = text.replace(old_region, new_region, 1)

anchor = '''                relevant["SellingTime"] = relevant.apply(format_selling_time, axis=1)

                relevant["LastPrice"] = pd.to_numeric(
                    relevant["LastPrice"], errors="coerce"
                ).map(
                    lambda value: f"€{value:.3f}" if pd.notna(value) else "—"
                )
'''

replacement = '''                relevant["SellingTime"] = relevant.apply(format_selling_time, axis=1)

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
                    lambda value: f"€{value:.3f}" if pd.notna(value) else "—"
                )
'''

if anchor not in text:
    raise SystemExit(
        "ERROR: Last Data LastPrice block did not match current file; "
        "no changes were written."
    )
text = text.replace(anchor, replacement, 1)

path.write_text(text, encoding="utf-8")
print("SUCCESS: fixed Last Data stock-region fallback and crypto LastPrice.")
