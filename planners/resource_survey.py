# Path: planners/resource_survey.py
# Purpose: Cluster observed resources and allocate deterministic electronics source sites.

from __future__ import annotations

from dataclasses import dataclass
import json
from math import floor
from pathlib import Path
from typing import Iterable, Mapping

from jsonschema import Draft7Validator

from planners.electronics_contracts import ADVANCED_CIRCUIT_RATE, SOLID_STAGE_COUNTS
from planners.recipe_data import FEED_HEADROOM
from planners.electronics_world import ElectronicsWorldSpec

SOLID_RESOURCES = {"iron-ore", "copper-ore", "coal", "stone"}
DRILL_RATE_PER_SECOND = 0.5
BASIC_OIL_PETROLEUM_YIELD = 0.45
SULFUR_PETROLEUM_RATE_PER_SECOND = 0.75
MIN_CRUDE_CAPACITY_PER_SECOND = (
    (ADVANCED_CIRCUIT_RATE * 20 + SULFUR_PETROLEUM_RATE_PER_SECOND)
    / BASIC_OIL_PETROLEUM_YIELD * FEED_HEADROOM
)
_SNAPSHOT_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "snapshot.schema.json"
OFFSHORE_CAPACITY_PER_SECOND = 1200.0
STAGE_ORDER = (
    ("iron_plate", "iron-ore"),
    ("copper_plate_ec", "copper-ore"),
    ("copper_plate_ac", "copper-ore"),
    ("iron_plate_acid", "iron-ore"),
    ("copper_plate_pu", "copper-ore"),
    ("iron_plate_pu", "iron-ore"),
)


@dataclass(frozen=True)
class ResourceCluster:
    resource: str
    tiles: frozenset[tuple[int, int]]
    total_amount: float

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs = [point[0] for point in self.tiles]
        ys = [point[1] for point in self.tiles]
        return min(xs), min(ys), max(xs), max(ys)


def _world(snapshot: Mapping) -> Mapping:
    schema = json.loads(_SNAPSHOT_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        Draft7Validator(schema).iter_errors(dict(snapshot)), key=lambda error: list(error.path)
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"Snapshot validation failed: {detail}")
    if snapshot["surface"] != "planner-sandbox":
        raise ValueError("Resource allocation only accepts the planner-sandbox surface")
    world = snapshot.get("world_observation")
    if not isinstance(world, Mapping) or world.get("version") != "1.0.0":
        raise ValueError("Snapshot needs a version 1.0.0 world_observation")
    bounds = world["bounds"]
    if bounds != {
        "x_min": -250, "y_min": -250,
        "x_max_exclusive": 250, "y_max_exclusive": 250,
    }:
        raise ValueError("Electronics survey requires the bounded 500x500 planner world")
    resource_positions = set()
    for entry in world["resource_tiles"]:
        position = (entry["position"]["x"], entry["position"]["y"])
        if position in resource_positions:
            raise ValueError(f"Duplicate observed resource position {position}")
        resource_positions.add(position)
        if not _position_in_world(bounds, position):
            raise ValueError(f"Observed resource position {position} is outside world bounds")
    water_positions = {(entry["x"], entry["y"]) for entry in world["water_tiles"]}
    if len(water_positions) != len(world["water_tiles"]):
        raise ValueError("Duplicate observed water tile")
    if not all(
        bounds["x_min"] <= x < bounds["x_max_exclusive"]
        and bounds["y_min"] <= y < bounds["y_max_exclusive"]
        for x, y in water_positions
    ):
        raise ValueError("Observed water tile is outside world bounds")
    return world


def _position_in_world(bounds: Mapping, position: tuple[float, float]) -> bool:
    return (
        bounds["x_min"] <= position[0] < bounds["x_max_exclusive"]
        and bounds["y_min"] <= position[1] < bounds["y_max_exclusive"]
    )


def _validate_block_bounds(bounds: Mapping) -> dict:
    if set(bounds) != {"x1", "y1", "x2", "y2"}:
        raise ValueError("block_bounds must contain exactly x1, y1, x2, y2")
    result = {key: float(value) for key, value in bounds.items()}
    if result["x1"] >= result["x2"] or result["y1"] >= result["y2"]:
        raise ValueError("block_bounds must be ordered and non-empty")
    if (result["x1"] < -250 or result["y1"] < -250
            or result["x2"] > 250 or result["y2"] > 250):
        raise ValueError("block_bounds must stay inside the observed world")
    if result["x2"] - result["x1"] > 512 or result["y2"] - result["y1"] > 512:
        raise ValueError("A local macro block may not exceed 512x512 tiles")
    return result

