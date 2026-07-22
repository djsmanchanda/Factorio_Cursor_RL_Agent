# Path: planners/infrastructure.py
# Purpose: Factory-wide infrastructure that ties SCATTERED production sites
# together: ONE electric network fed by a SINGLE power source, and ONE roboport
# logistic network whose construction radii cover every build site.
#
# ---------------------------------------------------------------------------
# LIVE-VERIFIED PROTOTYPE CONSTANTS (Factorio 2.0, RCON, surface
# 'planner-sandbox', force 'planner', 2026-07-22). Command used:
#   /sc for _,n in pairs({'small-electric-pole','medium-electric-pole',
#       'big-electric-pole','substation'}) do local p=prototypes.entity[n]
#       ... p.get_max_wire_distance() ... p.get_supply_area_distance() ... end
#       local r=prototypes.entity['roboport'] ... r.logistic_radius ...
#       r.construction_radius ...
# Raw output:
#   small-electric-pole  wire=7.5 supply=2.5
#   medium-electric-pole wire=9   supply=3.5
#   big-electric-pole    wire=32  supply=2      <- 32, NOT the 30 often quoted
#   substation           wire=18  supply=9
#   roboport logistic=25 construction=55
# Footprints (tile_width x tile_height, same query): big-electric-pole 2x2,
# substation 2x2, electric-energy-interface 2x2, roboport 4x4, medium pole 1x1.
#
# THE SHORTER-REACH RULE
#   Two poles are wired together only when their separation is within the
#   SHORTER of the two max_wire_distances. So a big pole (32) and a medium pole
#   (9) connect only within 9; a big pole and a substation within 18. Every
#   graph edge below uses min(wire_a, wire_b) for exactly this reason, and
#   validate_power_connectivity() is what catches a site whose substation was
#   parked 19 tiles from the spine and therefore silently had no power.
#
# THE SUPPLY-AREA RULE
#   A pole's supply area is the square `position +/- supply_area_distance`. An
#   entity is powered when its bounding box intersects that square. The single
#   electric-energy-interface is not a pole: it joins the network only because
#   some pole's supply area covers it, which is why plan_power_network() plants
#   a big pole two tiles east of the source and validate_power_connectivity()
#   refuses a plan where no emitted pole covers the interface.
#
# ROBOPORT LINKING
#   Each roboport owns a logistic square of radius 25 and a construction square
#   of radius 55. Two roboports join the same logistic network when their
#   logistic squares touch, i.e. Chebyshev separation <= 2 * 25 = 50. We plan
#   and validate against a conservative ROBOPORT_LINK_DISTANCE of 46 so a
#   rounded tile never lands on the cliff edge, and chain at spacing 40.
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from jsonschema import Draft7Validator

from planners.infrastructure_geometry import (
    FootprintPlacer,
    boxes_overlap,
    chebyshev_distance,
    distance,
    l_route,
    minimum_spanning_tree_edges,
    step_points,
)
from planners.local_layout_planner import _reject_fuel_entities

Point = Tuple[float, float]

POLE_SPECS: Dict[str, Dict[str, float]] = {
    "small-electric-pole": {"wire": 7.5, "supply": 2.5, "size": 1},
    "medium-electric-pole": {"wire": 9.0, "supply": 3.5, "size": 1},
    "big-electric-pole": {"wire": 32.0, "supply": 2.0, "size": 2},
    "substation": {"wire": 18.0, "supply": 9.0, "size": 2},
}

POWER_SOURCE_ENTITY = "electric-energy-interface"
POWER_SOURCE_SIZE = 2

# Spine step. Big poles reach 32; 28 leaves four tiles of routing slack.
# Colliding spine points are omitted only when final graph validation stays connected.
SPINE_SPACING = 28.0
# Distance from a site substation to the spine pole that feeds it. The binding
# limit is the substation's own 18-tile wire reach.
SITE_POLE_OFFSET = (5, 0)

ROBOPORT_ENTITY = "roboport"
ROBOPORT_SIZE = 4
ROBOPORT_LOGISTIC_RADIUS = 25.0
ROBOPORT_CONSTRUCTION_RADIUS = 55.0
ROBOPORT_LINK_DISTANCE = 46.0  # conservative; the hard game limit is 2*25 = 50
ROBOPORT_SPACING = 40

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLACEMENTS = {"place_entity", "place_ghost"}


def _point(action: dict) -> Point:
    position = action["position"]
    return (position["x"], position["y"])


def _actions(plan: dict) -> Iterable[dict]:
    for phase in plan.get("phases", []):
        yield from phase.get("actions", [])


