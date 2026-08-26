# Path: orchestrator/mall_builder.py
# Purpose: Allocate and service dense paired cells in one centralized parts mall.

from __future__ import annotations

from collections.abc import Callable, Mapping

from orchestrator import live_base, resource_patches
from orchestrator.game_bridge import GameBridge
from orchestrator.stage_services import StuckError, _submit
from planners.mall_layout import generate_paired_mall_layout
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
            (related_open if _related(occupied_recipe, recipe) else other_open).append(candidate)
        elif not left_present and not right_present and requester_name == "requester-chest":
            if _side_clear(client, surface, origin, "left"):
                return origin, "left"
        elif not left_present and not right_present and requester_name == "-":
            empty_origins.append(origin)
    if related_open or other_open:
        return (related_open or other_open)[0]
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
        recipe, spec["machine"], spec["ingredients"], spec["amounts"], origin,
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
            recipe, spec["machine"], spec["ingredients"], spec["amounts"],
            origin, side, product_amount=spec.get("product_amount", 1),
            craft_time=spec["craft_time"],
            set_recipe=spec.get("set_recipe", True),
            request_multiplier_override=request_multiplier_override,
        )
        action = next(
            action for phase in plan["phases"] for action in phase["actions"]
            if action.get("entity") == "requester-chest"
        )
        position = (action["position"]["x"], action["position"]["y"])
        merged = requesters.setdefault(position, {
            **action, "logistic_sections": [],
        })
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
        recipe, spec["machine"], spec["ingredients"], spec["amounts"], origin, side,
        stock_target=stock_target, product_amount=spec.get("product_amount", 1),
        craft_time=spec["craft_time"], set_recipe=spec.get("set_recipe", True),
        stock_gate_target=stock_gate_target,
        fill_chest=fill_chest,
        request_multiplier_override=request_multiplier_override,
    )
    plan["surface"], plan["force"] = surface, force
    machine = _slot_position(origin, side)
    provider = (
        origin[0] + 4.5, origin[1] + (0.5 if side == "left" else 2.5),
    )
    substation = (origin[0] + 4.0, origin[1] + 5.0)
    emit(f"compact parts mall for {recipe}: assigning {side} half of cell {origin}")
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
