# Path: orchestrator/laya_decision_flow.py
# Purpose: Define the bounded, auditable Laya Guided Runner decision protocol.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class LayaDecisionFlowError(ValueError):
    """Raised when a guided-runner decision frame is not auditable."""


# Health and execution are deterministic gates, not model choices. The model
# sees only the bounded strategic seams below and receives a fresh observation
# after every executed transaction.
DECISION_ORDER: tuple[str, ...] = (
    "serve_goal",
    "choose_operation",
    "select_site",
    "select_scale",
    "select_route",
    "select_research",
    "select_upgrade",
    "wait_or_act",
    "recovery",
    "select_candidate",
)

_OPERATION_SITE_STEPS = {
    "mine", "smelt", "assemble", "route", "power", "logistics",
    "repair", "upgrade", "retire", "reserve-space", "expand",
}


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_options(options: Sequence[Mapping[str, Any] | str], *, maximum: int = 12) -> list[dict[str, Any]]:
    """Keep only compact option identity; full plans remain outside model context."""
    if not 1 <= maximum <= 20:
        raise ValueError("maximum must be between 1 and 20")
    if not options or len(options) > maximum:
        raise LayaDecisionFlowError(f"decision requires between 1 and {maximum} options")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(options):
        if isinstance(raw, str):
            option_id, summary = raw.strip(), raw.strip()
            item: dict[str, Any] = {"option_id": option_id, "summary": summary}
        elif isinstance(raw, Mapping):
            option_id = str(raw.get("option_id") or raw.get("action_id") or "").strip()
            summary = str(raw.get("summary") or raw.get("label") or option_id).strip()
            item = {"option_id": option_id, "summary": summary}
            for key in ("operation_family", "tool_id", "signature", "plan_hash", "risk_tier"):
                if raw.get(key) is not None:
                    item[key] = str(raw[key])
            if isinstance(raw.get("features"), Mapping):
                item["features"] = {
                    str(key): value for key, value in list(raw["features"].items())[:4]
                    if isinstance(value, (int, float))
                }
        else:
            raise LayaDecisionFlowError(f"option {index} must be a string or object")
        if not option_id or len(option_id) > 128:
            raise LayaDecisionFlowError("option IDs must be non-empty and at most 128 characters")
        if option_id in seen:
            raise LayaDecisionFlowError(f"duplicate option ID: {option_id}")
        if not summary:
            raise LayaDecisionFlowError(f"option {option_id} has no summary")
        item["summary"] = summary[:160]
        item.setdefault("signature", _hash({"option_id": option_id, "summary": summary}))
        result.append(item)
        seen.add(option_id)
    return result


def next_decision_kind(
    decision_kind: str,
    *,
    operation_family: str | None = None,
    outcome: str = "pending",
    objective_drift: bool = False,
) -> str:
    """Advance one bounded decision seam; execution itself is never selected here."""
    if decision_kind not in DECISION_ORDER:
        raise LayaDecisionFlowError(f"unknown decision kind: {decision_kind}")
    if outcome in {"failed", "timed_out", "blocked"} or objective_drift:
        return "recovery"
    if decision_kind == "serve_goal":
        return "choose_operation"
    if decision_kind == "choose_operation":
        family = str(operation_family or "")
        if family == "research":
            return "select_research"
        if family == "upgrade":
            return "select_upgrade"
        if family in _OPERATION_SITE_STEPS:
            return "select_site"
        return "select_candidate"
    if decision_kind == "select_site":
        return "select_scale"
    if decision_kind == "select_scale":
        return "select_route"
    if decision_kind == "select_route":
        return "select_candidate"
    if decision_kind in {"select_research", "select_upgrade", "select_candidate"}:
        return "wait_or_act"
    if decision_kind == "wait_or_act":
        return "serve_goal"
    return "choose_operation"  # recovery


@dataclass(frozen=True)
class DecisionFrame:
    """One model question with a stable parent trajectory and local step."""

    trajectory_id: str
    step_index: int
    decision_kind: str
    decision_id: str
    state_hash: str
    options: tuple[dict[str, Any], ...]
    question: dict[str, Any]
    parent_decision_id: str | None = None


def build_decision_frame(
    state: Mapping[str, Any],
    decision_kind: str,
    options: Sequence[Mapping[str, Any] | str],
    *,
    trajectory_id: str,
    step_index: int,
    parent_decision_id: str | None = None,
    maximum_options: int = 12,
) -> DecisionFrame:
    """Build a compact question while retaining full state by hash externally."""
    if decision_kind not in DECISION_ORDER:
        raise LayaDecisionFlowError(f"unknown decision kind: {decision_kind}")
    if not trajectory_id or step_index < 0:
        raise LayaDecisionFlowError("trajectory_id is required and step_index must be non-negative")
    compact = normalize_options(options, maximum=maximum_options)
    snapshot_hash = str((state.get("provenance") or {}).get("state_snapshot_hash") or _hash(dict(state)))
    if not snapshot_hash.startswith("sha256:"):
        snapshot_hash = _hash(dict(state))
    decision_id = f"{trajectory_id}.s{step_index:04d}.{decision_kind}"
    question = {
        "type": "choice",
        "instructions": f"Choose the validated option for {decision_kind.replace('_', ' ')}.",
        "criteria": {item["option_id"]: item["summary"] for item in compact},
        "option_ids": [item["option_id"] for item in compact],
        "option_count": len(compact),
    }
    return DecisionFrame(
        trajectory_id=trajectory_id,
        step_index=step_index,
        decision_kind=decision_kind,
        decision_id=decision_id,
        state_hash=snapshot_hash,
        options=tuple(compact),
        question=question,
        parent_decision_id=parent_decision_id,
    )


__all__ = [
    "DECISION_ORDER",
    "DecisionFrame",
    "LayaDecisionFlowError",
    "build_decision_frame",
    "next_decision_kind",
    "normalize_options",
]
