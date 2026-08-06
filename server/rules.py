from __future__ import annotations

import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Rule:
    name: str
    system: str
    field: str
    operator: str
    value: Any = None
    severity: str = "warning"
    message: str = "Rule condition not satisfied."


COMPARATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def load_rules(path: str) -> list[Rule]:
    with Path(path).open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    return [Rule(**item) for item in document.get("rules", [])]


def evaluate_rule(rule: Rule, measurements: dict[str, Any]) -> tuple[bool, Any]:
    if rule.operator == "exists":
        actual = measurements.get(rule.field)
        return rule.field in measurements and actual is not None, actual
    if rule.operator == "not_exists":
        actual = measurements.get(rule.field)
        return rule.field not in measurements or actual is None, actual
    actual = measurements.get(rule.field)
    if actual is None:
        return False, None
    comparator = COMPARATORS.get(rule.operator)
    if comparator is None:
        raise ValueError(f"Unsupported operator: {rule.operator}")
    try:
        return bool(comparator(actual, rule.value)), actual
    except TypeError:
        return False, actual
