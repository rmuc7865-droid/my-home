from __future__ import annotations

import json
import os
import sqlite3
import sys
from bisect import bisect_left
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from replay_provenance import (
    build_replay_metadata,
    write_replay_metadata,
)

ROOT = Path("/app")
DB = Path(
    os.environ.get(
        "HOME_MONITOR_REPLAY_DB",
        "/data/monitor.db",
    )
)
CONFIG = ROOT / "server" / "telegram_notifications.yaml"
INSTRUMENTS = ROOT / "config" / "instruments.json"
OUT = Path("/data/analysis_results/c5_hours_sequential")
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
from shared.buy_signals import c2x_hybrid_signal
from shared.trading_decisions import (
    evaluate_sell_history,
    sell_applies_to_holding,
    trading_window_info,
)

VARIANTS = {
    "H06": 6.0,
    "H08": 8.0,
    "H10": 10.0,
    "H12": 12.0,   # production control
    "H14": 14.0,
    "H16": 16.0,
    "H18": 18.0,
    "H24": 24.0,
}

DEFAULT_PHASES = {
    "US": {"timezone": "America/New_York", "pre_start": "04:00"},
    "DE": {"timezone": "Europe/Berlin", "pre_start": "08:00"},
    "CRYPTO": {"timezone": "UTC", "pre_start": "00:00"},
}

def die(msg):
    raise SystemExit(f"ERROR: {msg}")

def pct(a, b):
    if a is None or b is None or not np.isfinite(a) or not np.isfinite(b) or a <= 0:
        return np.nan
    return (b / a - 1.0) * 100.0

def nearest_index(times, target, lo, hi, tolerance):
    j = bisect_left(times, target, lo, hi)
    cand = []
    if lo <= j < hi:
        cand.append(j)
    if lo <= j - 1 < hi:
        cand.append(j - 1)
    if not cand:
        return None
    k = min(cand, key=lambda x: abs(times[x] - target))
    return k if abs(times[k] - target) <= tolerance else None

def parse_hhmm(value):
    return datetime.strptime(str(value), "%H:%M").time()

