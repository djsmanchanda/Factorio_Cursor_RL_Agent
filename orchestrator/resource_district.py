# Path: orchestrator/resource_district.py
# Purpose: Persist and reconcile one expansion-bounded mine/refinery district.

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from planners.belt_bridge import RouteFailure, plan_belt_route
from planners.plan_validation import (
    entity_footprint_tiles,
    occupied_tile_indices,
    validate_no_collisions,
)
from planners.smelter_block import (
    REFINERY_CAPACITY_SCHEDULES,
    generate_managed_refinery_plan,
    refinery_interfaces,
)
from planners.transport_occupancy import (
    Occupant,
    OccupantIdentity,
    RouteOccupancy,
)

Tile = tuple[int, int]
Point = tuple[float, float]


class DistrictStateError(ValueError):
    """Persisted district identity or canonical geometry cannot be trusted."""


@dataclass(frozen=True, order=True)
class OwnedPlacement:
    """An exact action identity owned by one persisted district revision."""

    action_id: str
    entity: str
    position: Point
    direction: str | None
    status: str
    revision: int
    underground_type: str | None = None
    input_priority: str | None = None
    output_priority: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.entity:
            raise DistrictStateError("Owned placement identity must be non-empty")
        if not all(math.isfinite(value) for value in self.position):
            raise DistrictStateError("Owned placement position must be finite")
        if self.direction not in {None, "north", "east", "south", "west"}:
            raise DistrictStateError("Owned placement direction is invalid")
        if self.status not in {"prepared", "ghost", "built"}:
            raise DistrictStateError(
                "Owned placement status must be prepared, ghost, or built"
            )
        if self.revision < 1:
            raise DistrictStateError("Owned placement revision must be positive")
        if self.underground_type not in {None, "input", "output"}:
            raise DistrictStateError("Owned placement underground type is invalid")
        if self.underground_type is not None and not self.entity.endswith(
            "underground-belt"
        ):
            raise DistrictStateError(
                "Only underground-belt ownership may declare underground type"
            )
        for label, priority in {
            "input": self.input_priority,
            "output": self.output_priority,
        }.items():
            if priority not in {None, "left", "none", "right"}:
                raise DistrictStateError(
                    f"Owned placement {label} priority is invalid"
                )


@dataclass(frozen=True)
class CollectorBand:
    """One paired drill row around a shared collector belt."""

    order: int
    belt_y: float
    column_xs: tuple[float, ...]
    built_column_xs: tuple[float, ...] = ()
    pending_column_xs: tuple[float, ...] = ()
    planned_column_xs: tuple[float, ...] = ()


@dataclass(frozen=True)
class ManagedRefinery:
    """The single refinery site and its maximum generation footprint."""

    site_id: str
    origin: Point
    variant: str
    generation: int
    maximum_furnaces: int
    maximum_footprint_tiles: frozenset[Tile]
    current_furnaces: int = 0
    current_footprint_tiles: frozenset[Tile] = frozenset()
    ore_inputs: tuple[Point, ...] = ()
    ore_input_headings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransportPath:
    """Exact graph-selected belt geometry for one district connection."""

    path_id: str
    role: str
    lane_id: str
    source_band_order: int | None
    source: Point
    destination: Point
    source_heading: str
    destination_heading: str
    action_ids: tuple[str, ...]
    route_tiles: tuple[Tile, ...]
    reused_tiles: frozenset[Tile]
    underground_spans: tuple[tuple[Tile, Tile], ...]
    underground_reach: int

    def __post_init__(self) -> None:
        if not self.path_id or not self.lane_id:
            raise DistrictStateError("Transport path identity must be non-empty")
        if self.role not in {"collector", "manifold", "haul"}:
            raise DistrictStateError(f"Unknown transport path role {self.role!r}")
        if self.source_heading not in {"north", "east", "south", "west"}:
            raise DistrictStateError("Transport path source heading is invalid")
        if self.destination_heading not in {"north", "east", "south", "west"}:
            raise DistrictStateError("Transport path destination heading is invalid")
        if not all(math.isfinite(value) for value in (*self.source, *self.destination)):
            raise DistrictStateError("Transport path endpoints must be finite")
        if self.underground_reach <= 0:
            raise DistrictStateError("Transport path reach must be positive")
        for start, end in self.underground_spans:
            span = abs(end[0] - start[0]) + abs(end[1] - start[1])
            if span <= 0 or span > self.underground_reach:
                raise DistrictStateError(
                    f"Transport path {self.path_id} has an invalid underground span"
                )


@dataclass(frozen=True)
class TransportLane:
    """A capacity-bounded route from one or more bands to the refinery."""

    lane_id: str
    source_band_orders: tuple[int, ...]
    declared_items_per_second: float
    capacity_items_per_second: float
    belt_tier: str
    splitter_action_ids: tuple[str, ...] = ()
    refinery_input: Point | None = None
    path_ids: tuple[str, ...] = ()
    measured_items_per_second: float | None = None


