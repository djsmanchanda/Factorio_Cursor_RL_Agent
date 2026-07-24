# Path: planners/preflight.py
# Purpose: ONE offline preflight running every known live-failure check over a
# composed BuildPlan bundle before it reaches the game (M6 brief,
# docs/24_next_milestone_brief.md). Composes existing validators wherever they
# exist (planners.infrastructure, planners.plan_validation, core.fluid_systems)
# instead of re-deriving their rules -- see preflight()'s docstring.

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.fluid_systems import _orthogonally_adjacent, validate_network_purity, validate_underground_span
from planners.infrastructure import (
    POLE_SPECS, POWER_SOURCE_ENTITY, POWER_SOURCE_SIZE,
    ROBOPORT_CONSTRUCTION_RADIUS, ROBOPORT_ENTITY, ROBOPORT_LINK_DISTANCE,
)
from planners.infrastructure_geometry import boxes_overlap, chebyshev_distance, distance
from planners.plan_validation import (
    ENTITY_FOOTPRINTS, actions as _plan_actions, is_verified_pumpjack_attachment,
    occupied_tile_indices,
)
from planners.recipe_data import FORBIDDEN_FUEL_ENTITIES

Point = Tuple[float, float]
_PLACEMENTS = {"place_entity", "place_ghost"}

# ENTITY_FOOTPRINTS already carries every footprint this brief calls for
# except 'lab' (3x3, live-verified same as chemical-plant/electric-furnace).
FOOTPRINTS: Dict[str, int] = {**ENTITY_FOOTPRINTS, "lab": 3}

# Inserter pickup/drop reach in tiles: normal tiers reach 1, long-handed reaches 2.
INSERTER_REACH: Dict[str, int] = {
    "inserter": 1, "fast-inserter": 1, "bulk-inserter": 1, "stack-inserter": 1,
    "long-handed-inserter": 2,
}

# Entities that draw electricity: reachability from the single interface is
# meaningless for belts/pipes/chests and the poles/interface themselves.
POWERED_ENTITIES = frozenset(INSERTER_REACH) | {
    "assembling-machine-1", "assembling-machine-2", "assembling-machine-3",
    "electric-furnace", "electric-mining-drill", "chemical-plant", "oil-refinery",
    "lab", ROBOPORT_ENTITY, "pumpjack", "pump",
}

_DIRECTION_VECTORS = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}


def _footprint(entity: str) -> int:
    return FOOTPRINTS.get(entity, 1)


def _named_plans(bundle: Any) -> List[Tuple[str, dict]]:
    """Normalize to [(name, plan), ...]: a list of pairs, or a dict carrying
    'infrastructure' and 'plans' (planners.electronics_block's composition
    shape). Other dict keys are not BuildPlans and are ignored here;
    'fluid_segments' is consulted separately by the fluid-mixing check."""
    if isinstance(bundle, dict):
        combined = list(bundle.get("infrastructure", [])) + list(bundle.get("plans", []))
        if not combined:
            raise ValueError("preflight bundle dict has no 'infrastructure' or 'plans' entries")
        return combined
    return list(bundle)


def _all_placements(named_plans: List[Tuple[str, dict]]) -> List[Tuple[str, dict]]:
    """(plan_name, action) for every place_entity/place_ghost action."""
    return [
        (name, action) for name, plan in named_plans for action in _plan_actions(plan)
        if action.get("action_type") in _PLACEMENTS
    ]


def _pos(action: dict) -> Point:
    return (action["position"]["x"], action["position"]["y"])


# --- 1. duplicate tile: two entities emitted on the same tile ---------------

def _check_duplicate_tiles(placements):
    by_position: Dict[Point, List[Tuple[str, dict]]] = {}
    for name, action in placements:
        by_position.setdefault(_pos(action), []).append((name, action))
    failures = []
    for position, entries in by_position.items():
        if len(entries) > 1:
            names = ", ".join(f"{n}:{a['entity']}" for n, a in entries)
            failures.append({"check": "duplicate_tile",
                              "detail": f"Tile {position} claimed by {len(entries)} entities: {names}",
                              "positions": [position]})
    return failures, None


