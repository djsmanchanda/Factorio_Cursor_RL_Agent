# Path: training/laya_power.py
# Purpose: Realize power for a Laya build: reuse existing coverage, cover machines with medium poles, relay long distances with big poles, and allow shared substations only in mine and mall areas.

"""Power variants for one build (docs/rl/Laya_Strategy_Guidelines.md, Power).

The saved-world generator powers every build with a substation chain.  The
user's rules: substations are expensive (steel, advanced circuits, plastic)
and belong only in mine and mall areas, where one substation powers many
machines; elsewhere use medium poles; use big poles for long distances.

``power_variants`` strips the generator's power actions and returns the legal
alternatives for the same build.  It enumerates; the policy chooses.  Every
returned plan passes plan validation and has every wire gap within reach.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from orchestrator.laya_operations import sha256_hash
from planners.infrastructure_geometry import boxes_overlap, step_points
from planners.plan_validation import ENTITY_FOOTPRINTS, validate_build_plan
from training.laya_saved_world import ghost

POLES = {  # supply radius, wire reach, footprint
    "medium-electric-pole": (3.5, 9.0, 1),
    "big-electric-pole": (2.0, 30.0, 2),
    "substation": (9.0, 18.0, 2),
}
POWERED = frozenset({"electric-mining-drill", "electric-furnace", "assembling-machine-1", "assembling-machine-2",
                     "assembling-machine-3", "inserter", "long-handed-inserter", "fast-inserter", "roboport", "lab"})
# User rule 2026-10-03: substations only where one powers many machines.
SUBSTATION_FAMILIES = frozenset({"mine", "assemble"})
MAX_COVER_POLES = 6
LONG_DISTANCE = 18.0  # Beyond two medium spans a big-pole relay is offered.


def _size(entity: str) -> float:
    return POLES[entity][2] if entity in POLES else ENTITY_FOOTPRINTS.get(entity, 1)


def _xy(action: Mapping) -> tuple[float, float]:
    return action["position"]["x"], action["position"]["y"]


def _covers(pole: tuple[float, float], supply: float, action: Mapping) -> bool:
    # Supply area is a square of side 2*supply; any overlap powers the entity.
    return boxes_overlap(pole, 2 * supply, _xy(action), _size(action["entity"]))


def split_build(candidate: Mapping) -> tuple[list[dict], list[dict]]:
    actions = list(candidate["plan"]["phases"][0]["actions"])
    power = [a for a in actions if a["entity"] in POLES]
    return power, [a for a in actions if a["entity"] not in POLES]


def _clear(point: tuple[float, float], size: float, build: Sequence[Mapping]) -> bool:
    return not any(boxes_overlap(point, size, _xy(a), _size(a["entity"])) for a in build)


def _toward(a: tuple[float, float], b: tuple[float, float], distance: float) -> tuple[float, float]:
    span = math.dist(a, b)
    if span <= distance:
        return b
    return a[0] + (b[0] - a[0]) * distance / span, a[1] + (b[1] - a[1]) * distance / span


def medium_cover(build: Sequence[Mapping], anchor: tuple[float, float]) -> list[tuple[float, float]] | None:
    """Greedy medium-pole positions so every powered entity overlaps a supply area."""
    targets = [a for a in build if a["entity"] in POWERED]
    if not targets:
        return []
    xs = [_xy(a)[0] for a in targets]
    ys = [_xy(a)[1] for a in targets]
    spots = [(x + .5, y + .5) for x in range(math.floor(min(xs)) - 4, math.ceil(max(xs)) + 4)
             for y in range(math.floor(min(ys)) - 4, math.ceil(max(ys)) + 4)]
    spots = [s for s in spots if _clear(s, 1, build)]
    supply = POLES["medium-electric-pole"][0]
    uncovered, chosen = list(targets), []
    while uncovered and len(chosen) < MAX_COVER_POLES:
        best = max(spots, key=lambda s: (sum(_covers(s, supply, a) for a in uncovered), -math.dist(s, anchor)), default=None)
        if best is None or not any(_covers(best, supply, a) for a in uncovered):
            return None
        chosen.append(best)
        uncovered = [a for a in uncovered if not _covers(best, supply, a)]
    return None if uncovered else chosen


def _link(network: list[tuple[float, float]], target: tuple[float, float], build: Sequence[Mapping]) -> list:
    """Medium hops from the nearest network pole to ``target``, avoiding the build."""
    start = min(network, key=lambda p: math.dist(p, target))
    return [(x + .5, y + .5) for x, y in step_points(start, target, 7)[:-1] if _clear((x + .5, y + .5), 1, build)]


def _big_relay(anchor: tuple[float, float], target: tuple[float, float], build: Sequence[Mapping]) -> list:
    """Big poles from beside the anchor to beside the target (medium wire limits both ends)."""
    start, end = _toward(anchor, target, 6), _toward(target, anchor, 6)
    points = [(round(start[0]), round(start[1]))] + step_points(start, end, 26)
    return [p for p in dict.fromkeys(points) if _clear(p, 2, build)]


def _chain_ok(poles: Sequence[tuple[str, tuple[float, float]]], anchor: tuple[tuple[float, float], float]) -> bool:
    """Every new pole reaches some earlier pole (or the anchor) within the smaller wire reach."""
    placed = [anchor]
    for name, point in poles:
        reach = POLES[name][1]
        if not any(math.dist(point, p) <= min(reach, wire) for p, wire in placed):
            return False
        placed.append((point, reach))
    return True


def _variant(candidate: Mapping, build: list[dict], poles: list[tuple[str, tuple[float, float]]],
             anchor: tuple[tuple[float, float], float], tag: str, label: str) -> dict | None:
    if not _chain_ok(poles, anchor):
        return None
    actions = [ghost(name, x, y) for name, (x, y) in poles] + build
    plan = {**candidate["plan"], "phases": [{**candidate["plan"]["phases"][0], "actions": actions}]}
    try:
        validate_build_plan(plan)
    except ValueError:
        return None
    digest = sha256_hash({"plan": plan, "network_anchor": candidate.get("network_anchor")})
    summary = candidate["summary"].split(" via ")[0]
    return {**candidate, "option_id": f"{candidate['family']}.{tag}{digest[7:17]}", "plan": plan,
            "summary": f"{summary} via {label}", "power": tag}


def power_variants(candidate: Mapping, facts: Mapping) -> list[dict]:
    """Legal power alternatives for one build; never the generator's substation chain."""
    if not candidate.get("plan"):
        return [dict(candidate)]
    power, build = split_build(candidate)
    grid = [p for p in facts["poles"] if p["generated"]]
    targets = [a for a in build if a["entity"] in POWERED]
    if not grid or not targets:
        return []
    existing = [((p["position"]["x"], p["position"]["y"]), p["supply"]) for p in grid]
    if all(any(_covers(xy, supply, a) for xy, supply in existing) for a in targets):
        variant = _variant(candidate, build, [], (existing[0][0], 0.0), "e", "existing power")
        return [variant] if variant else []

    centre = (sum(_xy(a)[0] for a in targets) / len(targets), sum(_xy(a)[1] for a in targets) / len(targets))
    anchor_pole = min(grid, key=lambda p: math.dist((p["position"]["x"], p["position"]["y"]), centre))
    anchor = ((anchor_pole["position"]["x"], anchor_pole["position"]["y"]), float(anchor_pole["wire"]))
    variants = []

    cover = medium_cover(build, anchor[0])
    if cover:
        first = min(cover, key=lambda p: math.dist(p, anchor[0]))
        # Cover poles linked to each other with medium hops, starting from the pole nearest the grid.
        chain, network = [("medium-electric-pole", first)], [first]
        for point in sorted(cover, key=lambda p: math.dist(p, first))[1:]:
            chain += [("medium-electric-pole", h) for h in _link(network, point, build)] + [("medium-electric-pole", point)]
            network.append(point)
        relay = [("medium-electric-pole", h) for h in _link([anchor[0]], first, build)]
        variants.append(_variant(candidate, build, relay + chain, anchor, "m", "medium poles"))
        if math.dist(anchor[0], first) > LONG_DISTANCE:
            big = [("big-electric-pole", p) for p in _big_relay(anchor[0], first, build)]
            variants.append(_variant(candidate, build, big + chain, anchor, "b", "big-pole relay + medium poles"))

    end = next((a for a in reversed(power) if a["entity"] == "substation"), None)
    if candidate["family"] in SUBSTATION_FAMILIES and end is not None:
        target = _xy(end)
        hops = [("medium-electric-pole", h) for h in _link([anchor[0]], target, build)]
        if math.dist(anchor[0], target) > LONG_DISTANCE:
            hops = [("big-electric-pole", p) for p in _big_relay(anchor[0], target, build)]
        variants.append(_variant(candidate, build, hops + [("substation", target)], anchor, "s", "shared substation"))
    return [v for v in variants if v]
