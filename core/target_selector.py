# Path: core/target_selector.py
# Purpose: Deterministically select a read-only expansion target from pressure telemetry.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ExpansionTarget:
    target_block: str
    target_recipe: str
    confidence: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "target_block": self.target_block,
            "target_recipe": self.target_recipe,
            "confidence": float(self.confidence),
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


def select_expansion_target(
    pressure_attribution_map: dict,
    production_gap_estimate: dict,
    throughput_stress_index: float,
    phase_completion_ratio: float,
    schema_path: Path | None = None,
) -> ExpansionTarget:
    if type(pressure_attribution_map) is not dict or len(pressure_attribution_map) == 0:
        raise ValueError("pressure_attribution_map must be a non-empty object")
    if type(production_gap_estimate) is not dict or len(production_gap_estimate) == 0:
        raise ValueError("production_gap_estimate must be a non-empty object")

    if not isinstance(throughput_stress_index, (int, float)):
        raise ValueError("throughput_stress_index must be numeric")
    if not isinstance(phase_completion_ratio, (int, float)):
        raise ValueError("phase_completion_ratio must be numeric")

    throughput = min(1.0, max(0.0, float(throughput_stress_index)))
    phase_ratio = min(1.0, max(0.0, float(phase_completion_ratio)))

    block_ranked = []
    for block_id, value in pressure_attribution_map.items():
        if not isinstance(block_id, str) or block_id == "":
            raise ValueError("pressure_attribution_map keys must be non-empty strings")
        if not isinstance(value, (int, float)):
            raise ValueError("pressure_attribution_map values must be numeric")
        score = float(value)
        if score < 0.0 or score > 1.0:
            raise ValueError("pressure_attribution_map values must be in [0,1]")
        block_ranked.append((block_id, score))
    block_ranked.sort(key=lambda item: (-item[1], item[0]))

    recipe_ranked = []
    for recipe_name, value in production_gap_estimate.items():
        if not isinstance(recipe_name, str) or recipe_name == "":
            raise ValueError("production_gap_estimate keys must be non-empty strings")
        if not isinstance(value, int) or value < 0:
            raise ValueError("production_gap_estimate values must be non-negative integers")
        recipe_ranked.append((recipe_name, int(value)))
    recipe_ranked.sort(key=lambda item: (-item[1], item[0]))

    target_block, best_block = block_ranked[0]
    second_block = block_ranked[1][1] if len(block_ranked) > 1 else 0.0
    block_margin = min(1.0, max(0.0, float(best_block - second_block)))

    target_recipe, best_recipe = recipe_ranked[0]
    second_recipe = recipe_ranked[1][1] if len(recipe_ranked) > 1 else 0
    denom = max(1, best_recipe)
    recipe_margin = min(1.0, max(0.0, float(best_recipe - second_recipe) / float(denom)))

    confidence = (
        0.45 * throughput
        + 0.25 * block_margin
        + 0.20 * recipe_margin
        + 0.10 * phase_ratio
    )
    confidence = round(min(1.0, max(0.0, confidence)), 6)

    rationale = (
        f"Selected block '{target_block}' and recipe '{target_recipe}' from highest pressure/gap with "
        f"throughput={throughput:.3f}, block_margin={block_margin:.3f}, recipe_margin={recipe_margin:.3f}, "
        f"phase_completion={phase_ratio:.3f}."
    )

    target = ExpansionTarget(
        target_block=target_block,
        target_recipe=target_recipe,
        confidence=confidence,
        rationale=rationale,
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "expansion_target.schema.json"
    _validate_schema(target.to_dict(), schema_path, "ExpansionTarget")
    return target
