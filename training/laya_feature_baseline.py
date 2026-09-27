# Path: training/laya_feature_baseline.py
# Purpose: Features-only conditional-logit baseline; Laya must beat it to justify a text model.

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

# Option-level numeric features the toolkit already exposes, plus coarse option
# kind and the work-graph context every question carries.
_OPTION_FEATURES = (
    "entity_count", "pole_count", "material_cost", "machine_speed",
    "inserter_items_per_second", "wait_seconds", "variant_count",
)
_KINDS = ("serve", "wait", "variant", "escalate", "other")


def _kind(option_id: str) -> str:
    if option_id.startswith("serve."):
        return "serve"
    if option_id.startswith("wait."):
        return "wait"
    if "escalate" in option_id:
        return "escalate"
    if "." in option_id:
        return "variant"
    return "other"


def option_vector(option: Mapping[str, Any], state: Mapping[str, Any]) -> np.ndarray:
    features = option.get("features") or {}
    option_id = str(option.get("option_id") or option.get("id"))
    kind = _kind(option_id)
    branches = ((state.get("observation_digest") or {}).get("work_branches") or {})
    context = [float(len(branches.get("ready") or [])), float(len(branches.get("closed") or [])),
               float(len(branches.get("blocked") or {}))]
    numeric = [math.log1p(max(0.0, float(features.get(name) or 0.0))) for name in _OPTION_FEATURES]
    onehot = [1.0 if kind == name else 0.0 for name in _KINDS]
    # Kind-by-context interactions let "wait" depend on how much is still open.
    interactions = [onehot[index] * value for index in range(len(_KINDS)) for value in context]
    return np.array(numeric + onehot + interactions, dtype=np.float64)


def _case_rows(case: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    kind = next(iter(case["questions"]))
    ids = case["questions"][kind]["option_ids"]
    by_id = {str(option.get("option_id") or option.get("id")): option for option in case["state"]["options"]}
    if any(option_id not in by_id for option_id in ids):
        return None
    matrix = np.stack([option_vector(by_id[option_id], case["state"]) for option_id in ids])
    probabilities = case["gold"][kind]["probabilities"]
    target = np.array([float(probabilities.get(option_id, 0.0)) for option_id in ids])
    return matrix, target / max(target.sum(), 1e-12)


def train(cases: Sequence[Mapping[str, Any]], *, epochs: int = 400, rate: float = 0.05, l2: float = 1e-3) -> dict[str, Any]:
    rows = [row for row in (_case_rows(case) for case in cases) if row is not None]
    if not rows:
        raise ValueError("no usable cases")
    width = rows[0][0].shape[1]
    stacked = np.concatenate([matrix for matrix, _ in rows])
    mean, scale = stacked.mean(axis=0), stacked.std(axis=0) + 1e-6
    weights = np.zeros(width)
    for _ in range(epochs):
        gradient = l2 * weights
        for matrix, target in rows:
            scores = ((matrix - mean) / scale) @ weights
            probabilities = np.exp(scores - scores.max())
            probabilities /= probabilities.sum()
            gradient += ((matrix - mean) / scale).T @ (probabilities - target) / len(rows)
        weights -= rate * gradient
    return {"version": "laya-feature-baseline-v1", "mean": mean.tolist(), "scale": scale.tolist(),
            "weights": weights.tolist(), "cases": len(rows)}


class FeaturePredictor:
    """Predictor-compatible policy scoring options from their numeric features only."""

    tokenizer = None

    def __init__(self, model: Mapping[str, Any]):
        self.mean = np.array(model["mean"])
        self.scale = np.array(model["scale"])
        self.weights = np.array(model["weights"])

    @classmethod
    def load(cls, path: Path) -> "FeaturePredictor":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def predict(self, state, questions):
        kind = next(iter(questions))
        ids = list(questions[kind]["criteria"])
        by_id = {str(option.get("option_id") or option.get("id")): option for option in state.get("options") or ()}
        scores = np.array([
            float(((option_vector(by_id[option_id], state) - self.mean) / self.scale) @ self.weights)
            if option_id in by_id else -1e9 for option_id in ids
        ])
        probabilities = np.exp(scores - scores.max())
        probabilities /= probabilities.sum()
        choice = ids[int(probabilities.argmax())]
        return {"answers": {kind: {"choice": choice, "confidence": float(probabilities.max()),
                                   "probabilities": dict(zip(ids, map(float, probabilities)))}}}


def accuracy(model: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]) -> float:
    predictor = FeaturePredictor(model)
    hits = total = 0
    for case in cases:
        kind = next(iter(case["questions"]))
        answer = predictor.predict(case["state"], case["questions"])["answers"][kind]["choice"]
        total += 1
        hits += answer == case["gold"][kind]["label"]
    return hits / max(total, 1)


__all__ = ["FeaturePredictor", "accuracy", "option_vector", "train"]