def _tile_coordinate(position: Mapping) -> tuple[int, int]:
    x, y = float(position["x"]), float(position["y"])
    if x != floor(x) + 0.5 or y != floor(y) + 0.5:
        raise ValueError(f"Solid resource position {(x, y)} is not tile-centred")
    return floor(x), floor(y)


def snapshot_from_world_spec(payload: Mapping, *, tick: int = 0) -> dict:
    """Materialize the declared world as an offline observation for contract checks."""
    from planners.world_generation import validate_world_payload

    validate_world_payload(payload)
    resources = []
    for patch in payload["resource_patches"]:
        for y in range(patch["y1"], patch["y2"] + 1):
            for x in range(patch["x1"], patch["x2"] + 1):
                resources.append({
                    "resource": patch["resource"],
                    "position": {"x": x + 0.5, "y": y + 0.5},
                    "amount": patch["amount_per_tile"],
                })
    resources.extend({
        "resource": "crude-oil",
        "position": {"x": spot["position"][0], "y": spot["position"][1]},
        "amount": spot["amount"],
    } for spot in payload["crude_oil_spots"])
    lake = payload["water_lake"]
    water_tiles = [
        {"x": x, "y": y}
        for y in range(lake["y1"], lake["y2"] + 1)
        for x in range(lake["x1"], lake["x2"] + 1)
    ]
    resources.sort(key=lambda entry: (
        entry["resource"], entry["position"]["y"], entry["position"]["x"],
    ))
    return {
        "tick": tick,
        "surface": payload["surface"],
        "entities": [],
        "world_observation": {
            "version": "1.0.0",
            "seed": payload["seed"],
            "bounds": {
                "x_min": payload["bounds"]["x_min"],
                "y_min": payload["bounds"]["y_min"],
                "x_max_exclusive": payload["bounds"]["x_max_exclusive"],
                "y_max_exclusive": payload["bounds"]["y_max_exclusive"],
            },
            "resource_tiles": resources,
            "water_tiles": water_tiles,
        },
    }