# --- 2. footprint overlap: 3x3/5x5/4x4/1x1 bodies overlapping ---------------

def _check_footprint_overlap(placements):
    if not placements:
        return [], "no placements in bundle"
    failures = []
    for i in range(len(placements)):
        name_a, action_a = placements[i]
        pos_a, size_a = _pos(action_a), _footprint(action_a["entity"])
        for name_b, action_b in placements[i + 1:]:
            pos_b, size_b = _pos(action_b), _footprint(action_b["entity"])
            if boxes_overlap(pos_a, size_a, pos_b, size_b) and not is_verified_pumpjack_attachment(action_a, action_b):
                failures.append({"check": "footprint_overlap",
                                  "detail": f"{name_a}:{action_a['entity']} at {pos_a} overlaps "
                                            f"{name_b}:{action_b['entity']} at {pos_b}",
                                  "positions": [pos_a, pos_b]})
    return failures, None


# --- 3. power reach: pole graph rooted at the single EEI --------------------

def _check_power(placements):
    poles = [(name, a["entity"], _pos(a)) for name, a in placements if a["entity"] in POLE_SPECS]
    sources = [(name, _pos(a)) for name, a in placements if a["entity"] == POWER_SOURCE_ENTITY]
    if not poles and not sources:
        return [], "no poles or electric-energy-interface in bundle"

    if len(sources) != 1:
        return [{"check": "power_reach",
                  "detail": f"Expected exactly one {POWER_SOURCE_ENTITY}, found {len(sources)}",
                  "positions": [p for _, p in sources]}], None
    source_pos = sources[0][1]
    if not poles:
        return [{"check": "power_reach", "detail": f"No poles in bundle to carry power from {source_pos}",
                  "positions": [source_pos]}], None

    roots = [i for i, (_, entity, pos) in enumerate(poles)
             if boxes_overlap(pos, 2 * POLE_SPECS[entity]["supply"], source_pos, POWER_SOURCE_SIZE)]
    if not roots:
        return [{"check": "power_reach",
                  "detail": f"No pole's supply area covers the {POWER_SOURCE_ENTITY} at {source_pos}; it powers nothing",
                  "positions": [source_pos]}], None

    reached = set(roots)
    frontier = list(roots)
    while frontier:
        current = frontier.pop()
        _, entity_a, pos_a = poles[current]
        for i, (_, entity_b, pos_b) in enumerate(poles):
            if i in reached:
                continue
            reach = min(POLE_SPECS[entity_a]["wire"], POLE_SPECS[entity_b]["wire"])
            if distance(pos_a, pos_b) <= reach:
                reached.add(i)
                frontier.append(i)

    failures = []
    stranded_poles = [poles[i] for i in range(len(poles)) if i not in reached]
    if stranded_poles:
        detail = "; ".join(f"{n}:{e} at {p}" for n, e, p in stranded_poles[:8])
        failures.append({"check": "power_reach",
                          "detail": f"{len(stranded_poles)} pole(s) unreachable from {POWER_SOURCE_ENTITY} "
                                    f"at {source_pos}: {detail}",
                          "positions": [p for _, _, p in stranded_poles]})

    reached_poles = [poles[i] for i in reached]
    unpowered = []
    for name, action in placements:
        entity = action["entity"]
        if entity not in POWERED_ENTITIES:
            continue
        pos = _pos(action)
        covered = any(boxes_overlap(pos, _footprint(entity), pole_pos, 2 * POLE_SPECS[pole_entity]["supply"])
                      for _, pole_entity, pole_pos in reached_poles)
        if not covered:
            unpowered.append((name, entity, pos))
    if unpowered:
        detail = "; ".join(f"{n}:{e} at {p}" for n, e, p in unpowered[:8])
        failures.append({"check": "power_reach",
                          "detail": f"{len(unpowered)} powered entity(ies) uncovered by any connected pole's "
                                    f"supply area: {detail}",
                          "positions": [p for _, _, p in unpowered]})
    return failures, None


