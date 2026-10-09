# Path: orchestrator/mall_builder.py
# Purpose: Allocate and service dense paired cells in one centralized parts mall.

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping

from orchestrator import live_base, resource_patches
from orchestrator.extraction_transport import planned_footprint_tiles
from orchestrator.game_bridge import GameBridge
from orchestrator.material_reservations import plan_material_bill
from orchestrator.stage_services import StuckError, _submit, extend_power
from planners.infrastructure_geometry import footprint_tile_indices
from planners.mall_layout import generate_paired_mall_layout, recipe_group_name
from planners.plan_validation import ENTITY_FOOTPRINTS
from planners.quad_mall_layout import (
    DENSE_MALL_MODULE_OFFSETS,
    QUAD_MALL_GROUP_MACHINES, QUAD_MALL_MACHINE_OFFSETS,
    generate_quad_mall_layout,
)
from planners.recipe_data import LINE_RECIPES
from planners.stock_gating import stock_gate
from tools.rcon_client import RconClient

Point = tuple[float, float]
# Keep two tiles between adjacent machine footprints while removing the old
# five-tile horizontal and four-tile vertical dead space.  The 2x2 substation
# still clears the next row at six tiles; five would overlap the lower-row
# input inserter.
_CELL_PITCH = (11, 6)
_CELL_COLUMNS = 3
_CELL_ROWS = 10
DEMAND_MALL_SLOT_TARGET = 12
_DEMAND_CELL_COUNT = DEMAND_MALL_SLOT_TARGET // 2
# The first dense requester is the centre of the opening staggered pair bank.
# Its shared substation sits between the two seed cells and remains clear of
# the upper/lower expansion modules.
_QUAD_MALL_CENTER_OFFSET = (4.5, 1.5)
_PREFERRED_PAIRS = frozenset({
    frozenset({"electronic-circuit", "copper-cable"}),
    frozenset({"transport-belt", "copper-cable"}),
})
_INCOMPATIBLE_PAIRS = frozenset({
    frozenset({"electronic-circuit", "transport-belt"}),
    # Permanent gear and cable anchors occupy separate seed cells. Their
    # companion halves remain available for the two rotating startup slots.
    frozenset({"iron-gear-wheel", "copper-cable"}),
    # The gear seed's shared substation occupies the right-half service tile;
    # keep transport-belt out of that half and choose the next clear cell.
    frozenset({"iron-gear-wheel", "transport-belt"}),
    frozenset({"copper-cable"}),
})


def _configure_built_mall_recipes(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, machine_name: str, machine_positions: list[Point],
    stock_gate_target: int | None, emit: Callable[[str], None],
) -> None:
    """Assign recipes after bots have turned mall ghosts into machines."""
    actions = []
    for x, y in machine_positions:
        action = {
            "action_type": "configure_entity",
            "entity": machine_name,
            "position": {"x": x, "y": y},
            "recipe": recipe,
        }
        if stock_gate_target is not None:
            action["logistic_condition"] = stock_gate(recipe, stock_gate_target)
        actions.append(action)
    if not actions:
        return
    plan = {
        "surface": surface,
        "force": force,
        "phases": [{
            "name": f"mall_recipe_{recipe}",
            "actions": actions,
        }],
    }
    _submit(client, bridge, surface, plan, f"mall_recipe_{recipe}", emit)


def _clear_stale_side_requests(
    client: RconClient, surface: str, requester_action: dict,
    recipe: str, side: str,
) -> None:
    """Remove old permanent groups for the half being (re)assigned.

    The opposite half shares this chest and is deliberately preserved. A
    prior recipe on the same side is not: leaving its labelled section behind
    is how a two-machine cell accumulated three or more recipe groups.
    """
    if not hasattr(client, "command"):
        return
    position = (
        float(requester_action["position"]["x"]),
        float(requester_action["position"]["y"]),
    )
    desired = recipe_group_name(recipe, side)
    suffix = f":{side}"
    stale = [
        group for group in live_base.requester_logistic_groups(
            client, surface, position,
        )
        if group.startswith("mall:")
        and group.endswith(suffix)
        and group != desired
    ]
    cleared = requester_action.setdefault("clear_logistic_groups", [])
    for group in stale:
        if group not in cleared:
            cleared.append(group)


def compact_mall_project_bill(
    recipe: str, *, stock_target: int = 1,
    stock_gate_target: int | None = None,
    fill_chest: bool = False,
    request_multiplier_override: int | None = None,
    side: str = "left",
    shared_provider: bool = False,
    machine_name: str | None = None,
    include_substation: bool = True,
) -> dict[str, int]:
    """Incremental cell-half bill plus one craft of bootstrap ingredients."""
    spec = LINE_RECIPES[recipe]
    preview = generate_paired_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"],
        (0, 0), side, stock_target=stock_target,
        product_amount=spec.get("product_amount", 1),
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
        stock_gate_target=stock_gate_target, fill_chest=fill_chest,
        request_multiplier_override=request_multiplier_override,
        shared_provider=shared_provider,
        include_substation=include_substation,
    )
    bill: Counter[str] = Counter(plan_material_bill(preview))
    if side == "right":
        # The cell's centre requester and substation already belong to its
        # first half. Each half owns a provider, so the right half only reuses
        # the requester and substation.
        bill["requester-chest"] -= 1
        bill["substation"] -= 1
    for ingredient, amount in zip(
        spec["ingredients"], spec["amounts"], strict=True,
    ):
        bill[ingredient] += math.ceil(amount)
    return dict(sorted(
        (item, count) for item, count in bill.items() if count > 0
    ))


def mall_slot_count(
    client: RconClient, surface: str, reference_point: Point,
) -> int:
    """Live or ghosted paired-mall assembler slots already committed."""
    states = _district_state(client, surface, _cell_origins(reference_point))
    paired = sum(
        recipe != "-"
        for left, right, _requester in states.values()
        for recipe in (left, right)
    )
    return paired + quad_mall_slot_count(client, surface, reference_point)


def quad_mall_center(reference_point: Point) -> Point:
    """Stable center of the opening four-machine expansion bay."""
    start = (round(reference_point[0]) + 32, round(reference_point[1]) + 32)
    return (
        start[0] + _QUAD_MALL_CENTER_OFFSET[0] + DENSE_MALL_MODULE_OFFSETS[0][0],
        start[1] + _QUAD_MALL_CENTER_OFFSET[1] + DENSE_MALL_MODULE_OFFSETS[0][1],
    )


