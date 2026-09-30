#!/usr/bin/env python3
# Path: tools/laya_labels_to_cases.py
# Purpose: Turn faithful counterfactual labels into Laya training cases with soft outcome targets.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.laya_state import validate_state
from training.laya_workflow import QUESTION_INSTRUCTIONS
from training.laya_counterfactual import label_outcomes, label_repeat_outcomes, outcome_is_policy_evidence, preference_target


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


@lru_cache(maxsize=16)
def _journal_decisions(journal: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decisions, started = [], None
    for line in journal.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "run_started":
            started = event
        elif event.get("type") == "decision":
            decisions.append(event)
    if started is None:
        raise ValueError(f"{journal} has no run identity")
    return started, decisions


def _decision_state(journal: Path, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    started, decisions = _journal_decisions(journal)
    if not 0 <= index < len(decisions):
        raise ValueError(f"{journal} has no decision {index}")
    return started, decisions[index]


def label_to_case(label: dict[str, Any], temperature_ticks: float | None = None) -> tuple[dict[str, Any] | None, str]:
    """Return a completion-first case, or the reason its evidence is unresolved."""
    if not label.get("all_alternatives_faithful"):
        # A missing alternative would be scored as worst; refuse rather than guess.
        return None, "unfaithful alternative"
    try:
        outcomes = label_outcomes(label)
        expected = {option for option in label["options"] if "escalate" not in option}
        if set(label["cost_ticks"]) != expected or set(outcomes) != expected:
            return None, "missing alternative outcomes"
        repeats = label_repeat_outcomes(label)
        if repeats and (any(not outcome_is_policy_evidence(run) for run in repeats)
                        or len({run["status"] for run in repeats}) != 1):
            return None, "inconsistent base repeat outcomes"
        target, weight, exclusion = preference_target(
            label["cost_ticks"], outcomes,
            temperature_ticks if temperature_ticks is not None else float(label.get("temperature_ticks", 600.0)),
            float(label.get("noise_ticks") or 0),
        )
        if target is None:
            return None, exclusion
    except (OSError, ValueError, KeyError, TypeError):
        return None, "unavailable or invalid outcome evidence"
    started, decision = _decision_state(Path(label["base_journal"]), int(label["decision_index"]))
    options = decision["options"]
    option_ids = [str(option.get("option_id") or option.get("id")) for option in options]
    if option_ids != list(label["options"]):
        return None, "journal options differ from label"
    kind = str(label["decision_kind"])
    probabilities = {option_id: float(target.get(option_id, 0.0)) for option_id in option_ids}
    total = sum(probabilities.values())
    probabilities = {option_id: value / total for option_id, value in probabilities.items()}
    state = decision["state"]
    validate_state(state)
    costs = label["cost_ticks"]
    evidence = _hash({"label": label, "outcomes": outcomes, "repeats": repeats, "target": target, "state": state})
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
            "notes": f"completion-first-v1 {label['version']} rollout={label['rollout']} costs={costs} noise={label.get('noise_ticks')}",
        },
    }
    return case, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temperature-ticks", type=float,
                        help="Temperature among completing alternatives (default: label temperature, or 600 for legacy)")
    args = parser.parse_args(argv)
    emitted, excluded = 0, {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for path in args.labels:
            for line in path.read_text(encoding="utf-8").splitlines():
                case, reason = label_to_case(json.loads(line), args.temperature_ticks)
                if case is None:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                # Unsorted: criteria order must follow the state's option order.
                out.write(json.dumps(case) + "\n")
                emitted += 1
    print(json.dumps({"emitted": emitted, "excluded": excluded, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
