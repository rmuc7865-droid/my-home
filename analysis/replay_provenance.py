from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib


SCHEMA_VERSION = 1


def _get(mapping: dict, key: str, default=None):
    if not isinstance(mapping, dict):
        return default
    return mapping.get(key, default)


def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _iso(value):
    if value is None:
        return None

    try:
        return value.isoformat()
    except Exception:
        return str(value)


def build_replay_metadata(
    *,
    replay_type: str,
    experiment_parameter: str,
    variants: dict,
    production_value: float,
    cfg: dict,
    prices,
    db_path: Path,
    config_path: Path,
    instruments_path: Path,
    replay_script_path: Path,
) -> dict:

    buy = cfg.get("buy", {}) or {}
    sell = cfg.get("sell", {}) or {}

    measurement_count = int(len(prices))

    if measurement_count:
        first_measurement = _iso(
            prices["timestamp"].min()
        )
        last_measurement = _iso(
            prices["timestamp"].max()
        )
        ticker_count = int(
            prices["ticker"].nunique()
        )
    else:
        first_measurement = None
        last_measurement = None
        ticker_count = 0

    tested_values = [
        float(v)
        for v in variants.values()
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "replay_type": replay_type,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "database": {
            "path": str(db_path),
            "measurement_count":
                measurement_count,
            "ticker_count":
                ticker_count,
            "first_measurement":
                first_measurement,
            "last_measurement":
                last_measurement,
        },

        "production_config": {
            "movement_percent": float(
                _get(
                    sell,
                    "movement_percent",
                    2.0,
                )
            ),
            "c5_hours": float(
                _get(
                    sell,
                    "c5_hours",
                    12.0,
                )
            ),
            "c6_min_gain_percent": float(
                _get(
                    sell,
                    "c6_min_gain_percent",
                    4.7,
                )
            ),
            "c7_max_gain_percent": float(
                _get(
                    sell,
                    "c7_max_gain_percent",
                    5.0,
                )
            ),
        },

        "c2x_config": {
            "enabled": bool(
                _get(
                    buy,
                    "c2x_enabled",
                    False,
                )
            ),
            "window_minutes": float(
                _get(
                    buy,
                    "c2x_window_minutes",
                    30,
                )
            ),
            "soft_lowrise_percent": float(
                _get(
                    buy,
                    "c2x_soft_lowrise_percent",
                    6.0,
                )
            ),
            "hard_lowrise_percent": float(
                _get(
                    buy,
                    "c2x_hard_lowrise_percent",
                    8.0,
                )
            ),
            "acceleration_ratio_threshold": float(
                _get(
                    buy,
                    "c2x_acceleration_ratio_threshold",
                    4.0,
                )
            ),
            "baseline_days": int(
                _get(
                    buy,
                    "c2x_baseline_days",
                    7,
                )
            ),
            "max_closeb_percent": float(
                _get(
                    buy,
                    "c2x_max_closeb_percent",
                    8.0,
                )
            ),
            "max_peak_age_minutes": float(
                _get(
                    buy,
                    "c2x_max_peak_age_minutes",
                    30,
                )
            ),
            "max_peak_drawdown_percent": float(
                _get(
                    buy,
                    "c2x_max_peak_drawdown_percent",
                    2.5,
                )
            ),
            "spike_collapse_enabled": bool(
                _get(
                    buy,
                    "c2x_spike_collapse_enabled",
                    False,
                )
            ),
            "spike_collapse_window_minutes": float(
                _get(
                    buy,
                    "c2x_spike_collapse_window_minutes",
                    60,
                )
            ),
            "spike_collapse_min_rise_percent": float(
                _get(
                    buy,
                    "c2x_spike_collapse_min_rise_percent",
                    25.0,
                )
            ),
            "spike_collapse_min_drawdown_percent": float(
                _get(
                    buy,
                    "c2x_spike_collapse_min_drawdown_percent",
                    15.0,
                )
            ),
        },

        "experiment": {
            "parameter":
                experiment_parameter,
            "tested_values":
                tested_values,
            "production_value":
                float(production_value),
        },

        "artifacts": {
            "config_sha256":
                _sha256(config_path),
            "instruments_sha256":
                _sha256(instruments_path),
            "replay_script_sha256":
                _sha256(replay_script_path),
        },
    }


def write_replay_metadata(
    output_dir: Path,
    metadata: dict,
) -> Path:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = output_dir / "replay_metadata.json"

    path.write_text(
        json.dumps(
            metadata,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return path
