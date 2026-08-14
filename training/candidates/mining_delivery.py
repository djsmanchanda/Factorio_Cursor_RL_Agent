# Path: training/candidates/mining_delivery.py
# Purpose: Compile safe deterministic mining-delivery alternatives for policy selection.

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from planners.belt_bridge import bridge_belt_to_chest
from planners.plan_validation import actions, occupied_tile_indices, validate_build_plan
from training.canonical import plan_hash
from training.contracts import validate_scenario
from training.material_costs import MATERIAL_COST_BY_ENTITY
from training.power import POWER_CONSUMER_ENTITIES

_DRILL_RATE_PER_TICK = 0.5 / 60.0
_POWER_SOURCE_COVERAGE = 3.5
_POWER_SOURCE_POLE_OFFSET = 2.5
_OPPOSITE_DIRECTION = {"north": "south", "east": "west", "south": "north", "west": "east"}
_DIRECTION_VECTOR = {"north": (0.0, -1.0), "east": (1.0, 0.0), "south": (0.0, 1.0), "west": (-1.0, 0.0)}


def _is_surface_belt(entity: object) -> bool:
    return str(entity).endswith("transport-belt")

def orthogonal_route_lower_bound(
    start: tuple[float, float], destination: tuple[float, float],
) -> int:
    """Return the minimum orthogonal steps between two finite grid points."""
    points = tuple(float(value) for point in (start, destination) for value in point)
    if len(points) != 4 or any(not math.isfinite(value) for value in points):
        raise ValueError("route endpoints must contain two finite coordinates")
    start_x, start_y, destination_x, destination_y = points
    return math.ceil(abs(destination_x - start_x) + abs(destination_y - start_y))


def _belt_turn_count(route: Iterable[Mapping]) -> int:
    directions = [
        action.get("direction")
        for action in route
        if _is_surface_belt(action.get("entity"))
    ]
    return sum(left != right for left, right in zip(directions, directions[1:]))


def _position(x: float, y: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y)}


def _placement(entity: str, point: tuple[float, float], direction: str | None = None) -> dict:
    action = {"action_type": "place_entity", "entity": entity, "position": _position(*point)}
    if direction:
        action["direction"] = direction
    return action


def _drill_positions(
    scenario: Mapping, count: int, *, corridor_index: int = 0, corridor_count: int = 1,
) -> tuple[list[dict], float, list[float]]:
    patch = scenario["resource_patch"]["bounds"]
    if corridor_count < 1 or not 0 <= corridor_index < corridor_count:
        raise ValueError("mining corridor index is outside its corridor count")
    if corridor_count == 1:
        belt_y = math.floor((patch["y1"] + patch["y2"]) / 2) + 0.5
    else:
        patch_height = patch["y2"] - patch["y1"] + 1
        if patch_height < corridor_count * 8:
            raise ValueError("resource patch is too short for independent mining corridors")
        stride = patch_height / corridor_count
        belt_y = math.floor(patch["y1"] + stride * (corridor_index + 0.5)) + 0.5
    staged = "staged-demand" in scenario.get("curriculum", {}).get("tags", [])
    max_positions = (max(1, math.floor((patch["x2"] - patch["x1"] + 1) / 3))
                     if staged else 4)
    xs = [patch["x1"] + 1.5 + 3 * index for index in range(max_positions)]
    xs = [x for x in xs if x + 1.5 <= patch["x2"] + 1]
    sites = [(x, belt_y - 2, "south") for x in xs]
    sites += [(x, belt_y + 2, "north") for x in reversed(xs)]
    count = min(count, len(sites))
    return [_placement("electric-mining-drill", site[:2], site[2]) for site in sites[:count]], belt_y, xs

def _sink_entry(source: tuple[float, float], sink: tuple[float, float]) -> str:
    dx, dy = sink[0] - source[0], sink[1] - source[1]
    if abs(dx) >= abs(dy):
        return "west" if dx > 0 else "east"
    return "north" if dy > 0 else "south"


