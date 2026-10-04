from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Literal
import json

import pandas as pd
import yaml


# Works both on host (/opt/home-monitor) and API container (/app).
if Path("/app").exists():
    ROOT = Path("/app")
else:
    ROOT = Path("/opt/home-monitor")

CONFIG = ROOT / "server" / "telegram_notifications.yaml"

DATA_ROOT = (
    Path("/data")
    if Path("/data").exists()
    else ROOT
)

OUT_DIR = (
    DATA_ROOT
    / "analysis_results"
    / "weekly_parameter_optimizer"
)

OUTPUT = OUT_DIR / "weekly_parameter_optimizer.json"
TEXT_OUTPUT = OUT_DIR / "weekly_parameter_optimizer.txt"

C2X_REPORT = OUT_DIR / "c2x_weekly_evaluation.json"

C45_DIR = (
    DATA_ROOT
    / "analysis_results"
    / "c45_movement_sequential"
)

C5_DIR = (
    DATA_ROOT
    / "analysis_results"
    / "c5_hours_sequential"
)

C6_DIR = (
    DATA_ROOT
    / "analysis_results"
    / "c6_fine_sequential"
)


Status = Literal[
    "FROZEN",
    "ANALYZE",
    "INSUFFICIENT_EVIDENCE",
    "STALE_EVIDENCE",
    "INVALID_EVIDENCE",
    "KEEP_CURRENT",
    "WATCH",
    "UPDATE_CANDIDATE",
]


@dataclass
class Parameter:
    name: str
    rule: str
    config_key: str
    current_value: float
    status: Status
    structural: bool
    notes: str

    independent_events: int = 0
    ticker_days: int = 0
    affected_weeks: int = 0
    candidate_value: float | None = None
    evidence_score: float | None = None

    evidence: dict | None = None



REPLAY_PROVENANCE = {
    "c45": {
        "directory": C45_DIR,
        "replay_type": "c45_movement_sequential",
        "parameter": "sell.movement_percent",
        "script": (
            ROOT
            / "analysis"
            / "c45_movement_sequential_replay.py"
        ),
    },
    "c5": {
        "directory": C5_DIR,
        "replay_type": "c5_hours_sequential",
        "parameter": "sell.c5_hours",
        "script": (
            ROOT
            / "analysis"
            / "c5_hours_sequential_replay.py"
        ),
    },
    "c6": {
        "directory": C6_DIR,
        "replay_type": "c6_min_gain_sequential",
        "parameter": "sell.c6_min_gain_percent",
        "script": (
            ROOT
            / "analysis"
            / "c6_fine_sequential_replay.py"
        ),
    },
    "c7": {
        "directory": (
            DATA_ROOT
            / "analysis_results"
            / "c7_max_gain_sequential"
        ),
        "replay_type": "c7_max_gain_sequential",
        "parameter": "sell.c7_max_gain_percent",
        "script": (
            ROOT
            / "analysis"
            / "c7_max_gain_sequential_replay.py"
        ),
    },
}


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def expected_production_config(
    config: dict,
) -> dict:

    sell = config["sell"]

    return {
        "movement_percent": float(
            sell["movement_percent"]
        ),
        "c5_hours": float(
            sell["c5_hours"]
        ),
        "c6_min_gain_percent": float(
            sell["c6_min_gain_percent"]
        ),
        "c7_max_gain_percent": float(
            sell["c7_max_gain_percent"]
        ),
    }


def values_match(
    left,
    right,
    tolerance: float = 1e-9,
) -> bool:

    if isinstance(left, bool) or isinstance(right, bool):
        return left == right

    if (
        isinstance(left, (int, float))
        and isinstance(right, (int, float))
    ):
        return abs(
            float(left) - float(right)
        ) <= tolerance

    return left == right


def validate_replay_provenance(
    config: dict,
) -> dict:

    result = {
        "valid": False,
        "status": "INVALID_EVIDENCE",
        "reason": "",
        "replays": {},
        "dataset_fingerprint": None,
    }

    config_hash = sha256_file(CONFIG)

    instruments_path = (
        ROOT
        / "config"
        / "instruments.json"
    )

    if not instruments_path.exists():
        result["reason"] = (
            "Current instruments.json is missing."
        )
        return result

    instruments_hash = sha256_file(
        instruments_path
    )

    expected_prod = expected_production_config(
        config
    )

    loaded = {}

    # First validate each replay independently.
    for name, spec in REPLAY_PROVENANCE.items():

        metadata_path = (
            spec["directory"]
            / "replay_metadata.json"
        )

        replay_result = {
            "valid": False,
            "status": "INVALID_EVIDENCE",
            "reason": "",
            "metadata_file": str(
                metadata_path
            ),
        }

        result["replays"][name] = replay_result

        if not metadata_path.exists():
            replay_result["reason"] = (
                "Replay metadata is missing."
            )
            result["reason"] = (
                f"{name}: replay metadata is missing."
            )
            return result

        try:
            metadata = json.loads(
                metadata_path.read_text()
            )
        except Exception as exc:
            replay_result["reason"] = (
                f"Replay metadata cannot be read: {exc}"
            )
            result["reason"] = (
                f"{name}: invalid replay metadata."
            )
            return result

        loaded[name] = metadata

        if metadata.get("schema_version") != 1:
            replay_result["reason"] = (
                "Unsupported replay metadata schema."
            )
            result["reason"] = (
                f"{name}: unsupported metadata schema."
            )
            return result

        if (
            metadata.get("replay_type")
            != spec["replay_type"]
        ):
            replay_result["reason"] = (
                "Replay type does not match the "
                "expected evidence family."
            )
            result["reason"] = (
                f"{name}: replay type mismatch."
            )
            return result

        experiment = metadata.get(
            "experiment",
            {},
        )

        if (
            experiment.get("parameter")
            != spec["parameter"]
        ):
            replay_result["reason"] = (
                "Experiment parameter does not match "
                "the expected parameter."
            )
            result["reason"] = (
                f"{name}: experiment parameter mismatch."
            )
            return result

        database = metadata.get(
            "database",
            {},
        )

        required_db = [
            "path",
            "measurement_count",
            "ticker_count",
            "first_measurement",
            "last_measurement",
        ]

        missing_db = [
            key
            for key in required_db
            if database.get(key) is None
        ]

        if missing_db:
            replay_result["reason"] = (
                "Database fingerprint is incomplete: "
                + ", ".join(missing_db)
            )
            result["reason"] = (
                f"{name}: incomplete database fingerprint."
            )
            return result

        production = metadata.get(
            "production_config",
            {},
        )

        if any(
            key not in production
            for key in expected_prod
        ):
            replay_result["reason"] = (
                "Production configuration snapshot "
                "is incomplete."
            )
            result["reason"] = (
                f"{name}: incomplete production config."
            )
            return result

        stale_reasons = []

        for key, expected in expected_prod.items():
            actual = production.get(key)

            if not values_match(
                actual,
                expected,
            ):
                stale_reasons.append(
                    f"{key}: replay={actual}, "
                    f"current={expected}"
                )

        production_value = experiment.get(
            "production_value"
        )

        expected_experiment_value = {
            "sell.movement_percent":
                expected_prod["movement_percent"],
            "sell.c5_hours":
                expected_prod["c5_hours"],
            "sell.c6_min_gain_percent":
                expected_prod[
                    "c6_min_gain_percent"
                ],
            "sell.c7_max_gain_percent":
                expected_prod[
                    "c7_max_gain_percent"
                ],
        }[spec["parameter"]]

        if not values_match(
            production_value,
            expected_experiment_value,
        ):
            stale_reasons.append(
                "experiment production value: "
                f"replay={production_value}, "
                f"current={expected_experiment_value}"
            )

        artifacts = metadata.get(
            "artifacts",
            {},
        )

        if (
            artifacts.get("config_sha256")
            != config_hash
        ):
            stale_reasons.append(
                "production config file hash changed"
            )

        if (
            artifacts.get(
                "instruments_sha256"
            )
            != instruments_hash
        ):
            stale_reasons.append(
                "instruments.json hash changed"
            )

        script_path = spec["script"]

        if not script_path.exists():
            replay_result["reason"] = (
                "Current replay script is missing."
            )
            result["reason"] = (
                f"{name}: replay script is missing."
            )
            return result

        current_script_hash = sha256_file(
            script_path
        )

        if (
            artifacts.get(
                "replay_script_sha256"
            )
            != current_script_hash
        ):
            stale_reasons.append(
                "replay script hash changed"
            )

        if stale_reasons:
            replay_result["status"] = (
                "STALE_EVIDENCE"
            )
            replay_result["reason"] = (
                "; ".join(stale_reasons)
            )

            result["status"] = (
                "STALE_EVIDENCE"
            )
            result["reason"] = (
                f"{name}: "
                + replay_result["reason"]
            )

            return result

        replay_result["valid"] = True
        replay_result["status"] = "VALID"
        replay_result["reason"] = (
            "Replay metadata matches current "
            "configuration, instruments and code."
        )

    # Then require one exact immutable dataset universe
    # across all four replay families.
    fingerprints = {}

    for name, metadata in loaded.items():
        db = metadata["database"]

        fingerprint = (
            str(db["path"]),
            int(db["measurement_count"]),
            int(db["ticker_count"]),
            str(db["first_measurement"]),
            str(db["last_measurement"]),
        )

        fingerprints[name] = fingerprint

    unique_fingerprints = set(
        fingerprints.values()
    )

    if len(unique_fingerprints) != 1:
        result["status"] = "INVALID_EVIDENCE"
        result["reason"] = (
            "Replay families were generated from "
            "different database snapshots."
        )

        for name, fingerprint in (
            fingerprints.items()
        ):
            result["replays"][name][
                "dataset_fingerprint"
            ] = list(fingerprint)

        return result

    fingerprint = next(
        iter(unique_fingerprints)
    )

    # Require an explicit snapshot, rather than allowing
    # the continuously changing production database.
    snapshot_path = fingerprint[0]

    if snapshot_path == "/data/monitor.db":
        result["status"] = "INVALID_EVIDENCE"
        result["reason"] = (
            "Weekly optimization evidence must use an "
            "immutable snapshot, not /data/monitor.db."
        )
        return result

    result["valid"] = True
    result["status"] = "VALID"
    result["reason"] = (
        "All replay evidence matches the current "
        "configuration, instruments and replay code, "
        "and all replay families use the same "
        "immutable dataset."
    )

    result["dataset_fingerprint"] = {
        "path": fingerprint[0],
        "measurement_count": fingerprint[1],
        "ticker_count": fingerprint[2],
        "first_measurement": fingerprint[3],
        "last_measurement": fingerprint[4],
    }

    return result