def quad_support_inserter_type(recipe: str) -> str:
    """Return the required dense vertical transfer tier.

    Every dense vertical transfer is long-handed.  The long-handed recipe is
    seeded through the ordinary paired horizontal cell until one is in stock;
    it is never admitted to the dense bay with a regular-inserter fallback.
    """
    return "long-handed-inserter"


def dense_mall_module_centers(reference_point: Point) -> tuple[Point, ...]:
    """Return the fixed 4, 8, and 12-slot module centres."""
    first = quad_mall_center(reference_point)
    return tuple(
        (first[0] + dx, first[1] + dy)
        for dx, dy in DENSE_MALL_MODULE_OFFSETS
    )


def _quad_mall_state(client: RconClient, surface: str, center: Point) -> tuple[str, str, str]:
    """Read the two recipe groups and central requester for the quad module."""
    positions = {
        group: [
            (center[0] + QUAD_MALL_MACHINE_OFFSETS[side][0],
             center[1] + QUAD_MALL_MACHINE_OFFSETS[side][1])
            for side in sides
        ]
        for group, sides in QUAD_MALL_GROUP_MACHINES.items()
    }
    if not hasattr(client, "command"):
        return "-", "-", "-"
    literal = ",".join(
        "{" + str(x) + "," + str(y) + "}" for group in ("top", "bottom")
        for x, y in positions[group]
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "for _,p in ipairs({" + literal + "}) do local e=s.find_entities_filtered{position=p,radius=0.4,limit=1}[1];"
        "local ok,r=pcall(function() return e and e.get_recipe() end);out[#out+1]=(ok and r and r.name or '-') end;"
        "local c=s.find_entities_filtered{position={" + str(center[0]) + "," + str(center[1]) + "},radius=0.4,limit=1}[1];"
        "out[#out+1]=(c and c.name or '-');rcon.print(table.concat(out,','))"
    )
    raw = client.command("/sc " + lua).strip().split(",")
    values = (raw + ["-", "-", "-", "-", "-"])[:5]
    # The opening paired seed reuses the quad module's west/east positions.
    # Do not mistake those horizontal machines for a committed top/bottom
    # dense half; only the north/south positions prove quad expansion.
    top = values[0]
    bottom = values[3]
    return top, bottom, values[4]


def quad_mall_slot_count(
    client: RconClient, surface: str, reference_point: Point,
) -> int:
    """Committed assembler count across the reserved 4 -> 12-slot bank."""
    total = 0
    for center in dense_mall_module_centers(reference_point):
        top, bottom, requester = _quad_mall_state(client, surface, center)
        if requester in {"requester-chest", "entity-ghost"}:
            total += (2 if top != "-" else 0) + (2 if bottom != "-" else 0)
    return total


def preview_quad_mall_allocation(
    client: RconClient, surface: str, recipe: str, reference_point: Point,
) -> tuple[Point, str] | None:
    """Return the next open half, filling the three dense modules in order."""
    for center in dense_mall_module_centers(reference_point):
        top, bottom, requester = _quad_mall_state(client, surface, center)
        if requester not in {"requester-chest", "entity-ghost", "-"}:
            continue
        if top == "-":
            return center, "top"
        if bottom == "-":
            return center, "bottom"
    return None


def quad_mall_project_bill(
    recipe: str, group: str, *, stock_target: int = 1,
    machine_name: str | None = None, inserter_type: str | None = None,
    support_inserter_type: str | None = None,
    include_substation: bool = True,
) -> dict[str, int]:
    """Construction bill for one quad half, including only new shared parts."""
    spec = LINE_RECIPES[recipe]
    plan = generate_quad_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"],
        (0.0, 0.0), group, stock_target=stock_target,
        product_amount=spec.get("product_amount", 1), craft_time=spec["craft_time"],
        inserter_type=inserter_type,
        support_inserter_type=support_inserter_type,
        include_substation=include_substation,
    )
    return dict(sorted(
        (item, count) for item, count in Counter(plan_material_bill(plan)).items()
        if count > 0
    ))


def mall_demand_slot_count(
    client: RconClient, surface: str, reference_point: Point,
) -> int:
    """Live or ghosted slots in the post-plastic demand-capacity bank."""
    origins = _cell_origins(reference_point)[-_DEMAND_CELL_COUNT:]
    states = _district_state(client, surface, origins)
    return sum(
        recipe != "-"
        for left, right, _requester in states.values()
        for recipe in (left, right)
    )


def mall_entity_positions(
    client: RconClient, surface: str, force: str, reference_point: Point,
    entity_name: str,
) -> tuple[Point, ...]:
    """Exact, not-already-upgrading entities inside the compact mall district."""
    origins = _cell_origins(reference_point)
    dense_centers = dense_mall_module_centers(reference_point)
    min_x = min(origin[0] for origin in origins) - 2
    min_y = min(origin[1] for origin in origins)
    max_x = max(
        max(origin[0] for origin in origins) + 12,
        max(center[0] for center in dense_centers) + 8,
    )
    max_y = max(
        max(origin[1] for origin in origins) + 7,
        max(center[1] for center in dense_centers) + 8,
    )
    lua = (
        "local s=game.surfaces['" + surface + "'];local f=game.forces['" + force + "'];"
        "local out={};for _,e in pairs(s.find_entities_filtered{name='" + entity_name
        + "',force=f,area={{" + str(min_x) + "," + str(min_y) + "},{"
        + str(max_x) + "," + str(max_y) + "}}}) do "
        "if not e.to_be_upgraded() then out[#out+1]=e.position.x..':'..e.position.y end end;"
        "table.sort(out);rcon.print(table.concat(out,','))"
    )
    raw = client.command("/sc " + lua).strip()
    return tuple(
        (float(pair.split(":", 1)[0]), float(pair.split(":", 1)[1]))
        for pair in raw.split(",") if pair
    )


def preview_mall_allocation(
    client: RconClient, surface: str, recipe: str, reference_point: Point, *,
    demand_slot: bool = False,
) -> tuple[tuple[int, int], str] | None:
    """Expose the allocator's next stable cell half for exact bill pricing."""
    return _choose_slot(
        client, surface, recipe, reference_point, demand_slot=demand_slot,
    )


def mall_allocation_completes_pair(
    client: RconClient, surface: str,
    allocation: tuple[tuple[int, int], str] | None,
) -> bool:
    """Whether an allocation fills the open half of an existing cell."""
    if allocation is None:
        return False
    origin, _side = allocation
    state = _district_state(client, surface, [origin]).get(origin)
    if state is None:
        return False
    left, right, requester = state
    return requester == "requester-chest" and (left == "-") != (right == "-")


