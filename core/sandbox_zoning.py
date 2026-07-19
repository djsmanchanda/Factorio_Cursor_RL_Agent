# Path: core/sandbox_zoning.py
# Purpose: Deterministically assign planner-sandbox zones per block without layout synthesis.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from jsonschema import Draft7Validator

from core.block_prototypes import placeholder_stride


@dataclass(frozen=True)
class SandboxZone:
    block_id: str
    origin_x: int
    origin_y: int
    stride_x: int
    stride_y: int

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "origin_x": int(self.origin_x),
            "origin_y": int(self.origin_y),
            "stride_x": int(self.stride_x),
            "stride_y": int(self.stride_y),
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


def derive_sandbox_zones(
    block_ids: Iterable[str],
    spacing: int = 64,
    origin_y: int = 0,
    stride_x: int | None = None,
    stride_y: int | None = None,
    schema_path: Path | None = None,
) -> Dict[str, SandboxZone]:
    if spacing <= 0:
        raise ValueError("spacing must be > 0")
    if stride_x is not None and stride_x <= 0:
        raise ValueError("stride_x must be > 0")
    if stride_y is not None and stride_y <= 0:
        raise ValueError("stride_y must be > 0")

    unique = sorted({str(block_id) for block_id in block_ids if str(block_id) != ""})
    if len(unique) == 0:
        return {}

    zones: Dict[str, SandboxZone] = {}
    for index, block_id in enumerate(unique):
        # Stride defaults to the placeholder footprint plus one tile of
        # spacing; a fixed 2x2 stride made 3x3 ghosts overlap, so only the
        # first of each cluster could ever be revived by bots.
        block_stride = placeholder_stride(block_id)
        zone = SandboxZone(
            block_id=block_id,
            origin_x=index * spacing,
            origin_y=origin_y,
            stride_x=stride_x if stride_x is not None else block_stride,
            stride_y=stride_y if stride_y is not None else block_stride,
        )
        zones[block_id] = zone

    if schema_path is None:
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "sandbox_zone.schema.json"
    for zone in zones.values():
        _validate_schema(zone.to_dict(), schema_path, "SandboxZone")

    return zones