def load_config() -> dict:
    with CONFIG.open() as f:
        return yaml.safe_load(f)


def load_c2x_evidence() -> dict | None:
    if not C2X_REPORT.exists():
        return None

    try:
        with C2X_REPORT.open() as f:
            return json.load(f)
    except Exception:
        return None


def load_c45_evidence(
    config: dict,
) -> dict | None:

    comparison_path = C45_DIR / "comparison.csv"

    if not comparison_path.exists():
        return None

    comparison = pd.read_csv(comparison_path)

    if comparison.empty:
        return None

    production_value = float(
        config["sell"]["movement_percent"]
    )

    rows = comparison.copy()

    # M2_00 -> 2.00, M2_50 -> 2.50, etc.
    extracted = (
        rows["variant"]
        .astype(str)
        .str.extract(
            r"^M(\d+)_(\d+)$",
            expand=True,
        )
    )

    rows["candidate_value"] = pd.to_numeric(
        extracted[0],
        errors="coerce",
    ) + (
        pd.to_numeric(
            extracted[1],
            errors="coerce",
        )
        / 100.0
    )

    rows["sum_return"] = pd.to_numeric(
        rows["sum_return"],
        errors="coerce",
    )

    rows = rows.dropna(
        subset=[
            "candidate_value",
            "sum_return",
        ]
    ).copy()

    if rows.empty:
        return None

    prod_rows = rows[
        (
            rows["candidate_value"]
            - production_value
        ).abs() < 1e-9
    ]

    if prod_rows.empty:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": (
                "Current production C4/C5 movement value "
                "is not present in the latest replay grid."
            ),
            "production_value": production_value,
            "candidate_value": None,
            "source_files": [
                str(comparison_path),
            ],
        }

    prod = prod_rows.iloc[0]

    alternatives = rows[
        (
            rows["candidate_value"]
            - production_value
        ).abs() >= 1e-9
    ].copy()

    if alternatives.empty:
        return None

    cand = alternatives.sort_values(
        "sum_return",
        ascending=False,
    ).iloc[0]

    candidate_value = float(
        cand["candidate_value"]
    )

    prod_variant = str(prod["variant"])
    cand_variant = str(cand["variant"])

    prod_path = (
        C45_DIR
        / f"trades_{prod_variant}.csv"
    )

    cand_path = (
        C45_DIR
        / f"trades_{cand_variant}.csv"
    )

    if (
        not prod_path.exists()
        or not cand_path.exists()
    ):
        return None

    prod_trades = pd.read_csv(prod_path)
    cand_trades = pd.read_csv(cand_path)

    for df in (prod_trades, cand_trades):
        df["buy_time"] = pd.to_datetime(
            df["buy_time"],
            utc=True,
        )
        df["return_pct"] = pd.to_numeric(
            df["return_pct"],
            errors="coerce",
        )

    keys = ["ticker", "buy_time"]

    paired = prod_trades.merge(
        cand_trades,
        on=keys,
        how="inner",
        suffixes=("_prod", "_cand"),
    )

    paired = paired.dropna(
        subset=[
            "return_pct_prod",
            "return_pct_cand",
        ]
    ).copy()

    paired["delta"] = (
        paired["return_pct_cand"]
        - paired["return_pct_prod"]
    )

    changed = paired[
        paired["delta"].abs() > 1e-12
    ].copy()

    improved = int(
        (changed["delta"] > 0).sum()
    )

    worsened = int(
        (changed["delta"] < 0).sum()
    )

    same_entry_delta = float(
        changed["delta"].sum()
    )

    median_changed_delta = (
        float(changed["delta"].median())
        if not changed.empty
        else 0.0
    )

    positive = changed[
        changed["delta"] > 0
    ].copy()

    gross_positive = float(
        positive["delta"].sum()
    )

    best_trade_share = 0.0

    if gross_positive > 0.0:
        best_trade_share = float(
            positive["delta"].max()
            / gross_positive
            * 100.0
        )

    ex_best_trade_delta = same_entry_delta

    if not positive.empty:
        ex_best_trade_delta -= float(
            positive["delta"].max()
        )

    # Entry-path differences.
    prod_keys = prod_trades[
        keys
    ].drop_duplicates()

    cand_keys = cand_trades[
        keys
    ].drop_duplicates()

    only_prod = prod_keys.merge(
        cand_keys,
        on=keys,
        how="left",
        indicator=True,
    )

    only_prod = only_prod[
        only_prod["_merge"] == "left_only"
    ]

    only_cand = cand_keys.merge(
        prod_keys,
        on=keys,
        how="left",
        indicator=True,
    )

    only_cand = only_cand[
        only_cand["_merge"] == "left_only"
    ]

    def weekly_returns(df):
        x = df.copy()

        naive = (
            x["buy_time"]
            .dt.tz_convert("UTC")
            .dt.tz_localize(None)
        )

        x["week"] = (
            naive
            .dt.to_period("W-SUN")
            .astype(str)
        )

        return x.groupby(
            "week"
        )["return_pct"].sum()

    wp = weekly_returns(prod_trades)
    wc = weekly_returns(cand_trades)

    weekly = pd.concat(
        [
            wp.rename("production"),
            wc.rename("candidate"),
        ],
        axis=1,
    ).fillna(0.0)

    weekly["delta"] = (
        weekly["candidate"]
        - weekly["production"]
    )

    weeks_better = int(
        (weekly["delta"] > 1e-12).sum()
    )

    weeks_worse = int(
        (weekly["delta"] < -1e-12).sum()
    )

    weeks_equal = int(
        (
            weekly["delta"].abs()
            <= 1e-12
        ).sum()
    )

    total_delta = float(
        cand["sum_return"]
        - prod["sum_return"]
    )

    if len(weekly):
        leave_one_week_out = (
            total_delta
            - weekly["delta"]
        )

        worst_leave_one_week_out = float(
            leave_one_week_out.min()
        )

        median_week_delta = float(
            weekly["delta"].median()
        )

        best_week_delta = float(
            weekly["delta"].max()
        )

        delta_without_best_week = float(
            total_delta
            - best_week_delta
        )
    else:
        worst_leave_one_week_out = 0.0
        median_week_delta = 0.0
        delta_without_best_week = 0.0

    changed_tickers = int(
        changed["ticker"].nunique()
    )

    if not changed.empty:
        ticker_delta = (
            changed.groupby("ticker")["delta"]
            .sum()
        )

        leave_one_ticker_out = (
            same_entry_delta
            - ticker_delta
        )

        worst_leave_one_ticker_out = float(
            leave_one_ticker_out.min()
        )
    else:
        worst_leave_one_ticker_out = 0.0

    # Preserve the original conservative C45 decision
    # philosophy, but calculate it exclusively from the
    # fresh replay outputs.
    time_support = bool(
        weeks_better > weeks_worse
        and delta_without_best_week > 0.0
        and total_delta > 0.0
    )

    trade_level_robust = bool(
        ex_best_trade_delta > 0.0
        and median_changed_delta >= 0.0
        and improved >= worsened
    )

    if time_support and trade_level_robust:
        status = "UPDATE_CANDIDATE"
        reason = (
            "Candidate has positive aggregate, weekly, "
            "and same-entry robustness."
        )
    elif time_support:
        status = "WATCH"
        reason = (
            "Candidate has aggregate and weekly support, "
            "but same-entry benefit is concentrated or "
            "fails trade-level robustness."
        )
    else:
        status = "KEEP_CURRENT"
        reason = (
            "Candidate does not yet have sufficient "
            "cross-week robustness."
        )

    return {
        "production_value": production_value,
        "candidate_value": candidate_value,
        "status": status,
        "reason": reason,

        "production_variant": prod_variant,
        "candidate_variant": cand_variant,

        "production_trades": int(
            prod["trades"]
        ),
        "candidate_trades": int(
            cand["trades"]
        ),

        "production_sum_return": float(
            prod["sum_return"]
        ),
        "candidate_sum_return": float(
            cand["sum_return"]
        ),
        "total_delta": total_delta,

        "weeks_better": weeks_better,
        "weeks_worse": weeks_worse,
        "weeks_equal": weeks_equal,
        "median_week_delta": median_week_delta,
        "delta_without_best_week":
            delta_without_best_week,
        "worst_leave_one_week_out":
            worst_leave_one_week_out,

        "common_closed_entries": int(
            len(paired)
        ),
        "changed_outcomes": int(
            len(changed)
        ),
        "improved": improved,
        "worsened": worsened,
        "median_changed_delta":
            median_changed_delta,
        "same_entry_delta":
            same_entry_delta,
        "ex_best_trade_delta": float(
            ex_best_trade_delta
        ),

        "changed_tickers":
            changed_tickers,
        "worst_leave_one_ticker_out":
            worst_leave_one_ticker_out,

        "best_trade_share_of_gross_positive_pct":
            best_trade_share,

        "only_production_entries": int(
            len(only_prod)
        ),
        "only_candidate_entries": int(
            len(only_cand)
        ),

        "time_support": time_support,
        "trade_level_robust":
            trade_level_robust,

        "production_snapshot": {
            "c5_hours": float(
                config["sell"]["c5_hours"]
            ),
            "c6_min_gain_percent": float(
                config["sell"]["c6_min_gain_percent"]
            ),
            "c7_max_gain_percent": float(
                config["sell"]["c7_max_gain_percent"]
            ),
            "movement_percent":
                production_value,
        },

        "source_files": [
            str(comparison_path),
            str(prod_path),
            str(cand_path),
        ],
    }


