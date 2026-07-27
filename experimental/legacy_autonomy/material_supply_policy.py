# Path: experimental/legacy_autonomy/material_supply_policy.py
# Purpose: Deterministically derive material supply pressure for advisory confidence shaping.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class MaterialSupplySignal:
    backlog_ratio: float
    supply_level: str
    expansion_damping: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "backlog_ratio": float(self.backlog_ratio),
            "supply_level": self.supply_level,
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


def compute_material_supply_signal(
    progress_state: dict,
    metrics_summary: dict,
    production_gap_estimate: dict,
    schema_path: Path | None = None,
) -> MaterialSupplySignal:
    required_progress = {"committed_capacity", "current_capacity", "active_phase_capacity"}
    missing_progress = sorted(required_progress.difference(progress_state.keys()))
    if missing_progress:
        raise ValueError(f"ProgressState missing required fields for material supply: {missing_progress}")

    if "throughput_stress_index" not in metrics_summary:
        raise ValueError("metrics_summary must include throughput_stress_index for material supply")
    if type(production_gap_estimate) is not dict or len(production_gap_estimate) == 0:
        raise ValueError("production_gap_estimate must be a non-empty object for material supply")

    committed = int(progress_state["committed_capacity"])
    current = int(progress_state["current_capacity"])
    active_phase = int(progress_state["active_phase_capacity"])
    if committed < 0 or current < 0 or active_phase <= 0:
        raise ValueError("ProgressState capacities must be non-negative and active_phase_capacity > 0")

    backlog = committed - current
    backlog_ratio = min(1.0, max(0.0, float(backlog) / float(active_phase)))

    throughput_stress = float(metrics_summary["throughput_stress_index"])
    if throughput_stress < 0.0 or throughput_stress > 1.0:
        raise ValueError("metrics_summary.throughput_stress_index must be in [0,1]")

    dominant_gap = 0
    total_gap = 0
    for recipe_name, gap in production_gap_estimate.items():
        if not isinstance(recipe_name, str) or recipe_name == "":
            raise ValueError("production_gap_estimate keys must be non-empty strings")
        if not isinstance(gap, int) or gap < 0:
            raise ValueError("production_gap_estimate values must be non-negative integers")
        total_gap += int(gap)
        if int(gap) > dominant_gap:
            dominant_gap = int(gap)

    if total_gap <= 0:
        gap_pressure = 0.0
    else:
        gap_pressure = float(dominant_gap) / float(total_gap)

    # Optional bot context if available.
    bot_ratio = metrics_summary.get("bot_utilization_ratio")
    bot_context = 0.0
    if bot_ratio is not None:
        bot_ratio = float(bot_ratio)
        if bot_ratio < 0.0 or bot_ratio > 1.0:
            raise ValueError("metrics_summary.bot_utilization_ratio must be in [0,1] when provided")
        bot_context = bot_ratio

    supply_pressure = (
        0.40 * backlog_ratio
        + 0.35 * throughput_stress
        + 0.20 * gap_pressure
        + 0.05 * bot_context
    )
    supply_pressure = min(1.0, max(0.0, supply_pressure))

    if supply_pressure < 0.25:
        level, damping = "LOW", 0.0
    elif supply_pressure < 0.60:
        level, damping = "MEDIUM", 0.25
    else:
        level, damping = "HIGH", 0.55

    signal = MaterialSupplySignal(
        backlog_ratio=float(round(backlog_ratio, 6)),
        supply_level=level,
        expansion_damping=damping,
        rationale=(
            f"Supply pressure from backlog={backlog_ratio:.3f}, throughput={throughput_stress:.3f}, "
            f"dominant_gap_share={gap_pressure:.3f}, bot_context={bot_context:.3f} -> {level} ({damping:.2f})."
        ),
    )

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "material_supply.schema.json"
    _validate_schema(signal.to_dict(), schema_path, "MaterialSupplySignal")
    return signal
