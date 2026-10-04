from pathlib import Path
from bisect import bisect_left

import numpy as np
import pandas as pd

ROOT = Path("/app")
FEAT = (
    ROOT
    / "analysis_results"
    / "c2_recent_path"
    / "sequential"
    / "point_in_time_features.csv"
)
OUT = (
    ROOT
    / "analysis_results"
    / "c2_recent_path"
    / "c2x_release_severity"
)
OUT.mkdir(parents=True, exist_ok=True)

if not FEAT.exists():
    raise SystemExit(f"Missing feature cache: {FEAT}")

print("=" * 100)
print("C2X RELEASE-SEVERITY ANALYSIS - READ ONLY")
print("=" * 100)
print("Feature cache:", FEAT)
print("Output:", OUT)

df = pd.read_csv(FEAT)
df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
df["c2x_excluded"] = (
    df["c2x_excluded"]
    .astype(str)
    .str.lower()
    .eq("true")
)

numeric_cols = [
    "close", "closeb", "lowrise30", "acceleration_ratio",
    "rise60", "dd60", "age60",
    "rise120", "dd120", "age120",
]
for c in numeric_cols:
    if c in df:
        df[c] = pd.to_numeric(df[c], errors="coerce")

df = (
    df.sort_values(["ticker", "timestamp"])
    .drop_duplicates(["ticker", "timestamp"], keep="last")
    .reset_index(drop=True)
)

print(
    f"Rows: {len(df):,}; "
    f"tickers={df.ticker.nunique()}; "
    f"range={df.timestamp.min()} -> {df.timestamp.max()}"
)


def nearest_idx(times, target, lo, hi, tolerance_min=10):
    j = bisect_left(times, target, lo, hi)
    cand = []

    if lo <= j < hi:
        cand.append(j)
    if lo <= j - 1 < hi:
        cand.append(j - 1)

    if not cand:
        return None

    k = min(
        cand,
        key=lambda x: abs(
            (times[x] - target).total_seconds()
        )
    )

    diff = abs(
        (times[k] - target).total_seconds()
    ) / 60.0

    return k if diff <= tolerance_min else None


records = []

for n, (ticker, g) in enumerate(
    df.groupby("ticker", sort=False), 1
):
    g = g.sort_values("timestamp").reset_index(drop=True)

    times = list(g.timestamp)
    closes = g.close.to_numpy(float)
    excluded = g.c2x_excluded.to_numpy(bool)

    for i in range(len(g)):
        # We are interested only in observations that production
        # C2X currently considers eligible.
        if excluded[i]:
            continue

        now = times[i]

        rec = {
            "ticker": ticker,
            "timestamp": now,
            "close": closes[i],
            "closeb": g.iloc[i].closeb,
        }

        # ---------------------------------------------------------
        # Forward returns
        # ---------------------------------------------------------
        for mins in (15, 30, 60, 120):
            j = nearest_idx(
                times,
                now + pd.Timedelta(minutes=mins),
                i + 1,
                len(times),
                tolerance_min=10,
            )

            if j is None:
                rec[f"ret{mins}"] = np.nan
            else:
                rec[f"ret{mins}"] = (
                    closes[j] / closes[i] - 1.0
                ) * 100.0

        # ---------------------------------------------------------
        # Recent exclusion history
        # ---------------------------------------------------------
        prior_idx = np.where(excluded[:i])[0]

        if len(prior_idx):
            last_excl_i = int(prior_idx[-1])
            rec["minutes_since_exclusion"] = (
                now - times[last_excl_i]
            ).total_seconds() / 60.0
        else:
            last_excl_i = None
            rec["minutes_since_exclusion"] = np.nan

        for window in (30, 60, 90, 120):
            start = now - pd.Timedelta(minutes=window)

            idx = [
                k for k in range(i)
                if times[k] >= start and excluded[k]
            ]

            rec[f"excl_count_{window}"] = len(idx)

            if idx:
                accel = pd.to_numeric(
                    g.iloc[idx].acceleration_ratio,
                    errors="coerce",
                )
                lowrise = pd.to_numeric(
                    g.iloc[idx].lowrise30,
                    errors="coerce",
                )

                rec[f"max_accel_{window}"] = (
                    accel.max()
                    if accel.notna().any()
                    else np.nan
                )
                rec[f"max_lowrise_{window}"] = (
                    lowrise.max()
                    if lowrise.notna().any()
                    else np.nan
                )
            else:
                rec[f"max_accel_{window}"] = np.nan
                rec[f"max_lowrise_{window}"] = np.nan

        # ---------------------------------------------------------
        # Consecutive exclusion episode immediately preceding
        # release.
        #
        # Example:
        # T T T T F  -> release streak = 4
        # T F F F F  -> release streak = 0 at later F observations
        # ---------------------------------------------------------
        streak = 0
        k = i - 1

        while k >= 0 and excluded[k]:
            streak += 1
            k -= 1

        rec["release_streak"] = streak

        if streak:
            episode_idx = list(range(i - streak, i))

            accel = pd.to_numeric(
                g.iloc[episode_idx].acceleration_ratio,
                errors="coerce",
            )
            lowrise = pd.to_numeric(
                g.iloc[episode_idx].lowrise30,
                errors="coerce",
            )

            rec["episode_max_accel"] = (
                accel.max()
                if accel.notna().any()
                else np.nan
            )
            rec["episode_max_lowrise"] = (
                lowrise.max()
                if lowrise.notna().any()
                else np.nan
            )

            rec["episode_minutes"] = (
                times[i - 1] - times[i - streak]
            ).total_seconds() / 60.0 + 15.0
        else:
            rec["episode_max_accel"] = np.nan
            rec["episode_max_lowrise"] = np.nan
            rec["episode_minutes"] = 0.0

        records.append(rec)

    if n % 10 == 0:
        print(f"Processed {n} tickers")

