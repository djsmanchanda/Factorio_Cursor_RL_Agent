# Path: rl_advisor.py
# Purpose: Produce deterministic, non-authoritative RL action proposals from read-only observations.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jsonschema import Draft7Validator
from core.capacity_allocator import CapacityAllocation, allocate_phase_capacity
from core.target_selector import ExpansionTarget, select_expansion_target


@dataclass(frozen=True)
class RLActionProposal:
    proposed_action_type: str
    confidence: float
    rationale: str
    requires_authorization: bool = True
    target_block: Optional[str] = None
    expansion_target: Optional[ExpansionTarget] = None
    capacity_allocation: Optional[CapacityAllocation] = None

    def to_dict(self) -> dict:
        payload = {
            "proposed_action_type": self.proposed_action_type,
            "confidence": float(self.confidence),
            "rationale": self.rationale,
            "requires_authorization": True,
        }
        if self.target_block:
            payload["target_block"] = self.target_block
        if self.expansion_target:
            payload["expansion_target"] = self.expansion_target.to_dict()
        if self.capacity_allocation:
            payload["capacity_allocation"] = self.capacity_allocation.to_dict()
        return payload


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            loc = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {loc}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def _select_target_block(metrics_summary: dict) -> Optional[str]:
    blocks = metrics_summary.get("priority_blocks")
    if type(blocks) is not list or len(blocks) == 0:
        return None

    normalized = []
    for entry in blocks:
        if type(entry) is not dict:
            continue
        block = entry.get("block")
        score = entry.get("score")
        if isinstance(block, str) and block and isinstance(score, (int, float)):
            normalized.append((block, float(score)))

    if not normalized:
        return None

    # Deterministic: highest score first, then lexical block order for stable ties.
    normalized.sort(key=lambda item: (-item[1], item[0]))
    return normalized[0][0]