def _energy_fixture_tiles(scenario: Mapping) -> set[tuple[int, int]]:
    """Reserve every protected generation or storage fixture from candidate structures."""
    fixtures = [
        _placement(fixture["entity"], tuple(fixture["position"]))
        for fixture in scenario["fixtures"]
        if fixture["kind"] in {"power_source", "power_storage"}
    ]
    return occupied_tile_indices([("energy-fixtures", {"phases": [{"actions": fixtures}]})])


def _safe_bridge(
    source, sink, preferred: str, blocked: set[tuple[int, int]],
    *, belt_type: str = "transport-belt", transfer_type: str = "fast-inserter",
) -> list[dict]:
    directions = [preferred] + [name for name in ("north", "east", "south", "west") if name != preferred]
    failures = []
    for entry in directions:
        try:
            bridge = bridge_belt_to_chest(
                source, sink, entry_direction=entry, belt_type=belt_type,
                inserter_type=transfer_type, blocked_tiles=blocked,
            )
        except ValueError as exc:
            failures.append(str(exc))
            continue
        transfer = next(action for action in bridge if action["entity"] == transfer_type)
        if transfer_type.endswith("loader"):
            transfer["underground_type"] = "input"
            dx, dy = _DIRECTION_VECTOR[transfer["direction"]]
            transfer["position"] = {
                "x": transfer["position"]["x"] + dx * 0.5,
                "y": transfer["position"]["y"] + dy * 0.5,
            }
            transfer["direction"] = _OPPOSITE_DIRECTION[transfer["direction"]]
            tx, ty = transfer["position"].values()
            bridge[:] = [
                action for action in bridge
                if not (
                    _is_surface_belt(action["entity"])
                    and abs(action["position"]["x"] - tx)
                    + abs(action["position"]["y"] - ty) == 0.5
                )
            ]
        if any(_is_surface_belt(action["entity"]) and action["position"] == transfer["position"]
               for action in bridge):
            failures.append(f"{entry} route overlaps its sink transfer")
            continue
        return bridge
    raise ValueError("no self-consistent sink entry: " + "; ".join(failures))

def _belt_actions(
    scenario: Mapping, drills: list[dict], belt_y: float, xs: list[float], variant: int,
    sink_fixture_id: str | None = None, reserved_actions: Iterable[Mapping] = (),
) -> tuple[
    list[dict], list[dict], list[dict], tuple[float, float], tuple[float, float],
]:
    sink_fixture = next(
        fixture for fixture in scenario["fixtures"]
        if fixture["kind"] == "item_sink"
        and (sink_fixture_id is None or fixture["id"] == sink_fixture_id)
    )
    sink = tuple(float(value) for value in sink_fixture["position"])
    staged = "staged-demand" in scenario.get("curriculum", {}).get("tags", [])
    belt_type = "express-transport-belt" if staged else "transport-belt"
    transfer_type = "express-loader" if staged else "fast-inserter"
    # Keep the first delivery turn outside the final drill's 3x3 footprint.
    source_x = max(xs) + 2 if sink[0] >= sum(xs) / len(xs) else min(xs) - 2
    source = (source_x, belt_y)
    entry = _sink_entry(source, sink)
    reserved = list(drills) + list(reserved_actions)
    blocked = occupied_tile_indices([("reserved", {"phases": [{"actions": reserved}]})])
    blocked.update(_energy_fixture_tiles(scenario))
    bridge = [{**action, "action_type": "place_entity"}
              for action in _safe_bridge(
                  source, sink, entry, blocked,
                  belt_type=belt_type, transfer_type=transfer_type,
              )]
    direction = "east" if source_x > min(xs) else "west"
    if source_x > max(xs):
        row_start = min(xs) - 1
        row_end = source_x - 1
    else:
        row_start = source_x + 1
        row_end = max(xs) + 1
    row = []
    cursor = row_start
    while cursor <= row_end:
        row.append(_placement(belt_type, (cursor, belt_y), direction))
        cursor += 1
    occupied_positions = {
        (action["position"]["x"], action["position"]["y"])
        for action in bridge
        if (_is_surface_belt(action.get("entity"))
            or str(action.get("entity", "")).endswith("underground-belt")
            or str(action.get("entity", "")).endswith("splitter"))
    }
    unique_row = [
        action for action in row
        if (action["position"]["x"], action["position"]["y"]) not in occupied_positions
    ]
    return bridge + unique_row, bridge, unique_row, source, sink