def load_c5_evidence(
    config: dict,
) -> dict | None:

    comparison_path = C5_DIR / "comparison.csv"

    if not comparison_path.exists():
        return None

    comparison = pd.read_csv(comparison_path)

    if comparison.empty:
        return None

    production_value = float(
        config["sell"]["c5_hours"]
    )

    # Map replay variants such as H06/H10/H12 to their
    # numeric C5-hour values.
    rows = comparison.copy()

    rows["candidate_value"] = (
        rows["variant"]
        .astype(str)
        .str.extract(r"H(\d+)", expand=False)
    )

    rows["candidate_value"] = pd.to_numeric(
        rows["candidate_value"],
        errors="coerce",
    )

    rows = rows.dropna(
        subset=[
            "candidate_value",
            "sum_return",
        ]
    ).copy()

    if rows.empty:
        return None

    prod_rows = rows[
        (
            rows["candidate_value"]
            - production_value
        ).abs() < 1e-9
    ]

    if prod_rows.empty:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": (
                "Current production C5 value is not present "
                "in the latest replay grid."
            ),
            "production_value": production_value,
            "candidate_value": None,
            "source_files": [
                str(comparison_path),
            ],
        }

    prod = prod_rows.iloc[0]

    alternatives = rows[
        (
            rows["candidate_value"]
            - production_value
        ).abs() >= 1e-9
    ].copy()

    if alternatives.empty:
        return None

    cand = alternatives.sort_values(
        "sum_return",
        ascending=False,
    ).iloc[0]

    candidate_value = float(
        cand["candidate_value"]
    )

    prod_variant = str(prod["variant"])
    cand_variant = str(cand["variant"])

    prod_path = (
        C5_DIR
        / f"trades_{prod_variant}.csv"
    )

    cand_path = (
        C5_DIR
        / f"trades_{cand_variant}.csv"
    )

    if (
        not prod_path.exists()
        or not cand_path.exists()
    ):
        return None

    prod_trades = pd.read_csv(prod_path)
    cand_trades = pd.read_csv(cand_path)

    for df in (prod_trades, cand_trades):
        df["buy_time"] = pd.to_datetime(
            df["buy_time"],
            utc=True,
        )
        df["return_pct"] = pd.to_numeric(
            df["return_pct"],
            errors="coerce",
        )

    keys = ["ticker", "buy_time"]

    paired = prod_trades.merge(
        cand_trades,
        on=keys,
        how="inner",
        suffixes=("_prod", "_cand"),
    )

    paired = paired.dropna(
        subset=[
            "return_pct_prod",
            "return_pct_cand",
        ]
    ).copy()

    paired["delta"] = (
        paired["return_pct_cand"]
        - paired["return_pct_prod"]
    )

    changed = paired[
        paired["delta"].abs() > 1e-12
    ].copy()

    improved = int(
        (changed["delta"] > 0).sum()
    )

    worsened = int(
        (changed["delta"] < 0).sum()
    )

    same_entry_delta = float(
        changed["delta"].sum()
    )

    median_changed_delta = (
        float(changed["delta"].median())
        if not changed.empty
        else 0.0
    )

    positive = changed[
        changed["delta"] > 0
    ].copy()

    gross_positive = float(
        positive["delta"].sum()
    )

    best_trade_share = 0.0

    if gross_positive > 0.0:
        best_trade_share = float(
            positive["delta"].max()
            / gross_positive
            * 100.0
        )

    ex_best_trade_delta = same_entry_delta

    if not positive.empty:
        ex_best_trade_delta -= float(
            positive["delta"].max()
        )

    # Entry-path differences.
    prod_keys = prod_trades[
        keys
    ].drop_duplicates()

    cand_keys = cand_trades[
        keys
    ].drop_duplicates()

    only_prod = prod_keys.merge(
        cand_keys,
        on=keys,
        how="left",
        indicator=True,
    )

    only_prod = only_prod[
        only_prod["_merge"] == "left_only"
    ]

    only_cand = cand_keys.merge(
        prod_keys,
        on=keys,
        how="left",
        indicator=True,
    )

    only_cand = only_cand[
        only_cand["_merge"] == "left_only"
    ]

    # Weekly robustness.
    def weekly_returns(df):
        x = df.copy()

        # Strip timezone explicitly before Period conversion.
        naive = (
            x["buy_time"]
            .dt.tz_convert("UTC")
            .dt.tz_localize(None)
        )

        x["week"] = (
            naive
            .dt.to_period("W-SUN")
            .astype(str)
        )

        return x.groupby(
            "week"
        )["return_pct"].sum()

    wp = weekly_returns(prod_trades)
    wc = weekly_returns(cand_trades)

    weekly = pd.concat(
        [
            wp.rename("production"),
            wc.rename("candidate"),
        ],
        axis=1,
    ).fillna(0.0)

    weekly["delta"] = (
        weekly["candidate"]
        - weekly["production"]
    )

    weeks_better = int(
        (weekly["delta"] > 1e-12).sum()
    )

    weeks_worse = int(
        (weekly["delta"] < -1e-12).sum()
    )

    weeks_equal = int(
        (
            weekly["delta"].abs()
            <= 1e-12
        ).sum()
    )

    total_delta = float(
        cand["sum_return"]
        - prod["sum_return"]
    )

    if len(weekly):
        leave_one_week_out = (
            total_delta
            - weekly["delta"]
        )

        worst_leave_one_week_out = float(
            leave_one_week_out.min()
        )
    else:
        worst_leave_one_week_out = 0.0

    # Ticker concentration among changed same-entry outcomes.
    changed_tickers = int(
        changed["ticker"].nunique()
    )

    if not changed.empty:
        ticker_delta = (
            changed.groupby("ticker")["delta"]
            .sum()
        )

        leave_one_ticker_out = (
            same_entry_delta
            - ticker_delta
        )

        worst_leave_one_ticker_out = float(
            leave_one_ticker_out.min()
        )
    else:
        worst_leave_one_ticker_out = 0.0

    # Conservative evidence gates.
    aggregate_support = bool(
        total_delta > 0.0
    )

    time_support = bool(
        weeks_better > weeks_worse
        and worst_leave_one_week_out > 0.0
    )

    trade_support = bool(
        len(changed) >= 4
        and improved > worsened
        and median_changed_delta > 0.0
        and ex_best_trade_delta > 0.0
    )

    diversity_support = bool(
        changed_tickers >= 2
        and worst_leave_one_ticker_out > 0.0
        and best_trade_share <= 60.0
    )

    if (
        aggregate_support
        and time_support
        and trade_support
        and diversity_support
    ):
        status = "UPDATE_CANDIDATE"

        reason = (
            "Candidate has positive aggregate, cross-week, "
            "trade-level and cross-ticker robustness."
        )

    elif (
        aggregate_support
        and (
            time_support
            or trade_support
            or diversity_support
        )
    ):
        status = "WATCH"

        reason = (
            "Candidate improves aggregate return but does "
            "not yet pass all robustness gates."
        )

    else:
        status = "KEEP_CURRENT"

        reasons = []

        if not time_support:
            reasons.append(
                "fails leave-one-week-out/cross-week robustness"
            )

        if not trade_support:
            reasons.append(
                "changed-trade evidence is weak or concentrated"
            )

        if not diversity_support:
            reasons.append(
                "ticker diversity is insufficient"
            )

        reason = (
            "Raw candidate is not robust enough: "
            + "; ".join(reasons)
            + "."
        )

    return {
        "production_value": production_value,
        "candidate_value": candidate_value,
        "status": status,
        "reason": reason,

        "production_variant": prod_variant,
        "candidate_variant": cand_variant,

        "production_sum_return": float(
            prod["sum_return"]
        ),
        "candidate_sum_return": float(
            cand["sum_return"]
        ),
        "total_delta": total_delta,

        "common_entries": int(len(paired)),
        "changed_outcomes": int(len(changed)),
        "improved": improved,
        "worsened": worsened,
        "median_changed_delta": median_changed_delta,
        "same_entry_delta": same_entry_delta,
        "ex_best_trade_delta": float(
            ex_best_trade_delta
        ),

        "only_production_entries": int(
            len(only_prod)
        ),
        "only_candidate_entries": int(
            len(only_cand)
        ),

        "weeks_better": weeks_better,
        "weeks_worse": weeks_worse,
        "weeks_equal": weeks_equal,
        "worst_leave_one_week_out": (
            worst_leave_one_week_out
        ),

        "changed_tickers": changed_tickers,
        "worst_leave_one_ticker_out": (
            worst_leave_one_ticker_out
        ),

        "best_trade_share_of_gross_positive_pct": (
            best_trade_share
        ),

        "aggregate_support": aggregate_support,
        "time_support": time_support,
        "trade_support": trade_support,
        "diversity_support": diversity_support,

        "production_snapshot": {
            "c5_hours": float(
                config["sell"]["c5_hours"]
            ),
            "c6_min_gain_percent": float(
                config["sell"]["c6_min_gain_percent"]
            ),
            "c7_max_gain_percent": float(
                config["sell"]["c7_max_gain_percent"]
            ),
            "movement_percent": float(
                config["sell"]["movement_percent"]
            ),
        },

        "source_files": [
            str(comparison_path),
            str(prod_path),
            str(cand_path),
        ],
    }