# --- 4. fluid mixing: reuse core.fluid_systems.validate_network_purity -----

def _fluid_mixing_failures(segments: list) -> list:
    """Same adjacency rule as validate_network_purity, collecting every
    offending pair instead of raising on the first."""
    failures = []
    for i in range(len(segments)):
        seg_a = segments[i]
        fluid_a = seg_a.get("fluid")
        for j in range(i + 1, len(segments)):
            seg_b = segments[j]
            fluid_b = seg_b.get("fluid")
            if fluid_a is None or fluid_b is None or fluid_a == fluid_b:
                continue
            if seg_a.get("separated_by_pump") or seg_b.get("separated_by_pump"):
                continue
            for tile_a in seg_a["tiles"]:
                for tile_b in seg_b["tiles"]:
                    if _orthogonally_adjacent(tile_a, tile_b):
                        failures.append({"check": "fluid_mixing",
                                          "detail": f"tile {tile_a} ('{fluid_a}') is adjacent to tile {tile_b} "
                                                    f"('{fluid_b}') without a separating pump",
                                          "positions": [tile_a, tile_b]})
    return failures


def _check_fluid_mixing(bundle):
    segments = bundle.get("fluid_segments") if isinstance(bundle, dict) else None
    if not segments:
        return [], "bundle carries no non-empty 'fluid_segments'; cannot determine per-pipe fluid identity"
    try:
        validate_network_purity(segments)
    except ValueError:
        return _fluid_mixing_failures(segments), None
    return [], None


# --- 5. underground span: reuse core.fluid_systems.validate_underground_span

def _check_underground_span(placements):
    undergrounds = [(name, a) for name, a in placements if a["entity"] == "pipe-to-ground"]
    if not undergrounds:
        return [], "no pipe-to-ground entities in bundle"

    failures = []
    groups: Dict[Tuple[str, float], List[Tuple[str, dict, float]]] = {}
    for name, action in undergrounds:
        direction = action.get("direction")
        x, y = _pos(action)
        if direction in ("north", "south"):
            groups.setdefault(("vertical", x), []).append((name, action, y))
        elif direction in ("east", "west"):
            groups.setdefault(("horizontal", y), []).append((name, action, x))
        else:
            failures.append({"check": "underground_span",
                              "detail": f"{name}:pipe-to-ground at {(x, y)} has no/unknown direction; cannot pair it",
                              "positions": [(x, y)]})

    for members in groups.values():
        members = sorted(members, key=lambda m: m[2])
        if len(members) % 2 != 0:
            name, action, _ = members[-1]
            pos = _pos(action)
            failures.append({"check": "underground_span",
                              "detail": f"Unpaired pipe-to-ground {name} at {pos} (odd count sharing its axis)",
                              "positions": [pos]})
            members = members[:-1]
        for k in range(0, len(members), 2):
            name_a, action_a, _ = members[k]
            name_b, action_b, _ = members[k + 1]
            pos_a, pos_b = _pos(action_a), _pos(action_b)
            try:
                validate_underground_span(pos_a, pos_b)
            except ValueError as exc:
                failures.append({"check": "underground_span", "detail": f"{name_a}<->{name_b}: {exc}",
                                  "positions": [pos_a, pos_b]})
    return failures, None


# --- 6. inserter sanity: pickup/drop tiles both empty ground ----------------

