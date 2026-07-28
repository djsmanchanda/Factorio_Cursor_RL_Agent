# Path: orchestrator/stage_transport.py
# Purpose: How a stage receives each ingredient -- belt versus logistic bots, which belt tier, and which side of a chest a link attaches to.

from __future__ import annotations

import math
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.stage_services import (
    StuckError,
    _BOT_THROUGHPUT_LIMIT,
    _BRIDGE_SURVEY_MARGIN,
    _DEFAULT_INSERTER,
    _LOGISTIC_REQUEST,
    _STUCK_GRACE_SECONDS,
    _TRANSIT_SAFETY,
    _submit,
    _wait_for_ghosts,
    extend_power,
)
from planners.belt_bridge import (
    DIRECTION_VECTORS,
    UNDERGROUND_REACH,
    bridge_belt_to_chest,
    bridge_chest_to_chest,
    opposite,
    transit_seconds,
)
from planners.recipe_data import BELT_TIERS, LINE_RECIPES, MACHINE_SPEEDS
from tools.rcon_client import RconClient

Point = tuple[float, float]


def _through_belt_source(
    client: RconClient, surface: str, ingredient: str, provider: Point, *,
    upstream_shift: int = 1,
) -> Point | None:
    """Existing through-belt that should continue to the consuming stage.

    A mine provider is a side tap: provider, inserter one tile south, and the
    uninterrupted ore belt two tiles south. Treating that provider as a
    terminal chest made the bridge run through the inserter and chest.
    """
    mine_inserter = (provider[0], provider[1] + 1)
    mine_belt = (provider[0], provider[1] + 2)
    mine_entities = [
        live_base.entity_at(client, surface, mine_inserter),
        live_base.entity_at(client, surface, mine_belt),
    ]
    if (
        mine_entities[0] and mine_entities[0]["type"] == "inserter"
        and mine_entities[1] and mine_entities[1]["type"] == "transport-belt"
    ):
        shifted = (mine_belt[0] + upstream_shift, mine_belt[1])
        if upstream_shift:
            shifted_entity = live_base.entity_at(client, surface, shifted)
            if shifted_entity and shifted_entity["type"] == "transport-belt":
                return shifted
        return mine_belt

    if ingredient not in {"iron-plate", "copper-plate"}:
        return None
    sample_tile = (provider[0], provider[1] - 1)
    main_tile = (provider[0], provider[1] - 2)
    tail_tile = (provider[0] + 1, provider[1] - 2)
    entities = [
        live_base.entity_at(client, surface, sample_tile),
        live_base.entity_at(client, surface, main_tile),
        live_base.entity_at(client, surface, tail_tile),
    ]
    if (
        entities[0] and entities[0]["type"] == "inserter"
        and all(entity and entity["type"] == "transport-belt" for entity in entities[1:])
    ):
        return (provider[0] + 2, provider[1] - 2)
    return None


def _preserve_existing_side_tap_belts(
    client: RconClient, surface: str, source: Point, actions: list[dict],
) -> list[dict]:
    """Do not submit replacement ghosts over the first existing belt run."""
    belt_actions = [
        action for action in actions
        if action.get("entity") in BELT_TIERS
        or "underground-belt" in action.get("entity", "")
    ]
    preserve = {source}
    if belt_actions:
        first = belt_actions[0]
        first_position = (first["position"]["x"], first["position"]["y"])
        if first_position == source and len(belt_actions) > 1:
            next_position = (
                belt_actions[1]["position"]["x"],
                belt_actions[1]["position"]["y"],
            )
            existing = live_base.entity_at(client, surface, next_position)
            if existing and existing["type"] == "transport-belt":
                preserve.add(next_position)
    return [
        action for action in actions
        if not (
            action.get("entity") in BELT_TIERS
            and (action["position"]["x"], action["position"]["y"]) in preserve
        )
    ]