def mall_slot_is_demand(
    machine_position: Point, reference_point: Point,
) -> bool:
    """Whether a machine occupies the reserved post-plastic demand bank."""
    located = locate_mall_cell(machine_position, reference_point)
    if located is None:
        return False
    origin, _side = located
    return origin in set(_cell_origins(reference_point)[-_DEMAND_CELL_COUNT:])


def mall_slot_uses_shared_provider(
    client: RconClient, surface: str, machine_position: Point,
    reference_point: Point,
) -> bool:
    """Whether this slot belongs to a cell whose two halves share output."""
    located = locate_mall_cell(machine_position, reference_point)
    if located is None:
        return False
    origin, _side = located
    upper = live_base.entity_at(
        client, surface, (origin[0] + 4.5, origin[1] + 0.5),
    )
    lower = live_base.entity_at(
        client, surface, (origin[0] + 4.5, origin[1] + 2.5),
    )
    shared_output = live_base.entity_at(
        client, surface, (origin[0] + 5.5, origin[1] + 0.5),
    )
    return bool(
        upper and upper["name"] == "passive-provider-chest"
        and lower is None
        and shared_output
        and shared_output.get("name", "").endswith("inserter")
    )


def next_shared_provider_retrofit_plan(
    client: RconClient, surface: str, force: str, reference_point: Point,
) -> tuple[str, tuple[int, int], dict] | None:
    """Move one bootstrap right-half output onto its permanent own provider."""
    origins = _cell_origins(reference_point)
    states = _district_state(client, surface, origins)
    for origin in origins:
        _left, right, _requester = states[origin]
        if right not in LINE_RECIPES:
            continue
        machine = _slot_position(origin, "right")
        if not mall_slot_uses_shared_provider(
            client, surface, machine, reference_point,
        ):
            continue
        old_output_position = (origin[0] + 5.5, origin[1] + 0.5)
        old_output = live_base.entity_at(client, surface, old_output_position)
        if old_output is None:
            continue
        spec = LINE_RECIPES[right]
        permanent = generate_paired_mall_layout(
            right, spec["machine"], spec["ingredients"], spec["amounts"],
            origin, "right", stock_target=1,
            product_amount=spec.get("product_amount", 1),
            craft_time=spec["craft_time"],
            set_recipe=spec.get("set_recipe", True),
        )
        actions = permanent["phases"][0]["actions"]
        new_output = next(
            action for action in actions
            if action.get("entity", "").endswith("inserter")
            and action["position"] == {
                "x": origin[0] + 5.5, "y": origin[1] + 2.5,
            }
        )
        new_provider = next(
            action for action in actions
            if action.get("entity") == "passive-provider-chest"
        )
        plan = {
            "surface": surface,
            "force": force,
            "phases": [{
                "name": f"retrofit_shared_mall_{right}",
                "actions": [
                    {
                        "action_type": "remove_entity",
                        "entity": old_output["name"],
                        "position": {
                            "x": old_output_position[0],
                            "y": old_output_position[1],
                        },
                    },
                    new_output,
                    new_provider,
                ],
            }],
        }
        return right, origin, plan
    return None


def _cell_origins(reference_point: Point) -> list[tuple[int, int]]:
    """Stable district with the two staggered seed cells first."""
    start = (round(reference_point[0]) + 32, round(reference_point[1]) + 32)
    seed = [(start[0], start[1]), (start[0] + 6, start[1] + 5)]
    candidates = [
        (start[0] + column * _CELL_PITCH[0], start[1] + row * _CELL_PITCH[1])
        for row in range(_CELL_ROWS + 4) for column in range(_CELL_COLUMNS)
    ]
    def overlaps_seed(origin: tuple[int, int]) -> bool:
        left, top = origin
        right, bottom = left + 12, top + 7
        return any(
            left < sx + 12 and right > sx and top < sy + 7 and bottom > sy
            for sx, sy in seed
        )
    rest = [origin for origin in candidates if origin not in seed and not overlaps_seed(origin)]
    return seed + rest[: 30 - len(seed)]


def seed_shared_substation(reference_point: Point) -> Point:
    """Power anchor shared by the two opening paired cells."""
    start = (round(reference_point[0]) + 32, round(reference_point[1]) + 32)
    return (start[0] + 8.0, start[1] + 4.0)


def _existing_network_generation(
    client, surface: str, force: str, position: Point,
) -> float | None:
    """Generation on the network at `position`; None when unwired or unquerable.

    An unqueryable client (offline harness) degrades to None so the caller
    falls through to the normal `extend_power` survey instead of assuming
    either powered or unpowered.
    """
    try:
        if live_base.pole_network_id(client, surface, position) is None:
            return None
    except Exception:  # noqa: BLE001 -- offline client fakes cannot answer RCON
        return None
    try:
        return live_base.network_generation_kw(client, surface, force, position)
    except Exception:  # noqa: BLE001 -- same harness fallback
        return None


def seed_cell_uses_shared_substation(
    reference_point: Point, origin: tuple[int, int],
) -> bool:
    start = (round(reference_point[0]) + 32, round(reference_point[1]) + 32)
    return origin in {
        (start[0], start[1]), (start[0] + 6, start[1] + 5),
    }


def seed_cell_includes_substation(
    reference_point: Point, origin: tuple[int, int], side: str,
) -> bool:
    """Fund one reserved substation per paired cell, sharing the two seed cells."""
    origins = _cell_origins(reference_point)
    return side == "left" and origin != origins[1]


_SUBSTATION_WIRE_DISTANCE = 18.0


def _reserved_mall_substation_positions(reference_point: Point) -> set[Point]:
    positions = {seed_shared_substation(reference_point)}
    positions.update(
        reserved_substation_position(reference_point, origin)
        for origin in _cell_origins(reference_point)
    )
    positions.update(
        (center[0] + 3.5, center[1] + 2.5)
        for center in dense_mall_module_centers(reference_point)
    )
    return positions


