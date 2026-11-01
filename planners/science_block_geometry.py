# Path: planners/science_block_geometry.py
# Purpose: Offline, non-executable adapter from one explicit line specification
# to a validated remaining ghost plan plus separate direct/removal billing.
#
# This module never contacts a server, a runtime, or a live surface. It does not
# choose recipes, counts, origins, tiers, feed styles, or ingredients: every one
# of those is supplied by the caller. It delegates all geometry to the existing
# LocalLayoutPlanner.generate_line_layout generator, removes only the generator's
# local test power, rejects scripted item sources, and re-validates the result
# with the shared hard validators.

from __future__ import annotations

from collections import Counter
import math
from typing import Mapping

from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner
from planners.plan_validation import (
    assert_no_production_infinity,
    validate_build_plan,
)
from planners.line_layouts import LINE_FEED_STYLES
from planners.recipe_data import BELT_TIERS, INSERTER_TIERS, LINE_RECIPES, MACHINE_SPEEDS

_GHOST_PLACEMENTS = frozenset({"place_ghost", "place_tile_ghost"})
_NON_EXECUTABLE_PREFIX = "NON_EXECUTABLE_OFFLINE_GEOMETRY"

_REQUIRED_FIELDS = (
    "recipe",
    "machine_count",
    "origin_x",
    "origin_y",
    "belt_type",
    "inserter_type",
    "mining_feed",
    "feed_style",
    "direct_bus_ingredients",
    "chained_ingredients",
    "terminal_collector",
)

# Optional: an assembler tier other than the recipe default (for example
# assembling-machine-1 in an early world). Omitted means the recipe default.
_OPTIONAL_FIELDS = ("machine",)

# Deliberately not resolved offline. The caller can only learn these from the
# target world, the factory power network and measured runtime throughput.
_UNKNOWNS = {
    "site": (
        "Terrain, ore patch, existing entities and logistics coverage are not "
        "observed; the supplied origin is unverified."
    ),
    "power": (
        "Local test power was stripped. No source, electric network, pole reach, "
        "supply or consumption is established by this plan."
    ),
    "capacity": (
        "Sustained belt/inserter throughput, feed adequacy and material "
        "availability are not measured offline."
    ),
}


def bill_plan(plan: Mapping[str, object]) -> dict:
    """Count ghost placements, direct placements and removals separately.

    Counts are gross action counts, never netted against each other. Ghost
    placements include construction ghosts and landfill tile ghosts; direct
    placements are ``place_entity`` actions; removals are ``remove_entity``
    actions. Configuration actions are not placements and are not billed.
    """
    ghost_entities: Counter[str] = Counter()
    direct_entities: Counter[str] = Counter()
    removed_entities: Counter[str] = Counter()
    for phase in plan.get("phases", []):  # type: ignore[union-attr]
        for action in phase.get("actions", []):
            action_type = action.get("action_type")
            if action_type in _GHOST_PLACEMENTS:
                name = action.get("tile") if action_type == "place_tile_ghost" else action.get("entity")
                ghost_entities[str(name)] += 1
            elif action_type == "place_entity":
                direct_entities[str(action.get("entity"))] += 1
            elif action_type == "remove_entity":
                removed_entities[str(action.get("entity"))] += 1
    return {
        "ghost_placements": sum(ghost_entities.values()),
        "direct_placements": sum(direct_entities.values()),
        "removals": sum(removed_entities.values()),
        "ghost_entities": dict(sorted(ghost_entities.items())),
        "direct_entities": dict(sorted(direct_entities.items())),
        "removed_entities": dict(sorted(removed_entities.items())),
    }


def build_science_block_geometry(spec: Mapping[str, object]) -> dict:
    """Build and validate one explicit line specification, offline.

    The returned ``plan`` is the generator's remaining plan after stripping only
    its local ``electric-energy-interface`` and ``substation``; no other action
    is added, removed, moved or rewritten here. The specification is not a
    player-force build claim: direct placements or removals are reported, and
    site, power and capacity remain explicit unknowns.
    """
    normalised = _normalise_spec(spec)
    raw_plan = LocalLayoutPlanner().generate_line_layout(
        recipe=normalised["recipe"],
        machine_count=normalised["machine_count"],
        origin_x=normalised["origin_x"],
        origin_y=normalised["origin_y"],
        mining_feed=normalised["mining_feed"],
        belt_type=normalised["belt_type"],
        inserter_type=normalised["inserter_type"],
        feed_style=normalised["feed_style"],
        direct_bus_ingredients=normalised["direct_bus_ingredients"],
        chained_ingredients=normalised["chained_ingredients"],
        terminal_collector=normalised["terminal_collector"],
        machine=normalised.get("machine"),
    )
    remaining = strip_local_power(raw_plan)

    assert_no_production_infinity([("science-block", remaining)])
    validate_build_plan(remaining)

    bill = bill_plan(remaining)
    reasons = []
    if bill["direct_placements"]:
        reasons.append(f"{bill['direct_placements']} direct entity placement(s)")
    if bill["removals"]:
        reasons.append(f"{bill['removals']} removal(s)")
    marker = None
    if reasons:
        marker = (
            f"{_NON_EXECUTABLE_PREFIX}: " + ", ".join(reasons)
            + "; direct actions are not an allowed player-force build path"
        )

    return {
        "spec": {
            **normalised,
            "direct_bus_ingredients": list(normalised["direct_bus_ingredients"]),
            "chained_ingredients": list(normalised["chained_ingredients"]),
        },
        "plan": remaining,
        "counts": {
            "ghost_placements": bill["ghost_placements"],
            "direct_placements": bill["direct_placements"],
            "removals": bill["removals"],
        },
        "ghost_entities": bill["ghost_entities"],
        "direct_entities": bill["direct_entities"],
        "removed_entities": bill["removed_entities"],
        "has_direct_actions": bool(reasons),
        "non_executable_marker": marker,
        "player_force_build_claim": False,
        "validation": (
            "generate_line_layout -> strip_local_power -> "
            "assert_no_production_infinity -> validate_build_plan"
        ),
        "unknowns": dict(_UNKNOWNS),
    }


