#!/usr/bin/env python3
# Path: tools/laya_labels_to_cases.py
# Purpose: Turn faithful counterfactual labels into Laya training cases with soft outcome targets.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.laya_state import validate_state
from training.laya_workflow import QUESTION_INSTRUCTIONS


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _decision_state(journal: Path, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    decisions, started = [], None
    for line in journal.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "run_started":
            started = event
        elif event.get("type") == "decision":
            decisions.append(event)
    if started is None or index >= len(decisions):
        raise ValueError(f"{journal} has no decision {index}")
    return started, decisions[index]


def label_to_case(label: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Return (case, "") or (None, exclusion reason)."""
    if not label.get("all_alternatives_faithful"):
        # A missing alternative would be scored as worst; refuse rather than guess.
        return None, "unfaithful alternative"
    started, decision = _decision_state(Path(label["base_journal"]), int(label["decision_index"]))
    options = decision["options"]
    option_ids = [str(option.get("option_id") or option.get("id")) for option in options]
    if option_ids != list(label["options"]):
        return None, "journal options differ from label"
    kind = str(label["decision_kind"])
    target = label["soft_target"]
    probabilities = {option_id: float(target.get(option_id, 0.0)) for option_id in option_ids}
    total = sum(probabilities.values())
    probabilities = {option_id: value / total for option_id, value in probabilities.items()}
    state = decision["state"]
    validate_state(state)
    costs = label["cost_ticks"]
    spread = max(costs.values()) - min(costs.values())
    # Gaps inside replay noise teach little; keep them, but lightly.
    weight = 1.0 if spread > 2 * float(label.get("noise_ticks") or 0) else 0.3
    evidence = _hash({"label": label, "state": state})
    case = {
        "version": "1.0.0",
        "case_id": f"cf-{label['seed']}-k{label['decision_index']}-{evidence[7:19]}",
        "source": {
            "kind": "rl_transition",
            "run_id": str(started["run_id"]),
            "episode_id": str(started["episode_id"]),
            "trajectory_id": str(started["trajectory_id"]),
            "scenario_id": str(label["scenario_id"]),
            "scenario_seed": int(label["seed"]),
            "scenario_hash": str(started["scenario_hash"]),
            "provenance_hash": evidence,
        },
        "state": state,
        "questions": {
            kind: {
                "type": "choice",
                "instructions": QUESTION_INSTRUCTIONS[kind],
                "criteria": {str(option.get("option_id") or option.get("id")): str(option["summary"]) for option in options},
                "option_ids": option_ids,
                "option_count": len(option_ids),
            }
        },
        "gold": {
            kind: {
                "label": max(probabilities, key=probabilities.get),
                "probabilities": probabilities,
                "label_source": "counterfactual",
                "confidence": weight,
            }
        },
        "weights": {"case_weight": weight, "question_weights": {kind: weight},
                    "source_quality": 1.0, "temporal_decay": 1.0},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": "1.0.0",
        "split": "train",  # Reassigned from scenario_seed by the dataset builder.
        "label_provenance": {
            "method": "counterfactual", "evidence_hash": evidence,
            "notes": f"{label['version']} rollout={label['rollout']} costs={costs} noise={label.get('noise_ticks')}",
        },
    }
    return case, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    emitted, excluded = 0, {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for path in args.labels:
            for line in path.read_text(encoding="utf-8").splitlines():
                case, reason = label_to_case(json.loads(line))
                if case is None:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                out.write(json.dumps(case, sort_keys=True) + "\n")
                emitted += 1
    print(json.dumps({"emitted": emitted, "excluded": excluded, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