def cluster_resource_tiles(snapshot: Mapping) -> tuple[ResourceCluster, ...]:
    world = _world(snapshot)
    amounts: dict[str, dict[tuple[int, int], float]] = {}
    for entry in world["resource_tiles"]:
        resource = entry["resource"]
        if resource not in SOLID_RESOURCES:
            continue
        tile = _tile_coordinate(entry["position"])
        bucket = amounts.setdefault(resource, {})
        if tile in bucket:
            raise ValueError(f"Duplicate observed {resource} tile {tile}")
        bucket[tile] = float(entry["amount"])

    clusters: list[ResourceCluster] = []
    for resource in sorted(amounts):
        remaining = set(amounts[resource])
        while remaining:
            seed = min(remaining, key=lambda point: (point[1], point[0]))
            pending, connected = [seed], set()
            remaining.remove(seed)
            while pending:
                tile = pending.pop()
                connected.add(tile)
                x, y = tile
                neighbours = {(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}
                for neighbour in sorted(neighbours & remaining):
                    remaining.remove(neighbour)
                    pending.append(neighbour)
            clusters.append(ResourceCluster(
                resource, frozenset(connected),
                sum(amounts[resource][tile] for tile in connected),
            ))
    return tuple(sorted(clusters, key=lambda cluster: (
        cluster.resource, cluster.bounds[1], cluster.bounds[0], cluster.bounds,
    )))


def _contains_drill(cluster: ResourceCluster, centre: tuple[float, float]) -> bool:
    left, top = floor(centre[0] - 1.5), floor(centre[1] - 1.5)
    required = {
        (x, y)
        for x in range(left, left + 3)
        for y in range(top, top + 3)
    }
    return required <= cluster.tiles


def _drill_row(cluster: ResourceCluster, count: int) -> tuple[tuple[float, float], ...]:
    x1, y1, x2, y2 = cluster.bounds
    centre_y = (y1 + y2) / 2 - 1.5
    candidates = tuple((x1 + 1.5 + index * 3, centre_y) for index in range(count))
    if not candidates or candidates[-1][0] + 1.5 > x2 + 1:
        raise ValueError(f"{cluster.resource} patch {cluster.bounds} lacks room for {count} drills")
    if not all(_contains_drill(cluster, centre) for centre in candidates):
        raise ValueError(
            f"{cluster.resource} patch {cluster.bounds} has no complete deterministic drill row"
        )
    return candidates


def _cluster_lookup(clusters: Iterable[ResourceCluster]) -> dict[str, list[ResourceCluster]]:
    result: dict[str, list[ResourceCluster]] = {}
    for cluster in clusters:
        result.setdefault(cluster.resource, []).append(cluster)
    for resource in result:
        result[resource].sort(key=lambda cluster: (cluster.bounds[1], cluster.bounds[0]))
    return result


def _inside(bounds: Mapping, position: tuple[float, float], half_size: float) -> bool:
    return (
        position[0] - half_size >= bounds["x1"]
        and position[1] - half_size >= bounds["y1"]
        and position[0] + half_size <= bounds["x2"]
        and position[1] + half_size <= bounds["y2"]
    )


def _require_local(
    bounds: Mapping, positions: Iterable[tuple[float, float]], half_size: float, label: str,
) -> None:
    if not all(_inside(bounds, position, half_size) for position in positions):
        raise ValueError(
            f"{label} lies outside the declared local block; CityPlanner rail interface required"
        )


def _offshore_candidates(water_tiles: set[tuple[int, int]]) -> list[dict]:
    direction_specs = (
        ("north", (0, -1), (0.5, -0.5), (0, -3)),
        ("east", (1, 0), (1.5, 0.5), (3, 0)),
        ("south", (0, 1), (0.5, 1.5), (0, 3)),
        ("west", (-1, 0), (-0.5, 0.5), (-3, 0)),
    )
    candidates = []
    for x, y in sorted(water_tiles, key=lambda point: (point[1], point[0])):
        for priority, (direction, neighbour, centre, output) in enumerate(direction_specs):
            if (x + neighbour[0], y + neighbour[1]) in water_tiles:
                continue
            candidates.append({
                "priority": priority,
                "position": (x + centre[0], y + centre[1]),
                "output": (x + output[0], y + output[1]),
                "direction": direction,
            })
    centre_x = sum(x + 0.5 for x, _ in water_tiles) / len(water_tiles)
    centre_y = sum(y + 0.5 for _, y in water_tiles) / len(water_tiles)
    return sorted(candidates, key=lambda site: (
        site["priority"],
        abs(site["position"][0] - centre_x) + abs(site["position"][1] - centre_y),
        site["position"][1], site["position"][0],
    ))


def allocate_electronics_world(
    snapshot: Mapping,
    *,
    block_bounds: Mapping,
    validate_bundle: bool = True,
) -> dict:
    world = _world(snapshot)
    local_bounds = _validate_block_bounds(block_bounds)
    clusters = cluster_resource_tiles(snapshot)
    by_resource = _cluster_lookup(clusters)
    required_counts = {"iron-ore": 3, "copper-ore": 3, "coal": 1}
    for resource, count in required_counts.items():
        if len(by_resource.get(resource, [])) < count:
            raise ValueError(f"Survey found {len(by_resource.get(resource, []))} {resource} patches; needs {count}")

    available = {
        resource: list(resource_clusters)
        for resource, resource_clusters in by_resource.items()
    }
    ore_lines = []
    for stage, resource in STAGE_ORDER:
        count = SOLID_STAGE_COUNTS[stage]
        choice = None
        for cluster in available[resource]:
            try:
                drills = _drill_row(cluster, count)
            except ValueError:
                continue
            choice = (cluster, drills)
            break
        if choice is None:
            raise ValueError(f"Survey has no unused {resource} patch capable of {count} drills")
        cluster, drills = choice
        available[resource].remove(cluster)
        _require_local(local_bounds, drills, 1.5, f"{stage} mining site")
        patch_id = _cluster_id(cluster, by_resource[resource])
        ore_lines.append({
            "stage": stage,
            "resource": resource,
            "patch_id": patch_id,
            "drill_positions": [list(position) for position in drills],
            "capacity_per_second": count * DRILL_RATE_PER_SECOND,
        })

    coal = by_resource["coal"][0]
    coal_drills = _coal_row(coal, SOLID_STAGE_COUNTS.get("coal", 3))
    _require_local(local_bounds, coal_drills, 1.5, "coal mining site")
    coal_id = _cluster_id(coal, by_resource["coal"])

    oil = _oil_site(world, local_bounds)
    water = _water_site(world, local_bounds)
    ore_patches = [
        _patch_payload(cluster, _cluster_id(cluster, by_resource[cluster.resource]))
        for cluster in clusters if cluster.resource in {"iron-ore", "copper-ore", "coal"}
    ]
    payload = {
        "version": "1.0.0",
        "surface": snapshot["surface"],
        "survey_tick": snapshot["tick"],
        "map_bounds": {"x1": -250, "y1": -250, "x2": 250, "y2": 250},
        "ore_patches": ore_patches,
        "ore_lines": ore_lines,
        "coal_patch_id": coal_id,
        "coal_drill_positions": [list(position) for position in coal_drills],
        "coal_output_y": coal_drills[0][1] + 2,
        "coal_output_x": coal.bounds[2] + 10.5,
        "coal_capacity_per_second": len(coal_drills) * DRILL_RATE_PER_SECOND,
        "pumpjack_sites": [oil],
        "crude_pipe_tiles": [oil["output"]],
        "offshore_pump_sites": [water],
        "water_pipe_tiles": [water["output"]],
        "transport_scope": "local-block",
        "block_bounds": local_bounds,
    }
    spec = ElectronicsWorldSpec.from_payload(payload)
    if validate_bundle:
        from planners.electronics_block import build_electronics_block
        build_electronics_block(include_processing=True, world=spec)
    return payload


def _cluster_id(cluster: ResourceCluster, peers: list[ResourceCluster]) -> str:
    return f"survey-{cluster.resource}-{peers.index(cluster) + 1:02d}"


def _patch_payload(cluster: ResourceCluster, patch_id: str) -> dict:
    x1, y1, x2, y2 = cluster.bounds
    return {"id": patch_id, "item": cluster.resource, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _coal_row(cluster: ResourceCluster, count: int) -> tuple[tuple[float, float], ...]:
    x1, y1, _, y2 = cluster.bounds
    centre_y = (y1 + y2) / 2 - 0.5
    row = tuple((x1 + 2.5 + index * 3, centre_y) for index in range(count))
    if not all(_contains_drill(cluster, centre) for centre in row):
        raise ValueError("Coal patch lacks a complete deterministic drill row")
    return row


def _oil_site(world: Mapping, bounds: Mapping) -> dict:
    spots = sorted(
        (entry for entry in world["resource_tiles"] if entry["resource"] == "crude-oil"),
        key=lambda entry: (entry["position"]["y"], entry["position"]["x"]),
    )
    if not spots:
        raise ValueError("Survey found no crude-oil spots")
    local_spots = []
    for spot in spots:
        position = (float(spot["position"]["x"]), float(spot["position"]["y"]))
        output = (floor(position[0] - 1), floor(position[1] - 2))
        if _inside(bounds, position, 1.5) and _inside(bounds, output, 0.5):
            capacity = min(1000.0, float(spot["amount"]) / 3000.0)
            local_spots.append((capacity, position, output))
    if not local_spots:
        raise ValueError(
            "Crude-oil lies outside the declared local block; CityPlanner rail interface required"
        )
    capacity, position, output = sorted(
        local_spots, key=lambda candidate: (
            -candidate[0], candidate[1][1], candidate[1][0],
        )
    )[0]
    if capacity + 1e-9 < MIN_CRUDE_CAPACITY_PER_SECOND:
        raise ValueError(
            f"Observed crude-oil capacity {capacity:.6g}/s is below required "
            f"{MIN_CRUDE_CAPACITY_PER_SECOND:.6g}/s with headroom"
        )
    return {
        "position": list(position), "output": list(output), "resource": "crude-oil",
        "capacity_per_second": capacity, "direction": "north",
    }


def _shore_geometry_is_valid(candidate: Mapping, water_tiles: set[tuple[int, int]]) -> bool:
    position = candidate["position"]
    output = candidate["output"]
    direction = candidate["direction"]
    land = (floor(position[0]), floor(position[1]))
    output_tile = (floor(output[0]), floor(output[1]))
    if direction == "north":
        intake = (land[0], land[1] + 1)
        path = {(land[0], y) for y in range(output_tile[1], land[1] + 1)}
    elif direction == "east":
        intake = (land[0] - 1, land[1])
        path = {(x, land[1]) for x in range(land[0], output_tile[0] + 1)}
    elif direction == "south":
        intake = (land[0], land[1] - 1)
        path = {(land[0], y) for y in range(land[1], output_tile[1] + 1)}
    else:
        intake = (land[0] + 1, land[1])
        path = {(x, land[1]) for x in range(output_tile[0], land[0] + 1)}
    if intake not in water_tiles or path & water_tiles:
        return False
    if direction in {"north", "south"}:
        lateral = ((-1, 0), (1, 0))
    else:
        lateral = ((0, -1), (0, 1))
    return all(
        (intake[0] + dx, intake[1] + dy) in water_tiles
        and (land[0] + dx, land[1] + dy) not in water_tiles
        for dx, dy in lateral
    )

def _water_site(world: Mapping, bounds: Mapping) -> dict:
    water_tiles = {(entry["x"], entry["y"]) for entry in world["water_tiles"]}
    if not water_tiles:
        raise ValueError("Survey found no water tiles")
    candidates = _offshore_candidates(water_tiles)
    for candidate in candidates:
        position, output = candidate["position"], candidate["output"]
        if (_shore_geometry_is_valid(candidate, water_tiles)
                and _inside(bounds, position, 1.0)
                and _inside(bounds, output, 0.5)):
            return {
                "position": list(position), "output": list(output), "resource": "water",
                "capacity_per_second": OFFSHORE_CAPACITY_PER_SECOND,
                "direction": candidate["direction"],
            }
    raise ValueError("Survey found no valid in-block lake shoreline for an offshore pump")