def _validate(plan: dict) -> None:
    """Schema-validate a BuildPlan and enforce the electric-only invariant."""
    schema_path = _REPO_ROOT / "schemas" / "build_plan.schema.json"
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = list(Draft7Validator(schema).iter_errors(plan))
    if errors:
        joined = "\n".join(
            f"- {'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError("BuildPlan validation FAILED:\n" + joined)
    _reject_fuel_entities(plan)


# --- power ------------------------------------------------------------------

def _site_substation(site: dict) -> Point:
    if "substation" not in site:
        raise ValueError(f"Power site {site.get('name', site)!r} needs a 'substation' position")
    return tuple(site["substation"])


def _site_anchor(site: dict) -> Point:
    if site.get("pole_anchor") is not None:
        return tuple(site["pole_anchor"])
    substation = _site_substation(site)
    return (substation[0] + SITE_POLE_OFFSET[0], substation[1] + SITE_POLE_OFFSET[1])


def plan_power_network(
    sites: List[dict],
    source: tuple,
    spine_spacing: float = SPINE_SPACING,
    spine_pole: str = "big-electric-pole",
) -> dict:
    """ONE electric-energy-interface at `source`, a big-pole spine out to every
    site, and a substation at each site.

    `sites` entries are dicts:
        {"name": str,
         "substation": (x, y),       # 2x2, the site's local distribution hub
         "pole_anchor": (x, y)}      # optional; where the spine terminates.
                                     # Defaults to substation + SITE_POLE_OFFSET.
    A site's `pole_anchor` must lie within the substation's 18-tile wire reach.
    Callers give it explicitly whenever the straight default would drop a pole
    on top of a machine row -- the spine is routed rectilinearly precisely so
    those corridors are predictable.

    Geometry:
      * the interface sits at `source` (2x2);
      * a spine pole is planted at source + (2, 0): its 4x4 supply area covers
        the interface's east tiles, which is the ONLY way an EEI joins a network;
      * every site is reached by a leg routed horizontally along the source's y
        and then vertically at the anchor's x, with spine poles every
        `spine_spacing` tiles. Legs leaving the source in the same direction
        share their intermediate tiles exactly and dedupe to one pole.

    Emits three phases: power_source, power_spine, power_sites. Every entity is
    a place_entity (immediate), not a ghost: bots cannot build without power, so
    the network must exist before any ghost does.
    """
    if spine_pole not in POLE_SPECS:
        raise ValueError(f"Unknown spine pole: {spine_pole}")
    if not sites:
        raise ValueError("plan_power_network needs at least one site")
    source = (round(source[0]), round(source[1]))

    spine_size = POLE_SPECS[spine_pole]["size"]
    hub = (source[0] + 2, source[1])
    placer = FootprintPlacer(spine_size)
    placer.add(hub)

    for site in sites:
        anchor = _site_anchor(site)
        corners = l_route(hub, anchor)
        for start, end in zip(corners, corners[1:]):
            for point in step_points(start, end, spine_spacing):
                placer.add(point)

    substations: List[dict] = []
    seen: List[Point] = []
    for site in sites:
        position = _site_substation(site)
        if any(chebyshev_distance(position, other) < POLE_SPECS["substation"]["size"] for other in seen):
            continue
        seen.append(position)
        substations.append({"action_type": "place_entity", "entity": "substation",
                            "position": {"x": position[0], "y": position[1]}})

    buildable_spine = [
        point for point in placer.points
        if not any(
            boxes_overlap(point, spine_size, _point(action), POLE_SPECS["substation"]["size"])
            for action in substations
        )
    ]
    plan = {"phases": [
        {"name": "power_source", "actions": [
            {"action_type": "place_entity", "entity": POWER_SOURCE_ENTITY,
             "position": {"x": source[0], "y": source[1]}},
        ]},
        {"name": "power_spine", "actions": [
            {"action_type": "place_entity", "entity": spine_pole,
             "position": {"x": x, "y": y}} for x, y in buildable_spine
        ]},
        {"name": "power_sites", "actions": substations},
    ]}
    _validate(plan)
    validate_power_connectivity(plan)
    return plan


def power_poles(plan: dict) -> List[Tuple[str, Point]]:
    """Every pole the plan places, as (entity, position)."""
    return [(action["entity"], _point(action))
            for action in _actions(plan)
            if action.get("action_type") in _PLACEMENTS and action.get("entity") in POLE_SPECS]


def validate_power_connectivity(plan: dict) -> None:
    """Raise unless every pole in `plan` is wired back to the single source.

    Three failures are caught, all of which read in-game as an unexplained
    `no_power` on machines nowhere near the mistake:
      1. no electric-energy-interface, or more than one;
      2. no emitted pole whose supply area covers the interface, so the source
         feeds nothing;
      3. a pole (typically a site substation parked just past 18 tiles from the
         spine) that no chain of min(wire_a, wire_b) hops reaches.
    Overlapping footprints are rejected too: two 2x2 poles one tile apart cannot
    both be built, and the survivor is whichever the engine happened to place.
    """
    sources = [action for action in _actions(plan)
               if action.get("action_type") in _PLACEMENTS
               and action.get("entity") == POWER_SOURCE_ENTITY]
    if len(sources) != 1:
        raise ValueError(
            f"Power plan must contain exactly one {POWER_SOURCE_ENTITY}, found {len(sources)}"
        )
    source_position = _point(sources[0])

    poles = power_poles(plan)
    if not poles:
        raise ValueError("Power plan contains no poles")

    for index, (name_a, position_a) in enumerate(poles):
        for name_b, position_b in poles[index + 1:]:
            if boxes_overlap(position_a, POLE_SPECS[name_a]["size"],
                              position_b, POLE_SPECS[name_b]["size"]):
                raise ValueError(
                    f"Pole footprints overlap: {name_a} at {position_a} and "
                    f"{name_b} at {position_b}"
                )

    roots = [
        index for index, (name, position) in enumerate(poles)
        if boxes_overlap(position, 2 * POLE_SPECS[name]["supply"],
                          source_position, POWER_SOURCE_SIZE)
    ]
    if not roots:
        raise ValueError(
            f"No pole's supply area covers the {POWER_SOURCE_ENTITY} at {source_position}; "
            "the source would power nothing"
        )

    reached = set(roots)
    frontier = list(roots)
    while frontier:
        current = frontier.pop()
        name_a, position_a = poles[current]
        for index, (name_b, position_b) in enumerate(poles):
            if index in reached:
                continue
            reach = min(POLE_SPECS[name_a]["wire"], POLE_SPECS[name_b]["wire"])
            if distance(position_a, position_b) <= reach:
                reached.add(index)
                frontier.append(index)

    stranded = [f"{poles[i][0]} at {poles[i][1]}" for i in range(len(poles)) if i not in reached]
    if stranded:
        raise ValueError(
            "Poles unreachable from the power source (" + str(len(stranded)) + "): "
            + "; ".join(stranded[:8]) + ("; ..." if len(stranded) > 8 else "")
        )


# --- roboports --------------------------------------------------------------

def _site_position(site: dict) -> Point:
    if site.get("position") is not None:
        return tuple(site["position"])
    if site.get("substation") is not None:
        return tuple(site["substation"])
    raise ValueError(f"Roboport site {site.get('name', site)!r} needs a 'position'")


def _site_probe_points(site: dict) -> List[Point]:
    """Points that must fall inside some roboport's construction radius: the
    site position plus, when given, the four corners of its `extent` box."""
    points = [_site_position(site)]
    extent = site.get("extent")
    if extent:
        (x1, y1), (x2, y2) = extent
        points.extend([(x1, y1), (x2, y1), (x1, y2), (x2, y2)])
    return points


def plan_roboport_network(
    sites: List[dict],
    spacing: int = ROBOPORT_SPACING,
    waypoints: List[Sequence[Point]] | None = None,
) -> dict:
    """One chained roboport network covering every site.

    `sites` entries carry at least {"position": (x, y)} and optionally
    {"extent": ((x1, y1), (x2, y2))} naming the site's build bounding box, which
    validate_roboport_network() then requires to be inside a construction radius.

    Routing: by default the chain follows a minimum spanning tree over the site
    positions, each edge walked rectilinearly (horizontal then vertical) with a
    roboport every `spacing` tiles. `waypoints` overrides that with explicit
    polylines when the caller has reserved corridors and does not want the tree
    guessing a path across a machine row.

    Roboports are place_entity, not ghosts: nothing can build the first one.
    """
    if not sites:
        raise ValueError("plan_roboport_network needs at least one site")
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    if spacing > ROBOPORT_LINK_DISTANCE:
        raise ValueError(
            f"spacing {spacing} exceeds ROBOPORT_LINK_DISTANCE {ROBOPORT_LINK_DISTANCE}; "
            "consecutive roboports would fall into separate logistic networks"
        )

    if waypoints is None:
        positions = [_site_position(site) for site in sites]
        routes = [[positions[i], positions[j]] for i, j in minimum_spanning_tree_edges(positions)] or [[positions[0]]]
    else:
        routes = [list(route) for route in waypoints]

    placer = FootprintPlacer(ROBOPORT_SIZE)
    for route in routes:
        if not route:
            continue
        placer.add(route[0])
        for leg_start, leg_end in zip(route, route[1:]):
            corners = l_route(leg_start, leg_end)
            for start, end in zip(corners, corners[1:]):
                for point in step_points(start, end, spacing):
                    placer.add(point)

    plan = {"phases": [{"name": "roboport_network", "actions": [
        {"action_type": "place_entity", "entity": ROBOPORT_ENTITY, "position": {"x": x, "y": y}}
        for x, y in placer.points
    ]}]}
    _validate(plan)
    validate_roboport_network(plan, sites)
    return plan


def roboport_positions(plan: dict) -> List[Point]:
    return [_point(action) for action in _actions(plan)
            if action.get("action_type") in _PLACEMENTS
            and action.get("entity") == ROBOPORT_ENTITY]


def validate_roboport_network(plan: dict, sites: List[dict]) -> None:
    """Raise unless the plan is ONE logistic network that covers every site.

    Two independent failures:
      * a roboport whose nearest neighbour is further than
        ROBOPORT_LINK_DISTANCE -- the chain splits into two logistic networks
        and bots on one side never see the other side's ghosts;
      * a site (or a corner of its `extent`) outside every construction radius,
        so its ghosts are simply never built.
    Distances are Chebyshev because roboport logistic and construction areas are
    SQUARES, not circles.
    """
    positions = roboport_positions(plan)
    if not positions:
        raise ValueError("Roboport plan contains no roboports")

    for index, position_a in enumerate(positions):
        for position_b in positions[index + 1:]:
            if boxes_overlap(position_a, ROBOPORT_SIZE, position_b, ROBOPORT_SIZE):
                raise ValueError(f"Roboport footprints overlap at {position_a} and {position_b}")

    reached = {0}
    frontier = [0]
    while frontier:
        current = frontier.pop()
        for index, position in enumerate(positions):
            if index in reached:
                continue
            if chebyshev_distance(positions[current], position) <= ROBOPORT_LINK_DISTANCE:
                reached.add(index)
                frontier.append(index)
    if len(reached) != len(positions):
        stranded = [positions[i] for i in range(len(positions)) if i not in reached]
        nearest = {}
        for point in stranded:
            nearest[point] = min(
                (chebyshev_distance(point, other) for i, other in enumerate(positions) if other != point),
                default=float("inf"),
            )
        detail = "; ".join(f"{p} (nearest {nearest[p]:.0f} tiles)" for p in stranded[:8])
        raise ValueError(
            f"Roboports split into separate logistic networks; "
            f"{len(stranded)} unreachable from the first: {detail}"
        )

    for site in sites:
        for probe in _site_probe_points(site):
            if not any(chebyshev_distance(probe, position) <= ROBOPORT_CONSTRUCTION_RADIUS
                       for position in positions):
                closest = min(chebyshev_distance(probe, position) for position in positions)
                raise ValueError(
                    f"Site {site.get('name', '?')!r} point {probe} is {closest:.0f} tiles from the "
                    f"nearest roboport, past the {ROBOPORT_CONSTRUCTION_RADIUS:.0f}-tile "
                    "construction radius; its ghosts would never be built"
                )


# --- reconciling per-row power scaffolding with a single source -------------

def strip_local_power(plan: dict, remove_substations: bool = True) -> dict:
    """Return a copy of `plan` with its per-row power scaffolding removed.

    LocalLayoutPlanner.generate_line_layout, generate_lab_row and
    fluid_layouts.generate_fluid_machine_row each park their OWN
    electric-energy-interface plus substation beside the row, which is right for
    a standalone row and wrong for a factory: the user invariant is exactly one
    source and one electric network. Infrastructure owns both, so the row plans
    hand their substation positions to plan_power_network (as site anchors) and
    drop the local copies here. Phases left empty are dropped; nothing else in
    the plan is touched.
    """
    doomed = {POWER_SOURCE_ENTITY}
    if remove_substations:
        doomed.add("substation")
    phases = []
    for phase in plan.get("phases", []):
        actions = [action for action in phase.get("actions", [])
                   if not (action.get("action_type") == "place_entity"
                           and action.get("entity") in doomed)]
        if actions:
            phases.append({**phase, "actions": actions})
    return {**plan, "phases": phases}


def local_power_anchors(plan: dict) -> List[Point]:
    """Substation positions a row plan wanted, in emission order.

    These are the tiles the row generators computed to sit inside a medium
    pole's 9-tile reach of the row's own pole grid, so reusing them as
    plan_power_network site substations keeps every row powered while the
    factory still has exactly one source.
    """
    return [_point(action) for action in _actions(plan)
            if action.get("action_type") == "place_entity"
            and action.get("entity") == "substation"]
