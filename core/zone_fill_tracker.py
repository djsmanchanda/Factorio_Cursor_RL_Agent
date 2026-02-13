# Path: core/zone_fill_tracker.py
# Purpose: Deterministically compute per-block zone fill telemetry from projected ghosts.

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from jsonschema import Draft7Validator


ZONE_WIDTH = 64
ZONE_HEIGHT = 64


@dataclass(frozen=True)
class ZoneFill:
    block_id: str
    zone_capacity_estimate: int
    ghosts_present: int
    fill_ratio: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "zone_capacity_estimate": int(self.zone_capacity_estimate),
            "ghosts_present": int(self.ghosts_present),
            "fill_ratio": float(self.fill_ratio),
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


def derive_zone_fill(
    zones: Dict[str, object],
    ghosts_present_by_block: Dict[str, int],
    zone_width: int = ZONE_WIDTH,
    zone_height: int = ZONE_HEIGHT,
    schema_path: Path | None = None,
) -> List[ZoneFill]:
    if zone_width <= 0 or zone_height <= 0:
        raise ValueError("zone_width and zone_height must be > 0")

    if type(zones) is not dict:
        raise ValueError("zones must be an object")
    if type(ghosts_present_by_block) is not dict:
        raise ValueError("ghosts_present_by_block must be an object")

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "zone_fill.schema.json"

    fills: List[ZoneFill] = []
    for block_id in sorted(zones.keys()):
        zone = zones[block_id]
        stride_x = int(zone.stride_x)
        stride_y = int(zone.stride_y)
        if stride_x <= 0 or stride_y <= 0:
            raise ValueError("zone strides must be > 0 for zone fill")

        capacity_cols = int(math.floor(float(zone_width) / float(stride_x)))
        capacity_rows = int(math.floor(float(zone_height) / float(stride_y)))
        zone_capacity_estimate = max(0, capacity_cols * capacity_rows)

        ghosts_present = int(ghosts_present_by_block.get(block_id, 0))
        if ghosts_present < 0:
            raise ValueError("ghosts_present must be >= 0 for zone fill")

        if zone_capacity_estimate <= 0:
            fill_ratio = 1.0 if ghosts_present > 0 else 0.0
        else:
            fill_ratio = min(1.0, max(0.0, float(ghosts_present) / float(zone_capacity_estimate)))

        zone_fill = ZoneFill(
            block_id=str(block_id),
            zone_capacity_estimate=int(zone_capacity_estimate),
            ghosts_present=int(ghosts_present),
            fill_ratio=float(round(fill_ratio, 6)),
            rationale=(
                f"Zone {zone_width}x{zone_height} with stride {stride_x}x{stride_y}; "
                f"fill from projected ghosts for block '{block_id}'."
            ),
        )
        _validate_schema(zone_fill.to_dict(), schema_path, "ZoneFill")
        fills.append(zone_fill)

    return fills
