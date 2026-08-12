# Path: training/surrogate.py
# Purpose: Train an optional, offline reward and failure surrogate from durable episodes.

"""Optional PyTorch surrogate training, isolated from the live Factorio controller.

The surrogate is deliberately an *offline advisor*: it reads completed transition
evidence and emits a checkpoint plus metrics.  It does not select an action,
write to ``experience.db``, or import from the batch-controller modules.  A
future candidate-ranking integration must be separately evaluated and promoted.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from training.compute import resolve_training_compute
from training.features import MINING_DELIVERY_FEATURES_V2, policy_features


SURROGATE_VERSION = "1.0.0"
FEATURE_REGISTRY = MINING_DELIVERY_FEATURES_V2
TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out"})


@dataclass(frozen=True)
class SurrogateExample:
    """One immutable terminal decision and its observed outcome."""

    features: tuple[float, ...]
    reward: float
    failed: bool


def _json_object(value: str) -> dict:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _chosen_candidate(payload: Mapping) -> Mapping | None:
    chosen_id = payload.get("chosen_action_id")
    candidates = payload.get("candidates")
    if not isinstance(chosen_id, str) or not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if isinstance(candidate, Mapping) and candidate.get("action_id") == chosen_id:
            return candidate
    return None


def _example(payload: Mapping) -> SurrogateExample | None:
    """Convert one validated transition payload into the fixed feature contract."""
    observation = payload.get("observation")
    candidate = _chosen_candidate(payload)
    reward = payload.get("reward")
    result = payload.get("result")
    if not all(isinstance(value, Mapping) for value in (observation, candidate, reward, result)):
        return None
    try:
        values = policy_features(observation, candidate.get("features") or {})
        features = FEATURE_REGISTRY.vectorize({
            name: values[name] for name in FEATURE_REGISTRY.names if name in values
        })
        reward_total = float(reward["total"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(reward_total):
        return None
    return SurrogateExample(
        features=features,
        reward=reward_total,
        failed=(result.get("status") != "completed" or result.get("failure_kind") != "none"),
    )


def load_terminal_examples(database: Path | str, limit: int | None = None) -> list[SurrogateExample]:
    """Read terminal transitions without changing the training database."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive when provided")
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"training database does not exist: {path}")
    query = (
        "SELECT t.payload_json FROM transitions t JOIN episodes e USING(episode_id) "
        "WHERE e.status IN ('completed','failed','timed_out') "
        "ORDER BY e.ended_utc ASC, t.step_index ASC"
    )
    parameters: tuple[object, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(query, parameters)
        return [example for row in rows if (example := _example(_json_object(row[0]))) is not None]
    finally:
        connection.close()


def torch_runtime(require_cuda: bool = False):
    """Load the shared optional compute runtime for offline surrogate training."""
    runtime = resolve_training_compute("cuda" if require_cuda else "auto")
    if runtime.torch is None:
        if require_cuda:
            raise RuntimeError(f"CUDA was requested but {runtime.reason}")
        raise RuntimeError(
            "PyTorch is not installed. Install a CUDA-enabled PyTorch build before "
            "running the optional surrogate trainer."
        )
    return runtime.torch, runtime.device


def _split_examples(examples: list[SurrogateExample]) -> tuple[list[SurrogateExample], list[SurrogateExample]]:
    """Use a deterministic held-out tail while keeping tiny data sets trainable."""
    validation_size = max(1, len(examples) // 5)
    if len(examples) < 2:
        return examples, examples
    return examples[:-validation_size], examples[-validation_size:]


def train_surrogate(
    examples: Iterable[SurrogateExample], *, epochs: int = 80, batch_size: int = 256,
    learning_rate: float = 1e-3, hidden_width: int = 128, require_cuda: bool = False,
) -> tuple[dict, object]:
    """Fit compact reward and failure heads, using CUDA whenever it is available."""
    examples = list(examples)
    if len(examples) < 2:
        raise ValueError("at least two terminal transitions are required for surrogate training")
    if epochs < 1 or batch_size < 1 or learning_rate <= 0 or hidden_width < 1:
        raise ValueError("epochs, batch_size, learning_rate, and hidden_width must be positive")
    torch, device = torch_runtime(require_cuda=require_cuda)
    train_examples, validation_examples = _split_examples(examples)
    width = len(FEATURE_REGISTRY.names)

    class RewardFailureSurrogate(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone = torch.nn.Sequential(
                torch.nn.Linear(width, hidden_width), torch.nn.ReLU(),
                torch.nn.Linear(hidden_width, hidden_width), torch.nn.ReLU(),
            )
            self.reward_head = torch.nn.Linear(hidden_width, 1)
            self.failure_head = torch.nn.Linear(hidden_width, 1)

        def forward(self, values):
            hidden = self.backbone(values)
            return self.reward_head(hidden).squeeze(-1), self.failure_head(hidden).squeeze(-1)

    def tensors(rows: list[SurrogateExample]):
        return (
            torch.tensor([row.features for row in rows], dtype=torch.float32, device=device),
            torch.tensor([row.reward for row in rows], dtype=torch.float32, device=device),
            torch.tensor([float(row.failed) for row in rows], dtype=torch.float32, device=device),
        )

    train_x, train_reward, train_failure = tensors(train_examples)
    valid_x, valid_reward, valid_failure = tensors(validation_examples)
    model = RewardFailureSurrogate().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    mse = torch.nn.MSELoss()
    bce = torch.nn.BCEWithLogitsLoss()

    model.train()
    for _ in range(epochs):
        permutation = torch.randperm(len(train_examples), device=device)
        for start in range(0, len(train_examples), batch_size):
            selected = permutation[start:start + batch_size]
            predicted_reward, predicted_failure = model(train_x[selected])
            loss = mse(predicted_reward, train_reward[selected]) + bce(
                predicted_failure, train_failure[selected]
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()

    model.eval()
    with torch.no_grad():
        predicted_reward, predicted_failure = model(valid_x)
        reward_mae = torch.mean(torch.abs(predicted_reward - valid_reward)).item()
        failure_accuracy = torch.mean(
            (torch.sigmoid(predicted_failure) >= 0.5).eq(valid_failure >= 0.5).float()
        ).item()
    summary = {
        "version": SURROGATE_VERSION,
        "device": device,
        "cuda_available": bool(torch.cuda.is_available()),
        "feature_registry": FEATURE_REGISTRY.to_dict(),
        "examples": len(examples), "training_examples": len(train_examples),
        "validation_examples": len(validation_examples), "epochs": epochs,
        "batch_size": batch_size, "hidden_width": hidden_width,
        "validation_reward_mae": float(reward_mae),
        "validation_failure_accuracy": float(failure_accuracy),
    }
    return summary, model


def save_surrogate(path: Path | str, summary: Mapping, model: object, torch_module: object) -> None:
    """Persist the independent checkpoint only after offline training completes."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch_module.save({"summary": dict(summary), "state_dict": model.state_dict()}, output)