def _check_inserter_sanity(named_plans, placements):
    inserters = [(name, a) for name, a in placements if a["entity"] in INSERTER_REACH]
    if not inserters:
        return [], "no inserters in bundle"

    occupied = occupied_tile_indices(named_plans)
    failures = []
    for name, action in inserters:
        entity = action["entity"]
        direction = action.get("direction")
        cx, cy = _pos(action)
        if direction not in _DIRECTION_VECTORS:
            failures.append({"check": "inserter_sanity",
                              "detail": f"{name}:{entity} at {(cx, cy)} has no/unknown direction; "
                                        "cannot check pickup/drop",
                              "positions": [(cx, cy)]})
            continue
        reach = INSERTER_REACH[entity]
        dx, dy = _DIRECTION_VECTORS[direction]
        pickup = (cx + dx * reach, cy + dy * reach)
        drop = (cx - dx * reach, cy - dy * reach)
        pickup_tile = (int(pickup[0] - 0.5), int(pickup[1] - 0.5))
        drop_tile = (int(drop[0] - 0.5), int(drop[1] - 0.5))
        if pickup_tile not in occupied and drop_tile not in occupied:
            failures.append({"check": "inserter_sanity",
                              "detail": f"{name}:{entity} at {(cx, cy)} facing {direction}: pickup tile "
                                        f"{pickup_tile} and drop tile {drop_tile} are both empty ground; "
                                        "it will never move anything",
                              "positions": [(cx, cy), pickup, drop]})
    return failures, None


# --- 7. roboport coverage: every entity within radius, roboports chained ----

def _check_roboport_coverage(placements):
    roboports = [(name, _pos(a)) for name, a in placements if a["entity"] == ROBOPORT_ENTITY]
    if not roboports:
        return [], "no roboports in bundle"
    positions = [pos for _, pos in roboports]

    failures = []
    reached = {0}
    frontier = [0]
    while frontier:
        current = frontier.pop()
        for i, pos in enumerate(positions):
            if i in reached:
                continue
            if chebyshev_distance(positions[current], pos) <= ROBOPORT_LINK_DISTANCE:
                reached.add(i)
                frontier.append(i)
    if len(reached) != len(positions):
        stranded = [positions[i] for i in range(len(positions)) if i not in reached]
        failures.append({"check": "roboport_coverage",
                          "detail": f"{len(stranded)} roboport(s) split into a separate logistic network "
                                    f"(> {ROBOPORT_LINK_DISTANCE:.0f} tiles from the main chain): {stranded[:8]}",
                          "positions": stranded})

    # Construction radius only matters for ghosts: those are what bots build.
    # place_entity items (poles, the roboports themselves, feed scaffolding)
    # are placed directly, bypassing bots entirely.
    uncovered = []
    for name, action in placements:
        if action["entity"] == ROBOPORT_ENTITY or action.get("action_type") != "place_ghost":
            continue
        pos = _pos(action)
        if not any(chebyshev_distance(pos, rp) <= ROBOPORT_CONSTRUCTION_RADIUS for rp in positions):
            uncovered.append((name, action["entity"], pos))
    if uncovered:
        detail = "; ".join(f"{n}:{e} at {p}" for n, e, p in uncovered[:8])
        failures.append({"check": "roboport_coverage",
                          "detail": f"{len(uncovered)} entity(ies) outside every roboport's "
                                    f"{ROBOPORT_CONSTRUCTION_RADIUS:.0f}-tile construction radius: {detail}",
                          "positions": [p for _, _, p in uncovered]})
    return failures, None


# --- 8. water-lake terrain: no land entity may occupy a seeded water tile ---

def _action_tile_indices(action: dict) -> set[tuple[int, int]]:
    size = _footprint(action["entity"])
    x, y = _pos(action)
    left, top = int(x - size / 2), int(y - size / 2)
    return {(tile_x, tile_y) for tile_x in range(left, left + size)
            for tile_y in range(top, top + size)}