def _normalise_spec(spec: Mapping[str, object]) -> dict:
    if not isinstance(spec, Mapping):
        raise ValueError("Line specification must be a mapping")
    keys = set(spec)
    missing = [field for field in _REQUIRED_FIELDS if field not in keys]
    extra = sorted(str(field) for field in keys.difference(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
    if missing:
        raise ValueError(f"Line specification is missing explicit field(s): {missing}")
    if extra:
        raise ValueError(f"Line specification has unknown field(s): {extra}")

    recipe = spec["recipe"]
    if not isinstance(recipe, str) or recipe not in LINE_RECIPES:
        raise ValueError(f"No line recipe knowledge for: {recipe!r}")
    machine_count = spec["machine_count"]
    if type(machine_count) is not int or machine_count <= 0:
        raise ValueError("machine_count must be a positive integer")
    origin_x = _finite_number(spec["origin_x"], "origin_x")
    origin_y = _finite_number(spec["origin_y"], "origin_y")
    belt_type = spec["belt_type"]
    if not isinstance(belt_type, str) or belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type!r}")
    inserter_type = spec["inserter_type"]
    if not isinstance(inserter_type, str) or inserter_type not in INSERTER_TIERS:
        raise ValueError(f"Unknown inserter tier: {inserter_type!r}")
    mining_feed = spec["mining_feed"]
    if type(mining_feed) is not bool:
        raise ValueError("mining_feed must be an explicit boolean")
    feed_style = spec["feed_style"]
    if not isinstance(feed_style, str) or feed_style not in LINE_FEED_STYLES:
        raise ValueError(f"Unknown feed style: {feed_style!r}")
    terminal_collector = spec["terminal_collector"]
    if type(terminal_collector) is not bool:
        raise ValueError("terminal_collector must be an explicit boolean")

    ingredients = LINE_RECIPES[recipe]["ingredients"]
    direct_bus = _string_sequence(
        spec["direct_bus_ingredients"], "direct_bus_ingredients",
    )
    unknown_direct = sorted(set(direct_bus).difference(ingredients))
    if unknown_direct:
        raise ValueError(
            f"{recipe} has no direct-bus ingredient(s): {unknown_direct}"
        )
    if direct_bus and feed_style != "sideload":
        raise ValueError("direct-bus ingredients require feed_style='sideload'")

    chained = _index_sequence(spec["chained_ingredients"], "chained_ingredients")
    if feed_style == "chained":
        if not chained:
            raise ValueError("feed_style='chained' requires explicit chained ingredient indices")
    elif chained:
        raise ValueError("chained_ingredients are only valid with feed_style='chained'")
    out_of_range = sorted(index for index in chained if index >= len(ingredients))
    if out_of_range:
        raise ValueError(f"chained_ingredients out of range for {recipe}: {out_of_range}")

    machine = spec.get("machine")
    if machine is not None and (not isinstance(machine, str) or machine not in MACHINE_SPEEDS):
        raise ValueError(f"Unknown machine: {machine!r}")

    normalised = {
        "recipe": recipe,
        "machine_count": machine_count,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "belt_type": belt_type,
        "inserter_type": inserter_type,
        "mining_feed": mining_feed,
        "feed_style": feed_style,
        "direct_bus_ingredients": direct_bus,
        "chained_ingredients": chained,
        "terminal_collector": terminal_collector,
    }
    if machine is not None:
        normalised["machine"] = machine
    return normalised


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return value


def _string_sequence(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an explicit list or tuple")
    items = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{field} entries must be non-empty strings")
        items.append(item)
    if len(set(items)) != len(items):
        raise ValueError(f"{field} entries must be unique")
    return tuple(sorted(items))


def _index_sequence(value: object, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an explicit list or tuple")
    items = []
    for item in value:
        if type(item) is not int or item < 0:
            raise ValueError(f"{field} entries must be non-negative integers")
        items.append(item)
    if len(set(items)) != len(items):
        raise ValueError(f"{field} entries must be unique")
    return tuple(sorted(items))