def _replace_existing_source_belt(
    client: RconClient, surface: str, source: Point, actions: list[dict],
) -> list[dict]:
    """Let a bridge turn an existing mine endpoint into its route corner."""
    existing = live_base.entity_at(client, surface, source)
    if not existing or existing["type"] != "transport-belt":
        return actions
    if not any(
        action.get("position") == {"x": source[0], "y": source[1]}
        and action.get("entity", "").endswith(("transport-belt", "underground-belt"))
        for action in actions
    ):
        return actions
    # A mine output chest is a side tap: inserter one tile above the through
    # belt, passive chest two tiles above it. Removing that belt leaves the
    # chest visually present but disconnected. The handoff already starts
    # one tile upstream, so keep the tap belt and continue from its neighbor.
    for dx in (-1, 0, 1):
        tap_x = source[0] + dx
        tap_inserter = live_base.entity_at(
            client, surface, (tap_x, source[1] - 1),
        )
        tap_chest = live_base.entity_at(
            client, surface, (tap_x, source[1] - 2),
        )
        if (
            tap_inserter and tap_inserter["type"] == "inserter"
            and tap_chest and tap_chest["type"] in {
                "container", "logistic-container",
            }
        ):
            return _preserve_existing_side_tap_belts(
                client, surface, source, actions,
            )
    return [{
        "action_type": "remove_entity",
        "entity": existing["name"],
        "position": {"x": source[0], "y": source[1]},
    }, *actions]


def _toward(source: Point, dest: Point) -> str:
    dx, dy = dest[0] - source[0], dest[1] - source[1]
    if abs(dx) >= abs(dy):
        return "east" if dx > 0 else "west"
    return "south" if dy > 0 else "north"


def _clear_side(chest: Point, preferred: str, blocked: set[tuple[int, int]]) -> str:
    """Pick the side of `chest` a bridge can actually attach to.

    bridge_chest_to_chest puts an inserter one tile out and the belt's first
    tile two tiles out, so a side is usable only when BOTH are free. Facing the
    destination is merely the preference: a production stage sits on one side
    of its own output chest, so the direct side is frequently its own machine
    row -- observed live, where every attempt drove the belt back through the
    furnaces it had just built. Falls back to the preferred side when nothing
    is clear, so the caller still gets a plan and a real placement error
    rather than a silent no-op.
    """
    ordered = [preferred, *(d for d in DIRECTION_VECTORS if d != preferred)]
    for direction in ordered:
        vx, vy = DIRECTION_VECTORS[direction]
        tiles = {
            (math.floor(chest[0] + vx * step), math.floor(chest[1] + vy * step))
            for step in (1, 2)
        }
        if not (tiles & blocked):
            return direction
    return preferred


def _ingredient_demand(recipe: str, ingredient: str, machine_count: int) -> float:
    """Items/second of `ingredient` a `machine_count`-machine stage consumes."""
    spec = LINE_RECIPES[recipe]
    crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
    amount = spec["amounts"][spec["ingredients"].index(ingredient)]
    return amount * crafts_per_second


def _transport_mode(recipe: str, ingredient: str, machine_count: int) -> str:
    """"logistic" (requester chest, bots deliver) or "belt" (physical corridor).

    Small demand does not justify a belt run across the base; large demand
    cannot be served by bots at all. Chosen per ingredient, because one stage
    can easily need a trickle of one input and a torrent of another.
    """
    demand = _ingredient_demand(recipe, ingredient, machine_count)
    return "belt" if demand > _BOT_THROUGHPUT_LIMIT else "logistic"


_BELT_TIERS_CHEAPEST_FIRST = (
    "transport-belt", "fast-transport-belt", "express-transport-belt",
    "turbo-transport-belt",
)


def choose_belt_tier(stock: dict[str, int], needed: int) -> str:
    """Cheapest belt tier the base actually holds enough of.

    The default belt is only a preference. Picking a tier that is out of stock
    places ghosts nothing can build, so availability decides; ties break toward
    the slowest adequate tier, leaving faster belts for links that need them.

    `needed` is a worst-case Manhattan estimate made BEFORE the route exists, so
    it is a preference too, not a verdict. A real route runs underground for
    most of its length and needs far fewer surface belts than the straight-line
    guess, so refusing here aborted runs the base could comfortably afford --
    and did so before placing anything, where no remediation can see it. When no
    tier covers the estimate the deepest stock is used instead; affordability is
    then decided on the ACTUAL ghost counts by assert_affordable at submit time,
    which is the only place that knows the true requirement.
    """
    for tier in _BELT_TIERS_CHEAPEST_FIRST:
        if stock.get(tier, 0) >= needed:
            return tier
    return max(_BELT_TIERS_CHEAPEST_FIRST, key=lambda tier: stock.get(tier, 0))


def transport_grace_seconds(belt_type: str, belt_tiles: int) -> float:
    """Bounded machine-health grace including the first item's belt transit."""
    return _STUCK_GRACE_SECONDS + transit_seconds(belt_type, belt_tiles) * _TRANSIT_SAFETY


