from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd

DB = "/data/monitor.db"
OUT = Path("/data/analysis_results/c2_recent_path")
OUT.mkdir(parents=True, exist_ok=True)

LOOKBACKS = [30, 60, 120]
RISES = [8, 10, 12, 15, 20]
DRAWDOWNS = [1, 2, 3, 5, 7.5, 10]
AGES = [15, 30, 45, 60, 90, 120]
TOL = pd.Timedelta("10min")


def nearest(times, target, lo, hi, tolerance=TOL):
    j = bisect_left(times, target, lo, hi)
    candidates = []
    if lo <= j < hi:
        candidates.append(j)
    if lo <= j - 1 < hi:
        candidates.append(j - 1)
    if not candidates:
        return None
    k = min(candidates, key=lambda x: abs(times[x] - target))
    return k if abs(times[k] - target) <= tolerance else None


def ret(a, b):
    if a <= 0:
        return np.nan
    return (b / a - 1.0) * 100.0


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

obs = pd.read_sql_query("""
SELECT
    ticker,
    observation_time,
    price,
    closeb,
    raw_closeb_count,
    effective_closeb_count,
    minimum_closeb_count,
    raw_c2_breadth_satisfied,
    effective_c2_breadth_satisfied,
    lowrise30,
    acceleration_ratio,
    c2x_excluded,
    c2x_trigger
FROM c2x_observations
ORDER BY observation_time, ticker
""", con)

raw = pd.read_sql_query("""
SELECT timestamp, measurements_json, metadata_json
FROM measurements
ORDER BY timestamp
""", con)

con.close()

obs["observation_time"] = pd.to_datetime(
    obs["observation_time"], utc=True, errors="coerce"
)

rows = []
for r in raw.itertuples(index=False):
    try:
        m = json.loads(r.measurements_json or "{}")
        md = json.loads(r.metadata_json or "{}")
        ticker = str(md.get("ticker") or "").strip().upper()
        close = float(m.get("close"))
        ts = pd.to_datetime(r.timestamp, utc=True, errors="coerce")
    except Exception:
        continue

    if ticker and pd.notna(ts) and np.isfinite(close) and close > 0:
        rows.append((ticker, ts, close))

prices = pd.DataFrame(rows, columns=["ticker", "timestamp", "close"])
prices = (
    prices.sort_values(["ticker", "timestamp"])
          .drop_duplicates(["ticker", "timestamp"], keep="last")
)

history = {}
for ticker, g in prices.groupby("ticker"):
    g = g.sort_values("timestamp")
    history[ticker] = (
        list(g["timestamp"]),
        g["close"].to_numpy(dtype=float),
    )

print("=" * 88)
print("CURRENT C2X INCREMENTAL RECENT-PATH VALIDATION")
print("=" * 88)
print(f"C2X observations: {len(obs):,}")
print(
    f"Coverage: {obs.observation_time.min()} -> "
    f"{obs.observation_time.max()}"
)

# Current production-C2X eligible population.
eligible = obs[
    (obs["effective_c2_breadth_satisfied"].astype(bool))
    & (~obs["c2x_excluded"].astype(bool))
    & (obs["closeb"] >= 1.5)
].copy()

print(f"Current C2X eligible observations: {len(eligible):,}")
print(f"Tickers: {eligible.ticker.nunique():,}")
print(f"Days: {eligible.observation_time.dt.date.nunique():,}")

records = []

