# Path: training/candidates/mining_delivery.py
# Purpose: Compile safe deterministic mining-delivery alternatives for policy selection.

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from planners.belt_bridge import bridge_belt_to_chest
from planners.plan_validation import actions, occupied_tile_indices, validate_build_plan
from training.canonical import plan_hash
from training.contracts import validate_scenario

_DRILL_RATE_PER_TICK = 0.5 / 60.0
_POWER_SOURCE_COVERAGE = 3.5
_POWER_SOURCE_POLE_OFFSET = 2.5
_MATERIAL_COST = {
    "electric-mining-drill": 60,
    "fast-inserter": 11,
    "medium-electric-pole": 5,
    "transport-belt": 2,
}


def _position(x: float, y: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y)}


def _placement(entity: str, point: tuple[float, float], direction: str | None = None) -> dict:
    action = {"action_type": "place_entity", "entity": entity, "position": _position(*point)}
    if direction:
        action["direction"] = direction
    return action


def _drill_positions(scenario: Mapping, count: int) -> tuple[list[dict], float, list[float]]:
    patch = scenario["resource_patch"]["bounds"]
    belt_y = math.floor((patch["y1"] + patch["y2"]) / 2) + 0.5
    xs = [patch["x1"] + 1.5 + 3 * index for index in range(4)]
    xs = [x for x in xs if x + 1.5 <= patch["x2"] + 1]
    sites = [(x, belt_y - 2, "south") for x in xs]
    sites += [(x, belt_y + 2, "north") for x in reversed(xs)]
    if count > len(sites):
        raise ValueError("resource patch cannot fit the required drill candidate")
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


def _safe_bridge(source, sink, preferred: str, blocked: set[tuple[int, int]]) -> list[dict]:
    directions = [preferred] + [name for name in ("north", "east", "south", "west") if name != preferred]
    failures = []
    for entry in directions:
        try:
            bridge = bridge_belt_to_chest(
                source, sink, entry_direction=entry, belt_type="transport-belt",
                inserter_type="fast-inserter", blocked_tiles=blocked,
            )
        except ValueError as exc:
            failures.append(str(exc))
            continue
        inserter = next(action for action in bridge if action["entity"] == "fast-inserter")
        if any(action["entity"] == "transport-belt" and action["position"] == inserter["position"]
               for action in bridge):
            failures.append(f"{entry} route overlaps its sink inserter")
            continue
        return bridge
    raise ValueError("no self-consistent sink entry: " + "; ".join(failures))