def load_c6_evidence(
    config: dict,
) -> dict | None:

    comparison_path = C6_DIR / "comparison.csv"

    if not comparison_path.exists():
        return None

    df = pd.read_csv(comparison_path)

    if df.empty:
        return None

    current = float(
        config["sell"]["c6_min_gain_percent"]
    )

    # Parse values from:
    # C6_4_70_PROD, C6_4_85, C6_5_00, ...
    def parse_value(name):
        text = str(name)

        if not text.startswith("C6_"):
            return None

        parts = text.split("_")

        if len(parts) < 3:
            return None

        try:
            return float(
                f"{int(parts[1])}.{parts[2]}"
            )
        except Exception:
            return None

    df["parameter_value"] = [
        parse_value(x)
        for x in df["variant"]
    ]

    df["parameter_value"] = pd.to_numeric(
        df["parameter_value"],
        errors="coerce",
    )

    df["sum_return"] = pd.to_numeric(
        df["sum_return"],
        errors="coerce",
    )

    df = (
        df.dropna(
            subset=[
                "parameter_value",
                "sum_return",
            ]
        )
        .sort_values("parameter_value")
        .reset_index(drop=True)
    )

    if df.empty:
        return None

    prod_rows = df[
        (
            df["parameter_value"]
            - current
        ).abs() < 1e-9
    ]

    if prod_rows.empty:
        return {
            "production_value": current,
            "candidate_value": None,
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": (
                "Current production C6 value is not "
                "present in the latest fine replay grid."
            ),
            "source_files": [
                str(comparison_path),
            ],
        }

    prod = prod_rows.iloc[0]

    production_return = float(
        prod["sum_return"]
    )

    best_return = float(
        df["sum_return"].max()
    )

    # Exact/effectively exact plateau.
    #
    # 1e-6 pp is intentionally very tight: these points
    # represent the same sequential outcome, not merely
    # statistically similar performance.
    plateau_tolerance = 1e-6

    plateau = df[
        (
            best_return
            - df["sum_return"]
        ).abs() <= plateau_tolerance
    ].copy()

    plateau_values = sorted(
        float(x)
        for x in plateau["parameter_value"]
    )

    plateau_min = min(plateau_values)
    plateau_max = max(plateau_values)

    current_in_best_plateau = any(
        abs(x - current) < 1e-9
        for x in plateau_values
    )

    # Find nearest tested values outside the best plateau.
    below = df[
        df["parameter_value"] < plateau_min
    ]

    above = df[
        df["parameter_value"] > plateau_max
    ]

    lower_neighbor = None
    upper_neighbor = None

    if not below.empty:
        r = below.iloc[-1]

        lower_neighbor = {
            "value": float(
                r["parameter_value"]
            ),
            "sum_return": float(
                r["sum_return"]
            ),
            "delta_vs_best": float(
                r["sum_return"]
                - best_return
            ),
        }

    if not above.empty:
        r = above.iloc[0]

        upper_neighbor = {
            "value": float(
                r["parameter_value"]
            ),
            "sum_return": float(
                r["sum_return"]
            ),
            "delta_vs_best": float(
                r["sum_return"]
                - best_return
            ),
        }

    # Raw best is reported for transparency, but when
    # production already lies inside the best plateau
    # we deliberately do not recommend moving within it.
    raw_best_value = float(
        df.loc[
            df["sum_return"].idxmax(),
            "parameter_value",
        ]
    )

    if current_in_best_plateau:
        status = "KEEP_CURRENT"
        candidate_value = None

        reason = (
            f"Production C6={current:.2f}% is already "
            f"inside the best sequential plateau "
            f"{plateau_min:.2f}-{plateau_max:.2f}%. "
            "Moving within the plateau provides no "
            "observed benefit."
        )

        if upper_neighbor is not None:
            reason += (
                f" The next tested value above the "
                f"plateau ({upper_neighbor['value']:.2f}%) "
                f"reduces return by "
                f"{abs(upper_neighbor['delta_vs_best']):.3f} pp."
            )

    else:
        improvement = (
            best_return
            - production_return
        )

        if improvement <= 0.0:
            status = "KEEP_CURRENT"
            candidate_value = None
            reason = (
                "Production is not inside the detected "
                "best plateau, but no tested value "
                "materially improves aggregate return."
            )
        else:
            # Do not automatically promote it. A value
            # outside the plateau needs the deeper
            # trade/week/ticker robustness layer before
            # any production update.
            status = "WATCH"
            candidate_value = raw_best_value
            reason = (
                f"Production is outside the best "
                f"sequential plateau "
                f"{plateau_min:.2f}-{plateau_max:.2f}%. "
                f"Best tested aggregate improvement is "
                f"{improvement:+.3f} pp. "
                "Require trade/week/ticker robustness "
                "before recommending an update."
            )

    grid = []

    for _, r in df.iterrows():
        grid.append({
            "variant": str(r["variant"]),
            "value": float(
                r["parameter_value"]
            ),
            "sum_return": float(
                r["sum_return"]
            ),
            "delta_vs_production": float(
                r["sum_return"]
                - production_return
            ),
        })

    return {
        "production_value": current,
        "candidate_value": candidate_value,
        "status": status,
        "reason": reason,

        "production_sum_return": production_return,
        "best_sum_return": best_return,
        "raw_best_value": raw_best_value,

        "best_plateau_min": plateau_min,
        "best_plateau_max": plateau_max,
        "best_plateau_values": plateau_values,
        "current_in_best_plateau": (
            current_in_best_plateau
        ),

        "lower_neighbor": lower_neighbor,
        "upper_neighbor": upper_neighbor,

        "grid": grid,

        "production_snapshot": {
            "c5_hours": float(
                config["sell"]["c5_hours"]
            ),
            "c6_min_gain_percent": current,
            "c7_max_gain_percent": float(
                config["sell"]["c7_max_gain_percent"]
            ),
            "movement_percent": float(
                config["sell"]["movement_percent"]
            ),
        },

        "source_files": [
            str(comparison_path),
        ],
    }