def _report_intake_ceiling(
    recipe: str, ingredient: str, demand: float, ceiling: float,
    carrier: str, emit: Callable[[str], None],
) -> None:
    """Say whether the INTAKE can carry what this line wants to consume.

    An under-used line has two possible causes and they need opposite fixes:
    the source produces too little, or the link cannot carry what the source
    already makes. Only the second is visible from here, so it is named here --
    leaving upstream output as the remaining explanation when this link is not
    the binding constraint. Neither is a fault; both are capacity signals.
    """
    if demand > ceiling:
        emit(f"    CAPACITY: {ingredient} intake is {carrier}-limited -- {recipe} wants "
             f"{demand:.2f}/s but this link carries {ceiling:.2f}/s; raise the link tier "
             "to use the machines already built")
    else:
        emit(f"    {ingredient} intake heads {demand:.2f}/s against a {ceiling:.2f}/s link "
             "ceiling -- if this line under-runs, upstream output is the constraint")


def logistic_grace_seconds(
    client: RconClient, force: str, source_position: Point, feed_position: Point,
    demand: float, machine_count: int,
) -> tuple[float, float]:
    """Machine-health grace for a BOT-served link: (grace, estimated delivery).

    Belt links had a transit model and bot links had none -- they returned a
    flat 20s regardless of how far the provider was or how fast the upstream
    could actually supply. Two terms matter and neither is a constant:

    * flight: a bot flies to the provider and back to the requester, so the
      round trip is charged, at the force's real researched robot speed.
    * fill: the first delivery cannot outrun the source producing the goods.
      A freshly built mine starts empty, so supplying one item to each machine
      takes machine_count/demand seconds even with idle bots to spare.

    Being generous here costs nothing when a stage is healthy -- the health poll
    exits as soon as every machine is alive -- and only delays reporting a feed
    that is genuinely dead.
    """
    speed = live_base.logistic_robot_speed(client, force)
    flight = 2 * math.dist(source_position, feed_position) / speed if speed > 0 else 0.0
    fill = machine_count / demand if demand > 0 else 0.0
    return _STUCK_GRACE_SECONDS + (flight + fill) * _TRANSIT_SAFETY, flight + fill


def _plan_belt_transport(
    client: RconClient, surface: str, force: str, ingredient: str, source_position: Point,
    feed_position: Point, *, reuse_existing: bool, max_belt_route_tiles: int | None,
    additional_blocked: set[tuple[int, int]] | None = None,
    upstream_shift: int = 1,
) -> tuple[list[dict], str, bool]:
    belt_source = _through_belt_source(
        client, surface, ingredient, source_position,
        upstream_shift=upstream_shift,
    )
    route_source = belt_source or source_position
    span = int(abs(route_source[0] - feed_position[0])
               + abs(route_source[1] - feed_position[1])) + 4
    stock = live_base.available_items(client, surface, force)
    preferred = choose_belt_tier(stock, span)
    ignored = ()
    if reuse_existing:
        belt_names = tuple(UNDERGROUND_REACH)
        ignored = (
            *belt_names,
            *(name.replace("transport-belt", "underground-belt") for name in belt_names),
            _DEFAULT_INSERTER,
        )
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(route_source[0], feed_position[0]) - _BRIDGE_SURVEY_MARGIN,
         min(route_source[1], feed_position[1]) - _BRIDGE_SURVEY_MARGIN),
        (max(route_source[0], feed_position[0]) + _BRIDGE_SURVEY_MARGIN,
         max(route_source[1], feed_position[1]) + _BRIDGE_SURVEY_MARGIN),
        ignore_names=ignored,
    )
    blocked |= additional_blocked or set()
    blocked -= {
        (math.floor(route_source[0]), math.floor(route_source[1])),
        (math.floor(feed_position[0]), math.floor(feed_position[1])),
    }
    direction = _toward(route_source, feed_position)
    exit_direction = _clear_side(route_source, direction, blocked)
    entry_direction = _clear_side(feed_position, opposite(direction), blocked)

    # Route first, price second. Each tier reaches a different distance
    # underground, so only a generated route knows what it actually costs; the
    # first tier whose REAL bill of materials the base can pay wins, starting
    # from the estimate's preference and then widening. Judging tiers on a
    # straight-line guess rejected bridges that were affordable in practice.
    ordered = [preferred] + [t for t in _BELT_TIERS_CHEAPEST_FIRST if t != preferred]
    shortfalls: list[str] = []
    route_error: ValueError | None = None
    for tier in ordered:
        try:
            if belt_source is not None:
                actions = bridge_belt_to_chest(
                    belt_source, feed_position, entry_direction=entry_direction,
                    belt_type=tier, inserter_type=_DEFAULT_INSERTER,
                    blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
                )
                actions = _replace_existing_source_belt(
                    client, surface, belt_source, actions,
                )
            else:
                actions = bridge_chest_to_chest(
                    source_position, feed_position,
                    exit_direction=exit_direction, entry_direction=entry_direction,
                    belt_type=tier, inserter_type=_DEFAULT_INSERTER,
                    blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
                )
        except ValueError as error:
            route_error = error
            continue
        required: dict[str, int] = {}
        for action in actions:
            if action.get("action_type") == "place_ghost":
                required[action["entity"]] = required.get(action["entity"], 0) + 1
        short = {
            item: count - stock.get(item, 0)
            for item, count in required.items()
            if stock.get(item, 0) < count
        }
        if not short:
            return actions, tier, belt_source is not None
        shortfalls.append(
            f"{tier} route short " + ", ".join(
                f"{item} by {missing}" for item, missing in sorted(short.items())
            )
        )
    if not shortfalls and route_error is not None:
        raise StuckError(f"no belt route is available for this bridge: {route_error}")
    raise StuckError(
        "no belt tier can be afforded for this bridge -- " + "; ".join(shortfalls)
    )


