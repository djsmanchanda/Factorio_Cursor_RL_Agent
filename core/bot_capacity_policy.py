# Path: core/bot_capacity_policy.py
# Purpose: Deterministically derive bot capacity pressure from metrics summary.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class BotCapacitySignal:
    bot_utilization_ratio: float
    capacity_level: str
    expansion_damping: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "bot_utilization_ratio": float(self.bot_utilization_ratio),
            "capacity_level": self.capacity_level,
            "expansion_damping": float(self.expansion_damping),
            "rationale": self.rationale,
        }


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


def compute_bot_capacity_signal(
    metrics_summary: dict,
    schema_path: Path | None = None,
) -> BotCapacitySignal:
    if "bot_utilization_ratio" not in metrics_summary:
        raise ValueError("metrics_summary must include bot_utilization_ratio for bot capacity policy")
    ratio = float(metrics_summary["bot_utilization_ratio"])
    if ratio < 0.0 or ratio > 1.0:
        raise ValueError("metrics_summary.bot_utilization_ratio must be in [0,1]")

    if ratio < 0.50:
        level, damping = "LOW", 0.0
    elif ratio <= 0.80:
        level, damping = "MEDIUM", 0.20
    else:
        level, damping = "HIGH", 0.55

    signal = BotCapacitySignal(
        bot_utilization_ratio=float(round(ratio, 6)),
        capacity_level=level,
        expansion_damping=damping,
        rationale=f"Bot utilization ratio {ratio:.3f} mapped deterministically to {level} with damping {damping:.2f}.",
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "bot_capacity.schema.json"
    _validate_schema(signal.to_dict(), schema_path, "BotCapacitySignal")
    return signal