def load_c7_evidence(
    config: dict,
) -> dict | None:
    """
    Evaluate C7 max-gain candidates from the dedicated sequential
    replay.

    This is recommendation-only. It never modifies production.

    A higher raw return alone is insufficient for promotion because
    C7 currently affects relatively few trades. UPDATE_CANDIDATE
    therefore requires evidence distributed across trades, tickers,
    and weeks and must survive leave-one-out concentration tests.
    """

    c7_dir = (
        DATA_ROOT
        / "analysis_results"
        / "c7_max_gain_sequential"
    )
    comparison_path = c7_dir / "comparison.csv"

    if not comparison_path.exists():
        return None

    try:
        comparison = pd.read_csv(comparison_path)
    except Exception:
        return None

    required = {
        "variant",
        "sum_return",
    }

    if not required.issubset(comparison.columns):
        return None

    sell = config["sell"]
    production_value = float(
        sell.get("c7_max_gain_percent", 5.0)
    )

    def parse_value(name):
        text = str(name)

        if not text.startswith("C7_"):
            return None

        text = text[3:]

        if text.endswith("_PROD"):
            text = text[:-5]

        try:
            return float(text.replace("_", "."))
        except Exception:
            return None

    comparison = comparison.copy()
    comparison["c7_value"] = comparison["variant"].map(
        parse_value
    )
    comparison = comparison[
        comparison["c7_value"].notna()
    ].copy()

    if comparison.empty:
        return None

    comparison["sum_return"] = pd.to_numeric(
        comparison["sum_return"],
        errors="coerce",
    )
    comparison = comparison[
        comparison["sum_return"].notna()
    ].copy()

    if comparison.empty:
        return None

    # Locate the row corresponding to the current production setting.
    current_rows = comparison[
        (
            comparison["c7_value"]
            - production_value
        ).abs() < 1e-9
    ]

    if current_rows.empty:
        return {
            "production_value": production_value,
            "status": "INSUFFICIENT_EVIDENCE",
            "candidate_value": None,
            "reason": (
                "The current production C7 value is not present "
                "in the C7 sequential replay grid."
            ),
            "source_files": [
                str(comparison_path),
            ],
        }

    current_row = current_rows.iloc[0]
    production_sum_return = float(
        current_row["sum_return"]
    )

    # Raw best candidate. In a tie, prefer the value closest to
    # current production to avoid unnecessary parameter movement.
    best_return = float(comparison["sum_return"].max())

    best_rows = comparison[
        (
            comparison["sum_return"]
            - best_return
        ).abs() <= 1e-9
    ].copy()

    best_rows["distance_from_current"] = (
        best_rows["c7_value"] - production_value
    ).abs()

    best_row = best_rows.sort_values(
        [
            "distance_from_current",
            "c7_value",
        ]
    ).iloc[0]

    candidate_value = float(best_row["c7_value"])
    candidate_variant = str(best_row["variant"])
    total_delta = (
        best_return - production_sum_return
    )

    # If production itself is already raw-best, there is no reason
    # to investigate a move.
    if abs(candidate_value - production_value) < 1e-9:
        return {
            "production_value": production_value,
            "candidate_value": None,
            "status": "KEEP_CURRENT",
            "production_sum_return": production_sum_return,
            "best_sum_return": best_return,
            "total_delta": total_delta,
            "reason": (
                "Current production C7 is already in the "
                "best observed replay region."
            ),
            "source_files": [
                str(comparison_path),
            ],
        }

    prod_variant = str(current_row["variant"])

    prod_path = c7_dir / f"trades_{prod_variant}.csv"
    cand_path = c7_dir / f"trades_{candidate_variant}.csv"

    if not prod_path.exists() or not cand_path.exists():
        return {
            "production_value": production_value,
            "candidate_value": candidate_value,
            "candidate_variant": candidate_variant,
            "status": "INSUFFICIENT_EVIDENCE",
            "production_sum_return": production_sum_return,
            "best_sum_return": best_return,
            "total_delta": total_delta,
            "reason": (
                "C7 comparison exists but the production/candidate "
                "trade files required for robustness analysis "
                "are missing."
            ),
            "source_files": [
                str(comparison_path),
                str(prod_path),
                str(cand_path),
            ],
        }

    try:
        prod = pd.read_csv(prod_path)
        cand = pd.read_csv(cand_path)
    except Exception:
        return None

    for frame in (prod, cand):
        frame["buy_time"] = pd.to_datetime(
            frame["buy_time"],
            utc=True,
            errors="coerce",
        )
        frame["sell_time"] = pd.to_datetime(
            frame["sell_time"],
            utc=True,
            errors="coerce",
        )
        frame["return_pct"] = pd.to_numeric(
            frame["return_pct"],
            errors="coerce",
        )

    prod_closed = prod[
        prod["sell_time"].notna()
    ].copy()
    cand_closed = cand[
        cand["sell_time"].notna()
    ].copy()

    merged = prod_closed.merge(
        cand_closed,
        on=["ticker", "buy_time"],
        suffixes=("_prod", "_cand"),
    )

    merged["delta"] = (
        merged["return_pct_cand"]
        - merged["return_pct_prod"]
    )

    eps = 1e-10

    changed = merged[
        (merged["delta"].abs() > eps)
        | (
            merged["sell_time_prod"]
            != merged["sell_time_cand"]
        )
        | (
            merged["sell_reason_prod"]
            != merged["sell_reason_cand"]
        )
    ].copy()

    changed_outcomes = int(len(changed))
    improved = int(
        (changed["delta"] > eps).sum()
    )
    worsened = int(
        (changed["delta"] < -eps).sum()
    )

    same_entry_delta = float(
        changed["delta"].sum()
    )

    median_changed_delta = (
        float(changed["delta"].median())
        if changed_outcomes
        else 0.0
    )

    # --------------------------------------------------------
    # Sequential path differences
    # --------------------------------------------------------
    prod_keys = set(
        zip(prod["ticker"], prod["buy_time"])
    )
    cand_keys = set(
        zip(cand["ticker"], cand["buy_time"])
    )

    only_prod_keys = prod_keys - cand_keys
    only_cand_keys = cand_keys - prod_keys

    only_prod = prod[
        prod.apply(
            lambda r: (
                r["ticker"],
                r["buy_time"],
            ) in only_prod_keys,
            axis=1,
        )
    ].copy()

    only_cand = cand[
        cand.apply(
            lambda r: (
                r["ticker"],
                r["buy_time"],
            ) in only_cand_keys,
            axis=1,
        )
    ].copy()

    prod_only_return = float(
        only_prod["return_pct"].fillna(0).sum()
    )
    cand_only_return = float(
        only_cand["return_pct"].fillna(0).sum()
    )

    path_delta = (
        cand_only_return
        - prod_only_return
    )

    reconciled_delta = (
        same_entry_delta
        + path_delta
    )

    # --------------------------------------------------------
    # Weekly robustness
    # --------------------------------------------------------
    if len(merged):
        merged["week"] = (
            merged["buy_time"].dt.normalize()
            - pd.to_timedelta(
                merged["buy_time"].dt.weekday,
                unit="D",
            )
        ).dt.strftime("%Y-%m-%d")

        weekly = (
            merged.groupby(
                "week",
                as_index=False,
            )
            .agg(
                prod=("return_pct_prod", "sum"),
                cand=("return_pct_cand", "sum"),
            )
        )

        weekly["delta"] = (
            weekly["cand"]
            - weekly["prod"]
        )
    else:
        weekly = pd.DataFrame(
            columns=[
                "week",
                "prod",
                "cand",
                "delta",
            ]
        )

    weeks_better = int(
        (weekly["delta"] > eps).sum()
    )
    weeks_worse = int(
        (weekly["delta"] < -eps).sum()
    )
    weeks_equal = int(
        len(weekly)
        - weeks_better
        - weeks_worse
    )

    total_same = float(
        merged["delta"].sum()
    )

    if len(weekly):
        worst_leave_one_week_out = min(
            total_same - float(x)
            for x in weekly["delta"]
        )
    else:
        worst_leave_one_week_out = 0.0

    # --------------------------------------------------------
    # Ticker robustness
    # --------------------------------------------------------
    if len(merged):
        ticker_delta = (
            merged.groupby(
                "ticker",
                as_index=False,
            )
            .agg(
                delta=("delta", "sum")
            )
        )

        ticker_changed = ticker_delta[
            ticker_delta["delta"].abs() > eps
        ].copy()
    else:
        ticker_changed = pd.DataFrame(
            columns=["ticker", "delta"]
        )

    changed_tickers = int(
        len(ticker_changed)
    )

    if changed_tickers:
        worst_leave_one_ticker_out = min(
            total_same - float(x)
            for x in ticker_changed["delta"]
        )
    else:
        worst_leave_one_ticker_out = 0.0

    # --------------------------------------------------------
    # Best-trade concentration
    # --------------------------------------------------------
    positive = changed.loc[
        changed["delta"] > eps,
        "delta",
    ]

    gross_positive = float(
        positive.sum()
    )

    if len(positive) and gross_positive > 0:
        largest_positive = float(
            positive.max()
        )
        best_trade_share = (
            largest_positive
            / gross_positive
            * 100.0
        )
        ex_best_trade = (
            same_entry_delta
            - largest_positive
        )
    else:
        best_trade_share = 0.0
        ex_best_trade = 0.0

    # --------------------------------------------------------
    # Conservative promotion gates
    # --------------------------------------------------------
    #
    # WATCH:
    #   raw candidate is better and worth monitoring.
    #
    # UPDATE_CANDIDATE:
    #   requires substantially broader independent evidence.
    #
    # These gates intentionally prevent a handful of exceptional
    # winners from moving production automatically.
    #
    promotion_gates = {
        "positive_total_delta":
            total_delta > eps,

        "minimum_changed_outcomes":
            changed_outcomes >= 8,

        "more_improved_than_worsened":
            improved > worsened,

        "minimum_changed_tickers":
            changed_tickers >= 5,

        "minimum_better_weeks":
            weeks_better >= 3,

        "weekly_balance":
            weeks_better > weeks_worse,

        "positive_leave_one_week_out":
            worst_leave_one_week_out > eps,

        "positive_leave_one_ticker_out":
            worst_leave_one_ticker_out > eps,

        "positive_ex_best_trade":
            ex_best_trade > eps,

        # Prevent one positive trade from accounting for almost
        # all gross improvement.
        "positive_trade_concentration":
            best_trade_share <= 60.0,
    }

    all_promotion_gates = all(
        promotion_gates.values()
    )

    if total_delta <= eps:
        status = "KEEP_CURRENT"
        final_candidate = None
        reason = (
            "No C7 candidate improves total sequential replay "
            "return versus the current production value."
        )

    elif all_promotion_gates:
        status = "UPDATE_CANDIDATE"
        final_candidate = candidate_value
        reason = (
            f"C7 {candidate_value:.2f}% improves sequential "
            f"return by {total_delta:+.3f} pp and passes the "
            "trade, ticker, week, leave-one-out, and "
            "concentration robustness gates."
        )

    else:
        status = "WATCH"
        final_candidate = candidate_value

        failed = [
            name
            for name, passed
            in promotion_gates.items()
            if not passed
        ]

        reason = (
            f"C7 {candidate_value:.2f}% improves raw sequential "
            f"return by {total_delta:+.3f} pp, but evidence is "
            "not yet broad enough for a production change. "
            "Failed promotion gates: "
            + ", ".join(failed)
            + "."
        )

    return {
        "production_value": production_value,
        "candidate_value": final_candidate,
        "candidate_variant": candidate_variant,
        "status": status,

        "production_sum_return":
            production_sum_return,
        "best_sum_return":
            best_return,
        "total_delta":
            total_delta,

        "common_entries":
            int(len(merged)),
        "changed_outcomes":
            changed_outcomes,
        "improved":
            improved,
        "worsened":
            worsened,
        "median_changed_delta":
            median_changed_delta,
        "same_entry_delta":
            same_entry_delta,

        "production_only_entries":
            int(len(only_prod_keys)),
        "candidate_only_entries":
            int(len(only_cand_keys)),
        "production_only_return":
            prod_only_return,
        "candidate_only_return":
            cand_only_return,
        "path_delta":
            path_delta,
        "reconciled_delta":
            reconciled_delta,

        "weeks_better":
            weeks_better,
        "weeks_worse":
            weeks_worse,
        "weeks_equal":
            weeks_equal,
        "worst_leave_one_week_out":
            worst_leave_one_week_out,

        "changed_tickers":
            changed_tickers,
        "worst_leave_one_ticker_out":
            worst_leave_one_ticker_out,

        "best_trade_share_of_gross_positive_pct":
            best_trade_share,
        "same_entry_delta_ex_best_trade":
            ex_best_trade,

        "promotion_gates":
            promotion_gates,

        "reason":
            reason,

        "source_files": [
            str(comparison_path),
            str(prod_path),
            str(cand_path),
        ],
    }