def preflight_ingredient_transport(
    client: RconClient, surface: str, force: str, recipe: str, ingredient: str,
    source_position: Point, feed_position: Point, machine_count: int, *,
    max_belt_route_tiles: int | None = None,
    additional_blocked: set[tuple[int, int]] | None = None,
    mode: str | None = None,
    upstream_shift: int = 1,
) -> None:
    """Reject an illegal belt route before its destination stage is submitted."""
    if (mode or _transport_mode(recipe, ingredient, machine_count)) == "logistic":
        return
    _plan_belt_transport(
        client, surface, force, ingredient, source_position, feed_position,
        reuse_existing=False, max_belt_route_tiles=max_belt_route_tiles,
        additional_blocked=additional_blocked,
        upstream_shift=upstream_shift,
    )


def ensure_ingredient_transport(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ingredient: str, source_position: Point, feed_position: Point,
    machine_count: int, emit: Callable[[str], None], *, reuse_existing: bool = False,
    max_belt_route_tiles: int | None = None,
    mode: str | None = None,
    upstream_shift: int = 1,
) -> float:
    """Idempotently ensure one declared source-to-feed link."""
    demand = _ingredient_demand(recipe, ingredient, machine_count)
    if (mode or _transport_mode(recipe, ingredient, machine_count)) == "logistic":
        grace, delivery = logistic_grace_seconds(
            client, force, source_position, feed_position, demand, machine_count,
        )
        emit(f"  {recipe}: {ingredient} by logistic bots "
             f"({demand:.2f}/s, provider at {source_position})")
        emit(f"    {ingredient}: first delivery in ~{delivery:.0f}s "
             f"(bot round trip plus source ramp-up)")
        _report_intake_ceiling(recipe, ingredient, demand, _BOT_THROUGHPUT_LIMIT,
                               "logistic bots", emit)
        return grace

    if _through_belt_source(
        client, surface, ingredient, source_position,
        upstream_shift=upstream_shift,
    ) is not None:
        emit(f"  {recipe}: {ingredient} continues from its production belt; "
             "a regular inserter side-taps it without interrupting the main belt")
    emit(f"  {recipe}: {ingredient} needs a belt "
         f"({demand:.2f}/s exceeds the {_BOT_THROUGHPUT_LIMIT}/s bot limit)")
    actions, belt_type, _ = _plan_belt_transport(
        client, surface, force, ingredient, source_position, feed_position,
        reuse_existing=reuse_existing, max_belt_route_tiles=max_belt_route_tiles,
        upstream_shift=upstream_shift,
    )
    plan = {
        "phases": [{"name": f"bridge_{ingredient}_to_{recipe}", "actions": actions}],
        "surface": surface, "force": force,
    }
    _submit(client, bridge, surface, plan, f"bridge_{ingredient}_to_{recipe}", emit)
    belt_tiles = sum(1 for action in actions if "transport-belt" in action["entity"])
    grace = transport_grace_seconds(belt_type, belt_tiles)
    emit(f"    {ingredient}: {belt_tiles} belt tiles, first item arrives in "
         f"~{transit_seconds(belt_type, belt_tiles):.0f}s")
    _report_intake_ceiling(recipe, ingredient, demand, float(BELT_TIERS[belt_type]),
                           belt_type, emit)
    area = (
        (min(source_position[0], feed_position[0]) - 5,
         min(source_position[1], feed_position[1]) - 5),
        (max(source_position[0], feed_position[0]) + 5,
         max(source_position[1], feed_position[1]) + 5),
    )
    # Long bridges can have a quiet construction plateau while robots queue behind
    # earlier ghosts. Honor the route grace before declaring the bridge stalled.
    remaining = _wait_for_ghosts(
        client, surface, force, area,
        timeout_seconds=max(180.0, grace + 30.0),
        minimum_wait_seconds=grace,
    )
    if remaining:
        raise StuckError(
            f"{ingredient} transport to {recipe} still has {remaining} "
            "unbuilt ghosts after settling"
        )
    # A bridge is belt plus two INSERTERS, and an inserter is the one part of it
    # that needs power. The chest it loads from often sits outside every pole's
    # supply area -- nothing guarantees an upstream stage's output chest is
    # within reach of a pole -- so the bridge builds perfectly, the source
    # inserter never swings, and the belt stays empty for the full transit
    # window. The symptom is indistinguishable from a slow belt, which is
    # exactly why it has to be checked rather than waited out.
    for position in live_base.unpowered_entities(client, surface, area):
        emit(f"    bridge entity at {position} has no power -- connecting it")
        if not extend_power(client, bridge, surface, force, position, emit):
            raise StuckError(
                f"{ingredient} transport to {recipe} has an unpowered entity at "
                f"{position} that cannot be reached by a pole chain from any "
                "generating network"
            )
    return grace

