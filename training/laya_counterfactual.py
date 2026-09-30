# Path: training/laya_counterfactual.py | Purpose: Attribute replay outcomes and derive completion-first policy preferences.

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator

LABEL_VERSION = "laya-counterfactual-v4"
_VALIDATOR = Draft7Validator(json.loads(
    (Path(__file__).resolve().parents[1] / "schemas/laya_counterfactual_label.schema.json").read_text()
))


def needs_outcome_relabel(case: Mapping[str, Any]) -> bool:
    """Identify old exports from this labeller, leaving other evidence sources intact."""
    provenance = case.get("label_provenance") or {}
    if not isinstance(provenance, Mapping):
        return False
    return (str(case.get("case_id", "")).startswith("cf-")
            and provenance.get("method") == "counterfactual"
            and not str(provenance.get("notes", "")).startswith("completion-first-v1 "))


@lru_cache(maxsize=128)
def journal_outcome(journal: str) -> dict[str, Any]:
    """Summarize a finished replay without inferring health from its cost."""
    terminal = None
    cause = "unknown"
    cleanup_failed = False
    for line in Path(journal).read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "failure_packet":
            cause = event.get("cause", "unknown")
        elif event.get("type") == "termination":
            terminal = event
        elif event.get("type") == "cleanup" and event.get("status") == "failed":
            cleanup_failed = True
    if terminal is None:
        raise ValueError(f"replay has no termination: {journal}")
    final = terminal.get("final_observation") or {}
    failure = final.get("failure") or {}
    metrics = final.get("metrics") or {}
    status = "cleanup_failed" if cleanup_failed else terminal["status"]
    kind = str(failure.get("kind") or "none")
    violations = sum(int(metrics.get(key) or 0) for key in (
        "forbidden_entities", "out_of_bounds_entities", "budget_overruns",
    )) + int(metrics.get("fixtures_valid") is False)
    if cleanup_failed:
        cause = "environment"
    elif kind == "timeout" and final.get("status") == "timed_out":
        # The lab horizon is a policy non-completion, not infrastructure failure.
        cause = "policy"
    elif status == "completed":
        cause = "none"
    return {
        "status": status,
        "failure_cause": cause,
        "failure_kind": kind,
        "reason": str(terminal.get("reason") or failure.get("reason") or ""),
        "elapsed_ticks": metrics.get("completed_ticks", final.get("elapsed_ticks")),
        "safety_violations": violations,
        "progress": {key: metrics.get(key) for key in (
            "delivered_items", "rate_per_tick", "sustained_ticks",
        )},
        "journal": journal,
    }


def outcome_is_policy_evidence(outcome: Mapping[str, Any]) -> bool:
    if outcome.get("safety_violations") != 0:
        return False
    if outcome.get("status") == "completed":
        return outcome.get("failure_cause") == "none" and outcome.get("failure_kind") == "none"
    return outcome.get("failure_cause") == "policy" and outcome.get("status") in {
        "timed_out", "failed", "escalated",
    }


def soft_target(costs: Mapping[str, float], temperature_ticks: float) -> dict[str, float]:
    if not costs or not math.isfinite(temperature_ticks) or temperature_ticks <= 0:
        raise ValueError("targets require costs and a finite positive temperature")
    if any(not math.isfinite(value) or value < 0 for value in costs.values()):
        raise ValueError("costs must be finite and nonnegative")
    best = min(costs.values())
    weights = {option: math.exp(-(cost - best) / temperature_ticks) for option, cost in costs.items()}
    total = sum(weights.values())
    return {option: weight / total for option, weight in weights.items()}


def preference_target(
    costs: Mapping[str, float], outcomes: Mapping[str, Mapping[str, Any]],
    temperature_ticks: float, noise_ticks: float,
) -> tuple[dict[str, float] | None, float, str]:
    """Quarantine non-policy evidence; only completing choices get positive credit."""
    if set(costs) != set(outcomes):
        return None, 0.0, "missing alternative outcomes"
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) or value < 0 for value in costs.values()):
        return None, 0.0, "invalid alternative costs"
    if not math.isfinite(noise_ticks) or noise_ticks < 0:
        return None, 0.0, "invalid replay noise"
    if any(not outcome_is_policy_evidence(outcome) for outcome in outcomes.values()):
        return None, 0.0, "non-policy alternative outcome"
    completed = {option: costs[option] for option, outcome in outcomes.items() if outcome["status"] == "completed"}
    if not completed:
        return None, 0.0, "unresolved all-failed continuation"
    # A late, expensive completion still dominates a cheap non-completion.
    target = {option: 0.0 for option in costs}
    target.update(soft_target(completed, temperature_ticks))
    ordered = sorted(completed.values())
    weight = 1.0 if len(ordered) == 1 or ordered[1] - ordered[0] > 2 * noise_ticks else 0.3
    return target, weight, ""


def label_outcomes(label: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Old labels require their original journals; never guess statuses from costs."""
    if label.get("version") == LABEL_VERSION:
        error = next(_VALIDATOR.iter_errors(label), None)
        if error is not None:
            raise ValueError(f"invalid counterfactual evidence: {error.message}")
        outcomes = label.get("outcomes")
        if not isinstance(outcomes, dict) or any(not isinstance(x, dict) for x in outcomes.values()):
            raise ValueError("missing alternative outcomes")
        return outcomes
    if label.get("version") not in {"laya-counterfactual-v1", "laya-counterfactual-v2", "laya-counterfactual-v3"}:
        raise ValueError("unsupported counterfactual version")
    base = Path(label["base_journal"])
    suffix = f"-{label['seed']}-base0"
    if not base.stem.endswith(suffix) or label.get("base_choice") not in label["cost_ticks"]:
        raise ValueError("legacy label lacks replay identities")
    prefix = base.stem[:-len(suffix)]
    return {
        option: journal_outcome(str(base if option == label["base_choice"] else
            base.parent / f"{prefix}-{label['seed']}-k{label['decision_index']}-{option}.jsonl"))
        for option in label["cost_ticks"]
    }


def label_repeat_outcomes(label: Mapping[str, Any]) -> list[dict[str, Any]]:
    if label.get("version") == LABEL_VERSION:
        return label["base_repeat_outcomes"]
    base = Path(label["base_journal"])
    return [journal_outcome(str(base.with_name(base.stem.removesuffix("base0") + f"base{i}.jsonl")))
            for i in range(len(label.get("base_repeat_costs") or [0]))]
