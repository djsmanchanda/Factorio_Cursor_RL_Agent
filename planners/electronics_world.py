# Path: planners/electronics_world.py
# Purpose: Schema-load and validate surveyed resource coordinates for the electronics block.

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft7Validator

WORLD_SPEC_VERSION = "1.0.0"
_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "electronics_world_spec.schema.json"
_STAGE_RESOURCES = {
    "iron_plate": "iron-ore",
    "copper_plate_ec": "copper-ore",
    "copper_plate_ac": "copper-ore",
    "iron_plate_acid": "iron-ore",
    "copper_plate_pu": "copper-ore",
    "iron_plate_pu": "iron-ore",
}


def _schema_errors(payload: Mapping) -> list:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    return sorted(Draft7Validator(schema).iter_errors(dict(payload)), key=lambda e: list(e.path))


def _positions(values) -> tuple[tuple[float, float], ...]:
    return tuple(tuple(position) for position in values)


@dataclass(frozen=True, init=False)
class ElectronicsWorldSpec:
    version: str
    surface: str
    survey_tick: int
    map_bounds: dict
    ore_patches: tuple[dict, ...]
    ore_lines: tuple[dict, ...]
    coal_patch_id: str
    coal_drill_positions: tuple[tuple[float, float], ...]
    coal_output_y: float
    coal_output_x: float
    coal_capacity_per_second: float
    pumpjack_sites: tuple[dict, ...]
    crude_pipe_tiles: tuple[tuple[float, float], ...]
    offshore_pump_sites: tuple[dict, ...]
    water_pipe_tiles: tuple[tuple[float, float], ...]

    @classmethod
    def from_payload(cls, payload: Mapping) -> "ElectronicsWorldSpec":
        errors = _schema_errors(payload)
        if errors:
            details = "; ".join(
                f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
                for error in errors
            )
            raise ValueError(f"ElectronicsWorldSpec validation failed: {details}")
        normalized = dict(payload)
        normalized["map_bounds"] = dict(normalized["map_bounds"])
        normalized["ore_patches"] = tuple(dict(patch) for patch in normalized["ore_patches"])
        normalized["ore_lines"] = tuple(
            {**line, "drill_positions": _positions(line["drill_positions"])}
            for line in normalized["ore_lines"]
        )
        normalized["coal_drill_positions"] = _positions(normalized["coal_drill_positions"])
        normalized["pumpjack_sites"] = tuple(
            {**site, "position": tuple(site["position"]), "output": tuple(site["output"])}
            for site in normalized["pumpjack_sites"]
        )
        normalized["crude_pipe_tiles"] = _positions(normalized["crude_pipe_tiles"])
        normalized["offshore_pump_sites"] = tuple(
            {**site, "position": tuple(site["position"]), "output": tuple(site["output"])}
            for site in normalized["offshore_pump_sites"]
        )
        normalized["water_pipe_tiles"] = _positions(normalized["water_pipe_tiles"])
        instance = object.__new__(cls)
        for field, value in normalized.items():
            object.__setattr__(instance, field, value)
        instance._validate_survey()
        return instance

    def _validate_survey(self) -> None:
        if self.version != WORLD_SPEC_VERSION:
            raise ValueError(f"Unsupported electronics world-spec version: {self.version}")
        patches = {patch["id"]: patch for patch in self.ore_patches}
        if len(patches) != len(self.ore_patches):
            raise ValueError("Ore patch ids must be unique")
        for patch in self.ore_patches:
            if patch["x1"] > patch["x2"] or patch["y1"] > patch["y2"]:
                raise ValueError(f"Surveyed patch {patch['id']} has inverted bounds")
        lines = {line["stage"]: line for line in self.ore_lines}
        if set(lines) != set(_STAGE_RESOURCES):
            raise ValueError(
                "Surveyed ore lines must exactly declare ultimate block stages: "
                + ", ".join(sorted(_STAGE_RESOURCES))
            )
        all_drills: list[tuple[float, float]] = []
        for stage, expected_resource in _STAGE_RESOURCES.items():
            line = lines[stage]
            if line["resource"] != expected_resource:
                raise ValueError(f"Stage {stage} must survey {expected_resource}")
            self._validate_patch_reference(
                line["patch_id"], expected_resource, line["drill_positions"], patches,
            )
            all_drills.extend(line["drill_positions"])
        self._validate_patch_reference(
            self.coal_patch_id, "coal", self.coal_drill_positions, patches,
        )
        all_drills.extend(self.coal_drill_positions)
        if len(all_drills) != len(set(all_drills)):
            raise ValueError("Every surveyed mining drill position must be unique")
        self._validate_fluid_sites("crude-oil", self.pumpjack_sites, self.crude_pipe_tiles)
        self._validate_fluid_sites("water", self.offshore_pump_sites, self.water_pipe_tiles)

    @staticmethod
    def _validate_patch_reference(patch_id, resource, positions, patches) -> None:
        patch = patches.get(patch_id)
        if patch is None or patch["item"] != resource:
            raise ValueError(f"{resource} drills reference no matching surveyed patch: {patch_id}")
        for x, y in positions:
            if not (patch["x1"] <= x <= patch["x2"] and patch["y1"] <= y <= patch["y2"]):
                raise ValueError(f"{resource} drill {(x, y)} lies outside surveyed patch {patch_id}")

    @staticmethod
    def _validate_fluid_sites(resource: str, sites, pipe_tiles) -> None:
        outputs = {site["output"] for site in sites}
        if any(site["resource"] != resource for site in sites) or not outputs <= set(pipe_tiles):
            raise ValueError(f"{resource} survey sites or output pipe coordinates are inconsistent")

    def ore_origin(self, stage: str, resource: str, machine_count: int) -> tuple[int, int]:
        line = self.ore_line(stage, resource, machine_count)
        first_x, first_y = line["drill_positions"][0]
        origin = (first_x - 1.5, first_y + 1.5)
        if any(value != int(value) for value in origin):
            raise ValueError(f"Stage {stage} drill row does not derive an integer layout origin")
        return tuple(map(int, origin))

    def ore_line(self, stage: str, resource: str, machine_count: int) -> dict:
        line = next((entry for entry in self.ore_lines if entry["stage"] == stage), None)
        if line is None or line["resource"] != resource:
            raise ValueError(f"Missing surveyed {resource} drill row for stage {stage}")
        if len(line["drill_positions"]) != machine_count:
            raise ValueError(
                f"Stage {stage} needs {machine_count} surveyed drills, "
                f"got {len(line['drill_positions'])}"
            )
        return line

    def ore_capacity(self, stage: str) -> float:
        return float(next(line for line in self.ore_lines if line["stage"] == stage)["capacity_per_second"])

    @property
    def crude_capacity_per_second(self) -> float:
        return sum(float(site["capacity_per_second"]) for site in self.pumpjack_sites)

    @property
    def water_capacity_per_second(self) -> float:
        return sum(float(site["capacity_per_second"]) for site in self.offshore_pump_sites)


def load_electronics_world_spec(path: Path) -> ElectronicsWorldSpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ElectronicsWorldSpec.from_payload(payload)