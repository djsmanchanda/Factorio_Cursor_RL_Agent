# Path: orchestrator/stage_transport.py
# Purpose: How a stage receives each ingredient -- belt versus logistic bots, which belt tier, and which side of a chest a link attaches to.

from __future__ import annotations

import math
from typing import Callable

from orchestrator import live_base
from orchestrator.game_bridge import GameBridge
from orchestrator.parts_mall import MaterialShortage
from orchestrator.pole_relocation import (
    choose_pole_move,
    corridor_tiles,
    staged_relocation_plans,
)
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
    bridge_belt_to_belt,
    bridge_belt_to_chest,
    bridge_chest_to_belt,
    bridge_chest_to_chest,
    opposite,
    transit_seconds,
)
from planners.recipe_data import BELT_TIERS, LINE_RECIPES, MACHINE_SPEEDS
from tools.rcon_client import RconClient

Point = tuple[float, float]
OwnedBeltSignature = tuple[str, float, float, str]

_CARDINAL_DIRECTION = {0: "north", 4: "east", 8: "south", 12: "west"}


def _direct_belt_entry(
    feed_position: Point, blocked: set[tuple[int, int]], belt_direction: str,
) -> str:
    """Return the only approach that continues inline with a direct bus."""
    if belt_direction not in DIRECTION_VECTORS:
        raise ValueError(f"Unknown direct-belt direction: {belt_direction!r}")
    entry_direction = opposite(belt_direction)
    vx, vy = DIRECTION_VECTORS[entry_direction]
    approach = {
        (math.floor(feed_position[0] + vx * step),
         math.floor(feed_position[1] + vy * step))
        for step in (1, 2)
    }
    if approach & blocked:
        raise StuckError(
            f"No inline direct-belt approach to {feed_position}; the upstream "
            f"end of its {belt_direction}bound bus is occupied"
        )
    return entry_direction


def _release_owned_destination_approach(
    client: RconClient, surface: str, feed_position: Point,
    blocked: set[tuple[int, int]], belt_direction: str,
    owned_transport_tiles: set[tuple[int, int]] | None,
) -> None:
    """Reuse a persisted district route only where its live flow still agrees.

    A provisioning retry surveys its already-built route as occupied.  The two
    inline tiles immediately upstream of the refinery are legal to release only
    when the district ledger reserved them and they still hold belts flowing
    into the destination.  Everything else remains authoritative occupancy.
    """
    if not owned_transport_tiles or belt_direction not in {"east", "west"}:
        return
    entry_direction = opposite(belt_direction)
    vx, vy = DIRECTION_VECTORS[entry_direction]
    for step in (1, 2):
        tile = (
            math.floor(feed_position[0] + vx * step),
            math.floor(feed_position[1] + vy * step),
        )
        if tile not in owned_transport_tiles:
            continue
        position = (tile[0] + 0.5, tile[1] + 0.5)
        if live_base.transport_belt_direction_at(
            client, surface, position,
        ) == belt_direction:
            blocked.discard(tile)


def _through_belt_source(
    client: RconClient, surface: str, ingredient: str, provider: Point, *,
    upstream_shift: int = 1,
) -> Point | None:
    """Existing through-belt that should continue to the consuming stage.

    Legacy mines may expose a side tap (provider, inserter, then belt); newer
    refinery mines expose the belt directly at provider. Both must hand the
    bridge the uninterrupted belt, never a chest-shaped terminal source.
    """
    direct_belt = live_base.entity_at(client, surface, provider)
    if _entity_or_ghost_is(direct_belt, "transport-belt"):
        return (provider[0] + 1, provider[1])

    mine_inserter = (provider[0], provider[1] + 1)
    mine_belt = (provider[0], provider[1] + 2)
    mine_entities = [
        live_base.entity_at(client, surface, mine_inserter),
        live_base.entity_at(client, surface, mine_belt),
    ]
    shifted_tap_belt = (provider[0] - 2, provider[1] + 2)
    shifted_handoff = (provider[0] - 1, provider[1] + 2)
    shifted_entities = [
        live_base.entity_at(client, surface, shifted_tap_belt),
        live_base.entity_at(client, surface, shifted_handoff),
    ]
    if (
        _entity_or_ghost_is(mine_entities[0], "inserter")
        and all(
            _entity_or_ghost_is(entity, "transport-belt")
            for entity in shifted_entities
        )
    ):
        return shifted_handoff
    if (
        _entity_or_ghost_is(mine_entities[0], "inserter")
        and _entity_or_ghost_is(mine_entities[1], "transport-belt")
    ):
        shifted = (mine_belt[0] + upstream_shift, mine_belt[1])
        if upstream_shift:
            shifted_entity = live_base.entity_at(client, surface, shifted)
            if _entity_or_ghost_is(shifted_entity, "transport-belt"):
                return shifted
        return mine_belt

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


