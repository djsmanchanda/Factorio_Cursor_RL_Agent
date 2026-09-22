# Path: training/laya_comparison.py
# Purpose: Gate Laya replacement evidence against a preserved incumbent under matched starting conditions.

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Mapping, Sequence


class LayaComparisonError(ValueError):
    """A benchmark cannot establish a matched, independently executed comparison."""


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) or value == "sha256:" + "0" * 64:
        raise LayaComparisonError(f"{name} requires a non-placeholder SHA-256 digest")
    return value


def conditions_hash(conditions: Mapping[str, Any]) -> str:
    """Pin starting difficulty as well as seed; old easier saves are incomparable."""
    required = {
        "initial_state_hash", "starting_inventory", "research", "objective",
        "budgets", "factorio_version", "mod_set_hash", "bootstrap_profile",
    }
    if not isinstance(conditions, Mapping) or required - conditions.keys():
        raise LayaComparisonError("starting conditions are incomplete")
    for key in ("initial_state_hash", "mod_set_hash"):
        _digest(conditions[key], key)
    for key in ("starting_inventory", "objective", "budgets"):
        if not isinstance(conditions[key], Mapping):
            raise LayaComparisonError(f"{key} must be an explicit object")
    if not conditions["objective"] or not conditions["budgets"]:
        raise LayaComparisonError("objective and budgets cannot be empty")
    if not isinstance(conditions["research"], list):
        raise LayaComparisonError("research must explicitly list the initial technologies")
    if not all(isinstance(conditions[key], str) and conditions[key] for key in ("factorio_version", "bootstrap_profile")):
        raise LayaComparisonError("runtime version and bootstrap profile are required")
    try:
        return _hash(dict(conditions))
    except (ValueError, TypeError) as error:
        raise LayaComparisonError(f"starting conditions are not canonical JSON: {error}") from error


def _metric(record: Mapping, name: str) -> float:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        raise LayaComparisonError("execution metrics must be an object")
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise LayaComparisonError(f"{name} must be a finite nonnegative measured number")
    return float(value)


