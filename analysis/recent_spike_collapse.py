from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = "/data/monitor.db"
OUT = Path("/tmp/recent_spike_collapse")
OUT.mkdir(parents=True, exist_ok=True)

LOOKBACKS = [30, 60, 120, 180]
FORWARDS = [30, 60, 120]

PEAK_RISE_THRESHOLDS = [4, 6, 8, 10, 12, 15, 20, 30]
DRAWDOWN_THRESHOLDS = [1, 2, 3, 4, 5, 7.5, 10, 15, 20]
PEAK_AGE_MAX = [15, 30, 45, 60, 90, 120, 180]


def pct(a, b):
    if pd.isna(a) or pd.isna(b) or b == 0:
        return np.nan
    return (a / b - 1.0) * 100.0


# Database is explicitly opened read-only.
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

rows = pd.read_sql_query(
    """
    SELECT
        timestamp,
        measurements_json,
        metadata_json
    FROM measurements
    ORDER BY timestamp
    """,
    con,
)
con.close()

records = []

for r in rows.itertuples(index=False):
    try:
        m = json.loads(r.measurements_json)
        meta = json.loads(r.metadata_json)
    except Exception:
        continue

    if not isinstance(m, dict) or not isinstance(meta, dict):
        continue

    ticker = meta.get("ticker")
    if not ticker:
        continue

    try:
        close = float(m.get("close"))
        high = float(m.get("high", close))
        low = float(m.get("low", close))
    except (TypeError, ValueError):
        continue

    if not np.isfinite(close) or close <= 0:
        continue

    records.append(
        {
            "ticker": str(ticker),
            "time": pd.to_datetime(r.timestamp, utc=True, errors="coerce"),
            "close": close,
            "high": high if np.isfinite(high) else close,
            "low": low if np.isfinite(low) else close,
        }
    )

df = pd.DataFrame(records)
df = df.dropna(subset=["ticker", "time", "close"])
df = (
    df.sort_values(["ticker", "time"])
      .drop_duplicates(["ticker", "time"], keep="last")
      .reset_index(drop=True)
)

print("============================================================")
print("DATA")
print("============================================================")
print("usable measurements:", f"{len(df):,}")
print("tickers:", df.ticker.nunique())
print("range:", df.time.min(), "->", df.time.max())

all_features = []

for ticker, g0 in df.groupby("ticker", sort=False):
    g = g0.sort_values("time").reset_index(drop=True)

    for i in range(len(g)):
        now = pd.Timestamp(g.at[i, "time"])
        current = float(g.at[i, "close"])

        rec = {
            "ticker": ticker,
            "time": now,
            "close": current,
        }

        # Forward returns.
        for fwd in FORWARDS:
            target = now + pd.Timedelta(minutes=fwd)

            future = g[
                (g["time"] >= target)
                & (g["time"] <= target + pd.Timedelta(minutes=10))
            ]

            rec[f"ret{fwd}"] = (
                pct(float(future.iloc[0]["close"]), current)
                if not future.empty
                else np.nan
            )

        # Worst close over the next 30 minutes.
        future30 = g[
            (g["time"] > now)
            & (g["time"] <= now + pd.Timedelta(minutes=30))
        ]

        if not future30.empty:
            min_future = float(future30["close"].min())
            rec["worst30"] = pct(min_future, current)
            rec["drop4_30"] = int(rec["worst30"] <= -4.0)
        else:
            rec["worst30"] = np.nan
            rec["drop4_30"] = np.nan

        # Recent peak state.
        for lb in LOOKBACKS:
            start = now - pd.Timedelta(minutes=lb)

            hist = g[
                (g["time"] >= start)
                & (g["time"] <= now)
            ]

            if len(hist) < 2:
                rec[f"peak_rise_{lb}"] = np.nan
                rec[f"drawdown_{lb}"] = np.nan
                rec[f"peak_age_{lb}"] = np.nan
                continue

            peak_idx = hist["high"].idxmax()
            peak_price = float(hist.loc[peak_idx, "high"])
            peak_time = pd.Timestamp(hist.loc[peak_idx, "time"])
            baseline = float(hist.iloc[0]["close"])

            rec[f"peak_rise_{lb}"] = pct(peak_price, baseline)
            rec[f"drawdown_{lb}"] = pct(current, peak_price)
            rec[f"peak_age_{lb}"] = (
                (now - peak_time).total_seconds() / 60.0
            )

        all_features.append(rec)

