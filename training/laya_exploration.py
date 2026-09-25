# Path: training/laya_exploration.py
# Purpose: Explicit exploration gate that lets an untrained Laya drive disposable episodes without claiming trained capability.

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from training.laya_workflow import LayaWorkflowDirector


EXPLORATION_AUTHORITY = "exploration"


@dataclass(frozen=True, slots=True)
class ExplorationGate:
    """Run-level authorization for untrained, disposable-only data collection.

    This is deliberately not a checkpoint capability manifest: it asserts no
    trained scope, grants no comparison or production standing, and every
    artifact it produces is labelled as exploration.
    """

    seed: int
    epsilon: float

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("exploration seed must be a non-negative integer")
        if not math.isfinite(self.epsilon) or not 0.0 <= self.epsilon <= 1.0:
            raise ValueError("exploration epsilon must be in [0, 1]")

    def record(self) -> dict[str, Any]:
        return {
            "kind": EXPLORATION_AUTHORITY,
            "trained_capabilities": None,
            "seed": self.seed,
            "epsilon": self.epsilon,
            "runtime": "disposable_training",
            "production_authority": False,
            "comparison_eligible": False,
            # Exploration choices are never imitation targets; only measured
            # outcomes or evaluated counterfactuals may label them.
            "label_policy": "outcome_only",
        }


class ExplorationDirector:
    """Log the model's distribution, then act on it or on a seeded random option.

    Low confidence never escalates here: exploring uncertain decisions is the
    point. Escalation stays available only as an ordinary option.
    """

    def __init__(
        self,
        predictor: Any,
        gate: ExplorationGate,
        *,
        request_tokenizer: Any | None = None,
        **director_options: Any,
    ):
        self.gate = gate
        self.inner = LayaWorkflowDirector(
            predictor,
            confidence_threshold=0.0,
            request_tokenizer=request_tokenizer,
            **director_options,
        )

    def _draw(self, decision_kind: str, state: Mapping[str, Any], option_ids: Sequence[str]) -> random.Random:
        # Derived from the decision's own observation so a resumed run repeats
        # the same draw for the same state instead of depending on call order.
        snapshot = str((state.get("provenance") or {}).get("state_snapshot_hash") or "")
        material = json.dumps(
            [self.gate.seed, decision_kind, snapshot, list(option_ids)], sort_keys=True,
        )
        return random.Random(int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16))

    def decide(
        self,
        decision_kind: str,
        state: Mapping[str, Any],
        options: Sequence[Mapping[str, Any] | str],
    ) -> dict[str, Any]:
        result = self.inner.decide(decision_kind, state, options)
        normalized = list(result["state"]["options"])
        option_ids = [str(option["option_id"]) for option in normalized]
        model_choice = str(result["selected_option"]["option_id"])
        rng = self._draw(decision_kind, state, option_ids)
        explore = rng.random() < self.gate.epsilon
        selected = rng.choice(normalized) if explore else result["selected_option"]
        result["selected_option"] = selected
        result["selection"] = {
            "authority": EXPLORATION_AUTHORITY,
            "source": "exploration_random" if explore else "model",
            "model_choice": model_choice,
            "model_confidence": result["confidence"],
            "epsilon": self.gate.epsilon,
        }
        return result


__all__ = ["EXPLORATION_AUTHORITY", "ExplorationDirector", "ExplorationGate"]
