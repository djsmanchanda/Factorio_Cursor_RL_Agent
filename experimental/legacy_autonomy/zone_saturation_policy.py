# Path: experimental/legacy_autonomy/zone_saturation_policy.py
# Purpose: Deterministically map zone fill telemetry to expansion damping.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class ZoneSaturationSignal:
    block_id: str
    fill_ratio: float
    saturation_level: str
    expansion_damping: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "fill_ratio": float(self.fill_ratio),
            "saturation_level": self.saturation_level,
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


def _classify(fill_ratio: float) -> tuple[str, float]:
    if fill_ratio < 0.40:
        return "LOW", 0.0
    if fill_ratio < 0.75:
        return "MEDIUM", 0.25
    return "HIGH", 0.60


def compute_zone_saturation_signal(
    zone_fill_entries: List[dict],
    block_id: str,
    signal_schema_path: Path | None = None,
    zone_fill_schema_path: Path | None = None,
) -> ZoneSaturationSignal:
    if not isinstance(block_id, str) or block_id == "":
        raise ValueError("block_id must be a non-empty string for saturation policy")
    if type(zone_fill_entries) is not list or len(zone_fill_entries) == 0:
        raise ValueError("zone_fill entries must be a non-empty array")

    repo_root = Path(__file__).resolve().parents[2]
    if zone_fill_schema_path is None:
        zone_fill_schema_path = repo_root / "schemas" / "zone_fill.schema.json"
    if signal_schema_path is None:
        signal_schema_path = repo_root / "schemas" / "zone_saturation.schema.json"

    selected = None
    for entry in zone_fill_entries:
        if type(entry) is not dict:
            raise ValueError("zone_fill entries must be objects")
        _validate_schema(entry, zone_fill_schema_path, "ZoneFill")
        if entry.get("block_id") == block_id:
            selected = entry

    if selected is None:
        raise ValueError(f"ZoneFill missing target block: {block_id}")

    fill_ratio = float(selected["fill_ratio"])
    level, damping = _classify(fill_ratio)
    signal = ZoneSaturationSignal(
        block_id=block_id,
        fill_ratio=fill_ratio,
        saturation_level=level,
        expansion_damping=damping,
        rationale=(
            f"Zone fill ratio {fill_ratio:.3f} mapped deterministically to {level} "
            f"with expansion damping {damping:.2f}."
        ),
    )
    _validate_schema(signal.to_dict(), signal_schema_path, "ZoneSaturationSignal")
    return signal