def _safe_pole(point: tuple[float, float], occupied: set[tuple[int, int]]) -> tuple[float, float]:
    x, y = point
    choices = [
        (x + dx, y + dy)
        for radius in range(5)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) == radius
    ]
    available = next(
        (choice for choice in choices
         if (math.floor(choice[0]), math.floor(choice[1])) not in occupied),
        None,
    )
    if available is None:
        raise ValueError(f"no clear power-pole tile near {point}")
    return available


def _source_anchor(
    source: tuple[float, float], target: tuple[float, float], occupied: set[tuple[int, int]],
) -> tuple[float, float]:
    """Place the first pole in the source fixture's supply area, toward its route."""
    dx, dy = target[0] - source[0], target[1] - source[1]
    if abs(dx) >= abs(dy):
        offset = _POWER_SOURCE_POLE_OFFSET if dx >= 0 else -_POWER_SOURCE_POLE_OFFSET
        raw = (source[0] + offset, source[1] + 0.5)
    else:
        offset = _POWER_SOURCE_POLE_OFFSET if dy >= 0 else -_POWER_SOURCE_POLE_OFFSET
        raw = (source[0] + 0.5, source[1] + offset)
    anchor = _safe_pole(raw, occupied)
    if math.dist(source, anchor) > _POWER_SOURCE_COVERAGE:
        raise ValueError("candidate power source is outside its first pole supply area")
    return anchor


def _power_actions(
    scenario: Mapping, plan_actions: list[dict], belt_y: float, xs: list[float], variant: int,
) -> list[dict]:
    occupied = occupied_tile_indices([("production", {"phases": [{"actions": plan_actions}]})])
    consumers = [
        (action["position"]["x"], action["position"]["y"])
        for action in plan_actions
        if action.get("entity") in POWER_CONSUMER_ENTITIES
    ]
    if not consumers:
        raise ValueError("candidate contains no electricity consumers to supply")
    fixture = next(item for item in scenario["fixtures"] if item["kind"] == "power_source")
    source = tuple(float(value) for value in fixture["position"])
    occupied.update(_energy_fixture_tiles(scenario))
    ordered = sorted(consumers, key=lambda point: (math.dist(source, point), point))
    if variant:
        ordered.reverse()
    anchor = _source_anchor(source, ordered[0], occupied)
    points, edges = [anchor], []
    occupied.add((math.floor(anchor[0]), math.floor(anchor[1])))
    remaining = list(ordered)
    while remaining:
        target = min(
            remaining,
            key=lambda point: (min(math.dist(point, node) for node in points), point),
        )
        previous = min(points, key=lambda node: math.dist(node, target))
        if abs(target[0] - previous[0]) <= 3.5 and abs(target[1] - previous[1]) <= 3.5:
            remaining.remove(target)
            continue
        distance = math.dist(previous, target)
        steps = max(1, math.ceil(distance / 6.0))
        branch_previous = previous
        for index in range(1, steps + 1):
            ratio = index / steps
            raw = (
                round(previous[0] + (target[0] - previous[0]) * ratio) + 0.5,
                round(previous[1] + (target[1] - previous[1]) * ratio) + 0.5,
            )
            pole = _safe_pole(raw, occupied)
            if pole != branch_previous:
                points.append(pole)
                edges.append((branch_previous, pole))
                occupied.add((math.floor(pole[0]), math.floor(pole[1])))
                branch_previous = pole
        remaining.remove(target)
    unique = list(dict.fromkeys(points))
    if any(math.dist(left, right) > 9 for left, right in edges):
        raise ValueError("candidate power poles exceed medium-pole wire reach")
    if any(not any(abs(x - px) <= 3.5 and abs(y - py) <= 3.5 for px, py in unique)
           for x, y in consumers):
        raise ValueError("candidate power poles do not supply every electricity consumer")
    return [_placement("medium-electric-pole", point) for point in unique]

