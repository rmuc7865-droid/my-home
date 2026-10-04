from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml


# Support both:
#   host:      /opt/home-monitor
#   container: /app
if Path("/app/server/telegram_notifications.yaml").exists():
    APP_ROOT = Path("/app")
else:
    APP_ROOT = Path("/opt/home-monitor")

CONFIG = APP_ROOT / "server" / "telegram_notifications.yaml"

# The incremental validator currently writes its generated data
# under /opt/home-monitor even when executed in the API container.
DATA_CANDIDATES = [
    Path(
        "/data/analysis_results/"
        "c2_recent_path/current_c2x_candidates.csv"
    ),
    Path(
        "/opt/home-monitor/analysis_results/"
        "c2_recent_path/current_c2x_candidates.csv"
    ),
    APP_ROOT
    / "analysis_results"
    / "c2_recent_path"
    / "current_c2x_candidates.csv",
]

DATA = next(
    (p for p in DATA_CANDIDATES if p.exists()),
    DATA_CANDIDATES[0],
)

OUTPUT_ROOT = (
    Path("/data")
    if Path("/data").exists()
    else APP_ROOT
)

OUT_DIR = (
    OUTPUT_ROOT
    / "analysis_results"
    / "weekly_parameter_optimizer"
)

OUT_DIR.mkdir(parents=True, exist_ok=True)

REPORT_JSON = OUT_DIR / "c2x_weekly_evaluation.json"
REPORT_TXT = OUT_DIR / "c2x_weekly_evaluation.txt"

HISTORICAL_PIT = (
    APP_ROOT
    / "analysis_results"
    / "c2_recent_path"
    / "prod_snapshot"
    / "point_in_time_features.csv"
)

SEQUENTIAL_COMPARISON = (
    APP_ROOT
    / "analysis_results"
    / "c2_recent_path"
    / "spike_collapse"
    / "comparison.csv"
)



# Minimum forward evidence required before a parameter
# can become a serious update candidate.
MIN_EVENTS = 8
MIN_TICKERS = 4
MIN_DAYS = 5

# Candidate family deliberately kept small.
RISE_CANDIDATES = [20.0, 25.0, 30.0]
DD_CANDIDATES = [10.0, 12.5, 15.0, 20.0]

EPISODE_GAP_MINUTES = 45


def load_config():
    with CONFIG.open() as f:
        return yaml.safe_load(f)


def independent_events(
    df: pd.DataFrame,
    rise: float,
    dd: float,
) -> pd.DataFrame:

    q = df[
        (df["rise60"] >= rise)
        & (df["dd60"] >= dd)
    ].sort_values(["ticker", "timestamp"])

    events = []

    for ticker, g in q.groupby("ticker"):
        previous = None

        for _, row in g.iterrows():
            now = row["timestamp"]

            if (
                previous is None
                or now - previous
                > pd.Timedelta(minutes=float(EPISODE_GAP_MINUTES))
            ):
                events.append(row)

            previous = now

    if not events:
        return pd.DataFrame(columns=df.columns)

    return pd.DataFrame(events)


def evaluate(
    df: pd.DataFrame,
    rise: float,
    dd: float,
) -> dict:

    ev = independent_events(df, rise, dd)

    valid30 = ev[
        ev["ret30"].notna()
    ].copy()

    result = {
        "rise": rise,
        "drawdown": dd,
        "events": int(len(ev)),
        "tickers": (
            int(ev["ticker"].nunique())
            if len(ev) else 0
        ),
        "days": (
            int(ev["timestamp"].dt.date.nunique())
            if len(ev) else 0
        ),
        "drop4": (
            int(valid30["drop4"].sum())
            if len(valid30) else 0
        ),
        "drop4_rate": (
            float(valid30["drop4"].mean())
            if len(valid30) else None
        ),
        "positive30": (
            int((valid30["ret30"] > 0).sum())
            if len(valid30) else 0
        ),
        "mean_ret30": (
            float(valid30["ret30"].mean())
            if len(valid30) else None
        ),
        "mean_ret60": (
            float(ev["ret60"].mean())
            if len(ev) and ev["ret60"].notna().any()
            else None
        ),
        "mean_ret120": (
            float(ev["ret120"].mean())
            if len(ev) and ev["ret120"].notna().any()
            else None
        ),
    }

    result["evidence_sufficient"] = bool(
        result["events"] >= MIN_EVENTS
        and result["tickers"] >= MIN_TICKERS
        and result["days"] >= MIN_DAYS
    )

    return result


def fmt_pct(x):
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:+.3f}%"