def powered_reserved_substation_neighbor(
    client: RconClient, surface: str, force: str,
    reference_point: Point, target: Point,
) -> Point | None:
    """Find a real powered mall substation close enough to auto-wire `target`.

    Mall substations occupy reserved service tiles. Once a neighboring cell is
    powered, Factorio's substation wire range joins the next reserved anchor
    directly; a second pole corridor through the mall is unnecessary. Unknown
    telemetry falls back to the normal power-extension path.
    """
    if not callable(getattr(client, "command", None)):
        return None
    candidates = sorted(
        (
            position for position in _reserved_mall_substation_positions(reference_point)
            if position != target and _chebyshev(position, target) <= _SUBSTATION_WIRE_DISTANCE
        ),
        key=lambda position: _chebyshev(position, target),
    )
    if not candidates:
        return None
    margin = _SUBSTATION_WIRE_DISTANCE + 2
    records = live_base.area_entity_records(
        client, surface, force,
        (target[0] - margin, target[1] - margin),
        (target[0] + margin, target[1] + margin),
    )
    substations = {
        tuple(record["position"])
        for record in records
        if record.get("name") == "substation"
        and not record.get("is_ghost")
        and record.get("force") == force
    }
    for position in candidates:
        if position not in substations:
            continue
        network = live_base.pole_network_id(client, surface, position)
        if network is None:
            continue
        generation = live_base.network_generation_kw(
            client, surface, force, position,
        )
        if generation is not None and generation > 0:
            return position
    return None


def seed_expansion_reserved_tiles(reference_point: Point) -> set[tuple[int, int]]:
    """Reserve the whole dense bank before routing power or coverage.

    Reserving only the submitted half let a bridge pole or roboport land in a
    future top/bottom half.  Every module is cheap to describe from the same
    relative geometry, so reserve all three modules up front; this remains
    relative to the mall reference and works on other map seeds.
    """
    spec = LINE_RECIPES["iron-gear-wheel"]
    reserved: set[tuple[int, int]] = set()
    for center in dense_mall_module_centers(reference_point):
        for group in ("top", "bottom"):
            plan = generate_quad_mall_layout(
                "iron-gear-wheel", "assembling-machine-1",
                spec["ingredients"], spec["amounts"], center, group,
                inserter_type="inserter", support_inserter_type="long-handed-inserter",
                include_substation=True, craft_time=spec["craft_time"],
            )
            reserved.update(planned_footprint_tiles(plan))
    return reserved


PLANNED_CELL_SITES = 8  # future paired-cell sites kept clear of power and coverage routing


def _cell_footprint_tiles(origin: tuple[int, int]) -> set[tuple[int, int]]:
    """A paired cell's real footprint: both halves (machines, inserters, chests) and its own substation.

    Generated like the cell itself, so the gaps between cells stay free for the poles that power them.
    """
    spec = LINE_RECIPES["iron-gear-wheel"]
    tiles: set[tuple[int, int]] = set()
    for side in ("left", "right"):
        plan = generate_paired_mall_layout(
            "iron-gear-wheel", spec["machine"], spec["ingredients"], spec["amounts"], origin, side,
            craft_time=spec["craft_time"], set_recipe=False, include_substation=side == "left",
            substation_position=(origin[0] + 4.0, origin[1] + 5.0), shared_provider=False,
        )
        tiles |= planned_footprint_tiles(plan)
    return tiles


def planned_cell_reserved_tiles(
    reference_point: Point, current: tuple[int, int] | None = None, count: int = PLANNED_CELL_SITES,
) -> set[tuple[int, int]]:
    """Reserve the next ``count`` paired-cell sites after ``current`` before routing power.

    Reserving only the cell being built let its pole chain cross the sites of
    the cells that follow it, so later cells found poles on their land (and a
    failed cell left its bridge there). The district geometry is fixed and
    relative to the mall reference, so the coming sites are cheap to describe.
    """
    origins = _cell_origins(reference_point)
    start = origins.index(current) + 1 if current in origins else 0
    reserved: set[tuple[int, int]] = set()
    for origin in origins[start:start + count]:
        reserved |= _cell_footprint_tiles(origin)
    return reserved


def mall_grid_reserved_tiles(reference_point: Point) -> set[tuple[int, int]]:
    """Every tile the fixed mall grid reserves, built or not.

    Coverage and power must dodge ALL planned slot, dense-bank and shared
    substation footprints -- not just the entities that happen to exist -- so a
    roboport or pole chain cannot land on a slot the mall will fill later. The
    district geometry is fixed and relative to the mall reference, so the whole
    reservation is cheap to describe on any map seed.
    """
    reserved = set(seed_expansion_reserved_tiles(reference_point))
    reserved |= footprint_tile_indices(seed_shared_substation(reference_point), 2)
    for origin in _cell_origins(reference_point):
        reserved |= _cell_footprint_tiles(origin)
    return reserved


def reserved_substation_position(
    reference_point: Point, origin: tuple[int, int],
) -> Point:
    """The cell's reserved local bank service point, outside every planned slot.

    The substation sits in the inter-row service gap, below the slot's band of
    machines, inserters and chests, so it is a fixed local anchor the cell's
    machines sit inside rather than a tile a later slot or pole chain crosses.
    The two opening seed cells share the one between them.
    """
    if seed_cell_uses_shared_substation(reference_point, origin):
        return seed_shared_substation(reference_point)
    return (origin[0] + 4.0, origin[1] + 5.0)


_MALL_SLOT_SERVICE_RANGE = 9.0  # substation supply area radius