def _count_entities(plan: Mapping) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions(dict(plan)):
        entity = action.get("entity")
        if entity:
            counts[entity] = counts.get(entity, 0) + 1
    return counts


def _candidate(
    scenario: Mapping, variant: int, drill_counts: tuple[int, ...],
    target_rate_per_tick: float, sink_fixture_ids: tuple[str, ...],
) -> dict:
    stages = scenario.get("objective", {}).get("stages") or []
    corridor_count = max(len(sink_fixture_ids), 2 if len(stages) >= 3 else 1)
    line_specs = []
    all_drills: list[dict] = []
    for corridor_index, (sink_fixture_id, drill_count) in enumerate(zip(sink_fixture_ids, drill_counts)):
        drills, belt_y, xs = _drill_positions(
            scenario, drill_count, corridor_index=corridor_index, corridor_count=corridor_count,
        )
        line_specs.append((sink_fixture_id, drills, belt_y, xs))
        all_drills.extend(drills)

    line_results = {}
    transport_actions: list[dict] = []
    line_order = range(len(line_specs)) if variant == 0 else reversed(range(len(line_specs)))
    for line_index in line_order:
        sink_fixture_id, drills, belt_y, xs = line_specs[line_index]
        transport, delivery_route, collection_belts, source, sink = _belt_actions(
            scenario, drills, belt_y, xs, variant, sink_fixture_id,
            reserved_actions=all_drills + transport_actions,
        )
        transport_actions.extend(transport)
        line_results[line_index] = (delivery_route, collection_belts, source, sink)

    power = _power_actions(scenario, all_drills + transport_actions, 0.0, [], variant)
    plan = {
        "surface": scenario["environment"]["surface_name"],
        "force": scenario["environment"]["force_name"],
        "phases": [{"name": "mining_delivery", "actions": all_drills + transport_actions + power}],
    }
    validate_build_plan(plan)
    counts = _count_entities(plan)
    budget = scenario["construction_budget"]
    excess = {name: count for name, count in counts.items() if count > budget.get(name, 0)}
    if excess:
        raise ValueError(f"candidate exceeds construction budget: {excess}")

    actual_delivery_route_tiles = sum(
        sum(_is_surface_belt(action.get("entity")) for action in line_results[index][0])
        for index in range(len(line_specs))
    )
    collection_belt_tiles = sum(
        sum(_is_surface_belt(action.get("entity")) for action in line_results[index][1])
        for index in range(len(line_specs))
    )
    route_tiles = sum(count for entity, count in counts.items() if _is_surface_belt(entity))
    if route_tiles != actual_delivery_route_tiles + collection_belt_tiles:
        raise ValueError("candidate belt accounting does not match its generated plan")
    shortest_delivery_route_tiles = 0
    turn_count = 0
    for index in range(len(line_specs)):
        delivery_route, _collection, source, sink = line_results[index]
        terminal_transfer_hops = sum(
            str(action.get("entity", "")).endswith(("inserter", "loader"))
            for action in delivery_route
        )
        shortest_delivery_route_tiles += max(
            0, orthogonal_route_lower_bound(source, sink) - terminal_transfer_hops,
        )
        turn_count += _belt_turn_count(delivery_route)
    route_excess_tiles = max(0, actual_delivery_route_tiles - shortest_delivery_route_tiles)
    route_efficiency = (
        min(1.0, shortest_delivery_route_tiles / actual_delivery_route_tiles)
        if actual_delivery_route_tiles else float(shortest_delivery_route_tiles == 0)
    )
    unknown_costs = set(counts).difference(MATERIAL_COST_BY_ENTITY)
    if unknown_costs:
        raise ValueError(f"candidate has no material cost facts for: {sorted(unknown_costs)}")
    material_cost = sum(MATERIAL_COST_BY_ENTITY[name] * count for name, count in counts.items())
    occupied_land_tiles = len(occupied_tile_indices([("candidate", plan)]))
    digest = plan_hash(plan)
    drill_count = counts.get("electric-mining-drill", 0)
    return {
        "action_id": f"mining-direct-v{variant}-{digest[7:19]}",
        "plan_hash": digest,
        "features": {
            "predicted_completion": 1.0,
            "predicted_rate_per_tick": drill_count * _DRILL_RATE_PER_TICK,
            "drill_count": drill_count,
            "sink_count": len(sink_fixture_ids),
            "route_tiles": route_tiles,
            "collection_belt_tiles": collection_belt_tiles,
            "actual_delivery_route_tiles": actual_delivery_route_tiles,
            "shortest_delivery_route_tiles": shortest_delivery_route_tiles,
            "route_excess_tiles": route_excess_tiles,
            "route_efficiency": route_efficiency,
            "occupied_land_tiles": occupied_land_tiles,
            "turn_count": turn_count,
            "pole_count": counts.get("medium-electric-pole", 0),
            "material_cost": material_cost,
        },
        "plan": plan,
    }