def registry(
    config: dict,
    c2x: dict | None,
    c45: dict | None,
    c5: dict | None,
    c6: dict | None,
    c7: dict | None,
) -> list[Parameter]:

    buy = config["buy"]
    sell = config["sell"]

    movement_status: Status = "ANALYZE"
    movement_candidate = None
    movement_notes = (
        "Shared by C4 and C5. Sequential evidence "
        "not yet available to this report."
    )

    if c45 is not None:
        movement_status = c45["status"]

        if c45.get("candidate_value") is not None:
            movement_candidate = float(
                c45["candidate_value"]
            )

        movement_notes = c45["reason"]

    c2x_note = (
        "Structural overextension guard. "
        "Validated by sequential replay."
    )

    if c2x is not None:
        c2x_note += (
            f" Weekly spike-collapse status: "
            f"{c2x.get('status', 'UNKNOWN')}."
        )

    return [
        Parameter(
            name="C2X maximum CloseB",
            rule="C2X",
            config_key="buy.c2x_max_closeb_percent",
            current_value=float(
                buy["c2x_max_closeb_percent"]
            ),
            status="FROZEN",
            structural=True,
            notes=c2x_note,
        ),

        Parameter(
            name="C2X peak age",
            rule="C2X",
            config_key="buy.c2x_max_peak_age_minutes",
            current_value=float(
                buy["c2x_max_peak_age_minutes"]
            ),
            status="FROZEN",
            structural=True,
            notes=(
                "Peak-age dependency is non-monotonic; "
                "keep fixed pending more data."
            ),
        ),

        Parameter(
            name="C2X peak drawdown",
            rule="C2X",
            config_key="buy.c2x_max_peak_drawdown_percent",
            current_value=float(
                buy[
                    "c2x_max_peak_drawdown_percent"
                ]
            ),
            status="FROZEN",
            structural=True,
            notes=(
                "Validated using independent events, "
                "matched controls, leave-one-week-out "
                "and sequential replay."
            ),
        ),

        Parameter(
            name="C2X spike-collapse Rise/DD",
            rule="C2X",
            config_key=(
                "buy.c2x_spike_collapse_"
                "min_rise_percent / "
                "buy.c2x_spike_collapse_"
                "min_drawdown_percent"
            ),
            current_value=float(
                buy[
                    "c2x_spike_collapse_min_rise_percent"
                ]
            ),
            status=(
                c2x.get(
                    "status",
                    "INSUFFICIENT_EVIDENCE",
                )
                if c2x is not None
                else "INSUFFICIENT_EVIDENCE"
            ),
            structural=False,
            notes=(
                (
                    f"60-minute spike-collapse guard: "
                    f"Rise >= "
                    f"{buy['c2x_spike_collapse_min_rise_percent']:.1f}% "
                    f"AND drawdown >= "
                    f"{buy['c2x_spike_collapse_min_drawdown_percent']:.1f}%. "
                    f"{c2x.get('reason', '')}"
                )
                if c2x is not None
                else (
                    "60-minute spike-collapse guard. "
                    "Awaiting weekly evidence."
                )
            ),
            independent_events=(
                int(
                    c2x.get("production", {}).get(
                        "events", 0
                    )
                )
                if c2x is not None
                else 0
            ),
            ticker_days=(
                int(
                    c2x.get("production", {}).get(
                        "days", 0
                    )
                )
                if c2x is not None
                else 0
            ),
            evidence=c2x,
        ),

        Parameter(
            name="C4/C5 movement threshold",
            rule="C4+C5",
            config_key="sell.movement_percent",
            current_value=float(
                sell["movement_percent"]
            ),
            status=movement_status,
            structural=False,
            notes=movement_notes,
            affected_weeks=(
                int(
                    c45["weeks_better"]
                    + c45["weeks_worse"]
                )
                if c45 is not None
                else 0
            ),
            candidate_value=movement_candidate,
            evidence=c45,
        ),

        Parameter(
            name="C5 trading-time horizon",
            rule="C5",
            config_key="sell.c5_hours",
            current_value=float(
                sell["c5_hours"]
            ),
            status=(
                c5["status"]
                if c5 is not None
                else "ANALYZE"
            ),
            structural=False,
            notes=(
                c5["reason"]
                if c5 is not None
                else (
                    "Trailing trading-time horizon; "
                    "closed periods do not count."
                )
            ),
            affected_weeks=(
                int(
                    c5["weeks_better"]
                    + c5["weeks_worse"]
                )
                if c5 is not None
                and "weeks_better" in c5
                else 0
            ),
            candidate_value=(
                float(c5["candidate_value"])
                if c5 is not None
                and c5.get("candidate_value") is not None
                else None
            ),
            evidence=c5,
        ),

        Parameter(
            name="C6 minimum gain",
            rule="C6",
            config_key="sell.c6_min_gain_percent",
            current_value=float(
                sell["c6_min_gain_percent"]
            ),
            status=(
                c6["status"]
                if c6 is not None
                else "ANALYZE"
            ),
            structural=False,
            notes=(
                c6["reason"]
                if c6 is not None
                else (
                    "Weak-position EOD threshold. "
                    "Conditional on C4/C5 being false."
                )
            ),
            candidate_value=(
                float(c6["candidate_value"])
                if c6 is not None
                and c6.get("candidate_value") is not None
                else None
            ),
            evidence=c6,
        ),

        Parameter(
            name="C7 maximum gain",
            rule="C7",
            config_key="sell.c7_max_gain_percent",
            current_value=float(
                sell["c7_max_gain_percent"]
            ),
            status=(
                c7["status"]
                if c7 is not None
                else "ANALYZE"
            ),
            structural=False,
            notes=(
                "Strong-profit EOD threshold. "
                "Conditional on C4/C5 being false."
            ),
            candidate_value=(
                c7.get("candidate_value")
                if c7 is not None
                else None
            ),
            evidence=c7,
        ),
    ]