@dataclass(frozen=True)
class ResourceDistrictState:
    """Versioned canonical state for one persistent resource system."""

    district_id: str
    episode_id: str
    surface: str
    force: str
    ore: str
    recipe: str
    root: Point
    output: Point
    expansion_direction: str
    parallel_band_pitch: float
    collector_bands: tuple[CollectorBand, ...]
    reservations: Mapping[str, frozenset[Tile]]
    refinery: ManagedRefinery
    transport_lanes: tuple[TransportLane, ...]
    transport_paths: tuple[TransportPath, ...]
    belt_tier: str
    belt_capacity_items_per_second: float
    drill_items_per_second: float
    maximum_drills: int
    revision: int = 0
    pending_revision: int | None = None
    pending_plan_id: str | None = None
    owned_placements: tuple[OwnedPlacement, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise DistrictStateError(f"Unsupported district version {self.version!r}")
        for label, value in {
            "district_id": self.district_id,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "force": self.force,
            "ore": self.ore,
            "recipe": self.recipe,
            "belt_tier": self.belt_tier,
        }.items():
            if not isinstance(value, str) or not value:
                raise DistrictStateError(f"{label} must be non-empty")
        if self.expansion_direction not in {"east", "west"}:
            raise DistrictStateError("Expansion direction must be east or west")
        for label, value in {
            "parallel band pitch": self.parallel_band_pitch,
            "belt capacity": self.belt_capacity_items_per_second,
            "drill rate": self.drill_items_per_second,
        }.items():
            if not math.isfinite(value) or value <= 0:
                raise DistrictStateError(f"{label} must be finite and positive")
        if not all(math.isfinite(value) for value in (*self.root, *self.output)):
            raise DistrictStateError("District root and output must be finite")
        if self.maximum_drills <= 0 or self.maximum_drills % 2:
            raise DistrictStateError("maximum_drills must be positive and even")
        if self.revision < 0:
            raise DistrictStateError("District revision cannot be negative")
        if self.pending_revision is not None and self.pending_revision <= self.revision:
            raise DistrictStateError("Pending revision must be newer than committed revision")
        action_ids = [placement.action_id for placement in self.owned_placements]
        if len(action_ids) != len(set(action_ids)):
            raise DistrictStateError("Owned placement action IDs must be unique")
        path_ids = [path.path_id for path in self.transport_paths]
        if len(path_ids) != len(set(path_ids)):
            raise DistrictStateError("Transport path IDs must be unique")
        known_paths = set(path_ids)
        for lane in self.transport_lanes:
            for label, value in {
                "declared lane rate": lane.declared_items_per_second,
                "lane capacity": lane.capacity_items_per_second,
            }.items():
                if not math.isfinite(value) or value < 0:
                    raise DistrictStateError(f"{label} must be finite and non-negative")
            if lane.capacity_items_per_second <= 0:
                raise DistrictStateError("lane capacity must be positive")
            if lane.declared_items_per_second > lane.capacity_items_per_second + 1e-9:
                raise DistrictStateError(
                    f"Transport lane {lane.lane_id} exceeds its belt capacity"
                )
            if lane.measured_items_per_second is not None and (
                not math.isfinite(lane.measured_items_per_second)
                or lane.measured_items_per_second < 0
            ):
                raise DistrictStateError(
                    "measured lane rate must be finite and non-negative"
                )
            if not set(lane.path_ids) <= known_paths:
                raise DistrictStateError(
                    f"Transport lane {lane.lane_id} references an unknown path"
                )
        if self.refinery.current_furnaces < 0:
            raise DistrictStateError("Current refinery furnace count cannot be negative")
        if self.refinery.current_furnaces > self.refinery.maximum_furnaces:
            raise DistrictStateError("Current refinery exceeds its reserved generation")
        if len(self.refinery.ore_inputs) != len(self.refinery.ore_input_headings):
            raise DistrictStateError("Refinery input positions/headings disagree")

    def to_json(self) -> str:
        """Return stable JSON suitable for an episode reservation journal."""
        payload = {
            "version": self.version,
            "district_id": self.district_id,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "force": self.force,
            "ore": self.ore,
            "recipe": self.recipe,
            "root": list(self.root),
            "output": list(self.output),
            "expansion_direction": self.expansion_direction,
            "parallel_band_pitch": self.parallel_band_pitch,
            "collector_bands": [
                {
                    "order": band.order,
                    "belt_y": band.belt_y,
                    "column_xs": list(band.column_xs),
                    "built_column_xs": list(band.built_column_xs),
                    "pending_column_xs": list(band.pending_column_xs),
                    "planned_column_xs": list(band.planned_column_xs),
                }
                for band in self.collector_bands
            ],
            "reservations": {
                name: [list(tile) for tile in sorted(tiles)]
                for name, tiles in sorted(self.reservations.items())
            },
            "refinery": {
                "site_id": self.refinery.site_id,
                "origin": list(self.refinery.origin),
                "variant": self.refinery.variant,
                "generation": self.refinery.generation,
                "maximum_furnaces": self.refinery.maximum_furnaces,
                "current_furnaces": self.refinery.current_furnaces,
                "current_footprint_tiles": [
                    list(tile) for tile in sorted(self.refinery.current_footprint_tiles)
                ],
                "ore_inputs": [list(point) for point in self.refinery.ore_inputs],
                "ore_input_headings": list(self.refinery.ore_input_headings),
                "maximum_footprint_tiles": [
                    list(tile)
                    for tile in sorted(self.refinery.maximum_footprint_tiles)
                ],
            },
            "transport_lanes": [
                {
                    "lane_id": lane.lane_id,
                    "source_band_orders": list(lane.source_band_orders),
                    "declared_items_per_second": lane.declared_items_per_second,
                    "capacity_items_per_second": lane.capacity_items_per_second,
                    "belt_tier": lane.belt_tier,
                    "splitter_action_ids": list(lane.splitter_action_ids),
                    "refinery_input": (
                        list(lane.refinery_input)
                        if lane.refinery_input is not None else None
                    ),
                    "path_ids": list(lane.path_ids),
                    "measured_items_per_second": lane.measured_items_per_second,
                }
                for lane in self.transport_lanes
            ],
            "transport_paths": [
                {
                    "path_id": path.path_id,
                    "role": path.role,
                    "lane_id": path.lane_id,
                    "source_band_order": path.source_band_order,
                    "source": list(path.source),
                    "destination": list(path.destination),
                    "source_heading": path.source_heading,
                    "destination_heading": path.destination_heading,
                    "action_ids": list(path.action_ids),
                    "route_tiles": [list(tile) for tile in path.route_tiles],
                    "reused_tiles": [list(tile) for tile in sorted(path.reused_tiles)],
                    "underground_spans": [
                        [list(start), list(end)]
                        for start, end in path.underground_spans
                    ],
                    "underground_reach": path.underground_reach,
                }
                for path in self.transport_paths
            ],
            "belt_tier": self.belt_tier,
            "belt_capacity_items_per_second": self.belt_capacity_items_per_second,
            "drill_items_per_second": self.drill_items_per_second,
            "maximum_drills": self.maximum_drills,
            "revision": self.revision,
            "pending_revision": self.pending_revision,
            "pending_plan_id": self.pending_plan_id,
            "owned_placements": [
                {
                    "action_id": placement.action_id,
                    "entity": placement.entity,
                    "position": list(placement.position),
                    "direction": placement.direction,
                    "status": placement.status,
                    "revision": placement.revision,
                    "underground_type": placement.underground_type,
                    "input_priority": placement.input_priority,
                    "output_priority": placement.output_priority,
                }
                for placement in self.owned_placements
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, encoded: str) -> ResourceDistrictState:
        """Decode a supported journal entry without relaxing its version."""
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as error:
            raise DistrictStateError("Invalid persisted district JSON") from error
        if payload.get("version") != 1:
            raise DistrictStateError(
                f"Unsupported persisted district version {payload.get('version')!r}"
            )
        try:
            bands = tuple(
                CollectorBand(
                    order=int(item["order"]),
                    belt_y=float(item["belt_y"]),
                    column_xs=_point_axis(item["column_xs"]),
                    built_column_xs=_point_axis(item["built_column_xs"]),
                    pending_column_xs=_point_axis(item["pending_column_xs"]),
                    planned_column_xs=_point_axis(item["planned_column_xs"]),
                )
                for item in payload["collector_bands"]
            )
            refinery_payload = payload["refinery"]
            refinery = ManagedRefinery(
                site_id=str(refinery_payload["site_id"]),
                origin=_point(refinery_payload["origin"]),
                variant=str(refinery_payload["variant"]),
                generation=int(refinery_payload["generation"]),
                maximum_furnaces=int(refinery_payload["maximum_furnaces"]),
                maximum_footprint_tiles=frozenset(
                    _tile(tile)
                    for tile in refinery_payload["maximum_footprint_tiles"]
                ),
                current_furnaces=int(refinery_payload.get("current_furnaces", 0)),
                current_footprint_tiles=frozenset(
                    _tile(tile)
                    for tile in refinery_payload.get("current_footprint_tiles", ())
                ),
                ore_inputs=tuple(
                    _point(point) for point in refinery_payload.get("ore_inputs", ())
                ),
                ore_input_headings=tuple(
                    str(value)
                    for value in refinery_payload.get("ore_input_headings", ())
                ),
            )
            lanes = tuple(
                TransportLane(
                    lane_id=str(item["lane_id"]),
                    source_band_orders=tuple(
                        int(order) for order in item["source_band_orders"]
                    ),
                    declared_items_per_second=float(
                        item["declared_items_per_second"]
                    ),
                    capacity_items_per_second=float(
                        item["capacity_items_per_second"]
                    ),
                    belt_tier=str(item["belt_tier"]),
                    splitter_action_ids=tuple(item["splitter_action_ids"]),
                    refinery_input=(
                        _point(item["refinery_input"])
                        if item.get("refinery_input") is not None else None
                    ),
                    path_ids=tuple(str(value) for value in item.get("path_ids", ())),
                    measured_items_per_second=(
                        float(item["measured_items_per_second"])
                        if item.get("measured_items_per_second") is not None
                        else None
                    ),
                )
                for item in payload["transport_lanes"]
            )
            paths = tuple(
                TransportPath(
                    path_id=str(item["path_id"]),
                    role=str(item["role"]),
                    lane_id=str(item["lane_id"]),
                    source_band_order=(
                        int(item["source_band_order"])
                        if item.get("source_band_order") is not None else None
                    ),
                    source=_point(item["source"]),
                    destination=_point(item["destination"]),
                    source_heading=str(item["source_heading"]),
                    destination_heading=str(item["destination_heading"]),
                    action_ids=tuple(str(value) for value in item["action_ids"]),
                    route_tiles=tuple(_tile(tile) for tile in item["route_tiles"]),
                    reused_tiles=frozenset(
                        _tile(tile) for tile in item["reused_tiles"]
                    ),
                    underground_spans=tuple(
                        (_tile(pair[0]), _tile(pair[1]))
                        for pair in item["underground_spans"]
                    ),
                    underground_reach=int(item["underground_reach"]),
                )
                for item in payload.get("transport_paths", ())
            )
            reservations = MappingProxyType({
                str(name): frozenset(_tile(tile) for tile in tiles)
                for name, tiles in payload["reservations"].items()
            })
            owned = tuple(
                OwnedPlacement(
                    action_id=str(item["action_id"]),
                    entity=str(item["entity"]),
                    position=_point(item["position"]),
                    direction=item.get("direction"),
                    status=str(item["status"]),
                    revision=int(item["revision"]),
                    underground_type=item.get("underground_type"),
                    input_priority=item.get("input_priority"),
                    output_priority=item.get("output_priority"),
                )
                for item in payload["owned_placements"]
            )
            return cls(
                district_id=str(payload["district_id"]),
                episode_id=str(payload["episode_id"]),
                surface=str(payload["surface"]),
                force=str(payload["force"]),
                ore=str(payload["ore"]),
                recipe=str(payload["recipe"]),
                root=_point(payload["root"]),
                output=_point(payload["output"]),
                expansion_direction=str(payload["expansion_direction"]),
                parallel_band_pitch=float(payload["parallel_band_pitch"]),
                collector_bands=bands,
                reservations=reservations,
                refinery=refinery,
                transport_lanes=lanes,
                transport_paths=tuple(sorted(paths, key=lambda path: path.path_id)),
                belt_tier=str(payload["belt_tier"]),
                belt_capacity_items_per_second=float(
                    payload["belt_capacity_items_per_second"]
                ),
                drill_items_per_second=float(payload["drill_items_per_second"]),
                maximum_drills=int(payload["maximum_drills"]),
                revision=int(payload["revision"]),
                pending_revision=payload.get("pending_revision"),
                pending_plan_id=payload.get("pending_plan_id"),
                owned_placements=tuple(sorted(owned)),
                version=1,
            )
        except DistrictStateError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise DistrictStateError("Malformed persisted district state") from error


@dataclass(frozen=True)
class PhaseReason:
    code: str
    detail: str
    tile: Tile | None = None


@dataclass(frozen=True)
class PhaseDecision:
    outcome: str
    next_state: ResourceDistrictState
    resulting_drill_count: int
    variant: str | None
    plan: dict | None
    reason: PhaseReason | None = None
    raw_t_merges: tuple[Tile, ...] = ()


def _point(value: Any) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a two-coordinate point")
    return float(value[0]), float(value[1])


def _tile(value: Any) -> Tile:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a two-coordinate tile")
    return int(value[0]), int(value[1])


def _point_axis(values: Any) -> tuple[float, ...]:
    return tuple(float(value) for value in values)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _entity_tiles(entity: str, position: Point) -> frozenset[Tile]:
    return entity_footprint_tiles({
        "entity": entity,
        "position": {"x": position[0], "y": position[1]},
    })


def _band_reserved_tiles(band: CollectorBand) -> frozenset[Tile]:
    tiles: set[Tile] = set()
    for x in band.column_xs:
        for y in (band.belt_y - 2.0, band.belt_y + 2.0):
            tiles.update(_entity_tiles("electric-mining-drill", (x, y)))
        for offset in (-2.0, -1.0, 0.0):
            tiles.update(_entity_tiles("transport-belt", (x + offset, band.belt_y)))
    return frozenset(tiles)


def _rect_tiles(left: int, top: int, right: int, bottom: int) -> frozenset[Tile]:
    return frozenset(
        (x, y)
        for x in range(left, right + 1)
        for y in range(top, bottom + 1)
    )


def create_initial_envelope(
    *,
    episode_id: str,
    surface: str,
    force: str,
    ore: str,
    recipe: str,
    root: Point,
    output: Point,
    expansion_direction: str,
    initial_drills: int,
    longitudinal_drill_limit: int,
    maximum_drills: int,
    parallel_band_pitch: float,
    refinery_origin: Point,
    refinery_variant: str,
    refinery_generation: int,
    belt_tier: str,
    belt_capacity_items_per_second: float,
    drill_items_per_second: float,
) -> ResourceDistrictState:
    """Reserve the final envelope while marking only phase one as built."""
    if expansion_direction not in {"east", "west"}:
        raise DistrictStateError("Expansion direction must be east or west")
    counts = (initial_drills, longitudinal_drill_limit, maximum_drills)
    if any(count <= 0 or count % 2 for count in counts):
        raise DistrictStateError("Paired drill counts must be positive and even")
    if not initial_drills <= longitudinal_drill_limit <= maximum_drills:
        raise DistrictStateError("Drill limits must increase monotonically")
    finite_positive = {
        "parallel band pitch": parallel_band_pitch,
        "belt capacity": belt_capacity_items_per_second,
        "drill rate": drill_items_per_second,
    }
    for label, value in finite_positive.items():
        if not math.isfinite(value) or value <= 0:
            raise DistrictStateError(f"{label} must be finite and positive")
    try:
        maximum_furnaces = REFINERY_CAPACITY_SCHEDULES[refinery_generation - 1][-1]
    except IndexError as error:
        raise DistrictStateError(
            f"Unknown refinery generation {refinery_generation}"
        ) from error

    root = _point(root)
    output = _point(output)
    refinery_origin = _point(refinery_origin)
    direction_step = 3.0 if expansion_direction == "east" else -3.0
    longitudinal_columns = longitudinal_drill_limit // 2
    primary_columns = tuple(
        root[0] + direction_step * index
        for index in range(longitudinal_columns)
    )
    initial_columns = primary_columns[: initial_drills // 2]
    primary = CollectorBand(
        order=0,
        belt_y=root[1],
        column_xs=primary_columns,
        built_column_xs=initial_columns,
        planned_column_xs=initial_columns,
    )

    remaining_columns = (maximum_drills - longitudinal_drill_limit) // 2
    upper_count = (remaining_columns + 1) // 2
    lower_count = remaining_columns // 2
    parallel: list[CollectorBand] = []
    for order, count in ((-1, lower_count), (1, upper_count)):
        if count:
            parallel.append(CollectorBand(
                order=order,
                belt_y=primary.belt_y + order * parallel_band_pitch,
                column_xs=primary_columns[:count],
            ))
    bands = (primary, *parallel)

    district_id = _stable_id("resource-district", {
        "episode_id": episode_id,
        "surface": surface,
        "force": force,
        "ore": ore,
        "recipe": recipe,
        "root": root,
        "output": output,
    })
    refinery_site_id = _stable_id("refinery", {
        "district_id": district_id,
        "origin": refinery_origin,
        "generation": refinery_generation,
    })
    refinery_plan = generate_managed_refinery_plan(
        recipe,
        maximum_furnaces,
        origin_x=refinery_origin[0],
        origin_y=refinery_origin[1],
        variant=refinery_variant,
    )
    refinery_tiles = frozenset(
        occupied_tile_indices([("generation-maximum", refinery_plan)])
    )
    interfaces = refinery_interfaces(
        maximum_furnaces,
        origin_x=refinery_origin[0],
        origin_y=refinery_origin[1],
        variant=refinery_variant,
    )
    longitudinal = _band_reserved_tiles(primary)
    parallel_tiles = frozenset().union(*(
        _band_reserved_tiles(band) for band in parallel
    )) if parallel else frozenset()
    all_band_tiles = longitudinal | parallel_tiles
    min_band_y = min(tile[1] for tile in all_band_tiles)
    max_band_y = max(tile[1] for tile in all_band_tiles)
    output_tile = (math.floor(output[0]), math.floor(output[1]))
    manifold = _rect_tiles(
        output_tile[0] - 4,
        min_band_y,
        output_tile[0],
        max_band_y,
    )
    refinery_input_x = math.floor(refinery_origin[0])
    egress = _rect_tiles(
        min(output_tile[0], refinery_input_x),
        output_tile[1],
        max(output_tile[0], refinery_input_x),
        output_tile[1],
    )
    service: set[Tile] = set()
    for band in bands:
        for x in band.column_xs:
            for y in (band.belt_y - 4.0, band.belt_y + 4.0):
                service.update(_entity_tiles("medium-electric-pole", (x, y)))
    self_conflict = refinery_tiles & (all_band_tiles | service)
    if self_conflict:
        raise DistrictStateError(
            "Reserved refinery footprint overlaps the collector/service envelope "
            f"at {min(self_conflict)}"
        )
    reservations = MappingProxyType({
        "longitudinal": longitudinal,
        "parallel_bands": parallel_tiles,
        "manifold": manifold,
        "egress": egress,
        "service": frozenset(service),
        "refinery": refinery_tiles,
    })
    initial_lane = TransportLane(
        lane_id=f"{district_id}:lane:0",
        source_band_orders=(0,),
        declared_items_per_second=initial_drills * drill_items_per_second,
        capacity_items_per_second=belt_capacity_items_per_second,
        belt_tier=belt_tier,
    )
    return ResourceDistrictState(
        district_id=district_id,
        episode_id=episode_id,
        surface=surface,
        force=force,
        ore=ore,
        recipe=recipe,
        root=root,
        output=output,
        expansion_direction=expansion_direction,
        parallel_band_pitch=parallel_band_pitch,
        collector_bands=bands,
        reservations=reservations,
        refinery=ManagedRefinery(
            site_id=refinery_site_id,
            origin=refinery_origin,
            variant=refinery_variant,
            generation=refinery_generation,
            maximum_furnaces=maximum_furnaces,
            maximum_footprint_tiles=refinery_tiles,
            ore_inputs=tuple(interfaces.ore_inputs),
            ore_input_headings=tuple("east" for _ in interfaces.ore_inputs),
        ),
        transport_lanes=(initial_lane,),
        transport_paths=(),
        belt_tier=belt_tier,
        belt_capacity_items_per_second=belt_capacity_items_per_second,
        drill_items_per_second=drill_items_per_second,
        maximum_drills=maximum_drills,
    )


def reconcile_exact(
    persisted: ResourceDistrictState | None,
    *,
    episode_id: str,
    observed: tuple[OwnedPlacement, ...],
) -> ResourceDistrictState:
    """Recover only when the journal and exact live-shaped ownership agree."""
    if persisted is None:
        raise DistrictStateError("persisted district state is required for recovery")
    if persisted.episode_id != episode_id:
        raise DistrictStateError(
            "Persisted district episode does not match the active episode"
        )
    expected = tuple(sorted(persisted.owned_placements))
    actual = tuple(sorted(observed))
    expected_by_id = {item.action_id: item for item in expected}
    actual_by_id = {item.action_id: item for item in actual}
    if expected_by_id.keys() != actual_by_id.keys():
        mismatched = sorted(expected_by_id.keys() ^ actual_by_id.keys())
        detail = mismatched[0] if mismatched else "unknown"
        raise DistrictStateError(f"owned placement geometry mismatch for {detail}")
    for action_id in sorted(expected_by_id):
        before = expected_by_id[action_id]
        after = actual_by_id[action_id]
        if replace(before, status=after.status) != after:
            raise DistrictStateError(
                f"owned placement geometry mismatch for {action_id}"
            )
        status_rank = {"prepared": 0, "ghost": 1, "built": 2}
        if status_rank[after.status] < status_rank[before.status]:
            raise DistrictStateError(
                f"owned placement status regressed for {action_id}"
            )
    return replace(persisted, owned_placements=actual)


def complete_pending_revision(
    state: ResourceDistrictState,
) -> ResourceDistrictState:
    """Commit a pending revision only after every owned action is built."""
    pending = state.pending_revision
    if pending is None:
        return state
    pending_actions = tuple(
        placement for placement in state.owned_placements
        if placement.revision == pending
    )
    if not pending_actions:
        raise DistrictStateError(
            f"Pending revision {pending} has no owned placements"
        )
    if any(placement.status != "built" for placement in pending_actions):
        return state
    bands = tuple(replace(
        band,
        built_column_xs=band.planned_column_xs,
        pending_column_xs=(),
    ) for band in state.collector_bands)
    return replace(
        state,
        collector_bands=bands,
        revision=pending,
        pending_revision=None,
        pending_plan_id=None,
    )


def _path_component(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for character in value)
    ):
        raise DistrictStateError(f"{label} is not a safe state-path component")
    return value


def district_state_path(
    script_output: Path | str,
    *,
    episode_id: str,
    surface: str,
    force: str,
    ore: str,
) -> Path:
    """Return the unique state file for one episode-scoped resource system."""
    episode = _path_component(episode_id, "episode_id")
    key = _stable_id("district-key", {
        "surface": surface,
        "force": force,
        "ore": ore,
    })
    return (
        Path(script_output)
        / "logs"
        / "deterministic-resource-districts"
        / episode
        / f"{key}.json"
    )


def save_district_state(
    script_output: Path | str,
    state: ResourceDistrictState,
) -> Path:
    """Atomically persist one canonical district revision."""
    path = district_state_path(
        script_output,
        episode_id=state.episode_id,
        surface=state.surface,
        force=state.force,
        ore=state.ore,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(state.to_json() + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def load_district_state(
    script_output: Path | str,
    *,
    episode_id: str,
    surface: str,
    force: str,
    ore: str,
) -> ResourceDistrictState | None:
    """Load one state file and verify its requested scope exactly."""
    path = district_state_path(
        script_output,
        episode_id=episode_id,
        surface=surface,
        force=force,
        ore=ore,
    )
    if not path.is_file():
        return None
    try:
        state = ResourceDistrictState.from_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DistrictStateError(f"Persisted district state is unreadable: {path}") from error
    if (
        state.episode_id != episode_id
        or state.surface != surface
        or state.force != force
        or state.ore != ore
    ):
        raise DistrictStateError("Persisted district scope does not match requested scope")
    return state


def _planned_drills(state: ResourceDistrictState) -> int:
    return 2 * sum(len(band.planned_column_xs) for band in state.collector_bands)


def _placement_action(
    action_id: str,
    entity: str,
    position: Point,
    *,
    direction: str | None = None,
) -> dict:
    action = {
        "action_id": action_id,
        "action_type": "place_ghost",
        "entity": entity,
        "position": {"x": position[0], "y": position[1]},
    }
    if direction is not None:
        action["direction"] = direction
    return action


def _action_tiles(action: Mapping[str, Any]) -> frozenset[Tile]:
    return entity_footprint_tiles(dict(action))


def _column_actions(
    state: ResourceDistrictState,
    band: CollectorBand,
    columns: tuple[float, ...],
) -> list[dict]:
    direction = "west" if state.expansion_direction == "east" else "east"
    actions: list[dict] = []
    for x in columns:
        prefix = f"{state.district_id}:band:{band.order}:x:{x:g}"
        actions.extend((
            _placement_action(
                f"{prefix}:drill:north",
                "electric-mining-drill",
                (x, band.belt_y - 2.0),
                direction="south",
            ),
            _placement_action(
                f"{prefix}:drill:south",
                "electric-mining-drill",
                (x, band.belt_y + 2.0),
                direction="north",
            ),
        ))
        belt_offsets = (-2.0, -1.0, 0.0) if direction == "west" else (0.0, 1.0, 2.0)
        actions.extend(
            _placement_action(
                f"{prefix}:belt:{offset:g}",
                state.belt_tier,
                (x + offset, band.belt_y),
                direction=direction,
            )
            for offset in belt_offsets
        )
        actions.extend((
            _placement_action(
                f"{state.district_id}:service:pole:{x:g}:{band.belt_y - 4.0:g}",
                "medium-electric-pole",
                (x, band.belt_y - 4.0),
            ),
            _placement_action(
                f"{state.district_id}:service:pole:{x:g}:{band.belt_y + 4.0:g}",
                "medium-electric-pole",
                (x, band.belt_y + 4.0),
            ),
        ))
    return actions


def _matching_owned_occupant(
    occupant: Any,
    action: dict,
    district_id: str,
) -> bool:
    identity = getattr(occupant, "identity", None)
    return (
        identity is not None
        and getattr(identity, "district_id", None) == district_id
        and getattr(identity, "entity_id", None) == action["action_id"]
        and getattr(occupant, "name", None) == action["entity"]
        and getattr(occupant, "direction", None) == action.get("direction")
    )


def _first_conflict(
    state: ResourceDistrictState,
    actions: list[dict],
    occupancy: Any,
) -> tuple[Any, Tile] | None:
    occupants = tuple(getattr(occupancy, "occupants", ()))
    for action in actions:
        position = action["position"]
        tiles = _action_tiles(action)
        for occupant in occupants:
            overlap = tiles & frozenset(getattr(occupant, "tiles", ()))
            if not overlap:
                continue
            identity = getattr(occupant, "identity", None)
            if (
                getattr(occupant, "category", None) == "district_reservation"
                and identity is not None
                and getattr(identity, "district_id", None) == state.district_id
                and action["entity"] in getattr(
                    occupant, "allowed_entities", frozenset()
                )
            ):
                continue
            if _matching_owned_occupant(occupant, action, state.district_id):
                continue
            return occupant, min(overlap)
    return None


def _transport_for_bands(
    state: ResourceDistrictState,
    bands: tuple[CollectorBand, ...],
    *,
    active_orders: frozenset[int],
) -> tuple[str, tuple[TransportLane, ...], list[dict]]:
    rates = tuple(
        (
            band.order,
            2 * len(band.column_xs) * state.drill_items_per_second,
        )
        for band in bands
        if band.column_xs
    )
    if any(rate > state.belt_capacity_items_per_second + 1e-9 for _, rate in rates):
        raise DistrictStateError(
            "One collector band exceeds the selected belt tier capacity"
        )

    priority = lambda item: (item[0] != 0, abs(item[0]), item[0])
    total = sum(rate for _, rate in rates)
    if total <= state.belt_capacity_items_per_second + 1e-9:
        groups: list[list[tuple[int, float]]] = [sorted(rates, key=priority)]
        variant = "A"
    else:
        groups = []
        for item in sorted(rates, key=lambda value: (-value[1], *priority(value))):
            for group in groups:
                if sum(rate for _, rate in group) + item[1] <= (
                    state.belt_capacity_items_per_second + 1e-9
                ):
                    group.append(item)
                    break
            else:
                groups.append([item])
        groups = [sorted(group, key=priority) for group in groups]
        variant = "B"

    if len(groups) > len(state.refinery.ore_inputs):
        raise DistrictStateError(
            "The managed refinery has fewer owned ore interfaces than required lanes"
        )

    flow = "west" if state.expansion_direction == "east" else "east"
    flow_step = -1.0 if flow == "west" else 1.0
    lanes: list[TransportLane] = []
    actions: list[dict] = []
    for lane_index, group in enumerate(groups):
        orders = tuple(order for order, _ in group)
        lane_key = ",".join(str(order) for order in orders)
        lane_id = f"{state.district_id}:lane:{lane_key}"
        splitter_ids = tuple(
            f"{lane_id}:splitter:{index}"
            for index in range(max(0, len(orders) - 1))
        )
        lane_y = state.output[1] + lane_index * 4.0
        if active_orders & set(orders):
            for index, (action_id, auxiliary_order) in enumerate(
                zip(splitter_ids, orders[1:])
            ):
                auxiliary_y = lane_y + (-1.0 if auxiliary_order < 0 else 1.0)
                centre = (
                    state.output[0] + flow_step * (2.0 + index * 3.0),
                    (lane_y + auxiliary_y) / 2.0,
                )
                trunk_is_left = (
                    (flow == "east" and lane_y < centre[1])
                    or (flow == "west" and lane_y > centre[1])
                )
                action = _placement_action(
                    action_id,
                    "splitter",
                    centre,
                    direction=flow,
                )
                action["output_priority"] = "left" if trunk_is_left else "right"
                actions.append(action)
        lanes.append(TransportLane(
            lane_id=lane_id,
            source_band_orders=orders,
            declared_items_per_second=sum(rate for _, rate in group),
            capacity_items_per_second=state.belt_capacity_items_per_second,
            belt_tier=state.belt_tier,
            splitter_action_ids=splitter_ids,
            refinery_input=state.refinery.ore_inputs[lane_index],
        ))
    return variant, tuple(lanes), actions


def _belt_underground_name(belt_type: str) -> str:
    if not belt_type.endswith("transport-belt"):
        raise DistrictStateError(f"Unsupported district belt tier {belt_type!r}")
    return belt_type[:-len("transport-belt")] + "underground-belt"


def _reservation_occupants(state: ResourceDistrictState) -> tuple[Occupant, ...]:
    underground = _belt_underground_name(state.belt_tier)
    allowed = {
        "longitudinal": frozenset({
            state.belt_tier, underground, "electric-mining-drill",
            "medium-electric-pole",
        }),
        "parallel_bands": frozenset({
            state.belt_tier, underground, "electric-mining-drill",
            "medium-electric-pole",
        }),
        "manifold": frozenset({state.belt_tier, underground, "splitter"}),
        "egress": frozenset({state.belt_tier, underground, "splitter"}),
        "service": frozenset({"medium-electric-pole"}),
        "refinery": frozenset(),
    }
    return tuple(
        Occupant(
            category="district_reservation",
            tiles=tiles,
            identity=OccupantIdentity(
                district_id=state.district_id,
                entity_id=f"reservation:{name}",
            ),
            allowed_entities=allowed.get(name, frozenset()),
        )
        for name, tiles in sorted(state.reservations.items())
        if tiles
    )


def _owned_occupant(
    state: ResourceDistrictState, placement: OwnedPlacement,
) -> Occupant:
    action: dict[str, Any] = {
        "entity": placement.entity,
        "position": {"x": placement.position[0], "y": placement.position[1]},
    }
    if placement.direction is not None:
        action["direction"] = placement.direction
    return Occupant(
        category="pending_plan" if placement.status != "built" else "live_entity",
        tiles=_action_tiles(action),
        name=placement.entity,
        direction=placement.direction,
        underground_type=placement.underground_type,
        identity=OccupantIdentity(
            district_id=state.district_id,
            entity_id=placement.action_id,
        ),
    )


def _planning_occupants(
    state: ResourceDistrictState, occupancy: RouteOccupancy,
) -> list[Occupant]:
    occupants = list(occupancy.occupants)
    observed_ids = {
        occupant.identity.entity_id
        for occupant in occupants
        if occupant.identity is not None
        and occupant.identity.district_id == state.district_id
    }
    occupants.extend(
        _owned_occupant(state, placement)
        for placement in state.owned_placements
        if placement.action_id not in observed_ids
    )
    existing_reservations = {
        occupant.identity.entity_id
        for occupant in occupants
        if occupant.category == "district_reservation"
        and occupant.identity is not None
        and occupant.identity.district_id == state.district_id
    }
    occupants.extend(
        reservation for reservation in _reservation_occupants(state)
        if reservation.identity is not None
        and reservation.identity.entity_id not in existing_reservations
    )
    return occupants


def _append_action_occupant(
    occupants: list[Occupant], state: ResourceDistrictState, action: dict,
) -> None:
    occupants.append(Occupant(
        category="pending_plan",
        tiles=_action_tiles(action),
        name=action["entity"],
        direction=action.get("direction"),
        underground_type=action.get("underground_type"),
        identity=OccupantIdentity(
            district_id=state.district_id,
            entity_id=action["action_id"],
        ),
    ))


def _band_transport_source(
    state: ResourceDistrictState, band: CollectorBand,
) -> Point:
    if band.order == 0:
        return state.output
    offset = -2.0 if state.expansion_direction == "east" else 2.0
    return state.root[0] + offset, band.belt_y


def _route_action_id(path_id: str, index: int, action: Mapping[str, Any]) -> str:
    position = action["position"]
    endpoint = action.get("underground_type") or "surface"
    return (
        f"{path_id}:action:{index}:{action['entity']}:"
        f"{float(position['x']):g}:{float(position['y']):g}:{endpoint}"
    )


def _plan_transport_path(
    state: ResourceDistrictState,
    *,
    path_id: str,
    role: str,
    lane_id: str,
    source_band_order: int | None,
    source: Point,
    destination: Point,
    source_heading: str,
    destination_heading: str,
    occupants: list[Occupant],
    fresh_occupancy: RouteOccupancy | None,
    max_route_tiles: int | None,
    underground_reach: int | None,
) -> tuple[TransportPath | None, list[dict], RouteFailure | None]:
    result = plan_belt_route(
        source,
        destination,
        belt_type=state.belt_tier,
        occupancy=RouteOccupancy(tuple(occupants)),
        district_id=state.district_id,
        source_heading=source_heading,
        destination_heading=destination_heading,
        max_route_tiles=max_route_tiles,
        fresh_occupancy=fresh_occupancy,
        underground_reach=underground_reach,
    )
    if isinstance(result, RouteFailure):
        return None, [], result
    actions: list[dict] = []
    for index, raw_action in enumerate(result.actions):
        action = dict(raw_action)
        action["position"] = dict(raw_action["position"])
        action["action_id"] = _route_action_id(path_id, index, action)
        actions.append(action)
        _append_action_occupant(occupants, state, action)
    return TransportPath(
        path_id=path_id,
        role=role,
        lane_id=lane_id,
        source_band_order=source_band_order,
        source=source,
        destination=destination,
        source_heading=source_heading,
        destination_heading=destination_heading,
        action_ids=tuple(action["action_id"] for action in actions),
        route_tiles=result.route_tiles,
        reused_tiles=result.reused_tiles,
        underground_spans=result.underground_spans,
        underground_reach=result.underground_reach,
    ), actions, None


def _existing_or_plan_path(
    state: ResourceDistrictState,
    existing: Mapping[str, TransportPath],
    **kwargs: Any,
) -> tuple[TransportPath | None, list[dict], RouteFailure | None]:
    path_id = str(kwargs["path_id"])
    persisted = existing.get(path_id)
    if persisted is not None:
        expected = (
            kwargs["role"], kwargs["lane_id"], kwargs["source_band_order"],
            _point(kwargs["source"]), _point(kwargs["destination"]),
            kwargs["source_heading"], kwargs["destination_heading"],
        )
        actual = (
            persisted.role, persisted.lane_id, persisted.source_band_order,
            persisted.source, persisted.destination,
            persisted.source_heading, persisted.destination_heading,
        )
        if actual != expected:
            raise DistrictStateError(
                f"Persisted transport geometry disagrees for {path_id}"
            )
        return persisted, [], None
    return _plan_transport_path(state, **kwargs)


def _approach_point(point: Point, heading: str) -> Point:
    vector = {
        "north": (0.0, -1.0),
        "east": (1.0, 0.0),
        "south": (0.0, 1.0),
        "west": (-1.0, 0.0),
    }[heading]
    return point[0] - vector[0], point[1] - vector[1]


def _plan_transport_geometry(
    state: ResourceDistrictState,
    bands: tuple[CollectorBand, ...],
    lanes: tuple[TransportLane, ...],
    *,
    occupants: list[Occupant],
    fresh_occupancy: RouteOccupancy | None,
    max_route_tiles: int | None,
    underground_reach: int | None,
) -> tuple[tuple[TransportLane, ...], tuple[TransportPath, ...], list[dict], RouteFailure | None]:
    flow = "west" if state.expansion_direction == "east" else "east"
    step = -1.0 if flow == "west" else 1.0
    by_order = {band.order: band for band in bands}
    active_orders = {
        band.order for band in bands if band.planned_column_xs
    }
    existing = {path.path_id: path for path in state.transport_paths}
    paths = dict(existing)
    actions: list[dict] = []
    updated_lanes: list[TransportLane] = []

    def add_path(**kwargs: Any) -> RouteFailure | None:
        path, additions, failure = _existing_or_plan_path(
            state,
            existing,
            occupants=occupants,
            fresh_occupancy=fresh_occupancy,
            max_route_tiles=max_route_tiles,
            underground_reach=underground_reach,
            **kwargs,
        )
        if failure is not None:
            return failure
        assert path is not None
        paths[path.path_id] = path
        actions.extend(additions)
        return None

    for lane_index, lane in enumerate(lanes):
        lane_y = state.output[1] + lane_index * 4.0
        active_lane_orders = tuple(
            order for order in lane.source_band_orders if order in active_orders
        )
        if not active_lane_orders:
            updated_lanes.append(replace(lane, path_ids=()))
            continue
        splitter_count = len(lane.source_band_orders) - 1
        lane_paths: list[str] = []

        for source_index, order in enumerate(lane.source_band_orders):
            if order not in active_orders:
                continue
            if splitter_count == 0:
                destination = (state.output[0], lane_y)
            elif source_index == 0:
                destination = (state.output[0] + step, lane_y)
            else:
                splitter_index = source_index - 1
                auxiliary_y = lane_y + (-1.0 if order < 0 else 1.0)
                destination = (
                    state.output[0] + step * (1.0 + splitter_index * 3.0),
                    auxiliary_y,
                )
            path_id = f"{lane.lane_id}:collector:{order}"
            failure = add_path(
                path_id=path_id,
                role="collector",
                lane_id=lane.lane_id,
                source_band_order=order,
                source=_band_transport_source(state, by_order[order]),
                destination=destination,
                source_heading=flow,
                destination_heading=flow,
            )
            if failure is not None:
                return lanes, tuple(state.transport_paths), [], failure
            lane_paths.append(path_id)

        for connector_index in range(max(0, splitter_count - 1)):
            source = (
                state.output[0] + step * (3.0 + connector_index * 3.0),
                lane_y,
            )
            destination = (source[0] + step, lane_y)
            path_id = f"{lane.lane_id}:manifold:{connector_index}"
            failure = add_path(
                path_id=path_id,
                role="manifold",
                lane_id=lane.lane_id,
                source_band_order=None,
                source=source,
                destination=destination,
                source_heading=flow,
                destination_heading=flow,
            )
            if failure is not None:
                return lanes, tuple(state.transport_paths), [], failure
            lane_paths.append(path_id)

        manifold_output = (
            state.output[0] + step * (3.0 * splitter_count),
            lane_y,
        ) if splitter_count else (state.output[0], lane_y)
        assert lane.refinery_input is not None
        input_index = state.refinery.ore_inputs.index(lane.refinery_input)
        input_heading = state.refinery.ore_input_headings[input_index]
        path_id = f"{lane.lane_id}:haul"
        failure = add_path(
            path_id=path_id,
            role="haul",
            lane_id=lane.lane_id,
            source_band_order=None,
            source=manifold_output,
            destination=_approach_point(lane.refinery_input, input_heading),
            source_heading=flow,
            destination_heading=input_heading,
        )
        if failure is not None:
            return lanes, tuple(state.transport_paths), [], failure
        lane_paths.append(path_id)
        updated_lanes.append(replace(lane, path_ids=tuple(lane_paths)))

    return (
        tuple(updated_lanes),
        tuple(sorted(paths.values(), key=lambda path: path.path_id)),
        actions,
        None,
    )


def _filter_owned_actions(
    state: ResourceDistrictState, actions: list[dict],
) -> list[dict]:
    owned_by_id = {
        placement.action_id: placement for placement in state.owned_placements
    }
    filtered: list[dict] = []
    for action in actions:
        existing = owned_by_id.get(action["action_id"])
        if existing is None:
            filtered.append(action)
            continue
        position = action["position"]
        expected = (
            action["entity"], float(position["x"]), float(position["y"]),
            action.get("direction"), action.get("underground_type"),
            action.get("input_priority"), action.get("output_priority"),
        )
        actual = (
            existing.entity, existing.position[0], existing.position[1],
            existing.direction, existing.underground_type,
            existing.input_priority, existing.output_priority,
        )
        if actual != expected:
            raise DistrictStateError(
                "owned placement identity disagrees with canonical geometry: "
                f"{action['action_id']}"
            )
    return filtered


def plan_phase(
    state: ResourceDistrictState,
    *,
    target_drills: int,
    occupancy: Any,
    fresh_occupancy: RouteOccupancy | None = None,
    max_transport_route_tiles: int | None = None,
    underground_reach: int | None = None,
) -> PhaseDecision:
    """Plan one addition-only, whole-footprint-atomic district phase."""
    current = _planned_drills(state)
    if target_drills <= current:
        return PhaseDecision(
            outcome="noop",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan={"atomic": True, "phases": []},
            reason=PhaseReason("target_satisfied", "Requested drill target already exists"),
        )
    if target_drills > state.maximum_drills or target_drills % 2:
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason(
                "target_outside_envelope",
                "Requested paired drills exceed the reserved district envelope",
            ),
        )

    columns_needed = (target_drills - current) // 2
    selected: dict[int, tuple[float, ...]] = {}
    updated_bands: list[CollectorBand] = []
    for band in state.collector_bands:
        existing = set(band.planned_column_xs)
        available = tuple(x for x in band.column_xs if x not in existing)
        chosen = available[:columns_needed]
        columns_needed -= len(chosen)
        selected[band.order] = chosen
        planned = tuple(x for x in band.column_xs if x in existing or x in chosen)
        pending = tuple(x for x in band.column_xs if x in set(band.pending_column_xs) | set(chosen))
        updated_bands.append(replace(
            band,
            pending_column_xs=pending,
            planned_column_xs=planned,
        ))
    if columns_needed:
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason(
                "envelope_exhausted", "Reserved collector bands cannot satisfy target"
            ),
        )

    actions: list[dict] = []
    selected_bands: list[CollectorBand] = []
    for band in state.collector_bands:
        chosen = selected[band.order]
        if chosen:
            selected_bands.append(band)
            actions.extend(_column_actions(state, band, chosen))

    active_orders = frozenset(
        band.order for band in updated_bands if band.planned_column_xs
    )
    try:
        variant, lanes, manifold_actions = _transport_for_bands(
            state, tuple(updated_bands), active_orders=active_orders,
        )
    except DistrictStateError as error:
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason("transport_capacity_unavailable", str(error)),
        )
    actions.extend(manifold_actions)
    actions = _filter_owned_actions(state, actions)
    planning_occupants = _planning_occupants(state, occupancy)
    planning_occupancy = RouteOccupancy(tuple(planning_occupants))

    conflict = _first_conflict(state, actions, planning_occupancy)
    if conflict is not None:
        occupant, tile = conflict
        category = getattr(occupant, "category", None)
        parallel_growth = any(band.order for band in selected_bands)
        if category == "district_reservation":
            code = "district_reservation_conflict"
        elif parallel_growth:
            code = "parallel_band_blocked"
        else:
            code = "longitudinal_corridor_blocked"
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason(code, f"District phase is blocked by {category}", tile),
        )
    if fresh_occupancy is not None:
        fresh_conflict = _first_conflict(state, actions, fresh_occupancy)
        if fresh_conflict is not None:
            _occupant, tile = fresh_conflict
            return PhaseDecision(
                outcome="defer",
                next_state=state,
                resulting_drill_count=current,
                variant=None,
                plan=None,
                reason=PhaseReason(
                    "fresh_transport_conflict",
                    "Fresh occupancy invalidated the district structure",
                    tile,
                ),
            )
    for action in actions:
        _append_action_occupant(planning_occupants, state, action)

    bands_tuple = tuple(updated_bands)
    lanes, transport_paths, route_actions, route_failure = (
        _plan_transport_geometry(
            state,
            bands_tuple,
            lanes,
            occupants=planning_occupants,
            fresh_occupancy=fresh_occupancy,
            max_route_tiles=max_transport_route_tiles,
            underground_reach=underground_reach,
        )
    )
    if route_failure is not None:
        code = (
            "fresh_transport_conflict"
            if route_failure.reason == "fresh_occupancy_conflict"
            else "transport_route_unavailable"
        )
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason(code, route_failure.detail),
        )
    actions.extend(_filter_owned_actions(state, route_actions))
    next_revision = max(state.revision, state.pending_revision or 0) + 1
    plan_id = f"{state.district_id}:phase:{target_drills}:r{next_revision}"
    plan = {
        "atomic": True,
        "district_id": state.district_id,
        "plan_id": plan_id,
        "phases": [{
            "name": f"resource_district_{target_drills}",
            "actions": actions,
        }],
    }
    try:
        validate_no_collisions([("resource-district", plan)])
    except ValueError as error:
        return PhaseDecision(
            outcome="defer",
            next_state=state,
            resulting_drill_count=current,
            variant=None,
            plan=None,
            reason=PhaseReason("whole_footprint_collision", str(error)),
        )
    new_owned = list(state.owned_placements)
    for action in actions:
        position = action["position"]
        new_owned.append(OwnedPlacement(
            action_id=action["action_id"],
            entity=action["entity"],
            position=(float(position["x"]), float(position["y"])),
            direction=action.get("direction"),
            status="prepared",
            revision=next_revision,
            underground_type=action.get("underground_type"),
            input_priority=action.get("input_priority"),
            output_priority=action.get("output_priority"),
        ))
    next_state = replace(
        state,
        collector_bands=bands_tuple,
        transport_lanes=lanes,
        transport_paths=transport_paths,
        pending_revision=next_revision,
        pending_plan_id=plan_id,
        owned_placements=tuple(sorted(new_owned)),
    )
    return PhaseDecision(
        outcome="build",
        next_state=next_state,
        resulting_drill_count=target_drills,
        variant=variant,
        plan=plan,
    )