def _check_water_lake_overlap(placements, bundle: Any):
    if not isinstance(bundle, dict) or "water_lake_tiles" not in bundle:
        return [], "bundle declares no seeded water-lake tiles"
    water_tiles = {tuple(tile) for tile in bundle["water_lake_tiles"]}
    shoreline_pumps = {tuple(position) for position in bundle.get("water_shoreline_pump_positions", ())}
    failures = []
    for name, action in placements:
        position = _pos(action)
        if action["entity"] == "offshore-pump" and position in shoreline_pumps:
            continue
        overlap = _action_tile_indices(action) & water_tiles
        if overlap:
            failures.append({
                "check": "water_lake_overlap",
                "detail": f"{name}:{action['entity']} at {position} occupies seeded water tile(s) {sorted(overlap)[:4]}",
                "positions": [position],
            })
    return failures, None

# --- 8. electric-only: no burner/boiler/steam-engine entities ---------------

def _check_electric_only(placements):
    offenders = [(name, a["entity"], _pos(a)) for name, a in placements if a["entity"] in FORBIDDEN_FUEL_ENTITIES]
    if not offenders:
        return [], None
    detail = "; ".join(f"{n}:{e} at {p}" for n, e, p in offenders[:8])
    return [{"check": "electric_only", "detail": f"{len(offenders)} fuel-burning entity(ies) present: {detail}",
              "positions": [p for _, _, p in offenders]}], None


_CHECKS = (
    ("duplicate_tile", lambda named, placements, bundle: _check_duplicate_tiles(placements)),
    ("footprint_overlap", lambda named, placements, bundle: _check_footprint_overlap(placements)),
    ("power_reach", lambda named, placements, bundle: _check_power(placements)),
    ("fluid_mixing", lambda named, placements, bundle: _check_fluid_mixing(bundle)),
    ("underground_span", lambda named, placements, bundle: _check_underground_span(placements)),
    ("inserter_sanity", lambda named, placements, bundle: _check_inserter_sanity(named, placements)),
    ("roboport_coverage", lambda named, placements, bundle: _check_roboport_coverage(placements)),
    ("water_lake_overlap", lambda named, placements, bundle: _check_water_lake_overlap(placements, bundle)),
    ("electric_only", lambda named, placements, bundle: _check_electric_only(placements)),
)


def preflight(bundle: Any) -> dict:
    """Run every known live-failure check over the WHOLE composed `bundle`.

    `bundle` is a list of (name, plan) BuildPlan pairs, or a dict carrying
    'infrastructure' and 'plans' (planners.electronics_block's composition
    shape; 'fluid_segments', if present, feeds the fluid-mixing check). Every
    check runs over every plan together -- a cross-plan power or roboport gap
    is exactly what a per-plan validator cannot see.

    Returns {"ok", "failures", "checked", "skipped"}. A check that cannot run
    (no poles, no roboports, no fluid_segments, ...) is reported in "skipped"
    with a reason -- never silently treated as passed.
    """
    named_plans = _named_plans(bundle)
    placements = _all_placements(named_plans)

    result: Dict[str, Any] = {"ok": True, "failures": [], "checked": [], "skipped": []}
    for check_name, run in _CHECKS:
        failures, skip_reason = run(named_plans, placements, bundle)
        if skip_reason is not None:
            result["skipped"].append({"check": check_name, "why": skip_reason})
            continue
        result["checked"].append(check_name)
        result["failures"].extend(failures)
    result["ok"] = not result["failures"]
    return result


def assert_preflight(bundle: Any) -> None:
    """Raise ValueError with a readable multi-line report unless the bundle is clean."""
    result = preflight(bundle)
    if result["ok"]:
        return
    lines = [f"preflight FAILED: {len(result['failures'])} failure(s) across "
             f"{len(result['checked'])} check(s) run ({len(result['skipped'])} skipped)"]
    for failure in result["failures"]:
        lines.append(f"- [{failure['check']}] {failure['detail']}")
    if result["skipped"]:
        lines.append("Skipped checks (could not run):")
        for skipped in result["skipped"]:
            lines.append(f"- {skipped['check']}: {skipped['why']}")
    raise ValueError("\n".join(lines))
