# Path: planners/sandbox_infrastructure.py
# Purpose: Compose every sandbox build onto one canonical managed power and robot backbone.

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from core.execution_authorizer import authorize_execution
from planners.infrastructure_geometry import boxes_overlap
from planners.plan_validation import ENTITY_FOOTPRINTS, actions, occupied_tile_indices
from planners.infrastructure import (
    POLE_SPECS,
    local_power_anchors,
    plan_power_network,
    plan_roboport_network,
    roboport_positions,
    strip_local_power,
    validate_power_connectivity,
)
from planners.roboport_coverage import POWER_SITE_OFFSETS, SUBSTATION_SIZE, footprint_tiles

CANONICAL_POWER_SOURCE = (-160.0, -160.0)
CANONICAL_ROBOPORT_HUB = (-128.0, -128.0)
_ACTION_PERMISSIONS = {
    "place_ghost": "project_more_ghosts",
    "place_entity": "place_core_infrastructure",
    "remove_entity": "remove_entities",
}


def _iter_plans(plans: Sequence[object]) -> Iterable[dict]:
    for entry in plans:
        yield entry[1] if isinstance(entry, tuple) else entry


def build_layout_authorization(plans: Sequence[object]) -> dict:
    """Authorize only mutation classes actually present in these plans."""
    permissions = {
        _ACTION_PERMISSIONS[action["action_type"]]
        for plan in _iter_plans(plans)
        for phase in plan.get("phases", [])
        for action in phase.get("actions", [])
    }
    if not permissions:
        raise ValueError("Cannot authorize an empty plan set")
    approved = sorted(permissions)
    proposal = {
        "allowed_actions": approved,
        "blocked_actions": [],
        "requires_human_approval": False,
        "next_recommended_step": approved[0],
    }
    return authorize_execution(
        proposal=proposal,
        approved_actions=approved,
        authorization_source="policy",
    ).to_dict()


def topology_is_compatible(topology: dict) -> bool:
    planner_entities = int(topology.get("planner_factory_entities", -1))
    player_entities = int(topology.get("player_factory_entities", -1))
    if planner_entities == 0 and player_entities == 0:
        return True
    if planner_entities < 0 or player_entities < 0:
        return False
    return (
        topology.get("unified") is True
        and topology.get("canonical_power_source") is True
        and topology.get("canonical_roboport_hub") is True
        and player_entities == 0
    )


def _load_report(value) -> dict:
    if isinstance(value, dict):
        return value
    with Path(value).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require_compatible_topology(bridge) -> dict:
    """Fail closed unless existing factory topology owns the canonical backbone."""
    topology = _load_report(bridge.inspect_sandbox_topology())
    if not topology_is_compatible(topology):
        raise RuntimeError(
            "Existing planner-sandbox topology is incompatible with the canonical managed backbone; "
            "inspect it and use the explicit processing reset path"
        )
    return topology


def _anchor(anchor: object) -> dict:
    if isinstance(anchor, dict):
        if "x" not in anchor:
            raise ValueError("sandbox infrastructure anchor needs x")
        return {**anchor, "x": float(anchor["x"]), "y": float(anchor.get("y", -3))}
    if isinstance(anchor, (tuple, list)) and len(anchor) == 2:
        return {"x": float(anchor[0]), "y": float(anchor[1])}
    return {"x": float(anchor), "y": -3.0}


def roboport_power_sites(
    positions: Sequence[tuple], obstacles: set[tuple[int, int]] | None = None,
) -> list[dict]:
    """Give every roboport a non-overlapping substation within supply range.

    `obstacles` are tile indices already claimed by emitted geometry (production
    rows, belt and pipe routes). Without it a substation can be dropped straight
    onto a route the caller solved earlier; with it the offset search simply
    skips those slots.
    """
    sites = []
    substations: list[tuple[int, int]] = []
    claimed = obstacles or set()
    for index, position in enumerate(positions):
        point = (round(position[0]), round(position[1]))
        candidates = [(point[0] + dx, point[1] + dy) for dx, dy in POWER_SITE_OFFSETS]
        substation = next((
            candidate for candidate in candidates
            if not any(boxes_overlap(candidate, 2, other, 4) for other in positions)
            and not any(boxes_overlap(candidate, 2, other, 2) for other in substations)
            and not (footprint_tiles(candidate, SUBSTATION_SIZE) & claimed)
        ), None)
        if substation is None:
            raise ValueError(f"Cannot place a non-overlapping power site for roboport {point}")
        substations.append(substation)
        sites.append({
            "name": f"roboport_{index}",
            "substation": substation,
            "pole_anchor": (substation[0] - 5, substation[1]),
        })
    return sites


def _combined_plan(plans: Iterable[tuple[str, dict]]) -> dict:
    return {
        "phases": [
            {"name": f"{plan_name}/{phase['name']}", "actions": phase["actions"]}
            for plan_name, plan in plans
            for phase in plan["phases"]
        ]
    }


