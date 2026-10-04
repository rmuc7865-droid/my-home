from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DB = "/data/monitor.db"
OUTDIR = Path("/opt/home-monitor/analysis_results/c2_recent_path")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Current production C2 configuration.
MIN_CLOSEB = 1.5
MIN_BREADTH = 5

# Current US BUY window.
NY = ZoneInfo("America/New_York")
BUY_START_MIN = 10 * 60
BUY_END_MIN = 15 * 60 + 50

# Candidate recent-path guard grid.
LOOKBACKS = [30, 60, 120]
PEAK_RISES = [8, 10, 12, 15, 20]
DRAWDOWNS = [1, 2, 3, 5, 7.5, 10]
PEAK_AGES = [15, 30, 45, 60, 90, 120]

FWD_MINUTES = [30, 60, 120]
TOL_MIN = 10


def pct(a, b):
    if a is None or b is None or a <= 0:
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
    if abs(times[k] - target) <= tolerance:
        return k
    return None


print("=" * 78)
print("C2 RECENT-PATH CANDIDATE REPLAY")
print("=" * 78)

uri = f"file:{DB}?mode=ro"
con = sqlite3.connect(uri, uri=True)

q = """
SELECT timestamp, measurements_json, metadata_json
FROM measurements
ORDER BY timestamp
"""
raw = pd.read_sql_query(q, con)
con.close()

rows = []
for r in raw.itertuples(index=False):
    try:
        m = json.loads(r.measurements_json or "{}")
        md = json.loads(r.metadata_json or "{}")
    except Exception:
        continue

    ticker = str(md.get("ticker") or "").strip().upper()
    if not ticker:
        continue

    try:
        close = float(m.get("close"))
    except Exception:
        continue

    if not np.isfinite(close) or close <= 0:
        continue

    ts = pd.to_datetime(r.timestamp, utc=True, errors="coerce")
    if pd.isna(ts):
        continue

    rows.append((ticker, ts, close))

df = pd.DataFrame(rows, columns=["ticker", "timestamp", "close"])
df = (
    df.sort_values(["ticker", "timestamp"])
      .drop_duplicates(["ticker", "timestamp"], keep="last")
      .reset_index(drop=True)
)

print(f"Measurements loaded: {len(df):,}")
print(f"Tickers: {df.ticker.nunique():,}")
print(f"Range: {df.timestamp.min()} -> {df.timestamp.max()}")

# ---------------------------------------------------------------------------
# Build point-in-time features ticker by ticker.
# ---------------------------------------------------------------------------

features = []

for ticker, g in df.groupby("ticker", sort=False):
    g = g.sort_values("timestamp").reset_index(drop=True)

    times = list(g["timestamp"])
    closes = g["close"].to_numpy(dtype=float)
    n = len(g)

    for i in range(n):
        now = times[i]
        current = closes[i]

        row = {
            "ticker": ticker,
            "timestamp": now,
            "close": current,
        }

        # Production CloseB uses a ~2h baseline. We use exact 120m with
        # +/-30m tolerance, matching configured baseline tolerance.
        target = now - pd.Timedelta(minutes=120)
        j = nearest_index(
            times,
            target,
            0,
            i + 1,
            pd.Timedelta(minutes=30),
        )

        if j is None or closes[j] <= 0:
            row["closeb"] = np.nan
        else:
            row["closeb"] = pct(closes[j], current)

        # Recent-path state.
        for lb in LOOKBACKS:
            start = now - pd.Timedelta(minutes=lb)

            left = bisect_left(times, start, 0, i + 1)
            segment = closes[left:i + 1]

            if len(segment) == 0:
                row[f"peak_rise_{lb}"] = np.nan
                row[f"drawdown_{lb}"] = np.nan
                row[f"peak_age_{lb}"] = np.nan
                continue

            # Recent minimum -> subsequent maximum rise.
            best_rise = -np.inf
            running_low = segment[0]

            for value in segment:
                if running_low > 0:
                    best_rise = max(
                        best_rise,
                        (value / running_low - 1.0) * 100.0,
                    )
                running_low = min(running_low, value)

            # Most recent occurrence of max close.
            peak_local = int(np.where(segment == np.max(segment))[0][-1])
            peak_idx = left + peak_local
            peak_price = closes[peak_idx]
            peak_time = times[peak_idx]

            row[f"peak_rise_{lb}"] = (
                float(best_rise) if np.isfinite(best_rise) else np.nan
            )
            row[f"drawdown_{lb}"] = (
                (peak_price / current - 1.0) * 100.0
                if current > 0 else np.nan
            )
            row[f"peak_age_{lb}"] = (
                (now - peak_time).total_seconds() / 60.0
            )

        # Forward returns and worst close in next 30m.
        for fwd in FWD_MINUTES:
            target = now + pd.Timedelta(minutes=fwd)
            k = nearest_index(
                times,
                target,
                i + 1,
                n,
                pd.Timedelta(minutes=TOL_MIN),
            )
            row[f"ret{fwd}"] = (
                pct(current, closes[k]) if k is not None else np.nan
            )

        end30 = now + pd.Timedelta(minutes=30)
        right = bisect_left(times, end30, i + 1, n)

        future30 = closes[i + 1:right + 1]
        if len(future30):
            worst = float(np.min(future30))
            row["worst30"] = pct(current, worst)
            row["drop4_30"] = bool(row["worst30"] <= -4.0)
        else:
            row["worst30"] = np.nan
            row["drop4_30"] = False

        features.append(row)