for r in eligible.itertuples(index=False):
    ticker = str(r.ticker).upper()

    if ticker not in history:
        continue

    times, closes = history[ticker]
    now = r.observation_time

    i = bisect_left(times, now)

    if i >= len(times) or times[i] != now:
        # Permit a close measurement within 10 minutes of observation.
        k = nearest(
            times,
            now,
            max(0, i - 1),
            min(len(times), i + 1),
        )
        if k is None:
            continue
        i = k

    current = closes[i]

    out = {
        "ticker": ticker,
        "timestamp": now,
        "price": current,
        "closeb": float(r.closeb),
        "effective_count": int(r.effective_closeb_count),
        "c2x_trigger": r.c2x_trigger,
        "lowrise30": float(r.lowrise30),
    }

    for lb in LOOKBACKS:
        start = now - pd.Timedelta(minutes=int(lb))
        left = bisect_left(times, start, 0, i + 1)

        segment = closes[left:i + 1]

        if len(segment) == 0:
            out[f"rise{lb}"] = np.nan
            out[f"dd{lb}"] = np.nan
            out[f"age{lb}"] = np.nan
            continue

        # Maximum low -> later high rise within lookback.
        running_low = segment[0]
        best_rise = 0.0
        for value in segment:
            if running_low > 0:
                best_rise = max(
                    best_rise,
                    (value / running_low - 1.0) * 100.0,
                )
            running_low = min(running_low, value)

        peak_local = int(np.where(segment == np.max(segment))[0][-1])
        peak_idx = left + peak_local
        peak = closes[peak_idx]

        out[f"rise{lb}"] = best_rise
        out[f"dd{lb}"] = (peak / current - 1.0) * 100.0
        out[f"age{lb}"] = (
            (now - times[peak_idx]).total_seconds() / 60.0
        )

    for mins in (30, 60, 120):
        target = now + pd.Timedelta(minutes=mins)
        k = nearest(times, target, i + 1, len(times))
        out[f"ret{mins}"] = (
            ret(current, closes[k]) if k is not None else np.nan
        )

    end = now + pd.Timedelta(minutes=30)
    right = bisect_left(times, end, i + 1, len(times))

    future = closes[i + 1:right + 1]
    if len(future):
        worst = float(np.min(future))
        out["worst30"] = ret(current, worst)
        out["drop4"] = out["worst30"] <= -4.0
    else:
        out["worst30"] = np.nan
        out["drop4"] = False

    records.append(out)

df = pd.DataFrame(records)

print(f"Matched to price history: {len(df):,}")

baseline = df["drop4"].mean() * 100.0

print()
print("=" * 88)
print("CURRENT C2X BASELINE")
print("=" * 88)
print(f"Observations: {len(df):,}")
print(f"Drop >=4% next 30m: {baseline:.3f}%")
print(f"Mean Ret30:  {df.ret30.mean():+.3f}%")
print(f"Mean Ret60:  {df.ret60.mean():+.3f}%")
print(f"Mean Ret120: {df.ret120.mean():+.3f}%")

results = []

for lb in LOOKBACKS:
    for rise in RISES:
        for dd in DRAWDOWNS:
            for age in AGES:

                mask = (
                    (df[f"rise{lb}"] >= rise)
                    & (df[f"dd{lb}"] >= dd)
                    & (df[f"age{lb}"] <= age)
                )

                blocked = df[mask]
                kept = df[~mask]

                if blocked.empty:
                    continue

                results.append({
                    "lookback": lb,
                    "rise": rise,
                    "drawdown": dd,
                    "max_age": age,

                    "blocked": len(blocked),
                    "tickers": blocked.ticker.nunique(),
                    "days": blocked.timestamp.dt.date.nunique(),

                    "drop4_blocked":
                        int(blocked.drop4.sum()),

                    "blocked_drop4_rate":
                        blocked.drop4.mean() * 100.0,

                    "positive30_blocked":
                        int((blocked.ret30 > 0).sum()),

                    "ret30_blocked":
                        blocked.ret30.mean(),

                    "ret60_blocked":
                        blocked.ret60.mean(),

                    "ret120_blocked":
                        blocked.ret120.mean(),

                    "kept":
                        len(kept),

                    "kept_drop4_rate":
                        kept.drop4.mean() * 100.0
                        if len(kept) else np.nan,

                    "kept_ret30":
                        kept.ret30.mean()
                        if len(kept) else np.nan,

                    "risk_multiple":
                        (
                            blocked.drop4.mean() * 100.0 / baseline
                            if baseline > 0 else np.nan
                        ),
                })