def main():

    config = load_config()

    # C2X evidence is handled independently. The four
    # sequential sell-rule replay families below must pass
    # the joint provenance gate before their evidence may
    # influence recommendations.
    c2x = load_c2x_evidence()

    provenance = validate_replay_provenance(
        config
    )

    if provenance["valid"]:
        c45 = load_c45_evidence(config)
        c5 = load_c5_evidence(config)
        c6 = load_c6_evidence(config)
        c7 = load_c7_evidence(config)

    else:
        blocked_status = provenance["status"]
        blocked_reason = (
            "Sequential replay evidence blocked by "
            "provenance validation: "
            + provenance["reason"]
        )

        sell = config["sell"]

        c45 = {
            "production_value": float(
                sell["movement_percent"]
            ),
            "candidate_value": None,
            "status": blocked_status,
            "reason": blocked_reason,
        }

        c5 = {
            "production_value": float(
                sell["c5_hours"]
            ),
            "candidate_value": None,
            "status": blocked_status,
            "reason": blocked_reason,
        }

        c6 = {
            "production_value": float(
                sell["c6_min_gain_percent"]
            ),
            "candidate_value": None,
            "status": blocked_status,
            "reason": blocked_reason,
        }

        c7 = {
            "production_value": float(
                sell["c7_max_gain_percent"]
            ),
            "candidate_value": None,
            "status": blocked_status,
            "reason": blocked_reason,
        }

    params = registry(
        config,
        c2x,
        c45,
        c5,
        c6,
        c7,
    )

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = {
        "mode": "READ_ONLY",
        "config_file": str(CONFIG),
        "production_modified": False,
        "provenance": provenance,
        "c2x_evaluation": c2x,
        "c45_evaluation": c45,
        "c5_evaluation": c5,
        "c6_evaluation": c6,
        "c7_evaluation": c7,
        "parameters": [
            asdict(p)
            for p in params
        ],
    }

    OUTPUT.write_text(
        json.dumps(
            report,
            indent=2,
        )
        + "\n"
    )

    lines = []

    lines.append(
        "WEEKLY PARAMETER OPTIMIZER (READ ONLY)"
    )
    lines.append("")

    lines.append(
        f"Replay provenance: "
        f"{provenance['status']}"
    )

    if provenance.get("dataset_fingerprint"):
        fp = provenance["dataset_fingerprint"]

        lines.append(
            "Replay dataset: "
            f"{fp['measurement_count']:,} observations, "
            f"{fp['ticker_count']} tickers, "
            f"through {fp['last_measurement']}"
        )

    if not provenance["valid"]:
        lines.append(
            f"Replay evidence blocked: "
            f"{provenance['reason']}"
        )

    lines.append("")

    lines.append(
        f"{'RULE':<8}"
        f"{'PARAMETER':<30}"
        f"{'CURRENT':>10}  "
        f"{'CANDIDATE':>10}  "
        f"{'STATUS':<22}"
    )

    lines.append("-" * 86)

    for p in params:
        candidate = (
            "-"
            if p.candidate_value is None
            else f"{p.candidate_value:.2f}"
        )

        lines.append(
            f"{p.rule:<8}"
            f"{p.name:<30}"
            f"{p.current_value:>10.2f}  "
            f"{candidate:>10}  "
            f"{p.status:<22}"
        )

    if c2x is not None:
        lines.append("")
        lines.append("C2X SPIKE-COLLAPSE")
        lines.append(
            f"Status: {c2x.get('status')}"
        )
        lines.append(
            f"Reason: {c2x.get('reason')}"
        )

    if c45 is not None:
        lines.append("")
        lines.append("C4/C5 MOVEMENT")
        lines.append(
            f"Production: "
            f"{c45['production_value']:.2f}%"
        )

        if c45.get("candidate_value") is not None:
            lines.append(
                f"Watch candidate: "
                f"{c45['candidate_value']:.2f}%"
            )

        lines.append(
            f"Status: {c45['status']}"
        )

        if "total_delta" in c45:
            lines.append(
                f"Sequential delta: "
                f"{c45['total_delta']:+.3f} pp"
            )
            lines.append(
                f"Weeks better/worse: "
                f"{c45['weeks_better']}/"
                f"{c45['weeks_worse']}"
            )
            lines.append(
                f"Ex-best-week delta: "
                f"{c45['delta_without_best_week']:+.3f} pp"
            )
            lines.append(
                f"Same-entry delta: "
                f"{c45['same_entry_delta']:+.3f} pp"
            )
            lines.append(
                f"Improved/worsened changed entries: "
                f"{c45['improved']}/"
                f"{c45['worsened']}"
            )
            lines.append(
                f"Median changed-entry delta: "
                f"{c45['median_changed_delta']:+.3f} pp"
            )
            lines.append(
                f"Ex-best-trade delta: "
                f"{c45['ex_best_trade_delta']:+.3f} pp"
            )

        lines.append(
            f"Reason: {c45['reason']}"
        )

    if c5 is not None:
        lines.append("")
        lines.append("C5 TRADING-TIME HORIZON")
        lines.append(
            f"Production: "
            f"{c5['production_value']:.2f} h"
        )

        if c5.get("candidate_value") is not None:
            lines.append(
                f"Best raw candidate: "
                f"{c5['candidate_value']:.2f} h"
            )

        lines.append(
            f"Status: {c5['status']}"
        )

        if "total_delta" in c5:
            lines.append(
                f"Sequential delta: "
                f"{c5['total_delta']:+.3f} pp"
            )
            lines.append(
                f"Changed same-entry trades: "
                f"{c5['changed_outcomes']}"
            )
            lines.append(
                f"Improved/worsened: "
                f"{c5['improved']}/"
                f"{c5['worsened']}"
            )
            lines.append(
                f"Weeks better/worse/equal: "
                f"{c5['weeks_better']}/"
                f"{c5['weeks_worse']}/"
                f"{c5['weeks_equal']}"
            )
            lines.append(
                f"Worst leave-one-week-out: "
                f"{c5['worst_leave_one_week_out']:+.3f} pp"
            )
            lines.append(
                f"Changed tickers: "
                f"{c5['changed_tickers']}"
            )
            lines.append(
                f"Worst leave-one-ticker-out: "
                f"{c5['worst_leave_one_ticker_out']:+.3f} pp"
            )
            lines.append(
                f"Largest positive trade share: "
                f"{c5['best_trade_share_of_gross_positive_pct']:.2f}%"
            )

        lines.append(
            f"Reason: {c5['reason']}"
        )

    if c6 is not None:
        lines.append("")
        lines.append("C6 MINIMUM GAIN")
        lines.append(
            f"Production: "
            f"{c6['production_value']:.2f}%"
        )
        lines.append(
            f"Status: {c6['status']}"
        )

        if "best_plateau_min" in c6:
            lines.append(
                f"Best plateau: "
                f"{c6['best_plateau_min']:.2f}% - "
                f"{c6['best_plateau_max']:.2f}%"
            )
            lines.append(
                f"Production return: "
                f"{c6['production_sum_return']:+.3f}%"
            )
            lines.append(
                f"Best return: "
                f"{c6['best_sum_return']:+.3f}%"
            )
            lines.append(
                f"Current in best plateau: "
                f"{c6['current_in_best_plateau']}"
            )

            upper = c6.get("upper_neighbor")

            if upper is not None:
                lines.append(
                    f"Next value above plateau: "
                    f"{upper['value']:.2f}% "
                    f"({upper['delta_vs_best']:+.3f} pp)"
                )

            lower = c6.get("lower_neighbor")

            if lower is not None:
                lines.append(
                    f"Next value below plateau: "
                    f"{lower['value']:.2f}% "
                    f"({lower['delta_vs_best']:+.3f} pp)"
                )

        lines.append(
            f"Reason: {c6['reason']}"
        )

    if c7 is not None:
        lines.append("")
        lines.append("C7 MAXIMUM GAIN")
        lines.append(
            f"Production: "
            f"{c7['production_value']:.2f}%"
        )
        lines.append(
            f"Status: {c7['status']}"
        )

        candidate = c7.get("candidate_value")

        if candidate is not None:
            lines.append(
                f"Candidate: "
                f"{candidate:.2f}%"
            )

        if "total_delta" in c7:
            lines.append(
                f"Production return: "
                f"{c7['production_sum_return']:+.3f}%"
            )
            lines.append(
                f"Best replay return: "
                f"{c7['best_sum_return']:+.3f}%"
            )
            lines.append(
                f"Sequential delta: "
                f"{c7['total_delta']:+.3f} pp"
            )
            lines.append(
                f"Changed same-entry trades: "
                f"{c7['changed_outcomes']}"
            )
            lines.append(
                f"Improved/worsened: "
                f"{c7['improved']}/"
                f"{c7['worsened']}"
            )
            lines.append(
                f"Median changed delta: "
                f"{c7['median_changed_delta']:+.3f} pp"
            )
            lines.append(
                f"Same-entry delta: "
                f"{c7['same_entry_delta']:+.3f} pp"
            )
            lines.append(
                f"Path delta: "
                f"{c7['path_delta']:+.3f} pp"
            )
            lines.append(
                f"Reconciled delta: "
                f"{c7['reconciled_delta']:+.3f} pp"
            )
            lines.append(
                f"Weeks better/worse/equal: "
                f"{c7['weeks_better']}/"
                f"{c7['weeks_worse']}/"
                f"{c7['weeks_equal']}"
            )
            lines.append(
                f"Worst leave-one-week-out: "
                f"{c7['worst_leave_one_week_out']:+.3f} pp"
            )
            lines.append(
                f"Changed tickers: "
                f"{c7['changed_tickers']}"
            )
            lines.append(
                f"Worst leave-one-ticker-out: "
                f"{c7['worst_leave_one_ticker_out']:+.3f} pp"
            )
            lines.append(
                f"Largest positive trade share: "
                f"{c7['best_trade_share_of_gross_positive_pct']:.2f}%"
            )
            lines.append(
                f"Same-entry delta ex-best-trade: "
                f"{c7['same_entry_delta_ex_best_trade']:+.3f} pp"
            )
            lines.append(
                f"Production/candidate-only entries: "
                f"{c7['production_only_entries']}/"
                f"{c7['candidate_only_entries']}"
            )

            failed = [
                key
                for key, passed
                in c7.get(
                    "promotion_gates",
                    {},
                ).items()
                if not passed
            ]

            if failed:
                lines.append(
                    "Failed promotion gates: "
                    + ", ".join(failed)
                )

        lines.append(
            f"Reason: {c7['reason']}"
        )

    lines.append("")
    lines.append(
        "No production configuration was modified."
    )

    text = "\n".join(lines) + "\n"

    TEXT_OUTPUT.write_text(text)

    print()
    print(text, end="")
    print()
    print(f"JSON report: {OUTPUT}")
    print(f"Text report: {TEXT_OUTPUT}")


if __name__ == "__main__":
    main()