obs = pd.DataFrame(records)

obs["drop4_30"] = obs.ret30 <= -4.0
obs["gain4_30"] = obs.ret30 >= 4.0
obs["positive30"] = obs.ret30 > 0.0

obs.to_csv(
    OUT / "eligible_release_observations.csv",
    index=False,
)

valid = obs[obs.ret30.notna()].copy()

print()
print("=" * 100)
print("CURRENT C2X-ELIGIBLE BASELINE")
print("=" * 100)

print("Observations:", len(valid))
print("Tickers:", valid.ticker.nunique())
print("Days:", valid.timestamp.dt.date.nunique())
print(
    "Drop >=4% next30:",
    int(valid.drop4_30.sum()),
    f"({valid.drop4_30.mean()*100:.3f}%)",
)
print(
    "Gain >=4% next30:",
    int(valid.gain4_30.sum()),
    f"({valid.gain4_30.mean()*100:.3f}%)",
)
print(
    "Positive next30:",
    f"{valid.positive30.mean()*100:.3f}%",
)
print(
    "Ret30 mean/median:",
    round(valid.ret30.mean(), 4),
    round(valid.ret30.median(), 4),
)


def stats(label, x):
    x = x[x.ret30.notna()].copy()

    if not len(x):
        return None

    return {
        "rule": label,
        "n": len(x),
        "tickers": x.ticker.nunique(),
        "days": x.timestamp.dt.date.nunique(),
        "drop4_n": int(x.drop4_30.sum()),
        "drop4_pct": x.drop4_30.mean() * 100.0,
        "gain4_pct": x.gain4_30.mean() * 100.0,
        "positive30_pct": x.positive30.mean() * 100.0,
        "ret15": x.ret15.mean(),
        "ret30": x.ret30.mean(),
        "median30": x.ret30.median(),
        "ret60": x.ret60.mean(),
        "ret120": x.ret120.mean(),
        "worst30": x.ret30.min(),
        "best30": x.ret30.max(),
    }


rows = []

# -------------------------------------------------------------
# 1. Exclusion-count families
# -------------------------------------------------------------
for window in (30, 60, 90, 120):
    for count in (1, 2, 3, 4):
        x = valid[
            valid[f"excl_count_{window}"] >= count
        ]

        s = stats(
            f"EXCL{window}>={count}",
            x,
        )
        if s:
            rows.append(s)