feat = pd.DataFrame(features)

# ---------------------------------------------------------------------------
# Restrict to production US BUY window.
# ---------------------------------------------------------------------------

local = feat["timestamp"].dt.tz_convert(NY)
minute = local.dt.hour * 60 + local.dt.minute

feat = feat[
    (local.dt.weekday < 5)
    & (minute >= BUY_START_MIN)
    & (minute <= BUY_END_MIN)
].copy()

# ---------------------------------------------------------------------------
# Reconstruct C2 candidate observations.
#
# Breadth is calculated at each timestamp:
#   >=5 tickers with CloseB >=1.5%.
#
# This first replay intentionally reconstructs the C2 population before adding
# the NEW path guard. Existing C2X is evaluated separately in production and
# the next refinement can join persisted c2x_observations.
# ---------------------------------------------------------------------------

feat["raw_individual_c2"] = feat["closeb"] >= MIN_CLOSEB

breadth = (
    feat.groupby("timestamp")["raw_individual_c2"]
        .sum()
        .rename("raw_breadth")
)

feat = feat.join(breadth, on="timestamp")

cand = feat[
    feat["raw_individual_c2"]
    & (feat["raw_breadth"] >= MIN_BREADTH)
].copy()

print()
print("=" * 78)
print("RAW C2 CANDIDATE POPULATION")
print("=" * 78)
print(f"Candidate observations: {len(cand):,}")
print(f"Tickers: {cand.ticker.nunique():,}")
print(f"Days: {cand.timestamp.dt.date.nunique():,}")
print(
    f"Drop >=4% next 30m: "
    f"{cand.drop4_30.mean() * 100:.2f}%"
    if len(cand) else "No candidates"
)
for fwd in FWD_MINUTES:
    print(
        f"Mean Ret{fwd}: "
        f"{cand[f'ret{fwd}'].mean():+.3f}%"
    )

# ---------------------------------------------------------------------------
# Guard sweep.
# Guard means:
#   PeakRise >= threshold
#   AND DrawdownFromPeak >= threshold
#   AND PeakAge <= threshold
#
# Note drawdown is stored as positive magnitude from recent peak.
# ---------------------------------------------------------------------------

results = []

baseline_drop4 = cand["drop4_30"].mean() if len(cand) else np.nan