def mall_cell_footprint_conflicts(
    client: RconClient, surface: str, plan: dict,
) -> tuple[tuple[int, int], ...]:
    """Planned tiles a live foreign entity already occupies.

    The executor only rejects an entity whose CENTRE matches, so a 1x1 pole
    sitting under a 2x2 substation, or a stray chest on an inserter tile, slips
    through and the cell silently fails to build (live 2026-10-09: a ghost chest
    over a pole cost ~301 s). Only exact planned entities at their exact planned
    positions are allowed; another chest or inserter elsewhere in the footprint
    remains a real collision. Unknown surveys fail closed as a typed wait.
    """
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred

    def unavailable(reason: str) -> ProductionPrerequisiteDeferred:
        return ProductionPrerequisiteDeferred(
            f"mall footprint survey unavailable for {surface}: {reason}; "
            "parking this slot until it can be checked",
            code="mall_slot_survey_unavailable",
            classification="intended_difficulty",
            state="planned",
            details={"surface": surface, "reason": reason},
        )

    if client is None or not callable(getattr(client, "command", None)):
        raise unavailable("RCON client is unavailable")
    planned = planned_footprint_tiles(plan)
    if not planned:
        return ()
    planned_entities = {
        (action["entity"], float(action["position"]["x"]), float(action["position"]["y"]))
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("action_type") in {"place_entity", "place_ghost"}
        and action.get("entity") and action.get("position")
    }
    xs = [x for x, _ in planned]
    ys = [y for _, y in planned]
    minimum = (float(min(xs)), float(min(ys)))
    maximum = (float(max(xs) + 1), float(max(ys) + 1))
    try:
        owners = live_base.occupied_tile_owners(
            client, surface, minimum, maximum,
        )
        records = live_base.area_entity_records(
            client, surface, plan.get("force", "player"), minimum, maximum,
        )
        water = live_base.water_tiles(client, surface, minimum, maximum)
    except Exception as error:
        # A failed or malformed observation is not evidence that the slot is
        # clear. Keep the retry typed so the controller can revisit it later.
        raise unavailable(f"{type(error).__name__}: {error}") from error

    allowed_owner_centres: set[tuple[str, float, float]] = set()
    foreign_entity_tiles: set[tuple[int, int]] = set()
    for record in records:
        position = record["position"]
        actual_name = (
            record["ghost_name"] if record.get("is_ghost") else record["name"]
        )
        identity = (str(actual_name), float(position[0]), float(position[1]))
        if identity in planned_entities:
            allowed_owner_centres.add((
                str(record["name"]), float(position[0]), float(position[1]),
            ))
            if record.get("is_ghost"):
                allowed_owner_centres.add((
                    "entity-ghost", float(position[0]), float(position[1]),
                ))
            continue
        if (
            record.get("force") == "neutral"
            and record.get("type") in {"tree", "simple-entity"}
        ):
            continue
        footprint_name = str(actual_name)
        size = ENTITY_FOOTPRINTS.get(footprint_name, 1)
        foreign_entity_tiles.update(
            footprint_tile_indices(
                (float(position[0]), float(position[1])), size,
            )
        )

    allowed_tiles: set[tuple[int, int]] = set()
    for tile, (name, x, y) in owners.items():
        if (name, x, y) in allowed_owner_centres:
            allowed_tiles.add(tile)

    occupied_entities = set(owners)
    conflicts = (
        (occupied_entities - allowed_tiles)
        | foreign_entity_tiles
        | set(water)
    ) & planned
    return tuple(sorted(conflicts))


def assert_mall_cell_footprint_free(
    client: RconClient, surface: str, plan: dict, recipe: str,
    origin: tuple[int, int], side: str,
) -> None:
    """Fail clearly when a chosen slot is genuinely occupied."""
    conflicts = mall_cell_footprint_conflicts(client, surface, plan)
    if conflicts:
        raise StuckError(
            f"compact mall cell for {recipe} at {origin} ({side}) is already "
            f"occupied by foreign infrastructure: {list(conflicts[:4])}",
            code="mall_slot_occupied",
            state="failed",
            details={
                "recipe": recipe,
                "origin": [origin[0], origin[1]],
                "side": side,
                "tiles": [list(tile) for tile in conflicts[:16]],
            },
        )


def _bank_anchor(reference_point: Point) -> Point:
    """The opening mall bank's fixed centre: the adjacency reference for slots."""
    return quad_mall_center(reference_point)


def _chebyshev(left: Point, right: Point) -> float:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def _rank_empty_origins(
    candidates: list[tuple[int, int]], committed: list[tuple[int, int]],
    reference_point: Point,
) -> list[tuple[int, int]]:
    """Order empty cell sites nearest the existing bank first.

    A committed bank makes the next available site the one adjacent to it, so a
    new cell lands beside the mall instead of at a distant reserved site that
    then needs its own coverage and a long pole corridor (live 2026-10-10: the
    long-handed-inserter cell was assigned to the far (46,49) site). With
    nothing committed yet the fixed origin order is kept, so the seed cells stay
    first.
    """
    if not committed:
        return list(candidates)
    anchor = _bank_anchor(reference_point)

    def key(origin: tuple[int, int]) -> tuple[float, float, int, int]:
        return (
            min(_chebyshev(origin, other) for other in committed),
            _chebyshev(origin, anchor),
            origin[0],
            origin[1],
        )

    return sorted(candidates, key=key)


def _slot_position(origin: tuple[int, int], side: str) -> Point:
    return (origin[0] + (1.5 if side == "left" else 7.5), origin[1] + 1.5)


def _related(existing_recipe: str | None, recipe: str) -> bool:
    if not existing_recipe or existing_recipe not in LINE_RECIPES:
        return False
    return (
        existing_recipe in LINE_RECIPES[recipe]["ingredients"]
        or recipe in LINE_RECIPES[existing_recipe]["ingredients"]
    )


def _preferred_pair(existing_recipe: str | None, recipe: str) -> bool:
    return frozenset({existing_recipe, recipe}) in _PREFERRED_PAIRS


def _incompatible_pair(existing_recipe: str | None, recipe: str) -> bool:
    return frozenset({existing_recipe, recipe}) in _INCOMPATIBLE_PAIRS

def _district_state(
    client: RconClient, surface: str, origins: list[tuple[int, int]],
) -> dict[tuple[int, int], tuple[str, str, str]]:
    """Read every mall slot and central requester in one deterministic query."""
    literal = ",".join("{" + str(x) + "," + str(y) + "}" for x, y in origins)
    lua = (
        "local s=game.surfaces['" + surface + "'];local out={};"
        "local function at(x,y) return s.find_entities_filtered{position={x,y},radius=0.4,limit=1}[1] end;"
        "local function state(e) if not e then return '-' end;local ok,r=pcall(function() return e.get_recipe() end);"
        "return ok and r and r.name or '?' end;local function name(e) if not e then return '-' end;"
        "return e.type=='entity-ghost' and e.ghost_name or e.name end;"
        "for i,o in ipairs({" + literal + "}) do local x,y=o[1],o[2];"
        "out[#out+1]=i..','..state(at(x+1.5,y+1.5))..','..state(at(x+7.5,y+1.5))"
        "..','..name(at(x+4.5,y+1.5)) end;rcon.print(table.concat(out,';'))"
    )
    raw = client.command("/sc " + lua).strip()
    state = {}
    for record in raw.split(";"):
        if record:
            index, left, right, requester = record.split(",", 3)
            state[origins[int(index) - 1]] = (left, right, requester)
    return state


def _side_clear(
    client: RconClient, surface: str, origin: tuple[int, int], side: str,
) -> bool:
    ox, oy = origin
    if side == "left":
        machine_min, machine_max = (ox, oy), (ox + 3, oy + 3)
        points = [(ox + 3.5, oy + 1.5), (ox + 3.5, oy + 0.5), (ox + 4.5, oy + 0.5)]
        land_min, land_max = (ox, oy), (ox + 4, oy + 3)
    else:
        machine_min, machine_max = (ox + 6, oy), (ox + 9, oy + 3)
        points = [(ox + 5.5, oy + 1.5), (ox + 5.5, oy + 2.5), (ox + 4.5, oy + 2.5)]
        land_min, land_max = (ox + 5, oy), (ox + 9, oy + 3)
    return (
        live_base.area_clear(client, surface, machine_min, machine_max)
        and all(live_base.entity_at(client, surface, point) is None for point in points)
        and not resource_patches.box_has_reserved_patch(client, surface, land_min, land_max)
    )