def _entity_or_ghost_is(entity: dict | None, entity_type: str) -> bool:
    """Recognize planned transport before construction bots revive its ghost."""
    if not entity:
        return False
    if entity.get("type") == entity_type:
        return True
    ghost = entity.get("ghost_name", "")
    if entity_type == "transport-belt":
        return ghost.endswith("transport-belt")
    return entity_type == "inserter" and ghost.endswith("inserter")


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
    *, owned_source: OwnedBeltSignature | None = None,
) -> list[dict]:
    """Replace only an endpoint whose exact persisted signature authorizes it."""
    existing = live_base.entity_at(client, surface, source)
    if not existing or existing["type"] != "transport-belt":
        return actions
    actual_direction = _CARDINAL_DIRECTION.get(
        existing.get("direction"), existing.get("direction"),
    )
    if owned_source != (
        existing.get("name"), source[0], source[1], actual_direction,
    ):
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


def _raw_belt_exit_direction(
    ingredient: str, belt_source: Point, source_position: Point,
) -> str | None:
    """Continue a raw-resource belt toward its surveyed terminal, never sideways."""
    if ingredient not in {"iron-ore", "copper-ore", "coal", "stone"}:
        return None
    dx = belt_source[0] - source_position[0]
    dy = belt_source[1] - source_position[1]
    if abs(dx) >= abs(dy) and dx:
        return "west" if dx > 0 else "east"
    if dy:
        return "north" if dy > 0 else "south"
    return None


