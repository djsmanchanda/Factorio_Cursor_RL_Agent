# Path: training/laya_shadow.py
# Purpose: Compare Laya decisions with a baseline while keeping execution on the incumbent policy.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from training.laya_policy import LayaEscalation
from training.laya_dataset import _state_from_transition


def state_builder_for_scenario(scenario: Mapping[str, Any]):
    """Build the versioned Laya state used by a disposable shadow decision.

    The shadow path reuses the dataset adapter so live candidate identity and
    the training state contract stay aligned. It never chooses or executes an
    action; the caller still owns the baseline execution path.
    """
    frozen_scenario = dict(scenario)

    def build(observation: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        candidate_list = [dict(candidate) for candidate in candidates]
        if not candidate_list:
            raise ValueError("shadow state requires at least one candidate")
        transition = {
            "episode_id": str(frozen_scenario.get("scenario_id", "shadow-episode")),
            "started_tick": int(observation.get("tick", 0) or 0),
            "observation": dict(observation),
            "candidates": candidate_list,
            "chosen_action_id": str(candidate_list[0]["action_id"]),
            "scenario_hash": str(frozen_scenario.get("scenario_hash", "sha256:" + "0" * 64)),
            "result": {"failure_kind": "none"},
        }
        state = _state_from_transition(transition, frozen_scenario)
        # Preserve the complete option evidence for the shared sequence
        # boundary. The boundary owns deterministic projection and fails
        # closed on overflow; this adapter must not silently discard features
        # before the request is measured or recorded for replay.
        return state

    return build


def shadow_decision(
    laya_policy,
    baseline_policy,
    candidates: Sequence[Mapping[str, Any]],
    observation: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Return comparison evidence; the caller executes ``baseline_action`` only."""
    baseline = baseline_policy.select(candidates, observation, seed)
    record: dict[str, Any] = {
        "baseline_action_id": baseline["action_id"],
        "laya_action_id": None,
        "status": "escalated",
        "reason": None,
        "agreement": False,
    }
    try:
        laya = laya_policy.select(candidates, observation, seed)
    except LayaEscalation as error:
        record["reason"] = str(error)
    except Exception as error:
        record["reason"] = f"unexpected_laya_error: {error}"
    else:
        record["laya_action_id"] = laya["action_id"]
        record["status"] = "selected"
        record["agreement"] = laya["action_id"] == baseline["action_id"]
    record["baseline_action"] = dict(baseline)
    return record


__all__ = ["shadow_decision", "state_builder_for_scenario"]