def _choose_slot(
    client: RconClient, surface: str, recipe: str, reference_point: Point, *,
    demand_slot: bool = False,
) -> tuple[tuple[int, int], str] | None:
    """Pick a cell half for `recipe`.

    Nothing is read back off the shared requester: each half declares only its
    own labelled request group and the executor upserts it, so what the other
    half already asked for neither has to be known here nor merged in.
    """
    all_origins = _cell_origins(reference_point)
    origins = (
        all_origins[-_DEMAND_CELL_COUNT:]
        if demand_slot
        else all_origins[:-_DEMAND_CELL_COUNT]
    )
    states = _district_state(client, surface, origins)
    preferred_open: list[tuple[tuple[int, int], str]] = []
    related_open: list[tuple[tuple[int, int], str]] = []
    other_open: list[tuple[tuple[int, int], str]] = []
    empty_origins: list[tuple[int, int]] = []
    for origin in origins:
        left, right, requester_name = states[origin]
        left_present, right_present = left != "-", right != "-"
        if left_present != right_present and requester_name == "requester-chest":
            side = "right" if left_present else "left"
            if not _side_clear(client, surface, origin, side):
                continue
            occupied_recipe = left if left_present else right
            candidate = (origin, side)
            if _incompatible_pair(occupied_recipe, recipe):
                continue
            if _preferred_pair(occupied_recipe, recipe):
                preferred_open.append(candidate)
            elif _related(occupied_recipe, recipe):
                related_open.append(candidate)
            else:
                other_open.append(candidate)
        elif not left_present and not right_present and requester_name == "requester-chest":
            if _side_clear(client, surface, origin, "left"):
                return origin, "left"
        elif not left_present and not right_present and requester_name == "-":
            empty_origins.append(origin)
    if preferred_open or related_open or other_open:
        return (preferred_open or related_open or other_open)[0]
    empty_origins = _rank_empty_origins(
        empty_origins,
        [origin for origin in origins if states[origin][2] != "-"],
        reference_point,
    )
    for origin in empty_origins:
        footprint_min = (origin[0] - 2, origin[1])
        footprint_max = (origin[0] + 12, origin[1] + 7)
        if (
            live_base.area_clear(client, surface, footprint_min, footprint_max)
            and not resource_patches.box_has_reserved_patch(
                client, surface, footprint_min, footprint_max,
            )
        ):
            return origin, "left"
    return None

def locate_mall_cell(
    machine_position: Point, reference_point: Point,
) -> tuple[tuple[int, int], str] | None:
    """Inverse of slot allocation: which declared cell half holds this machine.

    The district is deterministic from `reference_point` (row-major, fixed
    pitch), so a machine either sits on exactly one declared slot or outside
    the mall entirely."""
    target = (float(machine_position[0]), float(machine_position[1]))
    for origin in _cell_origins(reference_point):
        for side in ("left", "right"):
            if _slot_position(origin, side) == target:
                return origin, side
    return None


def mall_cell_needs_rebuild(
    client: RconClient, surface: str, recipe: str, machine_position: Point,
    reference_point: Point,
) -> bool:
    """Whether a declared cell half is degraded enough to regenerate.

    Degraded means the declared feed chest is missing. An existing but empty
    chest is normally upstream starvation, not structural damage; rebuilding
    it cannot create supply and only produces zero-action churn.
    Pure presence checks only; False outside the mall district."""
    located = locate_mall_cell(machine_position, reference_point)
    if located is None:
        return False
    origin, side = located
    spec = LINE_RECIPES[recipe]
    plan = generate_paired_mall_layout(
        recipe, spec["machine"], spec["ingredients"], spec["amounts"], origin,
        side, product_amount=spec.get("product_amount", 1),
        # Blueprints carry the machine footprint only.  The recipe is applied
        # by the post-construction stock-gate configuration pass.
        craft_time=spec["craft_time"], set_recipe=False,
    )
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("entity") == "requester-chest":
                position = action["position"]
                held = live_base.chest_stored_items(
                    client, surface, (position["x"], position["y"]),
                )
                if held < 0:
                    return True
    return False


def rebuild_incomplete_mall_cell(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, machine_position: Point, reference_point: Point,
    emit: Callable[[str], None], *, stock_target: int = 1,
    machine_name: str | None = None,
) -> bool:
    """Regenerate a half-built paired cell's declared plan IN PLACE.

    Live run 32 (2026-08-22): an advanced-circuit cell came up with its
    assembler but none of its feed infrastructure; the strict line repair
    refused that geometry and killed the run. The paired-cell plan is
    deterministic from (origin, side) and the executor is idempotent, so
    resubmitting it at the cell's own origin places only what is missing
    instead of duplicating the cell on a fresh slot. Returns False when the
    machine does not belong to any declared mall cell.
    """
    located = locate_mall_cell(machine_position, reference_point)
    if located is None:
        return False
    origin, side = located
    spec = LINE_RECIPES[recipe]
    plan = generate_paired_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"], origin,
        side, stock_target=stock_target,
        product_amount=spec.get("product_amount", 1),
        # Blueprints carry the machine footprint only.  The recipe is applied
        # by the post-construction stock-gate configuration pass.
        craft_time=spec["craft_time"], set_recipe=False,
        include_substation=seed_cell_includes_substation(
            reference_point, origin, side,
        ),
        substation_position=seed_shared_substation(reference_point),
    )
    plan["surface"], plan["force"] = surface, force
    emit(
        f"rebuilding incomplete mall cell for {recipe} at {origin} ({side})"
    )
    _submit(client, bridge, surface, plan, f"repaired_mall_{recipe}", emit)
    return True