def _clear_side(
    chest: Point, preferred: str, blocked: set[tuple[int, int]],
) -> str | None:
    """The side of `chest` a bridge can actually attach to, or None.

    bridge_chest_to_chest puts an inserter one tile out and the belt's first
    tile two tiles out, so a side is usable only when BOTH are free. Facing the
    destination is merely the preference: a production stage sits on one side of
    its own output chest, so the direct side is frequently its own machine row
    -- observed live, where every attempt drove the belt back through the
    furnaces it had just built.

    RETURNS NONE WHEN NO SIDE IS USABLE. This used to fall back to the preferred
    side regardless, deliberately, so that a caller "still gets a plan and a real
    placement error rather than a silent no-op". That trade is what put a
    transport-belt ghost at (117.5,-18.5) on top of a fast-inserter the same
    system had placed twenty-five seconds earlier: the copper-cable line's feed
    chest is flanked by the line's own feed inserters, every side was blocked,
    and the bridge attached to one anyway.

    A plan known to collide before it is submitted is not a better diagnostic
    than a stated failure -- it is a build that damages the base and then
    reports the damage. The caller now says which chest cannot be reached.
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
    return None


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
_REFINERY_BELT_TIERS = ("transport-belt", "fast-transport-belt")


def _route_belt_tiers(destination_is_belt: bool) -> tuple[str, ...]:
    """Return belt tiers the live force can actually build for this route."""
    candidates = _REFINERY_BELT_TIERS if destination_is_belt else _BELT_TIERS_CHEAPEST_FIRST
    executable = tuple(tier for tier in candidates if tier in LINE_RECIPES)
    return executable or ("transport-belt",)


def _mixed_belt_actions(actions: list[dict], stock: dict[str, int]) -> list[dict]:
    """Use stocked fast belts for a shortfall in a regular-belt route."""
    regular = sum(
        1 for action in actions
        if action.get("action_type") == "place_ghost"
        and action.get("entity") == "transport-belt"
    )
    shortage = max(0, regular - stock.get("transport-belt", 0))
    replacements = min(shortage, stock.get("fast-transport-belt", 0))
    if replacements <= 0:
        return actions
    mixed: list[dict] = []
    regular_available = stock.get("transport-belt", 0)
    for action in actions:
        candidate = dict(action)
        is_regular = (
            candidate.get("action_type") == "place_ghost"
            and candidate.get("entity") == "transport-belt"
        )
        if is_regular and regular_available:
            regular_available -= 1
        elif is_regular and replacements:
            candidate["entity"] = "fast-transport-belt"
            replacements -= 1
        mixed.append(candidate)
    return mixed


def _choose_route_belt_tier(
    stock: dict[str, int], needed: int, *, destination_is_belt: bool,
) -> str:
    """Prefer the cheapest stocked executable tier; route cost decides upgrades."""
    del needed  # The Manhattan estimate must not promote a long link by itself.
    tiers = _route_belt_tiers(destination_is_belt)
    return next(
        (tier for tier in tiers if stock.get(tier, 0) > 0),
        tiers[0],
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


def _survey_belt_route(
    client: RconClient, surface: str, force: str, ingredient: str,
    source_position: Point, feed_position: Point, *, reuse_existing: bool,
    additional_blocked: set[tuple[int, int]] | None, upstream_shift: int,
    destination_is_belt: bool, destination_belt_direction: str,
    planned_belt_source: Point | None,
    allow_chest_source_to_belt: bool = False,
    through_flow_direction: str | None = None,
    owned_transport_tiles: set[tuple[int, int]] | None = None,
) -> tuple[Point | None, Point, set[tuple[int, int]], str, str]:
    """Survey the exact source/endpoint geometry before pricing belt tiers."""
    belt_source = _through_belt_source(
        client, surface, ingredient, source_position,
        upstream_shift=upstream_shift,
    ) or planned_belt_source
    if (
        destination_is_belt
        and belt_source is None
        and not allow_chest_source_to_belt
    ):
        raise StuckError(
            f"{ingredient} refinery feed requires an existing source belt at "
            f"{source_position}; refusing a chest/inserter side-feed"
        )
    route_source = belt_source or source_position
    planned_handoff = belt_source == planned_belt_source
    # A raw exit describes a belt that STANDS at the through source, or one
    # this transaction is about to lay. East-flow collector heads report the
    # empty tile past the head as the through source on re-plans; reading that
    # phantom as a west-flow terminal drove the haul backwards into the head
    # belt, and the route died on a corner it could never own (live run of
    # 2026-08-24 14:12).
    source_entity = live_base.entity_at(client, surface, route_source)
    through_belt_stands = _entity_or_ghost_is(source_entity, "transport-belt")
    blocked = live_base.occupied_tiles(
        client, surface,
        (min(route_source[0], feed_position[0]) - _BRIDGE_SURVEY_MARGIN,
         min(route_source[1], feed_position[1]) - _BRIDGE_SURVEY_MARGIN),
        (max(route_source[0], feed_position[0]) + _BRIDGE_SURVEY_MARGIN,
         max(route_source[1], feed_position[1]) + _BRIDGE_SURVEY_MARGIN),
    )
    blocked |= additional_blocked or set()
    # A belt endpoint owns its tile, so a continuing bridge may connect on it.
    # A CHEST source attaches beside itself (+1 inserter, +2 belt) and its tile
    # must stay an obstacle: releasing it let a route thread belts straight
    # over the provider chest and die on execution against real infrastructure
    # (live run of 2026-08-24 18:47, iron plate to the promoted pipe line).
    if belt_source is not None:
        blocked -= {
            (math.floor(route_source[0]), math.floor(route_source[1])),
        }
    blocked -= {
        (math.floor(feed_position[0]), math.floor(feed_position[1])),
    }
    direction = _toward(route_source, feed_position)
    raw_exit = (
        _raw_belt_exit_direction(ingredient, route_source, source_position)
        if (
            belt_source is not None and destination_is_belt
            and (through_belt_stands or planned_handoff)
        ) else None
    )
    raw_exit_tile = (
        (math.floor(route_source[0] + DIRECTION_VECTORS[raw_exit][0]),
         math.floor(route_source[1] + DIRECTION_VECTORS[raw_exit][1]))
        if raw_exit is not None else None
    )
    # An already built raw terminal keeps its surveyed handoff direction and
    # fails closed if that route cannot work. A freshly planned terminal may
    # choose another clear side rather than force a tunnel corner. When the
    # caller states the collector's flow, that flow IS the exit: the position
    # heuristic reads a planned east-flow head as a west-flow terminal and
    # drove the haul backwards into the head ghost while the row was still
    # unbuilt (live run of 2026-08-24 16:24, stone refinery feed).
    if through_flow_direction is not None:
        flow_tile = (
            math.floor(route_source[0] + DIRECTION_VECTORS[through_flow_direction][0]),
            math.floor(route_source[1] + DIRECTION_VECTORS[through_flow_direction][1]),
        )
        exit_direction = (
            through_flow_direction if flow_tile not in blocked else None
        ) or _clear_side(route_source, direction, blocked)
    else:
        exit_direction = (
            raw_exit
            if raw_exit is not None and (
                not planned_handoff or raw_exit_tile not in blocked
            )
            else None
        ) or _clear_side(route_source, direction, blocked)
    if source_entity and source_entity.get("type") == "transport-belt" and hasattr(client, "command"):
        # A live output belt is an immutable interface, including plate
        # side taps feeding conversion chests. Never turn it toward the target.
        observed_exit = live_base.transport_belt_direction_at(client, surface, route_source)
        if observed_exit not in DIRECTION_VECTORS:
            raise StuckError(f"Cannot observe output belt direction at {route_source}")
        exit_direction = observed_exit
    entry_direction = _clear_side(feed_position, opposite(direction), blocked)
    if destination_is_belt:
        _release_owned_destination_approach(
            client, surface, feed_position, blocked,
            destination_belt_direction, owned_transport_tiles,
        )
        entry_direction = _direct_belt_entry(
            feed_position, blocked, destination_belt_direction,
        )
    if entry_direction is None:
        raise StuckError(
            f"{ingredient} cannot reach its feed endpoint at {feed_position}: "
            "every side of it is already occupied, so no inserter and belt "
            "pair fits. Building anyway would place belt on top of whatever "
            "is standing there."
        )
    if exit_direction is None:
        raise StuckError(
            f"{ingredient} cannot leave its source at {route_source}: every "
            "side of it is already occupied."
        )
    return belt_source, route_source, blocked, entry_direction, exit_direction


def _route_belt_actions(
    client: RconClient, surface: str, ingredient: str,
    source_position: Point, feed_position: Point, belt_source: Point | None,
    route_source: Point, blocked: set[tuple[int, int]], entry_direction: str,
    exit_direction: str, tier: str, max_belt_route_tiles: int | None,
    destination_is_belt: bool, destination_belt_direction: str,
) -> list[dict]:
    """Generate one route tier after geometry has passed the fail-closed survey."""
    if belt_source is not None and destination_is_belt:
        actions = bridge_belt_to_belt(
            belt_source, feed_position, entry_direction=entry_direction,
            belt_type=tier, blocked_tiles=blocked,
            max_route_tiles=max_belt_route_tiles,
            destination_direction=destination_belt_direction,
            exit_direction=exit_direction,
        )
        return _replace_existing_source_belt(
            client, surface, belt_source, actions,
        )
    if belt_source is not None:
        actions = bridge_belt_to_chest(
            belt_source, feed_position, entry_direction=entry_direction,
            belt_type=tier, inserter_type=_DEFAULT_INSERTER,
            blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
            exit_direction=exit_direction,
        )
        return _replace_existing_source_belt(
            client, surface, belt_source, actions,
        )
    if destination_is_belt:
        return bridge_chest_to_belt(
            source_position, feed_position,
            exit_direction=exit_direction, entry_direction=entry_direction,
            belt_type=tier, inserter_type=_DEFAULT_INSERTER,
            blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
            destination_direction=destination_belt_direction,
        )
    return bridge_chest_to_chest(
        source_position, feed_position,
        exit_direction=exit_direction, entry_direction=entry_direction,
        belt_type=tier, inserter_type=_DEFAULT_INSERTER,
        blocked_tiles=blocked, max_route_tiles=max_belt_route_tiles,
    )


def _plan_belt_transport(
    client: RconClient, surface: str, force: str, ingredient: str, source_position: Point,
    feed_position: Point, *, reuse_existing: bool, max_belt_route_tiles: int | None,
    additional_blocked: set[tuple[int, int]] | None = None,
    upstream_shift: int = 1, destination_is_belt: bool = False,
    reserved_transport_belts: int = 0,
    destination_belt_direction: str = "east",
    planned_belt_source: Point | None = None,
    allow_chest_source_to_belt: bool = False,
    through_flow_direction: str | None = None,
    required_belt_type: str | None = None,
    defer_required_tier_affordability: bool = False,
    owned_transport_tiles: set[tuple[int, int]] | None = None,
) -> tuple[list[dict], str, bool]:
    """Choose the first affordable legal tier after one shared geometry survey."""
    belt_source, route_source, blocked, entry_direction, exit_direction = (
        _survey_belt_route(
            client, surface, force, ingredient, source_position, feed_position,
            reuse_existing=reuse_existing, additional_blocked=additional_blocked,
            upstream_shift=upstream_shift, destination_is_belt=destination_is_belt,
            destination_belt_direction=destination_belt_direction,
            planned_belt_source=planned_belt_source,
            allow_chest_source_to_belt=allow_chest_source_to_belt,
            through_flow_direction=through_flow_direction,
            owned_transport_tiles=owned_transport_tiles,
        )
    )
    span = int(abs(route_source[0] - feed_position[0])
               + abs(route_source[1] - feed_position[1])) + 4
    stock = dict(live_base.available_items(client, surface, force))
    if reserved_transport_belts:
        stock["transport-belt"] = max(
            0, stock.get("transport-belt", 0) - reserved_transport_belts,
        )
    tier_order = _route_belt_tiers(destination_is_belt)
    if required_belt_type is not None:
        if required_belt_type not in tier_order:
            raise ValueError(
                f"{required_belt_type} is not a supported belt tier for this route"
            )
        ordered = [required_belt_type]
    else:
        preferred = _choose_route_belt_tier(
            stock, span, destination_is_belt=destination_is_belt,
        )
        ordered = [preferred] + [tier for tier in tier_order if tier != preferred]
    shortfalls: list[str] = []
    requirements: dict[str, dict[str, int]] = {}
    route_error: ValueError | None = None
    for tier in ordered:
        try:
            actions = _route_belt_actions(
                client, surface, ingredient, source_position, feed_position,
                belt_source, route_source, blocked, entry_direction,
                exit_direction, tier, max_belt_route_tiles,
                destination_is_belt, destination_belt_direction,
            )
        except ValueError as error:
            route_error = error
            continue
        required = {
            action["entity"]: sum(
                1 for candidate in actions
                if candidate.get("action_type") == "place_ghost"
                and candidate["entity"] == action["entity"]
            )
            for action in actions
            if action.get("action_type") == "place_ghost"
        }
        short = {
            item: count - stock.get(item, 0)
            for item, count in required.items()
            if stock.get(item, 0) < count
        }
        if required_belt_type is None and tier == "transport-belt" and short:
            actions = _mixed_belt_actions(actions, stock)
            required = {
                action["entity"]: sum(
                    1 for candidate in actions
                    if candidate.get("action_type") == "place_ghost"
                    and candidate["entity"] == action["entity"]
                )
                for action in actions
                if action.get("action_type") == "place_ghost"
            }
            short = {
                item: count - stock.get(item, 0)
                for item, count in required.items()
                if stock.get(item, 0) < count
            }
        if not short or (
            tier == required_belt_type and defer_required_tier_affordability
        ):
            return actions, tier, belt_source is not None
        requirements[tier] = required
        shortfalls.append(
            f"{tier} route short " + ", ".join(
                f"{item} by {missing}" for item, missing in sorted(short.items())
            )
        )
    if not shortfalls and route_error is not None:
        raise StuckError(
            f"no belt route is available for this bridge: {route_error}",
            code="belt_bridge_unroutable",
            details={
                "ingredient": ingredient,
                "source_position": [source_position[0], source_position[1]],
                "feed_position": [feed_position[0], feed_position[1]],
                "route_source": [route_source[0], route_source[1]],
                "entry_direction": entry_direction,
                "exit_direction": exit_direction,
                "destination_is_belt": destination_is_belt,
                "destination_belt_direction": destination_belt_direction,
                "tiers_attempted": list(ordered),
                "belt_source": list(belt_source) if belt_source is not None else None,
                "max_route_tiles": max_belt_route_tiles,
                "planner_error": str(route_error),
            },
        ) from route_error
    for tier in ordered:
        if tier in requirements:
            raise MaterialShortage(
                f"belt bridge for {ingredient}", requirements[tier], stock,
            )
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
    destination_is_belt: bool = False,
    destination_belt_direction: str = "east",
    allow_chest_source_to_belt: bool = False,
) -> None:
    """Reject an illegal belt route before its destination stage is submitted."""
    if (mode or _transport_mode(recipe, ingredient, machine_count)) == "logistic":
        return
    _plan_belt_transport(
        client, surface, force, ingredient, source_position, feed_position,
        reuse_existing=False, max_belt_route_tiles=max_belt_route_tiles,
        additional_blocked=additional_blocked,
        upstream_shift=upstream_shift, destination_is_belt=destination_is_belt,
        destination_belt_direction=destination_belt_direction,
        allow_chest_source_to_belt=allow_chest_source_to_belt,
        )


def relocate_blocking_poles(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    source_position: Point, feed_position: Point, emit: Callable[[str], None],
) -> int:
    """Nudge aside any pole standing on the corridor, and report how many moved.

    A pole is the one obstacle worth moving rather than routing around: it is a
    one-tile entity whose job is to stand *somewhere* in a supply area, not on
    one exact tile. Everything else on a route -- a machine, a chest, a drill --
    is where it is for a reason.

    A pole only moves when the new position still covers every consumer it was
    powering AND still reaches every pole it was wired to. If no such position
    exists it stays, and the caller routes around it as before.
    """
    corridor = corridor_tiles(source_position, feed_position)
    area = (
        (min(source_position[0], feed_position[0]) - 2,
         min(source_position[1], feed_position[1]) - 2),
        (max(source_position[0], feed_position[0]) + 2,
         max(source_position[1], feed_position[1]) + 2),
    )
    standing = [
        (name, position)
        for name, position in live_base.poles_in_area(client, surface, area)
        if (math.floor(position[0]), math.floor(position[1])) in corridor
    ]
    if not standing:
        return 0
    blocked = live_base.occupied_tiles(client, surface, *area)
    moves = []
    for name, position in standing:
        context = live_base.pole_context(client, surface, position)
        if context is None:
            continue
        move = choose_pole_move(
            name, position,
            supplied=context["supplied"], neighbours=context["neighbours"],
            blocked=blocked, keep_clear=corridor,
        )
        if move is None:
            emit(
                f"  POLE STAYS: {name} at {position} blocks the route but cannot "
                "move without dropping something off the network -- routing around"
            )
            continue
        emit(
            f"  POLE MOVED: {name} {position} -> {move.new} to clear the belt "
            f"route ({move.tiles:.0f} tile(s); supply and wiring preserved)"
        )
        moves.append(move)
    if not moves:
        return 0
    placement, retirement = staged_relocation_plans(moves)
    for plan in (placement, retirement):
        plan["surface"], plan["force"] = surface, force
    _submit(
        client, bridge, surface, placement, "place_relocated_poles", emit,
    )
    _submit(
        client, bridge, surface, retirement, "retire_relocated_poles", emit,
    )
    return len(moves)


def ensure_ingredient_transport(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ingredient: str, source_position: Point, feed_position: Point,
    machine_count: int, emit: Callable[[str], None], *, reuse_existing: bool = False,
    max_belt_route_tiles: int | None = None,
    mode: str | None = None,
    upstream_shift: int = 1,
    destination_is_belt: bool = False,
    destination_belt_direction: str = "east",
    allow_chest_source_to_belt: bool = False,
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
             "the belt continues directly without a side-loading inserter")
    # State the ACTUAL reason. This used to assert the demand exceeded the bot
    # limit on every belt route, including ones a caller forced -- so the log
    # read "0.30/s exceeds the 3.0/s bot limit", which is plainly false and hid
    # why a trickle was getting a belt corridor.
    reason = (
        f"{demand:.2f}/s exceeds the {_BOT_THROUGHPUT_LIMIT}/s bot limit"
        if demand > _BOT_THROUGHPUT_LIMIT
        else f"belt transport was requested for this link; {demand:.2f}/s would "
             f"fit inside the {_BOT_THROUGHPUT_LIMIT}/s bot limit"
    )
    emit(f"  {recipe}: {ingredient} needs a belt ({reason})")
    try:
        actions, belt_type, _ = _plan_belt_transport(
            client, surface, force, ingredient, source_position, feed_position,
            reuse_existing=reuse_existing, max_belt_route_tiles=max_belt_route_tiles,
            upstream_shift=upstream_shift, destination_is_belt=destination_is_belt,
            destination_belt_direction=destination_belt_direction,
            allow_chest_source_to_belt=allow_chest_source_to_belt,
        )
    except StuckError as blocked_route:
        # Before giving up, check whether what is in the way is merely a pole.
        emit(f"  ROUTE BLOCKED: {blocked_route}")
        if not relocate_blocking_poles(
            client, bridge, surface, force, source_position, feed_position, emit,
        ):
            raise
        actions, belt_type, _ = _plan_belt_transport(
            client, surface, force, ingredient, source_position, feed_position,
            reuse_existing=reuse_existing, max_belt_route_tiles=max_belt_route_tiles,
            upstream_shift=upstream_shift, destination_is_belt=destination_is_belt,
            destination_belt_direction=destination_belt_direction,
            allow_chest_source_to_belt=allow_chest_source_to_belt,
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
                # A REQUESTER either way. A belt-fed endpoint used to be a
                # plain steel chest, which only a belt can fill -- so when the
                # bridge for that ingredient failed, nothing could ever fill it
                # and the line was permanently dead. Observed on a
                # transport-belt line whose gear belt was built and whose
                # iron-plate belt was not: seven machines, zero working, and a
                # repair pass that kept reporting it merely "supply-starved".
                #
                # A requester takes belt input through its inserter exactly as
                # a steel chest does, and bots keep it alive meanwhile. Once the
                # belt runs, the chest stays full and the request goes quiet on
                # its own, so this costs nothing when the belt does exist.
                action["entity"] = "requester-chest"
                action["logistic_request"] = {"name": ingredient, "count": request_count}
                positions[ingredient] = (action["position"]["x"], action["position"]["y"])
    return positions


def _direct_single_belt_feed(
    plan: dict, ingredient: str, flow_direction: str = "east",
) -> Point:
    """Remove a single-ingredient feed chest and expose the upstream belt end."""
    if flow_direction not in {"north", "east", "south", "west"}:
        raise ValueError("Direct belt feed direction must be cardinal")
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
                    and abs(action["position"]["x"] - cx)
                    + abs(action["position"]["y"] - cy) == 1
                )
            ]
        belt_positions.extend(
            (action["position"]["x"], action["position"]["y"])
            for phase in plan["phases"] for action in phase["actions"]
            if "transport-belt" in action.get("entity", "")
            and abs(action["position"]["x"] - cx)
            + abs(action["position"]["y"] - cy) == 2
        )
    if not belt_positions:
        raise StuckError(f"{ingredient} feed chest has no adjacent input belt")
    vector = {
        "north": (0, -1), "east": (1, 0),
        "south": (0, 1), "west": (-1, 0),
    }[flow_direction]
    return min(
        belt_positions,
        key=lambda point: point[0] * vector[0] + point[1] * vector[1],
    )

def _publish_output_chest(plan: dict) -> None:
    """Make a stage's collection chest a passive provider, so its product is
    visible to the logistic network and can be requested by downstream stages
    (and by construction bots for building material)."""
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "steel-chest":
                action["entity"] = "passive-provider-chest"