res = pd.DataFrame(results)

# With only ~12 days of C2X history, don't demand huge sample sizes.
robust = res[
    (res.blocked >= 8)
    & (res.tickers >= 4)
    & (res.days >= 4)
].copy()

robust = robust.sort_values(
    ["blocked_drop4_rate", "drop4_blocked", "blocked"],
    ascending=[False, False, False],
)

print()
print("=" * 88)
print("TOP INCREMENTAL GUARDS AFTER CURRENT C2X")
print("=" * 88)

cols = [
    "lookback",
    "rise",
    "drawdown",
    "max_age",
    "blocked",
    "tickers",
    "days",
    "drop4_blocked",
    "blocked_drop4_rate",
    "positive30_blocked",
    "ret30_blocked",
    "ret60_blocked",
    "ret120_blocked",
    "kept_drop4_rate",
    "risk_multiple",
]

print(robust[cols].head(80).to_string(index=False))

# Event-level version:
# consecutive qualifying observations of same ticker <=45m apart = one event.
event_rows = []

for params in robust.head(80).itertuples(index=False):
    lb = int(params.lookback)

    mask = (
        (df[f"rise{lb}"] >= params.rise)
        & (df[f"dd{lb}"] >= params.drawdown)
        & (df[f"age{lb}"] <= params.max_age)
    )

    b = df[mask].sort_values(["ticker", "timestamp"]).copy()

    events = []

    for ticker, g in b.groupby("ticker"):
        previous = None

        for row in g.itertuples(index=False):
            if (
                previous is None
                or row.timestamp - previous
                    > pd.Timedelta(minutes=45)
            ):
                events.append(row)

            previous = row.timestamp

    if not events:
        continue

    ev = pd.DataFrame([x._asdict() for x in events])

    event_rows.append({
        "lookback": lb,
        "rise": params.rise,
        "drawdown": params.drawdown,
        "max_age": params.max_age,
        "events": len(ev),
        "event_tickers": ev.ticker.nunique(),
        "event_days": ev.timestamp.dt.date.nunique(),
        "event_drop4":
            int(ev.drop4.sum()),
        "event_drop4_rate":
            ev.drop4.mean() * 100.0,
        "event_positive30":
            int((ev.ret30 > 0).sum()),
        "event_ret30":
            ev.ret30.mean(),
        "event_ret60":
            ev.ret60.mean(),
        "event_ret120":
            ev.ret120.mean(),
    })

events = pd.DataFrame(event_rows)

if not events.empty:
    events = events.sort_values(
        ["event_drop4_rate", "event_drop4", "events"],
        ascending=[False, False, False],
    )

    print()
    print("=" * 88)
    print("EVENT-LEVEL VALIDATION")
    print("=" * 88)
    print(events.head(50).to_string(index=False))

# NCPL specifically.
print()
print("=" * 88)
print("NCPL 29 SEP AFTER CURRENT C2X")
print("=" * 88)

ncpl = df[
    (df.ticker == "NCPL")
    & (df.timestamp >= pd.Timestamp(
        "2026-09-29 13:00", tz="UTC"
    ))
    & (df.timestamp <= pd.Timestamp(
        "2026-09-29 15:30", tz="UTC"
    ))
]

if len(ncpl):
    print(ncpl.to_string(index=False))
else:
    print(
        "No NCPL row survived the current-C2X eligible population."
    )

df.to_csv(OUT / "current_c2x_candidates.csv", index=False)
res.to_csv(OUT / "incremental_guard_grid.csv", index=False)
robust.to_csv(OUT / "incremental_guard_robust.csv", index=False)

if not events.empty:
    events.to_csv(
        OUT / "incremental_guard_events.csv",
        index=False,
    )

print()
print("=" * 88)
print("SAVED")
print("=" * 88)
for p in sorted(OUT.glob("incremental_*")):
    print(p)
print(OUT / "current_c2x_candidates.csv")
print("DONE")