def refresh_paired_mall_requests(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, machine_positions: list[Point], reference_point: Point,
    emit: Callable[[str], None], *, request_multiplier_override: int | None = None,
    machine_name: str | None = None,
) -> bool:
    """Migrate complete paired cells to one request section per machine."""
    spec = LINE_RECIPES[recipe]
    requesters: dict[Point, dict] = {}
    for machine_position in machine_positions:
        located = locate_mall_cell(machine_position, reference_point)
        if located is None:
            continue
        origin, side = located
        plan = generate_paired_mall_layout(
            recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"],
            origin, side, product_amount=spec.get("product_amount", 1),
            craft_time=spec["craft_time"],
            set_recipe=spec.get("set_recipe", True),
            request_multiplier_override=request_multiplier_override,
        )
        action = next(
            action for phase in plan["phases"] for action in phase["actions"]
            if action.get("entity") == "requester-chest"
        )
        _clear_stale_side_requests(
            client, surface, action, recipe, side,
        )
        position = (action["position"]["x"], action["position"]["y"])
        merged = requesters.setdefault(position, {
            **action, "logistic_sections": [],
        })
        cleared = merged.setdefault("clear_logistic_groups", [])
        for group in action.get("clear_logistic_groups", []):
            if group not in cleared:
                cleared.append(group)
        merged["logistic_sections"].extend(action["logistic_sections"])
    if not requesters:
        return False
    plan = {"surface": surface, "force": force, "phases": [{
        "name": f"paired_mall_requests_{recipe}",
        "actions": list(requesters.values()),
    }]}
    _submit(client, bridge, surface, plan, f"paired_mall_requests_{recipe}", emit)
    return True


def build_compact_mall_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ingredient_sources: Mapping[str, Point], reference_point: Point,
    bring_stage_up: Callable, emit: Callable[[str], None], *, stock_target: int = 1,
    stock_gate_target: int | None = None,
    fill_chest: bool = False,
    request_multiplier_override: int | None = None,
    shared_provider: bool = False,
    machine_name: str | None = None,
    demand_slot: bool = False,
    quad_mall: bool = False,
) -> Point:
    """Fill one slot in the centralized dense mall, leaving its pair assignable."""
    if quad_mall:
        return build_quad_mall_stage(
            client, bridge, surface, force, recipe, ingredient_sources,
            reference_point, bring_stage_up, emit, stock_target=stock_target,
            stock_gate_target=stock_gate_target, fill_chest=fill_chest,
            request_multiplier_override=request_multiplier_override,
            machine_name=machine_name,
        )
    spec = LINE_RECIPES[recipe]
    if not spec.get("set_recipe", True):
        # find_line counts machines by the recipe they have SET. A furnace takes
        # its recipe from whatever is inserted, so an idle one has none and can
        # never be counted -- the caller sees zero however many were built, and
        # builds another every pass. Twelve steel-plate furnaces went up across
        # six mall cells that way before the livelock guard stopped it.
        raise StuckError(
            f"{recipe} is smelted, not assembled: its machines take a recipe from "
            "what is inserted, so a mall cell for it could never be counted again "
            "and would be rebuilt every pass. It needs a smelting stage."
        )
    allocation = _choose_slot(
        client, surface, recipe, reference_point, demand_slot=demand_slot,
    )
    if allocation is None:
        raise StuckError(f"No assignable slot remains in the compact parts mall for {recipe}")
    origin, side = allocation
    plan = generate_paired_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"], origin, side,
        stock_target=stock_target, product_amount=spec.get("product_amount", 1),
        # Blueprints carry the machine footprint only.  The recipe is applied
        # by the post-construction stock-gate configuration pass.
        craft_time=spec["craft_time"], set_recipe=False,
        include_substation=seed_cell_includes_substation(
            reference_point, origin, side,
        ),
        substation_position=reserved_substation_position(reference_point, origin),
        stock_gate_target=stock_gate_target,
        fill_chest=fill_chest or recipe in {"iron-gear-wheel", "copper-cable"},
        request_multiplier_override=request_multiplier_override,
        shared_provider=False,
    )
    plan["surface"], plan["force"] = surface, force
    machine = _slot_position(origin, side)
    provider = (
        origin[0] + 4.5,
        origin[1] + (0.5 if side == "left" else 2.5),
    )
    substation = reserved_substation_position(reference_point, origin)
    requester_action = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "requester-chest"
    )
    _clear_stale_side_requests(
        client, surface, requester_action, recipe, side,
    )
    emit(f"compact parts mall for {recipe}: assigning {side} half of cell {origin}")
    assert_mall_cell_footprint_free(
        client, surface, plan, recipe, origin, side,
    )
    reserved_tiles = planned_footprint_tiles(plan) | mall_grid_reserved_tiles(reference_point)
    if callable(getattr(client, "command", None)):
        existing_network = live_base.pole_network_id(
            client, surface, substation,
        )
        existing_generation = (
            live_base.network_generation_kw(
                client, surface, force, substation,
            )
            if existing_network is not None else None
        )
        adjacent_power = (
            powered_reserved_substation_neighbor(
                client, surface, force, reference_point, substation,
            )
            if existing_generation is None or existing_generation <= 0 else None
        )
        if adjacent_power is not None:
            emit(
                f"  MALL SUBSTATION LINK: reserved substation at {substation} "
                f"will auto-connect to powered mall anchor {adjacent_power}"
            )
        if (
            (existing_generation is None or existing_generation <= 0)
            and adjacent_power is None
            and not extend_power(
                client, bridge, surface, force, substation, emit,
                reserved_tiles=reserved_tiles,
            )
        ):
            raise StuckError(
                f"compact mall for {recipe} cannot stage generated power beside "
                f"its planned substation at {substation}"
            )
    _submit(client, bridge, surface, plan, f"paired_mall_{recipe}", emit)
    chests = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action["entity"] in {"requester-chest", "passive-provider-chest"}
    ]
    stage_area = ((origin[0] - 2, origin[1]), (origin[0] + 12, origin[1] + 7))
    bring_stage_up(
        client, bridge, surface, force, f"compact mall for {recipe}",
        origin, stage_area, substation, [machine], emit,
        logistic_chest_positions=[*ingredient_sources.values(), *chests],
        reserved_tiles=reserved_tiles,
    )
    _configure_built_mall_recipes(
        client, bridge, surface, force, recipe,
        machine_name or spec["machine"], [machine], stock_gate_target, emit,
    )
    return provider