def propose_rl_action(
    observation: dict,
    seed: int = 0,
    observation_schema_path: Path | None = None,
    proposal_schema_path: Path | None = None,
) -> RLActionProposal:
    repo_root = Path(__file__).resolve().parent
    if observation_schema_path is None:
        observation_schema_path = repo_root / "rl_observation.schema.json"
    if proposal_schema_path is None:
        proposal_schema_path = repo_root / "rl_action_proposal.schema.json"

    _validate_schema(observation, observation_schema_path, "RLObservation")

    progress = observation["progress_state"]
    phasing = observation["capacity_phasing"]
    metrics = observation["latest_metrics_summary"]
    phase_status = observation["phase_completion_status"]

    desired = int(phasing["desired_active_capacity"])
    previous = int(phasing["previous_active_capacity"])
    current = int(progress["current_capacity"])
    active = int(progress["active_phase_capacity"])
    next_allowed = set(phasing.get("next_allowed_actions", []))
    bot_headroom = metrics.get("bot_headroom")
    target_block = _select_target_block(metrics)
    bot_utilization_ratio = float(observation["bot_utilization_ratio"])
    power_stress_ratio = float(observation["power_stress_ratio"])
    construction_backlog_estimate = int(observation["construction_backlog_estimate"])
    phase_completion_ratio = float(observation["phase_completion_ratio"])
    factory_density_score = float(observation["factory_density_score"])
    spatial_pressure_index = float(observation["spatial_pressure_index"])
    throughput_stress_index = float(observation["throughput_stress_index"])
    pressure_attribution_map = observation["pressure_attribution_map"]
    production_gap_estimate = observation["production_gap_estimate"]

    if type(pressure_attribution_map) is not dict or len(pressure_attribution_map) == 0:
        raise ValueError("pressure_attribution_map must be a non-empty object")
    pressure_values = []
    for block_id, value in pressure_attribution_map.items():
        if not isinstance(block_id, str) or block_id == "":
            raise ValueError("pressure_attribution_map keys must be non-empty strings")
        if not isinstance(value, (int, float)):
            raise ValueError("pressure_attribution_map values must be numeric")
        numeric = float(value)
        if numeric < 0.0 or numeric > 1.0:
            raise ValueError("pressure_attribution_map values must be in [0,1]")
        pressure_values.append(numeric)

    if type(production_gap_estimate) is not dict or len(production_gap_estimate) == 0:
        raise ValueError("production_gap_estimate must be a non-empty object")
    recipe_gaps = []
    for recipe_name, value in production_gap_estimate.items():
        if not isinstance(recipe_name, str) or recipe_name == "":
            raise ValueError("production_gap_estimate keys must be non-empty strings")
        if not isinstance(value, int) or value < 0:
            raise ValueError("production_gap_estimate values must be non-negative integers")
        recipe_gaps.append(int(value))

    expansion_target = select_expansion_target(
        pressure_attribution_map=pressure_attribution_map,
        production_gap_estimate=production_gap_estimate,
        throughput_stress_index=throughput_stress_index,
        phase_completion_ratio=phase_completion_ratio,
    )
    capacity_allocation = allocate_phase_capacity(
        expansion_target=expansion_target.to_dict(),
        progress_state=progress,
        capacity_phasing=phasing,
        throughput_stress_index=throughput_stress_index,
    )

    if seed != 0:
        raise ValueError("RL advisor is deterministic-only in this phase; seed must be 0")

    derived_phase_completion_ratio = 0.0
    if active > 0:
        derived_phase_completion_ratio = float(current) / float(active)
    if abs(phase_completion_ratio - derived_phase_completion_ratio) > 1e-6:
        raise ValueError("phase_completion_ratio does not match ProgressState-derived ratio")

    derived_backlog = max(0, int(progress["committed_capacity"]) - current)
    if construction_backlog_estimate != derived_backlog:
        raise ValueError("construction_backlog_estimate does not match ProgressState-derived backlog")

    candidates = []
    if phase_status == "BLOCKED":
        candidates.append(("hold_position", 0.98, "Reconciliation blocked; advisory recommends hold."))
    elif phase_status in {"PARTIAL", "INCOMPLETE"}:
        candidates.append(("hold_position", 0.90, "Phase incomplete; hold until readiness conditions are clear."))
    else:
        if desired > previous and "ghost_expand" in next_allowed:
            confidence = 0.74
            if isinstance(bot_headroom, (int, float)) and float(bot_headroom) < 0:
                confidence -= 0.20
            if bot_utilization_ratio >= 0.95:
                confidence -= 0.12
            if power_stress_ratio >= 1.0:
                confidence -= 0.10
            if construction_backlog_estimate > 0:
                confidence -= 0.08
            if factory_density_score >= 1.0:
                confidence -= 0.06
            if spatial_pressure_index >= 0.80:
                confidence -= 0.10
            throughput_boost = 0.06 * throughput_stress_index
            if spatial_pressure_index >= 0.80:
                throughput_boost = throughput_boost * 0.35
            confidence += throughput_boost
            sorted_pressures = sorted(pressure_values, reverse=True)
            dominant = sorted_pressures[0]
            second = sorted_pressures[1] if len(sorted_pressures) > 1 else 0.0
            if dominant >= 0.70 and (dominant - second) >= 0.15:
                confidence += 0.03
            sorted_gaps = sorted(recipe_gaps, reverse=True)
            dominant_gap = sorted_gaps[0]
            second_gap = sorted_gaps[1] if len(sorted_gaps) > 1 else 0
            if dominant_gap >= 2 and (dominant_gap - second_gap) >= 1:
                confidence += 0.03
            confidence += 0.02 * expansion_target.confidence
            if capacity_allocation.allocated_now <= 0 and capacity_allocation.reserved_for_later <= 0:
                confidence -= 0.08
            else:
                phase_capacity = max(1, capacity_allocation.phase_capacity)
                allocation_ratio = float(capacity_allocation.allocated_now) / float(phase_capacity)
                confidence += 0.04 * allocation_ratio
            candidates.append(
                (
                    "project_more_ghosts",
                    max(0.0, min(1.0, confidence)),
                    "Capacity increase requested and ghost expansion is currently allowed.",
                )
            )

        if desired > previous and current >= active:
            confidence = 0.79
            if phase_completion_ratio >= 1.0:
                confidence += 0.05
            if construction_backlog_estimate > 0:
                confidence -= 0.04
            if spatial_pressure_index >= 0.70:
                confidence += 0.03
            throughput_boost = 0.05 * throughput_stress_index
            if spatial_pressure_index >= 0.80:
                throughput_boost = throughput_boost * 0.40
            confidence += throughput_boost
            candidates.append(
                (
                    "request_phase_advance",
                    max(0.0, min(1.0, confidence)),
                    "Current capacity has reached active phase; advance can be requested.",
                )
            )

        if "ghost_upgrade" in next_allowed and current < desired:
            confidence = 0.62
            if power_stress_ratio >= 1.0:
                confidence += 0.05
            if bot_utilization_ratio >= 0.9:
                confidence += 0.04
            if spatial_pressure_index >= 0.75:
                confidence += 0.05
            candidates.append(
                (
                    "request_module_upgrade",
                    max(0.0, min(1.0, confidence)),
                    "Upgrade path is allowed and may improve throughput toward target.",
                )
            )

        if not candidates:
            candidates.append(("hold_position", 0.86, "No strong advisory signal for expansion or advance."))

    # Deterministic tie-break: confidence desc, then lexical action name.
    scored = [(confidence, action, rationale) for action, confidence, rationale in candidates]
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_confidence, best_action, best_rationale = scored[0]

    proposal = RLActionProposal(
        proposed_action_type=best_action,
        confidence=round(float(best_confidence), 3),
        rationale=best_rationale,
        target_block=target_block,
        expansion_target=expansion_target,
        capacity_allocation=capacity_allocation,
        requires_authorization=True,
    )

    payload = proposal.to_dict()
    _validate_schema(payload, proposal_schema_path, "RLActionProposal")
    return proposal


def main() -> int:
    import sys

    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python rl_advisor.py <rl_observation.json> [seed]")
        return 1

    observation_path = Path(sys.argv[1]).resolve()
    seed = int(sys.argv[2]) if len(sys.argv) == 3 else 0
    observation = _load_json(observation_path)
    proposal = propose_rl_action(observation, seed=seed)
    print(json.dumps(proposal.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
