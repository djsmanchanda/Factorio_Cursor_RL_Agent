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
from planners.mall_layout import generate_paired_mall_layout, recipe_group_name
from planners.recipe_data import LINE_RECIPES
from tools.rcon_client import RconClient

Point = tuple[float, float]
# Keep two tiles between adjacent machine footprints while removing the old
# five-tile horizontal and four-tile vertical dead space.  The 2x2 substation
# still clears the next row at six tiles; five would overlap the lower-row
# input inserter.
_CELL_PITCH = (11, 6)
_CELL_COLUMNS = 3
_CELL_ROWS = 8
_PREFERRED_PAIRS = frozenset({
    frozenset({"electronic-circuit", "copper-cable"}),
    frozenset({"transport-belt", "copper-cable"}),
})
_INCOMPATIBLE_PAIRS = frozenset({
    frozenset({"electronic-circuit", "transport-belt"}),
    frozenset({"copper-cable"}),
})


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
    )
    bill: Counter[str] = Counter(plan_material_bill(preview))
    if side == "right":
        # The cell's centre requester and substation already belong to its
        # first half. A bootstrap-shared right half also reuses that half's
        # provider, so none of those entities belongs in its incremental bill.
        bill["requester-chest"] -= 1
        bill["substation"] -= 1
        if shared_provider:
            bill["passive-provider-chest"] -= 1
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
    min_x = min(origin[0] for origin in origins) - 2
    min_y = min(origin[1] for origin in origins)
    max_x = max(origin[0] for origin in origins) + 12
    max_y = max(origin[1] for origin in origins) + 7
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
    client: RconClient, surface: str, recipe: str, reference_point: Point,
) -> tuple[tuple[int, int], str] | None:
    """Expose the allocator's next stable cell half for exact bill pricing."""
    return _choose_slot(client, surface, recipe, reference_point)


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
    """Stable row-major district: cells touch their exact working footprint."""
    start = (round(reference_point[0]) + 32, round(reference_point[1]) + 32)
    return [
        (start[0] + column * _CELL_PITCH[0], start[1] + row * _CELL_PITCH[1])
        for row in range(_CELL_ROWS) for column in range(_CELL_COLUMNS)
    ]


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
    client: RconClient, surface: str, recipe: str, reference_point: Point,
) -> tuple[tuple[int, int], str] | None:
    """Pick a cell half for `recipe`.

    Nothing is read back off the shared requester: each half declares only its
    own labelled request group and the executor upserts it, so what the other
    half already asked for neither has to be known here nor merged in.
    """
    origins = _cell_origins(reference_point)
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
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
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
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
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
) -> Point:
    """Fill one slot in the centralized dense mall, leaving its pair assignable."""
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
    allocation = _choose_slot(client, surface, recipe, reference_point)
    if allocation is None:
        raise StuckError(f"No assignable slot remains in the compact parts mall for {recipe}")
    origin, side = allocation
    plan = generate_paired_mall_layout(
        recipe, machine_name or spec["machine"], spec["ingredients"], spec["amounts"], origin, side,
        stock_target=stock_target, product_amount=spec.get("product_amount", 1),
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
        stock_gate_target=stock_gate_target,
        fill_chest=fill_chest,
        request_multiplier_override=request_multiplier_override,
        shared_provider=shared_provider,
    )
    plan["surface"], plan["force"] = surface, force
    machine = _slot_position(origin, side)
    provider = (
        origin[0] + 4.5,
        origin[1] + (0.5 if side == "left" or shared_provider else 2.5),
    )
    substation = (origin[0] + 4.0, origin[1] + 5.0)
    requester_action = next(
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "requester-chest"
    )
    _clear_stale_side_requests(
        client, surface, requester_action, recipe, side,
    )
    emit(f"compact parts mall for {recipe}: assigning {side} half of cell {origin}")
    if hasattr(client, "command"):
        existing_network = live_base.pole_network_id(
            client, surface, substation,
        )
        existing_generation = (
            live_base.network_generation_kw(
                client, surface, force, substation,
            )
            if existing_network is not None else None
        )
        if (
            (existing_generation is None or existing_generation <= 0)
            and not extend_power(
                client, bridge, surface, force, substation, emit,
                reserved_tiles=planned_footprint_tiles(plan),
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
    )
    return provider