def build_quad_mall_stage(
    client: RconClient, bridge: GameBridge, surface: str, force: str,
    recipe: str, ingredient_sources: Mapping[str, Point], reference_point: Point,
    bring_stage_up: Callable, emit: Callable[[str], None], *, stock_target: int = 1,
    stock_gate_target: int | None = None, fill_chest: bool = False,
    request_multiplier_override: int | None = None,
    machine_name: str | None = None,
) -> Point:
    """Fill one half of the four-machine bootstrap module."""
    spec = LINE_RECIPES[recipe]
    if recipe == "long-handed-inserter" and callable(
        getattr(client, "command", None),
    ):
        stock = live_base.transferable_items(client, surface, force)
        if stock.get("long-handed-inserter", 0) <= 0:
            from orchestrator.autonomous_builder import (
                ProductionPrerequisiteDeferred,
            )
            raise ProductionPrerequisiteDeferred(
                "dense long-handed-inserter mall waits for existing "
                "long-handed support stock; seed it in a paired cell first",
                code="dense_long_handed_support_wait",
                state="supply_wait",
                details={"item": recipe},
            )
    allocation = preview_quad_mall_allocation(
        client, surface, recipe, reference_point,
    )
    if allocation is None:
        raise StuckError("No assignable half remains in the quad bootstrap mall")
    center, group = allocation
    # The opening four slots are the regular-inserter seed bank.  The dense
    # expansion adds vertical north/south machines around that bank; their
    # requester and provider transfers use long-handed inserters in the
    # declared order. Horizontal west/east positions keep regular inserters.
    seed_inserter = "inserter"
    support_inserter = quad_support_inserter_type(recipe)
    plan = generate_quad_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"], center,
        group, stock_target=stock_target,
        product_amount=spec.get("product_amount", 1), craft_time=spec["craft_time"],
        stock_gate_target=stock_gate_target, fill_chest=fill_chest,
        # Assemblers are blueprinted first; configure the recipe only after
        # construction has produced a real machine.
        set_recipe=False,
        request_multiplier_override=request_multiplier_override,
        inserter_type=seed_inserter,
        support_inserter_type=support_inserter,
        include_substation=not (
            center == quad_mall_center(reference_point) and group == "top"
        ),
    )
    # Older runs admitted this expansion with regular vertical inserters.
    # Remove those exact wrong-tier entities/ghosts before resubmitting the
    # corrected long-handed pair; otherwise idempotent placement sees an
    # occupied tile and leaves the fourth machine half permanently missing.
    if callable(getattr(client, "command", None)):
        wrong_supports = []
        for side in QUAD_MALL_GROUP_MACHINES[group]:
            if side not in {"north", "south"}:
                continue
            y = -3.0 if side == "north" else 3.0
            output_y = -2.0 if side == "north" else 2.0
            for y_offset in (y, output_y):
                position = (center[0], center[1] + y_offset)
                existing = live_base.entity_at(client, surface, position)
                if not existing:
                    continue
                actual = (
                    existing.get("ghost_name")
                    if existing.get("type") == "entity-ghost"
                    else existing.get("name")
                )
                if actual in {"inserter", "fast-inserter", "bulk-inserter"}:
                    wrong_supports.append({
                        "action_type": "remove_entity",
                        "entity": actual,
                        "position": {"x": position[0], "y": position[1]},
                    })
        if wrong_supports:
            plan["phases"][0]["actions"] = (
                wrong_supports + plan["phases"][0]["actions"]
            )
            emit(
                f"  QUAD MALL REPAIR: replacing {len(wrong_supports)} "
                "vertical inserter(s) with long-handed transfers"
            )
    plan["surface"], plan["force"] = surface, force
    emit(f"quad bootstrap mall for {recipe}: assigning {group} half at {center}")
    seed_module = center == quad_mall_center(reference_point) and group == "top"
    assert_mall_cell_footprint_free(
        client, surface, plan, recipe,
        (int(center[0]), int(center[1])), group,
    )
    reserved_tiles = (
        planned_footprint_tiles(plan)
        | mall_grid_reserved_tiles(reference_point)
    )
    substation = (
        seed_shared_substation(reference_point)
        if seed_module else (center[0] + 3.5, center[1] + 2.5)
    )
    if group == "top" and hasattr(client, "command"):
        # Mirror the compact-mall staging pre-check: a substation whose network
        # already generates (notably the shared seed anchor beside the quad
        # seed bay) must be accepted without a bridge survey, otherwise the
        # first admission defers forever and the mall can never grow past its
        # seed slots (live 2026-09-17: stuck at 3 of 8 slots).
        existing_generation = _existing_network_generation(
            client, surface, force, substation,
        )
        adjacent_power = (
            powered_reserved_substation_neighbor(
                client, surface, force, reference_point, substation,
            )
            if existing_generation is None or existing_generation <= 0 else None
        )
        if adjacent_power is not None:
            emit(
                f"  MALL SUBSTATION LINK: reserved substation at {substation} "
                f"will auto-connect to powered mall anchor {adjacent_power}"
            )
        if (
            (existing_generation is None or existing_generation <= 0)
            and adjacent_power is None
            and not extend_power(
                client, bridge, surface, force, substation, emit,
                reserved_tiles=(
                    reserved_tiles
                ),
            )
        ):
            # Staging wait on real infrastructure is a supply_wait, not a
            # terminal bug (run 5, 2026-09-17: the first quad-top admission
            # killed the run at the shared seed gap). Park the admission;
            # the queued-demand paths retry while power lands. The lazy
            # import keeps autonomous_builder -> mall_builder one-way.
            from orchestrator.autonomous_builder import (
                ProductionPrerequisiteDeferred,
            )
            emit(
                f"  QUAD MALL POWER STAGING RETRY: {recipe} top half waits "
                f"for generated power at {substation}; parking the "
                "admission and retrying after re-observation"
            )
            raise ProductionPrerequisiteDeferred(
                f"quad mall for {recipe} cannot stage generated power at "
                f"{substation}; retrying after re-observation",
                code="quad_power_staging_deferred",
                state="power_wait",
                details={
                    "recipe": recipe,
                    "substation": [substation[0], substation[1]],
                },
            )
    _submit(client, bridge, surface, plan, f"quad_mall_{recipe}_{group}", emit)
    machines = [
        (center[0] + QUAD_MALL_MACHINE_OFFSETS[side][0],
         center[1] + QUAD_MALL_MACHINE_OFFSETS[side][1])
        for side in QUAD_MALL_GROUP_MACHINES[group]
    ]
    chests = [
        (action["position"]["x"], action["position"]["y"])
        for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") in {"requester-chest", "passive-provider-chest"}
    ]
    stage_area = ((center[0] - 7, center[1] - 7), (center[0] + 7, center[1] + 7))
    bring_stage_up(
        client, bridge, surface, force, f"quad bootstrap mall for {recipe}",
        center, stage_area, substation, machines, emit,
        logistic_chest_positions=[*ingredient_sources.values(), *chests],
        reserved_tiles=reserved_tiles,
    )
    _configure_built_mall_recipes(
        client, bridge, surface, force, recipe,
        machine_name or spec["machine"], machines, stock_gate_target, emit,
    )
    provider_offset = (0.0, -1.0) if group == "top" else (0.0, 1.0)
    return (center[0] + provider_offset[0], center[1] + provider_offset[1])