def load_data():
    if not DB.exists():
        die(f"DB not found: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    raw = pd.read_sql_query("""
        SELECT id, system, timestamp, measurements_json, metadata_json
        FROM measurements ORDER BY timestamp, id
    """, con)
    con.close()

    rows = []
    for r in raw.itertuples(index=False):
        try:
            m = json.loads(r.measurements_json or "{}")
            md = json.loads(r.metadata_json or "{}")
            ticker = str(md.get("ticker") or "").strip().upper()
            ts = pd.to_datetime(r.timestamp, utc=True, errors="coerce")
            close = float(m.get("close"))
            high = float(m.get("high", close))
        except Exception:
            continue
        if not ticker or pd.isna(ts) or not np.isfinite(close) or close <= 0:
            continue
        sell_time = pd.to_numeric(m.get("sell_time_seconds"), errors="coerce")
        rows.append({
            "id": int(r.id), "ticker": ticker, "timestamp": ts,
            "close": close, "high": high if np.isfinite(high) else close,
            "sell_time_seconds": sell_time,
            "system": r.system,
            "asset_type": str(md.get("asset_type") or ""),
        })
    df = pd.DataFrame(rows)
    df = (df.sort_values(["ticker", "timestamp", "id"])
            .drop_duplicates(["ticker", "timestamp"], keep="last")
            .reset_index(drop=True))
    return df

def load_regions():
    rows = json.loads(INSTRUMENTS.read_text(encoding="utf-8"))
    out = {}
    for r in rows:
        t = str(r.get("Ticker") or "").strip().upper()
        region = str(r.get("MarketRegion") or "").strip().upper()
        if t and region:
            out[t] = region
    return out

def path_features(times, closes, i, lb):
    now = times[i]
    left = bisect_left(times, now - pd.Timedelta(minutes=lb), 0, i + 1)
    seg = closes[left:i+1]
    if len(seg) == 0:
        return np.nan, np.nan, np.nan
    running_low = seg[0]
    best_rise = 0.0
    for value in seg:
        if running_low > 0:
            best_rise = max(best_rise, (value / running_low - 1.0) * 100.0)
        running_low = min(running_low, value)
    peak_local = int(np.where(seg == np.max(seg))[0][-1])
    peak_idx = left + peak_local
    peak = closes[peak_idx]
    dd = (peak / closes[i] - 1.0) * 100.0
    age = (now - times[peak_idx]).total_seconds() / 60.0
    return float(best_rise), float(dd), float(age)

def reconstruct_features(df, cfg):
    buy = cfg["buy"]
    tol = pd.Timedelta(minutes=int(buy.get("baseline_tolerance_minutes", 30)))
    baseline = pd.Timedelta(hours=float(buy.get("baseline_hours", 2)))
    records = []

    print("Building point-in-time C2/C2X/path features...")
    for n_ticker, (ticker, g) in enumerate(df.groupby("ticker", sort=False), 1):
        g = g.sort_values(["timestamp", "id"]).reset_index(drop=True)
        times = list(g.timestamp)
        closes = g.close.to_numpy(float)
        highs = g.high.to_numpy(float)

        # c2x_hybrid_signal expects a DataFrame; retain only this ticker's past.
        for i in range(len(g)):
            now = times[i]
            j = nearest_index(times, now - baseline, 0, i + 1, tol)
            if j is None:
                continue
            closeb = pct(closes[j], closes[i])
            if not np.isfinite(closeb):
                continue

            hist = g.iloc[:i+1][["timestamp", "close"]].copy()
            sig = c2x_hybrid_signal(
                hist, now,
                lowrise_window_minutes=int(buy.get("c2x_window_minutes", 30)),
                hard_lowrise_percent=float(buy.get("c2x_hard_lowrise_percent", 8.0)),
                soft_lowrise_percent=float(buy.get("c2x_soft_lowrise_percent", 6.0)),
                acceleration_ratio_threshold=float(buy.get("c2x_acceleration_ratio_threshold", 4.0)),
                baseline_days=int(buy.get("c2x_baseline_days", 7)),
                pair_tolerance_minutes=10,
            )

            # Production additional C2X guards.
            closeb_too_high = closeb > float(buy.get("c2x_max_closeb_percent", 8.0))
            start120 = now - pd.Timedelta(minutes=120)
            left120 = bisect_left(times, start120, 0, i + 1)
            seg120 = closes[left120:i+1]
            peak_age = np.nan
            peak_dd = np.nan
            if len(seg120):
                peak_local = int(np.where(seg120 == np.max(seg120))[0][-1])
                peak_idx = left120 + peak_local
                peak_age = (now - times[peak_idx]).total_seconds() / 60.0
                peak_dd = (closes[peak_idx] / closes[i] - 1.0) * 100.0
            stale_peak = bool(
                closeb > 0
                and np.isfinite(peak_age)
                and np.isfinite(peak_dd)
                and peak_age > float(buy.get("c2x_max_peak_age_minutes", 30.0))
                and peak_dd > float(buy.get("c2x_max_peak_drawdown_percent", 2.5))
            )
            c2x_excluded = bool(sig.excluded or closeb_too_high or stale_peak)

            r60, d60, a60 = path_features(times, closes, i, 60)
            r120, d120, a120 = path_features(times, closes, i, 120)
            records.append({
                "ticker": ticker, "timestamp": now, "close": closes[i],
                "high": highs[i], "closeb": closeb,
                "sell_time_seconds": g.iloc[i].sell_time_seconds,
                "c2x_excluded": c2x_excluded,
                "c2x_trigger": sig.trigger,
                "lowrise30": sig.low_rise_percent,
                "acceleration_ratio": sig.acceleration_ratio,
                "rise60": r60, "dd60": d60, "age60": a60,
                "rise120": r120, "dd120": d120, "age120": a120,
            })
        if n_ticker % 10 == 0:
            print(f"  features: {n_ticker} tickers")

    feat = pd.DataFrame(records)
    feat.sort_values(["timestamp", "ticker"], inplace=True)
    feat.to_csv(OUT / "point_in_time_features.csv", index=False)
    return feat

def c6_remaining_minutes(ts, market):
    if not market.get("regular_close"):
        return None
    tz = ZoneInfo(str(market.get("timezone") or "UTC"))
    local = ts.tz_convert(tz)
    close_local = pd.Timestamp(
        datetime.combine(local.date(), parse_hhmm(market["regular_close"])), tz=tz
    )
    return (close_local - local).total_seconds() / 60.0

def c6_preexists(buy_time, action_time, remaining, close_minutes):
    if remaining is None:
        return False
    elapsed = max(0.0, float(close_minutes) - float(remaining))
    window_start = action_time - pd.Timedelta(minutes=float(elapsed))
    return bool(buy_time < window_start)

def market_open_for_c4(ts, region, market, phase_cfg):
    m = dict(market)
    p = dict(DEFAULT_PHASES.get(region) or {})
    p.update(phase_cfg or {})
    if p.get("pre_start"):
        m["sell_start"] = str(p["pre_start"])
    return trading_window_info(ts, m, "sell").is_open

def replay(name, variant_value, feat, prices, regions, cfg):
    buy_cfg = cfg["buy"]
    sell_cfg = cfg["sell"]
    buy_cfg = cfg["buy"]

    spike_collapse_min_rise = float(
        buy_cfg.get(
            "c2x_spike_collapse_min_rise_percent",
            25.0,
        )
    )
    spike_collapse_min_drawdown = float(
        buy_cfg.get(
            "c2x_spike_collapse_min_drawdown_percent",
            15.0,
        )
    )
    windows = cfg["trading_windows"]
    phases = cfg.get("trading_phases") or {}
    min_closeb = float(buy_cfg.get("minimum_closeb_percent", 1.5))
    min_breadth = int(buy_cfg.get("minimum_closeb_count", 5))
    max_open = int(buy_cfg.get("max_open_tickers", 10))
    batch_limit = int(buy_cfg.get("buy_batch_limit", 6))
    # C5-hours experiment:
    # movement_percent stays FIXED at production.
    # Only c5_hours changes between variants.
    movement = float(
        sell_cfg.get("movement_percent", 2.0)
    )
    c5_hours = float(variant_value)
    c6_close = float(sell_cfg.get("c6_close_minutes", 30.0))
    c6_min = float(sell_cfg.get("c6_min_gain_percent", 2.0))
    c7_max = float(sell_cfg.get("c7_max_gain_percent", 5.0))

    price_hist = {t: g.sort_values("timestamp").copy() for t, g in prices.groupby("ticker")}
    open_pos = {}
    trades = []
    blocked = []
    used_signal = set()

    # Production-style BUY snapshot.
    #
    # At evaluation time T, every ticker contributes its newest known
    # feature observation with timestamp <= T. The row's own timestamp
    # remains the BUY signal timestamp.
    feat_sorted = feat.sort_values(
        ["timestamp", "ticker"]
    ).copy()

    latest_by_ticker = {}

    for ts, new_rows in feat_sorted.groupby("timestamp", sort=True):

        for new_row in new_rows.itertuples(index=False):
            latest_by_ticker[new_row.ticker] = new_row._asdict()

        snap = pd.DataFrame(
            latest_by_ticker.values()
        ).copy()

        if snap.empty:
            continue

        # SELL existing holdings first.
        for ticker in list(open_pos):
            pos = open_pos[ticker]
            if ticker not in price_hist:
                continue
            hist = price_hist[ticker]
            hist = hist[(hist.timestamp <= ts) & (hist.timestamp >= pos["buy_time"])]
            if hist.empty or hist.iloc[-1].timestamp != ts:
                continue
            current = float(hist.iloc[-1].close)

            # Match production market_region_for_row() on SELL as well:
            # explicit MarketRegion wins; otherwise crypto -> CRYPTO,
            # Polygon stock collector -> US.
            region = regions.get(ticker)
            if not region:
                source_row = hist.iloc[-1]
                asset_type = str(
                    source_row.get("asset_type") or ""
                ).lower()
                system = str(
                    source_row.get("system") or ""
                ).lower()

                if asset_type == "crypto":
                    region = "CRYPTO"
                elif system == "polygon":
                    region = "US"

            market = windows.get(region)
            if not market:
                continue

            decision = evaluate_sell_history(
                ticker_df=hist,
                latest_time=ts,
                current_price=current,
                movement_percent=movement,
                c5_hours=c5_hours,
                init_time=pos["buy_time"],
                market_region=region,
                market_config=market,
                phase_config=phases.get(region) or {},
            )
            normal_window = trading_window_info(ts, market, "sell")
            rem = c6_remaining_minutes(ts, market) if normal_window.is_open else None
            gain = pct(pos["buy_price"], current)
            preexists = c6_preexists(pos["buy_time"], ts, rem, c6_close)
            c6 = bool(
                not decision.c4_satisfied and not decision.c5_satisfied
                and normal_window.is_open and rem is not None
                and 0 <= rem <= c6_close and preexists and gain < c6_min
            )
            c7 = bool(
                not decision.c4_satisfied and not decision.c5_satisfied
                and normal_window.is_open and rem is not None
                and 0 <= rem <= c6_close and preexists and gain > c7_max
            )
            c45 = bool(decision.should_sell and sell_applies_to_holding(decision, pos["buy_time"]))
            c4_actionable = market_open_for_c4(ts, region, market, phases.get(region) or {})
            c45_actionable = bool(
                (decision.c4_satisfied and c4_actionable)
                or (decision.c5_satisfied and normal_window.is_open)
            )
            should = bool((c45 and c45_actionable) or c6 or c7)
            if not should:
                continue

            reasons = []
            if c45 and decision.c4_satisfied and c4_actionable: reasons.append("C4")
            if c45 and decision.c5_satisfied and normal_window.is_open: reasons.append("C5")
            if c6: reasons.append("C6")
            if c7: reasons.append("C7")
            ret = pct(pos["buy_price"], current)
            trades.append({
                "variant": name, "ticker": ticker,
                "buy_time": pos["buy_time"], "buy_price": pos["buy_price"],
                "sell_time": ts, "sell_price": current,
                "return_pct": ret, "sell_reason": "+".join(reasons),
                "hold_hours": (ts - pos["buy_time"]).total_seconds()/3600.0,
                "buy_closeb": pos["closeb"],
            })
            del open_pos[ticker]

        # Effective C2 breadth is production C2X filtered, before RecentPath.
        eligible_c2 = snap[
            (snap.closeb >= min_closeb) & (~snap.c2x_excluded.astype(bool))
        ].copy()
        if len(eligible_c2) < min_breadth:
            continue

        # Production ordering: descending CloseB, then open/trading/liquidity/path.
        # Match production ordering exactly:
        # descending CloseB, ticker ascending.
        eligible_c2.sort_values(
            ["closeb", "ticker"],
            ascending=[False, True],
            inplace=True,
        )
        candidates = []
        for row in eligible_c2.itertuples(index=False):
            ticker = row.ticker

            signal_time = pd.to_datetime(
                row.timestamp,
                utc=True,
            )

            signal_key = (
                ticker,
                signal_time,
            )

            if ticker in open_pos or signal_key in used_signal:
                continue
            # Match production market_region_for_row():
            # explicit MarketRegion wins; otherwise crypto -> CRYPTO,
            # Polygon stock collector -> US.
            region = regions.get(ticker)
            if not region:
                source_rows = prices[
                    (prices["ticker"] == ticker)
                    & (prices["timestamp"] == signal_time)
                ]
                if not source_rows.empty:
                    source_row = source_rows.iloc[-1]
                    asset_type = str(
                        source_row.get("asset_type") or ""
                    ).lower()
                    system = str(
                        source_row.get("system") or ""
                    ).lower()

                    if asset_type == "crypto":
                        region = "CRYPTO"
                    elif system == "polygon":
                        region = "US"

            market = windows.get(region)
            if (
                not market
                or not trading_window_info(
                    signal_time,
                    market,
                    "buy",
                ).is_open
            ):
                continue

            # BUY-side end-of-day C6/C7 block.
            # Production evaluates this against the ticker's own
            # latest market-data timestamp.
            sell_window = trading_window_info(
                signal_time,
                market,
                "sell",
            )

            rem = (
                c6_remaining_minutes(
                    signal_time,
                    market,
                )
                if sell_window.is_open
                else None
            )
            if rem is not None and 0 <= rem <= c6_close:
                continue

            max_sell_time = market.get("max_buy_sell_time_seconds")
            if max_sell_time is not None:
                st = pd.to_numeric(row.sell_time_seconds, errors="coerce")
                if pd.isna(st) or float(st) > float(max_sell_time):
                    continue

            # Production spike-collapse guard.
            # This is FIXED for every C4/C5 movement variant:
            # Rise60 >= 25% AND DD60 >= 15%.
            #
            # Therefore the experiment changes only movement_percent.
            spike_collapse = bool(
                pd.notna(row.rise60)
                and pd.notna(row.dd60)
                and round(float(row.rise60), 10)
                >= spike_collapse_min_rise
                and round(float(row.dd60), 10)
                >= spike_collapse_min_drawdown
            )

            if spike_collapse:
                blocked.append({
                    "variant": name,
                    "ticker": ticker,
                    "timestamp": signal_time,
                    "close": row.close,
                    "closeb": row.closeb,
                    "rise60": row.rise60,
                    "dd60": row.dd60,
                    "age60": row.age60,
                    "rise120": row.rise120,
                    "dd120": row.dd120,
                    "age120": row.age120,
                })
                continue

            candidates.append(row)

        slots = max(0, max_open - len(open_pos))
        for row in candidates[:min(batch_limit, slots)]:
            signal_time = pd.to_datetime(
                row.timestamp,
                utc=True,
            )

            open_pos[row.ticker] = {
                "buy_time": signal_time,
                "buy_price": float(row.close),
                "closeb": float(row.closeb),
            }

            used_signal.add(
                (
                    row.ticker,
                    signal_time,
                )
            )

    # Mark still-open positions.
    for ticker, pos in open_pos.items():
        trades.append({
            "variant": name, "ticker": ticker,
            "buy_time": pos["buy_time"], "buy_price": pos["buy_price"],
            "sell_time": pd.NaT, "sell_price": np.nan,
            "return_pct": np.nan, "sell_reason": "OPEN",
            "hold_hours": np.nan, "buy_closeb": pos["closeb"],
        })

    trade_columns = [
        "variant", "ticker", "buy_time", "buy_price",
        "sell_time", "sell_price", "return_pct",
        "sell_reason", "hold_hours", "buy_closeb",
    ]
    blocked_columns = [
        "variant", "ticker", "timestamp", "close", "closeb",
        "rise60", "dd60", "age60",
        "rise120", "dd120", "age120",
    ]

    return (
        pd.DataFrame(trades, columns=trade_columns),
        pd.DataFrame(blocked, columns=blocked_columns),
    )

def summarize(name, trades, blocked):
    closed = trades[trades.sell_time.notna()].copy()
    reasons = Counter()
    for s in closed.sell_reason.fillna(""):
        for r in str(s).split("+"):
            if r: reasons[r] += 1
    return {
        "variant": name,
        "trades": len(trades),
        "closed": len(closed),
        "open": int(trades.sell_time.isna().sum()),
        "sum_return": closed.return_pct.sum(),
        "avg_return": closed.return_pct.mean(),
        "median_return": closed.return_pct.median(),
        "win_rate": (closed.return_pct > 0).mean()*100 if len(closed) else np.nan,
        "worst": closed.return_pct.min(),
        "best": closed.return_pct.max(),
        "avg_hold_h": closed.hold_hours.mean(),
        "C4": reasons["C4"], "C5": reasons["C5"],
        "C6": reasons["C6"], "C7": reasons["C7"],
        "blocked_observations": len(blocked),
        "blocked_tickers": blocked.ticker.nunique() if len(blocked) else 0,
    }

def compare_to_base(all_trades, summaries):
    base = all_trades["H12"]
    base_closed = base[base.sell_time.notna()].copy()
    rows = []
    for name, tr in all_trades.items():
        if name == "BASE":
            continue
        # Match BASE entries absent in variant by ticker+buy_time.
        vkeys = set(zip(tr.ticker, tr.buy_time))
        missing = base_closed[
            ~base_closed.apply(lambda r: (r.ticker, r.buy_time) in vkeys, axis=1)
        ]
        rows.append({
            "variant": name,
            "base_entries_not_taken": len(missing),
            "profitable_base_entries_not_taken": int((missing.return_pct > 0).sum()),
            "losing_base_entries_not_taken": int((missing.return_pct < 0).sum()),
            "sum_return_of_base_entries_not_taken": missing.return_pct.sum(),
        })
    return pd.DataFrame(rows)

def main():
    if not CONFIG.exists() or not INSTRUMENTS.exists():
        die("production config/instruments missing")
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    for key in ("buy", "sell", "trading_windows"):
        if key not in cfg:
            die(f"missing config section: {key}")

    print("="*90)
    print("C2/C2X SEQUENTIAL SPIKE-COLLAPSE REPLAY - READ ONLY")
    print("="*90)
    print("DB:", DB, "(opened mode=ro)")
    print("Output:", OUT)
    prices = load_data()
    regions = load_regions()
    print(f"Measurements: {len(prices):,}; tickers={prices.ticker.nunique()}")
    print(f"Range: {prices.timestamp.min()} -> {prices.timestamp.max()}")

    feat_path = ROOT / "analysis_results" / "c2_recent_path" / "prod_snapshot" / "point_in_time_features.csv"
    if feat_path.exists():
        print("Using cached point-in-time features:", feat_path)
        feat = pd.read_csv(feat_path)
        feat["timestamp"] = pd.to_datetime(feat["timestamp"], utc=True)
        feat["c2x_excluded"] = feat["c2x_excluded"].astype(str).str.lower().eq("true")
    else:
        feat = reconstruct_features(prices, cfg)

    summaries = []
    all_trades = {}
    for name, variant_value in VARIANTS.items():
        print(
            f"\\n--- REPLAY {name} "
            f"movement={variant_value:.2f}% ---"
        )
        trades, blocked = replay(name, variant_value, feat, prices, regions, cfg)
        trades.to_csv(OUT / f"trades_{name}.csv", index=False)
        blocked.to_csv(OUT / f"blocked_{name}.csv", index=False)
        all_trades[name] = trades
        s = summarize(name, trades, blocked)
        summaries.append(s)
        print(pd.DataFrame([s]).to_string(index=False))

    summary = pd.DataFrame(summaries)
    diff = compare_to_base(all_trades, summary)
    summary = summary.merge(diff, on="variant", how="left")
    summary.to_csv(OUT / "comparison.csv", index=False)

    metadata = build_replay_metadata(
        replay_type="c5_hours_sequential",
        experiment_parameter="sell.c5_hours",
        variants=VARIANTS,
        production_value=float(cfg["sell"].get("c5_hours", 12.0)),
        cfg=cfg,
        prices=prices,
        db_path=DB,
        config_path=CONFIG,
        instruments_path=INSTRUMENTS,
        replay_script_path=Path(__file__),
    )

    # Spike-collapse is currently hard-coded in these sequential
    # replay scripts at Rise60 >= 25% and DD60 >= 15%.
    # Record those effective values explicitly rather than implying
    # that the replay dynamically consumed the YAML thresholds.
    metadata["effective_replay_settings"] = {
        "spike_collapse_min_rise_percent": float(
            cfg["buy"].get(
                "c2x_spike_collapse_min_rise_percent",
                25.0,
            )
        ),
        "spike_collapse_min_drawdown_percent": float(
            cfg["buy"].get(
                "c2x_spike_collapse_min_drawdown_percent",
                15.0,
            )
        ),
    }

    metadata_path = write_replay_metadata(
        OUT,
        metadata,
    )

    print("\n" + "="*90)
    print("FINAL COMPARISON")
    print("="*90)
    print(summary.to_string(index=False))

    print("\n" + "="*90)
    print("NCPL")
    print("="*90)
    for name, tr in all_trades.items():
        n = tr[tr.ticker.eq("NCPL")]
        if len(n):
            print(f"\n{name}")
            print(n.to_string(index=False))

    print("\nSaved:", OUT / "comparison.csv")
    print("Metadata:", metadata_path)
    print("DONE")

if __name__ == "__main__":
    main()