# -------------------------------------------------------------
# 2. Immediate release streak
# -------------------------------------------------------------
for streak in (1, 2, 3, 4):
    x = valid[
        valid.release_streak >= streak
    ]

    s = stats(
        f"STREAK>={streak}",
        x,
    )
    if s:
        rows.append(s)

# -------------------------------------------------------------
# 3. Recent count + severity combinations
# -------------------------------------------------------------
for window in (60, 90):
    for count in (2, 3, 4):
        for accel in (4, 6, 8, 10, 12):
            x = valid[
                (valid[f"excl_count_{window}"] >= count)
                & (
                    valid[f"max_accel_{window}"]
                    >= accel
                )
            ]

            s = stats(
                f"EXCL{window}>={count}"
                f"_ACCEL>={accel}",
                x,
            )
            if s:
                rows.append(s)

# -------------------------------------------------------------
# 4. Immediate episode severity
# -------------------------------------------------------------
for streak in (1, 2, 3, 4):
    for accel in (4, 6, 8, 10, 12):
        x = valid[
            (valid.release_streak >= streak)
            & (valid.episode_max_accel >= accel)
        ]

        s = stats(
            f"STREAK>={streak}"
            f"_ACCEL>={accel}",
            x,
        )
        if s:
            rows.append(s)

# -------------------------------------------------------------
# 5. Recent count + LowRise severity
# -------------------------------------------------------------
for window in (60, 90):
    for count in (2, 3, 4):
        for lr in (6, 8, 10, 12):
            x = valid[
                (valid[f"excl_count_{window}"] >= count)
                & (
                    valid[f"max_lowrise_{window}"]
                    >= lr
                )
            ]

            s = stats(
                f"EXCL{window}>={count}"
                f"_LOWRISE>={lr}",
                x,
            )
            if s:
                rows.append(s)

summary = pd.DataFrame(rows)

if len(summary):
    summary["risk_multiple"] = (
        summary.drop4_pct
        / (valid.drop4_30.mean() * 100.0)
    )

    summary.to_csv(
        OUT / "severity_grid.csv",
        index=False,
    )

    supported = summary[
        (summary["n"] >= 15)
        & (summary["tickers"] >= 5)
        & (summary["days"] >= 5)
    ].copy()

    supported.sort_values(
        [
            "drop4_pct",
            "n",
        ],
        ascending=[
            False,
            False,
        ],
        inplace=True,
    )

    supported.to_csv(
        OUT / "supported_candidates.csv",
        index=False,
    )

    print()
    print("=" * 100)
    print("TOP SUPPORTED CANDIDATES")
    print("n>=15, tickers>=5, days>=5")
    print("=" * 100)

    if len(supported):
        cols = [
            "rule",
            "n",
            "tickers",
            "days",
            "drop4_n",
            "drop4_pct",
            "risk_multiple",
            "gain4_pct",
            "positive30_pct",
            "ret30",
            "median30",
            "ret60",
            "ret120",
            "worst30",
            "best30",
        ]

        print(
            supported[cols]
            .head(40)
            .round(3)
            .to_string(index=False)
        )
    else:
        print("No supported candidates.")

# -------------------------------------------------------------
# NCPL Sep 29 exact observation
# -------------------------------------------------------------
print()
print("=" * 100)
print("NCPL SEP 29 14:15 UTC")
print("=" * 100)

target = obs[
    (obs.ticker == "NCPL")
    & (
        obs.timestamp
        == pd.Timestamp("2026-09-29 14:15:00+00:00")
    )
]

if len(target):
    cols = [
        "ticker",
        "timestamp",
        "close",
        "closeb",
        "minutes_since_exclusion",
        "release_streak",
        "episode_max_accel",
        "episode_max_lowrise",
        "excl_count_30",
        "excl_count_60",
        "excl_count_90",
        "max_accel_60",
        "max_accel_90",
        "max_lowrise_60",
        "ret15",
        "ret30",
        "ret60",
        "ret120",
    ]

    print(
        target[cols]
        .round(4)
        .to_string(index=False)
    )
else:
    print("Target observation not found.")

print()
print("Saved:")
print(" ", OUT / "eligible_release_observations.csv")
print(" ", OUT / "severity_grid.csv")
print(" ", OUT / "supported_candidates.csv")
print("DONE")