def _belt_actions(
    scenario: Mapping, drills: list[dict], belt_y: float, xs: list[float], variant: int,
) -> list[dict]:
    sink_fixture = next(f for f in scenario["fixtures"] if f["kind"] == "item_sink")
    sink = tuple(float(value) for value in sink_fixture["position"])
    source_x = max(xs) + 1 if sink[0] >= sum(xs) / len(xs) else min(xs) - 1
    source = (source_x, belt_y)
    entry = _sink_entry(source, sink)
    blocked = occupied_tile_indices([("drills", {"phases": [{"actions": drills}]})])
    blocked.update(_energy_fixture_tiles(scenario))
    bridge = [{**action, "action_type": "place_entity"}
              for action in _safe_bridge(source, sink, entry, blocked)]
    direction = "east" if source_x > min(xs) else "west"
    row = [
        _placement("transport-belt", (x + 0.5, belt_y), direction)
        for x in range(math.floor(min(xs)) - 1, math.floor(max(xs)) + 2)
        if not math.isclose(x + 0.5, source_x)
    ]
    occupied_positions = {
        (action["position"]["x"], action["position"]["y"])
        for action in bridge if action.get("entity") == "transport-belt"
    }
    unique_row = [
        action for action in row
        if (action["position"]["x"], action["position"]["y"]) not in occupied_positions
    ]
    return bridge + unique_row


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
    top = [(x, belt_y - 4) for x in xs[::2]]
    bottom = [(x, belt_y + 4) for x in reversed(xs[::2])]
    targets = top + bottom if variant == 0 else list(reversed(bottom + top))
    fixture = next(item for item in scenario["fixtures"] if item["kind"] == "power_source")
    source = tuple(float(value) for value in fixture["position"])
    occupied.update(_energy_fixture_tiles(scenario))
    anchor = _source_anchor(source, targets[0], occupied)
    points, previous = [anchor], anchor
    for target in targets:
        distance = math.dist(previous, target)
        steps = max(1, math.ceil(distance / 6.0))
        for index in range(1, steps + 1):
            ratio = index / steps
            raw = (
                round(previous[0] + (target[0] - previous[0]) * ratio) + 0.5,
                round(previous[1] + (target[1] - previous[1]) * ratio) + 0.5,
            )
            points.append(_safe_pole(raw, occupied))
        previous = target
    unique = list(dict.fromkeys(points))
    if any(math.dist(left, right) > 9 for left, right in zip(unique, unique[1:])):
        raise ValueError("candidate power poles exceed medium-pole wire reach")
    drill_points = [
        (action["position"]["x"], action["position"]["y"])
        for action in plan_actions if action.get("entity") == "electric-mining-drill"
    ]
    if any(not any(abs(dx - px) <= 3.5 and abs(dy - py) <= 3.5 for px, py in unique)
           for dx, dy in drill_points):
        raise ValueError("candidate power poles do not supply every drill")
    return [_placement("medium-electric-pole", point) for point in unique]

def _count_entities(plan: Mapping) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions(dict(plan)):
        entity = action.get("entity")
        if entity:
            counts[entity] = counts.get(entity, 0) + 1
    return counts


def _candidate(scenario: Mapping, variant: int, drill_count: int) -> dict:
    drills, belt_y, xs = _drill_positions(scenario, drill_count)
    transport = _belt_actions(scenario, drills, belt_y, xs, variant)
    power = _power_actions(scenario, drills + transport, belt_y, xs, variant)
    plan = {
        "surface": scenario["environment"]["surface_name"],
        "force": scenario["environment"]["force_name"],
        "phases": [{"name": "mining_delivery", "actions": drills + transport + power}],
    }
    validate_build_plan(plan)
    counts = _count_entities(plan)
    budget = scenario["construction_budget"]
    excess = {name: count for name, count in counts.items() if count > budget.get(name, 0)}
    if excess:
        raise ValueError(f"candidate exceeds construction budget: {excess}")
    route_tiles = counts.get("transport-belt", 0)
    material_cost = sum(_MATERIAL_COST.get(name, 1) * count for name, count in counts.items())
    digest = plan_hash(plan)
    return {
        "action_id": f"mining-direct-v{variant}-{digest[7:19]}",
        "plan_hash": digest,
        "features": {
            "predicted_completion": 1.0,
            "predicted_rate_per_tick": drill_count * _DRILL_RATE_PER_TICK,
            "drill_count": drill_count,
            "route_tiles": route_tiles,
            "turn_count": 1 + variant,
            "pole_count": counts.get("medium-electric-pole", 0),
            "material_cost": material_cost,
        },
        "plan": plan,
    }


def mining_delivery_candidates(scenario: Mapping) -> list[dict]:
    """Return two safe alternatives without allowing RL to design geometry."""
    validate_scenario(scenario)
    target = float(scenario["objective"]["target_rate_per_tick"])
    minimum = max(1, math.ceil(target / _DRILL_RATE_PER_TICK))
    budget = int(scenario["construction_budget"]["electric-mining-drill"])
    counts: Iterable[int] = (minimum, min(budget, minimum + 1))
    candidates = [_candidate(scenario, variant, count) for variant, count in enumerate(counts)]
    if len({candidate["plan_hash"] for candidate in candidates}) != len(candidates):
        raise ValueError("candidate catalog must contain distinct BuildPlans")
    return candidates