def main():

    config = load_config()
    buy = config["buy"]

    production_rise = float(
        buy["c2x_spike_collapse_min_rise_percent"]
    )
    production_dd = float(
        buy["c2x_spike_collapse_min_drawdown_percent"]
    )
    production_window = int(
        buy["c2x_spike_collapse_window_minutes"]
    )

    if production_window != 60:
        raise SystemExit(
            "Evaluator currently supports only the "
            "60-minute spike-collapse family."
        )

    df = pd.read_csv(DATA)

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True,
        errors="coerce",
    )

    numeric = [
        "rise60",
        "dd60",
        "ret30",
        "ret60",
        "ret120",
        "worst30",
    ]

    for col in numeric:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df["drop4"] = (
        df["worst30"] <= -4.0
    )

    candidates = []

    pairs = {
        (r, d)
        for r in RISE_CANDIDATES
        for d in DD_CANDIDATES
    }

    # Production must always be evaluated even if its
    # values are later changed outside the candidate grid.
    pairs.add(
        (production_rise, production_dd)
    )

    for rise, dd in sorted(pairs):
        candidates.append(
            evaluate(df, rise, dd)
        )

    production = next(
        x for x in candidates
        if x["rise"] == production_rise
        and x["drawdown"] == production_dd
    )

    # A challenger is only considered mature when it has
    # sufficient independent forward evidence.
    mature = [
        x for x in candidates
        if x["evidence_sufficient"]
        and not (
            x["rise"] == production_rise
            and x["drawdown"] == production_dd
        )
    ]

    # WATCH candidates are deliberately not recommendations.
    # Rank only for diagnostic review.
    watch = sorted(
        mature,
        key=lambda x: (
            -(x["drop4_rate"] or 0.0),
            x["mean_ret30"]
            if x["mean_ret30"] is not None
            else 999.0,
            -x["events"],
        ),
    )

    if production["evidence_sufficient"]:
        status = "KEEP_CURRENT"
        reason = (
            "Production threshold has reached the minimum "
            "forward-evidence requirement. No automatic "
            "parameter modification is permitted."
        )
    else:
        status = "INSUFFICIENT_EVIDENCE"
        reason = (
            "Production threshold has not yet reached "
            f"{MIN_EVENTS} independent events, "
            f"{MIN_TICKERS} tickers and {MIN_DAYS} days. "
            "Keep the current production setting."
        )

    # --------------------------------------------------------
    # Historical PIT + sequential replay evidence
    # --------------------------------------------------------
    historical = {
        "available": False,
        "note": None,
    }

    if HISTORICAL_PIT.exists() and SEQUENTIAL_COMPARISON.exists():
        pit = pd.read_csv(
            HISTORICAL_PIT,
            usecols=["ticker", "timestamp"],
        )
        pit["timestamp"] = pd.to_datetime(
            pit["timestamp"],
            utc=True,
            errors="coerce",
        )

        comparison = pd.read_csv(SEQUENTIAL_COMPARISON)

        base_rows = comparison[
            comparison["variant"] == "BASE"
        ]

        prod_rows = comparison[
            comparison["variant"] == "R25_DD15"
        ]

        if len(base_rows) == 1 and len(prod_rows) == 1:
            base = base_rows.iloc[0]
            prod = prod_rows.iloc[0]

            pit_first = pit["timestamp"].min()
            pit_last = pit["timestamp"].max()

            forward_first = df["timestamp"].min()
            forward_last = df["timestamp"].max()

            overlap_start = max(pit_first, forward_first)
            overlap_end = min(pit_last, forward_last)

            overlaps = bool(overlap_start <= overlap_end)

            historical = {
                "available": True,
                "pit_rows": int(len(pit)),
                "pit_tickers": int(pit["ticker"].nunique()),
                "pit_days": int(
                    pit["timestamp"].dt.date.nunique()
                ),
                "pit_first": str(pit_first),
                "pit_last": str(pit_last),
                "forward_overlap": overlaps,
                "overlap_start": (
                    str(overlap_start) if overlaps else None
                ),
                "overlap_end": (
                    str(overlap_end) if overlaps else None
                ),
                "base_trades": int(base["trades"]),
                "base_closed": int(base["closed"]),
                "base_sum_return": float(base["sum_return"]),
                "production_trades": int(prod["trades"]),
                "production_closed": int(prod["closed"]),
                "production_sum_return": float(
                    prod["sum_return"]
                ),
                "sequential_delta": float(
                    prod["sum_return"] - base["sum_return"]
                ),
                "base_entries_not_taken": int(
                    prod["base_entries_not_taken"]
                ),
                "profitable_base_entries_not_taken": int(
                    prod["profitable_base_entries_not_taken"]
                ),
                "losing_base_entries_not_taken": int(
                    prod["losing_base_entries_not_taken"]
                ),
                "sum_return_of_base_entries_not_taken": float(
                    prod["sum_return_of_base_entries_not_taken"]
                ),
                "note": (
                    "PIT and forward periods overlap; they are "
                    "not independent validation datasets."
                ),
            }

    report = {
        "mode": "READ_ONLY",
        "parameter_family": "C2X_SPIKE_COLLAPSE",
        "data_first": str(df["timestamp"].min()),
        "data_last": str(df["timestamp"].max()),
        "observations": int(len(df)),
        "requirements": {
            "minimum_events": MIN_EVENTS,
            "minimum_tickers": MIN_TICKERS,
            "minimum_days": MIN_DAYS,
        },
        "production": production,
        "historical_sequential": historical,
        "status": status,
        "reason": reason,
        "watch_candidates": watch[:5],
        "all_candidates": candidates,
        "production_modified": False,
    }

    REPORT_JSON.write_text(
        json.dumps(report, indent=2) + "\n"
    )

    lines = []

    lines.append("=" * 72)
    lines.append("WEEKLY C2X PARAMETER EVALUATION")
    lines.append("=" * 72)
    lines.append("")
    lines.append("MODE: READ ONLY")
    lines.append(
        f"Data: {df['timestamp'].min()} -> "
        f"{df['timestamp'].max()}"
    )
    lines.append(
        f"Eligible observations: {len(df):,}"
    )

    lines.append("")
    lines.append("PRODUCTION SPIKE-COLLAPSE")
    lines.append("-" * 72)
    lines.append(
        f"Window:       {production_window} min"
    )
    lines.append(
        f"Rise:         {production_rise:g}%"
    )
    lines.append(
        f"Drawdown:     {production_dd:g}%"
    )
    lines.append(
        f"Events:       {production['events']}"
    )
    lines.append(
        f"Tickers:      {production['tickers']}"
    )
    lines.append(
        f"Days:         {production['days']}"
    )
    lines.append(
        f"Drop >=4%:    "
        f"{production['drop4']}/"
        f"{production['events']}"
    )
    lines.append(
        f"Positive30:   {production['positive30']}"
    )
    lines.append(
        f"Mean Ret30:   "
        f"{fmt_pct(production['mean_ret30'])}"
    )
    lines.append(
        f"Mean Ret60:   "
        f"{fmt_pct(production['mean_ret60'])}"
    )
    lines.append(
        f"Mean Ret120:  "
        f"{fmt_pct(production['mean_ret120'])}"
    )

    lines.append("")
    lines.append("HISTORICAL / SEQUENTIAL EVIDENCE")
    lines.append("-" * 72)

    if historical["available"]:
        lines.append(
            f"PIT coverage:  {historical['pit_days']} days / "
            f"{historical['pit_tickers']} tickers / "
            f"{historical['pit_rows']:,} rows"
        )
        lines.append(
            f"BASE return:   "
            f"{historical['base_sum_return']:+.3f}%"
        )
        lines.append(
            f"25/15 return:  "
            f"{historical['production_sum_return']:+.3f}%"
        )
        lines.append(
            f"Replay delta:  "
            f"{historical['sequential_delta']:+.3f} pp"
        )
        lines.append(
            f"Entries blocked: "
            f"{historical['base_entries_not_taken']}"
        )
        lines.append(
            f"Blocked winners: "
            f"{historical['profitable_base_entries_not_taken']}"
        )
        lines.append(
            f"Blocked losers:  "
            f"{historical['losing_base_entries_not_taken']}"
        )
        lines.append(
            "Independence:   NO - PIT and forward "
            "validation periods overlap"
        )
    else:
        lines.append("Historical replay evidence unavailable.")

    lines.append("")
    lines.append(f"STATUS: {status}")
    lines.append(reason)

    lines.append("")
    lines.append("MATURE WATCH CANDIDATES")
    lines.append("-" * 72)

    if not watch:
        lines.append(
            "None have sufficient independent "
            "forward evidence."
        )
    else:
        lines.append(
            " Rise    DD  Events  Tickers  Days"
            "  Drop4%  Pos30   Ret30"
        )

        for x in watch[:5]:
            rate = (
                x["drop4_rate"] * 100.0
                if x["drop4_rate"] is not None
                else float("nan")
            )

            ret30 = (
                x["mean_ret30"]
                if x["mean_ret30"] is not None
                else float("nan")
            )

            lines.append(
                f"{x['rise']:5.1f}"
                f"{x['drawdown']:6.1f}"
                f"{x['events']:8d}"
                f"{x['tickers']:9d}"
                f"{x['days']:6d}"
                f"{rate:8.1f}"
                f"{x['positive30']:7d}"
                f"{ret30:8.3f}"
            )

    lines.append("")
    lines.append(
        "No production configuration was modified."
    )

    text = "\n".join(lines) + "\n"

    REPORT_TXT.write_text(text)

    print(text)
    print("JSON:", REPORT_JSON)
    print("TXT: ", REPORT_TXT)


if __name__ == "__main__":
    main()