def compare_matched_runs(
    suite: Mapping[str, Any],
    baseline: Sequence[Mapping[str, Any]],
    challenger: Sequence[Mapping[str, Any]],
    *,
    minimum_seeds_per_family: int = 3,
) -> dict[str, Any]:
    """Produce review evidence, never switch controllers or grant production authority.

    The frozen suite prevents silently dropping failed pairs. Each row represents
    an independently executed held-out episode. Strict per-case non-regression
    is intentionally conservative; statistical confidence needs larger cohorts.
    """
    if minimum_seeds_per_family < 3:
        raise LayaComparisonError("at least three independent seeds per family are required")
    if not isinstance(suite, Mapping) or suite.get("version") != "1.0.0" or not suite.get("suite_id") or not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise LayaComparisonError("a versioned frozen benchmark suite is required")
    expected, seeds = {}, set()
    for case in suite["cases"]:
        if not isinstance(case, Mapping):
            raise LayaComparisonError("benchmark cases must be objects")
        identifier, family, seed = case.get("case_id"), case.get("family"), case.get("seed")
        if not isinstance(identifier, str) or not identifier or identifier in expected:
            raise LayaComparisonError("benchmark case IDs must be unique")
        if not isinstance(family, str) or not family or type(seed) is not int or seed < 0:
            raise LayaComparisonError("benchmark family and nonnegative seed are required")
        if (family, seed) in seeds or case.get("split") not in {"held_out", "test"}:
            raise LayaComparisonError("benchmark needs independent held-out seeds")
        seeds.add((family, seed))
        expected[identifier] = {**case, "conditions_hash": conditions_hash(case.get("conditions"))}

    execution_ids, artifact_hashes = set(), set()

    def index(rows, kind):
        if not isinstance(rows, (list, tuple)):
            raise LayaComparisonError("execution records must be an array")
        result, controllers = {}, set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise LayaComparisonError("execution records must be objects")
            identifier = row.get("case_id")
            if identifier not in expected or identifier in result:
                raise LayaComparisonError("unexpected or duplicate execution case")
            if row.get("controller_kind") != kind or row.get("execution_authority") != "primary":
                raise LayaComparisonError("shadow or training-selector results cannot establish workflow replacement")
            if row.get("evidence_level") != "observed_live":
                raise LayaComparisonError("replacement requires observed live execution evidence")
            controllers.add(_digest(row.get("controller_hash"), "controller_hash"))
            artifact_hash = _digest(row.get("artifact_hash"), "artifact_hash")
            execution_id = row.get("execution_id")
            if not isinstance(execution_id, str) or not execution_id or execution_id in execution_ids or artifact_hash in artifact_hashes:
                raise LayaComparisonError("execution identities and artifacts must be independent and unique")
            execution_ids.add(execution_id)
            artifact_hashes.add(artifact_hash)
            if row.get("seed") != expected[identifier]["seed"] or row.get("family") != expected[identifier]["family"]:
                raise LayaComparisonError("execution seed/family differs from frozen benchmark case")
            runtime = row.get("runtime")
            if not isinstance(runtime, Mapping) or runtime.get("kind") != "disposable_training":
                raise LayaComparisonError("comparison requires explicit disposable runtime provenance")
            if not str(runtime.get("surface", "")).startswith("training/") or not str(runtime.get("force", "")).startswith("training-"):
                raise LayaComparisonError("comparison runtime identity is outside disposable scope")
            for field in ("worker_id", "save_id"):
                if not isinstance(runtime.get(field), str) or not runtime[field]:
                    raise LayaComparisonError(f"comparison runtime requires {field}")
            for field in ("initial_state_hash", "mod_set_hash", "factorio_version"):
                if runtime.get(field) != expected[identifier]["conditions"][field]:
                    raise LayaComparisonError(f"runtime provenance mismatch: {field}")
            if row.get("conditions_hash") != expected[identifier]["conditions_hash"]:
                raise LayaComparisonError(f"starting conditions differ: {identifier}")
            if not isinstance(row.get("metrics"), Mapping) or type(row["metrics"].get("objective_completed")) is not bool:
                raise LayaComparisonError("objective completion must be explicitly measured")
            for name in ("safety_violations", "elapsed_ticks", "material_cost", "sustained_rate", "escalations"):
                _metric(row, name)
            result[identifier] = row
        if set(result) != set(expected):
            raise LayaComparisonError("every frozen benchmark case requires an execution result")
        if len(controllers) != 1:
            raise LayaComparisonError("a cohort must pin one controller artifact")
        return result

    incumbent = index(baseline, "deterministic_workflow")
    candidate = index(challenger, "laya")
    reasons, improved = [], []
    families = Counter(case["family"] for case in expected.values())
    for family, count in families.items():
        if count < minimum_seeds_per_family:
            reasons.append(f"insufficient_independent_seeds:{family}:{count}")
    for identifier in expected:
        old, new = incumbent[identifier], candidate[identifier]
        if _metric(old, "safety_violations") or _metric(new, "safety_violations"):
            reasons.append(f"safety_violation:{identifier}")
        if not new["metrics"]["objective_completed"]:
            reasons.append(f"objective_incomplete:{identifier}")
            continue
        if not old["metrics"]["objective_completed"]:
            improved.append(identifier)
            continue
        old_costs = [_metric(old, key) for key in ("elapsed_ticks", "material_cost", "escalations")]
        new_costs = [_metric(new, key) for key in ("elapsed_ticks", "material_cost", "escalations")]
        old_rate, new_rate = _metric(old, "sustained_rate"), _metric(new, "sustained_rate")
        if any(new_value > old_value for new_value, old_value in zip(new_costs, old_costs)) or new_rate < old_rate:
            reasons.append(f"non_regression_failed:{identifier}")
        elif any(new_value < old_value for new_value, old_value in zip(new_costs, old_costs)) or new_rate > old_rate:
            improved.append(identifier)
    if not improved:
        reasons.append("no_measured_improvement")
    return {
        "version": "1.0.0", "suite_id": suite["suite_id"], "suite_hash": _hash(suite),
        "baseline_evidence_hash": _hash(list(baseline)), "challenger_evidence_hash": _hash(list(challenger)),
        "status": "candidate_for_review" if not reasons else "blocked",
        "cases": len(expected), "families": dict(families), "improved_cases": improved,
        "blocking_reasons": reasons, "eligible_for_replacement_review": not reasons,
        "eligible_for_real_nauvis": False, "incumbent_retained": True,
        "limitation": "Evidence gate only; small cohorts do not establish statistical confidence or authorize a controller switch.",
    }
