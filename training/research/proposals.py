# Path: training/research/proposals.py
# Purpose: Validate LLM proposals against an allowlist of policy-only settings.

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


POLICY_RANGES = {
    "alpha": (0.01, 5.0, float),
    "regularization": (0.01, 100.0, float),
    "mutation_scale": (0.001, 1.0, float),
    "elite_fraction": (0.05, 0.5, float),
    "population_size": (4, 64, int),
}


@dataclass(frozen=True)
class PolicyProposal:
    hypothesis: str
    changes: dict[str, float | int]
    expected_effect: str
    stop_condition: str

    def to_dict(self) -> dict:
        return {
            "hypothesis": self.hypothesis,
            "changes": dict(self.changes),
            "expected_effect": self.expected_effect,
            "stop_condition": self.stop_condition,
        }


def _bounded_change(name: str, value: object) -> float | int:
    if name not in POLICY_RANGES:
        raise ValueError(f"research proposal cannot modify {name!r}")
    low, high, value_type = POLICY_RANGES[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if value_type is int and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value_type(value)


def validate_proposal(payload: Mapping) -> PolicyProposal:
    required = {"hypothesis", "changes", "expected_effect", "stop_condition"}
    if set(payload) != required:
        raise ValueError("research proposal has missing or unknown fields")
    if not isinstance(payload["changes"], Mapping) or not payload["changes"]:
        raise ValueError("research proposal changes must be a non-empty object")
    text = {name: str(payload[name]).strip() for name in required - {"changes"}}
    if any(not value for value in text.values()):
        raise ValueError("research proposal text fields cannot be empty")
    changes = {
        str(name): _bounded_change(str(name), value)
        for name, value in payload["changes"].items()
    }
    return PolicyProposal(changes=changes, **text)


def apply_proposal(config: Mapping, proposal: PolicyProposal) -> dict:
    updated = dict(config)
    updated.update(proposal.changes)
    return updated