def validate_transport_continuity(
    state: ResourceDistrictState,
) -> tuple[bool, str | None]:
    """Trace every active collector through emitted geometry to one refinery input."""
    active_orders = {
        band.order for band in state.collector_bands if band.planned_column_xs
    }
    path_by_id = {path.path_id: path for path in state.transport_paths}
    owned_ids = {placement.action_id for placement in state.owned_placements}
    covered: set[int] = set()
    for lane in state.transport_lanes:
        lane_orders = active_orders & set(lane.source_band_orders)
        if not lane_orders:
            continue
        if lane.refinery_input not in state.refinery.ore_inputs:
            return False, f"lane {lane.lane_id} has no exact refinery input"
        if lane.declared_items_per_second > lane.capacity_items_per_second + 1e-9:
            return False, f"lane {lane.lane_id} exceeds belt capacity"
        try:
            lane_paths = tuple(path_by_id[path_id] for path_id in lane.path_ids)
        except KeyError as error:
            return False, f"lane {lane.lane_id} references missing path {error.args[0]}"
        haul = tuple(path for path in lane_paths if path.role == "haul")
        if len(haul) != 1:
            return False, f"lane {lane.lane_id} needs exactly one haul path"
        refinery_input = lane.refinery_input
        input_index = state.refinery.ore_inputs.index(refinery_input)
        expected_approach = _approach_point(
            refinery_input, state.refinery.ore_input_headings[input_index],
        )
        if haul[0].destination != expected_approach:
            return False, f"lane {lane.lane_id} does not reach its refinery interface"
        collectors = {
            path.source_band_order: path
            for path in lane_paths
            if path.role == "collector"
        }
        if not lane_orders <= collectors.keys():
            missing = min(lane_orders - collectors.keys())
            return False, f"collector band {missing} has no emitted route"
        covered.update(lane_orders)
        expected_splitters = max(0, len(lane.source_band_orders) - 1)
        if len(lane.splitter_action_ids) != expected_splitters:
            return False, f"lane {lane.lane_id} has an incomplete splitter manifold"
        if expected_splitters and not set(lane.splitter_action_ids) <= owned_ids:
            return False, f"lane {lane.lane_id} splitter ownership is incomplete"
        for path in lane_paths:
            if not path.route_tiles:
                return False, f"transport path {path.path_id} has no geometry"
            if path.route_tiles[0] != (
                math.floor(path.source[0]), math.floor(path.source[1])
            ):
                return False, f"transport path {path.path_id} starts off interface"
            if path.route_tiles[-1] != (
                math.floor(path.destination[0]), math.floor(path.destination[1])
            ):
                return False, f"transport path {path.path_id} ends off interface"
            if any(
                abs(right[0] - left[0]) + abs(right[1] - left[1]) != 1
                for left, right in zip(path.route_tiles, path.route_tiles[1:])
            ):
                return False, f"transport path {path.path_id} is discontinuous"
            if not set(path.action_ids) <= owned_ids:
                return False, f"transport path {path.path_id} lacks exact action ownership"
            for start, end in path.underground_spans:
                span = abs(end[0] - start[0]) + abs(end[1] - start[1])
                if span > path.underground_reach:
                    return False, f"transport path {path.path_id} exceeds live reach"
    if covered != active_orders:
        missing = sorted(active_orders - covered)
        return False, f"active collector bands are not connected: {missing}"
    return True, None
