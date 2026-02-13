# Path: rl_advisor.py
# Purpose: Produce deterministic, non-authoritative RL action proposals from read-only observations.

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class RLActionProposal:
    proposed_action_type: str
    confidence: float
    rationale: str
    requires_authorization: bool = True
    target_block: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "proposed_action_type": self.proposed_action_type,
            "confidence": float(self.confidence),
            "rationale": self.rationale,
            "requires_authorization": True,
        }
        if self.target_block:
            payload["target_block"] = self.target_block
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

    rng = random.Random(seed)

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
            candidates.append(
                (
                    "project_more_ghosts",
                    max(0.0, min(1.0, confidence)),
                    "Capacity increase requested and ghost expansion is currently allowed.",
                )
            )

        if desired > previous and current >= active:
            candidates.append(
                ("request_phase_advance", 0.79, "Current capacity has reached active phase; advance can be requested.")
            )

        if "ghost_upgrade" in next_allowed and current < desired:
            candidates.append(
                ("request_module_upgrade", 0.62, "Upgrade path is allowed and may improve throughput toward target.")
            )

        if not candidates:
            candidates.append(("hold_position", 0.86, "No strong advisory signal for expansion or advance."))

    # Deterministic tie-break: confidence desc, then seeded pseudo-random jitter, then action name.
    scored = []
    for action, confidence, rationale in candidates:
        scored.append((confidence, rng.random(), action, rationale))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    best_confidence, _, best_action, best_rationale = scored[0]

    proposal = RLActionProposal(
        proposed_action_type=best_action,
        confidence=round(float(best_confidence), 3),
        rationale=best_rationale,
        target_block=target_block,
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