for lb in LOOKBACKS:
    for rise in PEAK_RISES:
        for dd in DRAWDOWNS:
            for age in PEAK_AGES:

                mask = (
                    (cand[f"peak_rise_{lb}"] >= rise)
                    & (cand[f"drawdown_{lb}"] >= dd)
                    & (cand[f"peak_age_{lb}"] <= age)
                )

                blocked = cand[mask]
                kept = cand[~mask]

                if len(blocked) == 0:
                    continue

                results.append({
                    "lookback": lb,
                    "peak_rise": rise,
                    "drawdown": dd,
                    "max_peak_age": age,

                    "blocked": len(blocked),
                    "blocked_tickers": blocked.ticker.nunique(),
                    "blocked_days": blocked.timestamp.dt.date.nunique(),

                    "blocked_drop4_rate":
                        blocked.drop4_30.mean() * 100.0,

                    "drop4_events_blocked":
                        int(blocked.drop4_30.sum()),

                    "positive30_blocked":
                        int((blocked.ret30 > 0).sum()),

                    "mean_blocked_ret30":
                        blocked.ret30.mean(),
                    "mean_blocked_ret60":
                        blocked.ret60.mean(),
                    "mean_blocked_ret120":
                        blocked.ret120.mean(),

                    "kept": len(kept),
                    "kept_drop4_rate":
                        kept.drop4_30.mean() * 100.0
                        if len(kept) else np.nan,

                    "kept_ret30":
                        kept.ret30.mean()
                        if len(kept) else np.nan,

                    "baseline_drop4_rate":
                        baseline_drop4 * 100.0
                        if np.isfinite(baseline_drop4)
                        else np.nan,
                })

res = pd.DataFrame(results)

if not res.empty:
    res["risk_multiple"] = (
        res["blocked_drop4_rate"]
        / res["baseline_drop4_rate"].replace(0, np.nan)
    )

    # Robust enough to avoid choosing a one-ticker anomaly.
    robust = res[
        (res.blocked >= 20)
        & (res.blocked_tickers >= 5)
        & (res.blocked_days >= 5)
    ].copy()

    robust = robust.sort_values(
        [
            "blocked_drop4_rate",
            "drop4_events_blocked",
            "blocked",
        ],
        ascending=[False, False, False],
    )

    print()
    print("=" * 78)
    print("TOP ROBUST C2 PATH-GUARD REGIMES")
    print("=" * 78)

    cols = [
        "lookback",
        "peak_rise",
        "drawdown",
        "max_peak_age",
        "blocked",
        "blocked_tickers",
        "blocked_days",
        "blocked_drop4_rate",
        "drop4_events_blocked",
        "positive30_blocked",
        "mean_blocked_ret30",
        "mean_blocked_ret60",
        "mean_blocked_ret120",
        "kept_drop4_rate",
        "risk_multiple",
    ]

    print(
        robust[cols]
        .head(60)
        .to_string(index=False)
    )

    res.to_csv(OUTDIR / "all_guard_results.csv", index=False)
    robust.to_csv(OUTDIR / "robust_guard_results.csv", index=False)

# ---------------------------------------------------------------------------
# NCPL Sep 29 inspection.
# ---------------------------------------------------------------------------

print()
print("=" * 78)
print("NCPL 2026-09-29")
print("=" * 78)

ncpl = cand[
    (cand.ticker == "NCPL")
    & (cand.timestamp >= pd.Timestamp("2026-09-29 13:00", tz="UTC"))
    & (cand.timestamp <= pd.Timestamp("2026-09-29 15:30", tz="UTC"))
].copy()

show = [
    "timestamp",
    "close",
    "closeb",
    "raw_breadth",
    "peak_rise_30",
    "drawdown_30",
    "peak_age_30",
    "peak_rise_60",
    "drawdown_60",
    "peak_age_60",
    "peak_rise_120",
    "drawdown_120",
    "peak_age_120",
    "ret30",
    "ret60",
    "ret120",
    "worst30",
    "drop4_30",
]

if len(ncpl):
    print(ncpl[show].to_string(index=False))
else:
    print("NCPL has no RAW C2 candidate rows in this interval.")

cand.to_csv(OUTDIR / "raw_c2_candidates.csv", index=False)

print()
print("=" * 78)
print("SAVED")
print("=" * 78)
print(OUTDIR / "raw_c2_candidates.csv")
print(OUTDIR / "all_guard_results.csv")
print(OUTDIR / "robust_guard_results.csv")
print("DONE")