def mining_delivery_candidates(
    scenario: Mapping, *, target_rate_per_tick: float | None = None,
    sink_fixture_id: str | None = None,
    sink_fixture_ids: tuple[str, ...] | None = None,
    validate_contract: bool = True,
) -> list[dict]:
    """Return safe mining alternatives for one or more measured delivery sinks."""
    if validate_contract:
        validate_scenario(scenario)
    if sink_fixture_id is not None and sink_fixture_ids is not None:
        raise ValueError("use sink_fixture_id or sink_fixture_ids, not both")
    target = float(
        target_rate_per_tick
        if target_rate_per_tick is not None
        else scenario["objective"]["target_rate_per_tick"]
    )
    resolved_sink_ids = tuple(sink_fixture_ids or ())
    if not resolved_sink_ids:
        if sink_fixture_id is not None:
            resolved_sink_ids = (sink_fixture_id,)
        else:
            objective_ids = scenario.get("objective", {}).get("destination_fixture_ids") or ()
            resolved_sink_ids = tuple(objective_ids) or (
                str(scenario["objective"]["destination_fixture_id"]),
            )
    known_sink_ids = {
        str(fixture["id"]) for fixture in scenario["fixtures"] if fixture["kind"] == "item_sink"
    }
    if not resolved_sink_ids or not set(resolved_sink_ids) <= known_sink_ids:
        raise ValueError("candidate delivery sinks are not declared item fixtures")

    budget = int(scenario["construction_budget"]["electric-mining-drill"])
    patch = scenario["resource_patch"]["bounds"]
    staged = "staged-demand" in scenario.get("curriculum", {}).get("tags", [])
    patch_positions = max(1, math.floor((patch["x2"] - patch["x1"] + 1) / 3))
    available_per_corridor = patch_positions * 2 if staged else 8
    per_sink_target = target / len(resolved_sink_ids)
    minimum_per_sink = max(1, math.ceil(per_sink_target / _DRILL_RATE_PER_TICK))
    if minimum_per_sink * len(resolved_sink_ids) > budget:
        raise ValueError("staged sink demand exceeds the mining drill budget")
    if len(resolved_sink_ids) > 1:
        if minimum_per_sink > available_per_corridor:
            raise ValueError("staged sink demand exceeds one mining corridor")
        catalogs = (
            tuple(minimum_per_sink for _ in resolved_sink_ids),
            tuple(minimum_per_sink for _ in resolved_sink_ids),
        )
    else:
        available = min(budget, available_per_corridor)
        lower = min(minimum_per_sink, available)
        upper = min(available, lower + 1)
        if upper == lower:
            lower = max(1, upper - 1)
        catalogs = ((lower,), (upper,))
    candidates = [
        _candidate(scenario, variant, counts, target, resolved_sink_ids)
        for variant, counts in enumerate(catalogs)
    ]
    if len({candidate["plan_hash"] for candidate in candidates}) != len(candidates):
        raise ValueError("candidate catalog must contain distinct BuildPlans")
    return candidates