def _swap_infinity_chests(
    plan: dict, modes: dict[str, str], *, request_count: int = _LOGISTIC_REQUEST,
) -> dict[str, Point]:
    """Turn every infinity-chest feeder into a real REQUESTER chest asking for
    its ingredient, so logistic bots deliver it.

    The sandbox pipeline used infinity chests (an infinite cheat source); on a
    real base the alternative is a physical belt from the upstream stage. A
    stage-to-stage belt costs ~50 belts and has to route around everything
    already built, while a requester costs one chest and no corridor at all --
    which matters because belts are a consumable the base has to produce.
    Returns {ingredient: chest_position}.
    """
    positions: dict[str, Point] = {}
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "infinity-chest":
                ingredient = action.pop("infinity_filter")
                if modes.get(ingredient, "logistic") == "logistic":
                    action["entity"] = "requester-chest"
                    action["logistic_request"] = {"name": ingredient, "count": request_count}
                else:
                    # Belt-fed: a plain chest the incoming belt unloads into.
                    action["entity"] = "steel-chest"
                positions[ingredient] = (action["position"]["x"], action["position"]["y"])
    return positions


def _direct_single_belt_feed(
    plan: dict, ingredient: str, flow_direction: str = "east",
) -> Point:
    """Remove a single-ingredient feed chest and expose the upstream belt end."""
    if flow_direction not in {"east", "west"}:
        raise ValueError("Direct belt feed direction must be east or west")
    chests = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "infinity-chest"
        and action.get("infinity_filter") == ingredient
    ]
    if not chests:
        raise StuckError(f"{ingredient} line has no deterministic feed endpoint")
    belt_positions: list[Point] = []
    for chest in chests:
        cx, cy = chest["position"]["x"], chest["position"]["y"]
        for phase in plan["phases"]:
            phase["actions"] = [
                action for action in phase["actions"]
                if action is not chest and not (
                    action.get("entity", "").endswith("inserter")
                    and action["position"]["x"] == cx
                    and abs(action["position"]["y"] - cy) == 1
                )
            ]
        belt_positions.extend(
            (action["position"]["x"], action["position"]["y"])
            for phase in plan["phases"] for action in phase["actions"]
            if "transport-belt" in action.get("entity", "")
            and action["position"]["x"] == cx
            and abs(action["position"]["y"] - cy) == 2
        )
    if not belt_positions:
        raise StuckError(f"{ingredient} feed chest has no adjacent input belt")
    selector = min if flow_direction == "east" else max
    return selector(belt_positions, key=lambda point: point[0])

def _publish_output_chest(plan: dict) -> None:
    """Make a stage's collection chest a passive provider, so its product is
    visible to the logistic network and can be requested by downstream stages
    (and by construction bots for building material)."""
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "steel-chest":
                action["entity"] = "passive-provider-chest"