feat = pd.DataFrame(all_features)

print()
print("============================================================")
print("BASELINE FORWARD BEHAVIOUR")
print("============================================================")

valid = feat.dropna(subset=["ret30"])

print("n:", f"{len(valid):,}")
print("ret30 mean:", round(valid["ret30"].mean(), 4))
print("ret30 median:", round(valid["ret30"].median(), 4))
print("drop4_30:", round(valid["drop4_30"].mean() * 100, 2), "%")

summary_rows = []

for lb in LOOKBACKS:
    for rise in PEAK_RISE_THRESHOLDS:
        for dd in DRAWDOWN_THRESHOLDS:
            for age in PEAK_AGE_MAX:

                x = feat[
                    (feat[f"peak_rise_{lb}"] >= rise)
                    & (feat[f"drawdown_{lb}"] <= -dd)
                    & (feat[f"peak_age_{lb}"] <= age)
                ].dropna(subset=["ret30"])

                if len(x) < 10:
                    continue

                summary_rows.append(
                    {
                        "lookback": lb,
                        "peak_rise": rise,
                        "drawdown": dd,
                        "peak_age": age,
                        "n": len(x),
                        "tickers": x["ticker"].nunique(),
                        "days": x["time"].dt.date.nunique(),
                        "drop4_pct": x["drop4_30"].mean() * 100,
                        "ret30": x["ret30"].mean(),
                        "ret60": x["ret60"].mean(),
                        "ret120": x["ret120"].mean(),
                        "worst30": x["worst30"].mean(),
                    }
                )

summary = pd.DataFrame(summary_rows)

if summary.empty:
    print("No candidate groups with n >= 10.")
    raise SystemExit(0)

summary["risk_score"] = (
    summary["drop4_pct"]
    - 3.0 * summary["ret30"]
    - summary["ret60"]
)

summary = summary.sort_values(
    ["risk_score", "n"],
    ascending=[False, False],
)

print()
print("============================================================")
print("TOP POST-SPIKE RISK REGIMES")
print("============================================================")

show = [
    "lookback", "peak_rise", "drawdown", "peak_age",
    "n", "tickers", "days",
    "drop4_pct", "ret30", "ret60", "ret120", "worst30",
]

print(summary[show].head(40).to_string(index=False))

robust = summary[
    (summary["n"] >= 50)
    & (summary["tickers"] >= 10)
    & (summary["days"] >= 10)
]

print()
print("============================================================")
print("ROBUST GROUPS: n>=50, >=10 tickers, >=10 days")
print("============================================================")

if robust.empty:
    print("None")
else:
    print(robust[show].head(40).to_string(index=False))

print()
print("============================================================")
print("NCPL 2026-09-29 INCIDENT")
print("============================================================")

ncpl = feat[
    (feat["ticker"] == "NCPL")
    & (feat["time"] >= pd.Timestamp("2026-09-29 13:00", tz="UTC"))
    & (feat["time"] <= pd.Timestamp("2026-09-29 16:00", tz="UTC"))
]

ncpl_cols = [
    "time", "close",
    "peak_rise_30", "drawdown_30", "peak_age_30",
    "peak_rise_60", "drawdown_60", "peak_age_60",
    "peak_rise_120", "drawdown_120", "peak_age_120",
    "ret30", "ret60", "ret120", "drop4_30",
]

print(ncpl[ncpl_cols].to_string(index=False))

summary.to_csv(OUT / "threshold_grid.csv", index=False)
feat.to_csv(OUT / "observations.csv", index=False)

print()
print("Output:", OUT / "threshold_grid.csv")
print("Output:", OUT / "observations.csv")
