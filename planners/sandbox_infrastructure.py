# Path: planners/sandbox_infrastructure.py
# Purpose: Compose every sandbox build onto one canonical managed power and robot backbone.

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from core.execution_authorizer import authorize_execution
from planners.infrastructure import (
    local_power_anchors,
    plan_power_network,
    plan_roboport_network,
    roboport_positions,
    strip_local_power,
    validate_power_connectivity,
)

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


def roboport_power_sites(positions: Sequence[tuple]) -> list[dict]:
    """Group adjacent bridge ports onto buildable, covering substations."""
    clusters: list[list[tuple]] = []
    for position in positions:
        point = (round(position[0]), round(position[1]))
        if clusters and max(
            abs(point[0] - clusters[-1][-1][0]),
            abs(point[1] - clusters[-1][-1][1]),
        ) <= 20:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    sites = []
    for index, cluster in enumerate(clusters):
        x = round(sum(point[0] for point in cluster) / len(cluster))
        y = round(sum(point[1] for point in cluster) / len(cluster))
        sites.append({
            "name": f"roboport_{index}",
            "substation": (x - 8, y),
            "pole_anchor": (x - 13, y),
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


def compose_managed_sandbox(
    plans: Sequence[tuple[str, dict]],
    anchors: Sequence[object],
    materials: dict,
    *,
    bots_per_roboport: int = 30,
    ore_patches: Sequence[dict] = (),
) -> dict:
    """Strip local sources and extend the canonical sandbox backbone."""
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
    robots = plan_roboport_network(robot_sites)

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

    power_sites.extend(roboport_power_sites(roboport_positions(robots)))


    power = plan_power_network(power_sites, source=CANONICAL_POWER_SOURCE)
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
    if ore_patches:
        scaffolding["ore_patches"] = list(ore_patches)
    return {"infrastructure": infrastructure, "plans": stripped, "scaffolding": scaffolding}