def _resolve_spine_pole_overlaps(
    power: dict, obstacle_plans: Sequence[tuple[str, dict]],
    obstacle_tiles: set[tuple[int, int]] | None = None,
) -> None:  # obstacle_plans and tiles: everything a pole must dodge
    """Relocate colliding spine poles within routing slack; never disconnect by deletion."""
    obstacles = [
        (
            (action["position"]["x"], action["position"]["y"]),
            ENTITY_FOOTPRINTS.get(action["entity"], 1),
        )
        for _, plan in obstacle_plans
        for action in actions(plan)
        if action.get("action_type") in {"place_entity", "place_ghost"}
    ] + [((x + 0.5, y + 0.5), 1) for x, y in obstacle_tiles or ()]
    fixed_power = [
        (
            (action["position"]["x"], action["position"]["y"]),
            ENTITY_FOOTPRINTS.get(action["entity"], 1),
        )
        for phase in power["phases"]
        if phase["name"] != "power_spine"
        for action in phase["actions"]
        if action.get("action_type") in {"place_entity", "place_ghost"}
    ]
    offsets = [(0, 0)] + [
        (dx, dy)
        for radius in range(1, 5)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) == radius
    ]
    claimed = list(fixed_power)
    spine = next(phase for phase in power["phases"] if phase["name"] == "power_spine")
    for action in spine["actions"]:
        original = (action["position"]["x"], action["position"]["y"])
        size = POLE_SPECS[action["entity"]]["size"]
        candidates = [(original[0] + dx, original[1] + dy) for dx, dy in offsets]
        position = next((
            candidate for candidate in candidates
            if not any(boxes_overlap(candidate, size, other, other_size)
                       for other, other_size in obstacles + claimed)
        ), None)
        if position is None:
            raise ValueError(f"Cannot relocate colliding spine pole at {original}")
        action["position"] = {"x": position[0], "y": position[1]}
        claimed.append((position, size))


def compose_managed_sandbox(
    plans: Sequence[tuple[str, dict]],
    anchors: Sequence[object],
    materials: dict,
    *,
    bots_per_roboport: int = 30,
    extra_roboports: Sequence[tuple] = (),
    obstacle_plans: Sequence[tuple[str, dict]] = (),
    obstacle_tiles: set[tuple[int, int]] | None = None,
) -> dict:
    """Strip local sources and extend the canonical sandbox backbone.

    `extra_roboports` are positions derived from the FINISHED block geometry
    (planners.roboport_coverage) rather than from `anchors`; `obstacle_plans` are
    the plans that geometry lives in. `obstacle_tiles` adds immutable terrain
    cells (such as seeded lakes); both keep substations and spine poles clear.
    """
    normalized = [_anchor(anchor) for anchor in anchors]
    if not normalized:
        raise ValueError("managed sandbox composition needs at least one anchor")
    if not any(
        (anchor["x"], anchor["y"]) == CANONICAL_ROBOPORT_HUB for anchor in normalized
    ):
        normalized.insert(0, {
            "name": "canonical_hub",
            "x": CANONICAL_ROBOPORT_HUB[0],
            "y": CANONICAL_ROBOPORT_HUB[1],
        })

    robot_sites = [
        {
            "name": str(anchor.get("name", f"anchor_{index}")),
            "position": (anchor["x"], anchor["y"]),
            **({"extent": anchor["extent"]} if anchor.get("extent") else {}),
        }
        for index, anchor in enumerate(normalized)
    ]
    robots = plan_roboport_network(robot_sites, extra_positions=extra_roboports)
    terrain_obstacles = obstacle_tiles or set()

    stripped = []
    power_sites = []
    for plan_name, plan in plans:
        for index, substation in enumerate(local_power_anchors(plan)):
            power_sites.append({
                "name": f"{plan_name}_row_{index}",
                "substation": substation,
                "pole_anchor": (substation[0] - 5, substation[1]),
            })
        stripped.append((plan_name, strip_local_power(plan)))

    occupied_obstacles = terrain_obstacles | occupied_tile_indices(
        stripped + [("unified_roboports", robots)] + list(obstacle_plans),
    )
    # The final backbone must not land on completed production routes.
    power_sites.extend(roboport_power_sites(roboport_positions(robots), occupied_obstacles))

    power = plan_power_network(
        power_sites, source=CANONICAL_POWER_SOURCE, blocked_tiles=occupied_obstacles,
    )
    _resolve_spine_pole_overlaps(
        power, stripped + [("unified_roboports", robots)] + list(obstacle_plans),
        terrain_obstacles,
    )
    infrastructure = [("unified_power", power), ("unified_roboports", robots)]
    validate_power_connectivity(_combined_plan(infrastructure + stripped))

    robot_positions = roboport_positions(robots)
    scaffold_anchors = [{"x": x, "y": y} for x, y in robot_positions]
    hub_index = robot_positions.index(CANONICAL_ROBOPORT_HUB)
    hub_x, hub_y = robot_positions[hub_index]
    scaffold_anchors[hub_index].update({
        "materials": dict(materials),
        "provider_position": {"x": hub_x + 4, "y": hub_y - 5},
        "storage_position": {"x": hub_x + 6, "y": hub_y - 5},
    })
    scaffolding = {
        "managed_infrastructure": True,
        "anchors": scaffold_anchors,
        "bots_per_roboport": bots_per_roboport,
    }

    return {"infrastructure": infrastructure, "plans": stripped, "scaffolding": scaffolding}