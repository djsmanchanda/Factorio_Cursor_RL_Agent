# Path: planners/resource_layouts.py
# Purpose: Deterministic real coal, crude-oil, and water source layouts from supplied coordinates.

from __future__ import annotations

from math import floor, isclose

from planners.infrastructure import POLE_SPECS
from planners.plan_validation import ENTITY_FOOTPRINTS, validate_build_plan
from planners.recipe_data import BELT_TIERS

_DIRECTION_VECTORS = {
    "north": (0, -1), "east": (1, 0),
    "south": (0, 1), "west": (-1, 0),
}

# The pipe stands immediately outside the 3x3 pumpjack footprint. The
# graphical connector is on the machine edge, but placing a pipe on that edge
# tile collides with the pumpjack (live run 2026-08-26 04:43).
_PUMPJACK_OUTPUT_OFFSETS = {
    "north": (-1, -2), "east": (2, -1),
    "south": (1, 2), "west": (-2, 1),
}

_OFFSHORE_OUTPUT_OFFSETS = {
    # Factorio's offshore-pump direction points into the water. The pipe
    # connector is the immediately adjacent tile on the opposite, landward
    # side. Skipping one extra tile leaves the pump disconnected.
    "north": (0, 1), "east": (-1, 0),
    "south": (0, -1), "west": (1, 0),
}

ROW_POLE = "medium-electric-pole"

# Keep the first collector straight for a bounded reserve before a refinery
# haul is allowed to turn.  Twenty-four tiles puts the continuation near the
# 115..120 coordinates seen in the early iron layout without reserving an
# unbounded corridor on every patch.
DIRECT_EAST_CONTINUATION_TILES = 24

_SPLITTER_FOR_BELT = {
    "transport-belt": "splitter",
    "fast-transport-belt": "fast-splitter",
    "express-transport-belt": "express-splitter",
    "turbo-transport-belt": "turbo-splitter",
}

_UNDERGROUND_REACH = {
    "transport-belt": 5,
    "fast-transport-belt": 7,
    "express-transport-belt": 9,
    "turbo-transport-belt": 11,
}


def verified_pumpjack_output_tile(site: dict) -> tuple[int, int] | None:
    """Return the rotated pumpjack connector tile for a cardinal direction."""
    offset = _PUMPJACK_OUTPUT_OFFSETS.get(site.get("direction", "north"))
    if offset is None:
        return None
    x, y = site["position"]
    return floor(x + offset[0]), floor(y + offset[1])


def verified_offshore_pump_output_tile(site: dict) -> tuple[int, int] | None:
    """Return the pipe tile immediately landward of a legal offshore pump."""
    offset = _OFFSHORE_OUTPUT_OFFSETS.get(site.get("direction", "north"))
    if offset is None:
        return None
    x, y = site["position"]
    return floor(x + offset[0]), floor(y + offset[1])


def _row_pole_positions(
    anchor: tuple[float, float], entity: str, last_x: float,
) -> list[tuple[float, float]]:
    """Medium poles across the row at a pitch derived from the pole's own supply
    radius, so EVERY machine in the row sits inside a supply square.

    A single pole at the anchor only reaches `supply` tiles, which silently left
    the far end of a wide mining row unpowered (a three-drill coal row already
    overshoots it). The pitch is `2 * supply`, at which neighbouring supply
    squares meet, and stays inside the pole's `wire` reach so the row is still
    one connected chain. strip_local_power() drops the row's EEI and substation
    but keeps these poles, so they are what actually feeds the row in a factory.
    """
    supply = POLE_SPECS[ROW_POLE]["supply"]
    pitch = min(POLE_SPECS[ROW_POLE]["wire"], 2 * supply)
    # A `size`x`size` body centred at cx is inside the supply square while
    # |pole_x - cx| < supply + size/2.
    reach = supply + ENTITY_FOOTPRINTS.get(entity, 1) / 2
    positions = [tuple(anchor)]
    while positions[-1][0] + reach <= last_x:
        positions.append((positions[-1][0] + pitch, anchor[1]))
    return positions


def even_size_center(x: float, y: float) -> dict:
    """Snap a 2x2 entity's centre onto the tile grid the game will accept.

    An even-sized body (substation, electric-energy-interface) centres on a tile
    BOUNDARY, so its centre must be integral; odd-sized bodies centre on tile
    middles and are the reason row geometry here is built on .5 coordinates.
    Emitting a .5 centre for a 2x2 does not fail -- the game silently relocates
    it by up to 0.707 tiles on creation, and every later lookup by the planned
    position then misses the entity that was actually built. Observed live: a
    mine substation planned at (50.5, 23.5) was created at (51, 24), so the
    unpowered-machine remedy could not find its own substation and did nothing
    for six rounds.
    """
    return {"x": float(round(x)), "y": float(round(y))}


def _power_scaffold(
    anchor: tuple[float, float], entity: str = "electric-mining-drill", last_x: float | None = None,
    include_row_poles: bool = True, include_energy_interface: bool = True,
) -> list[dict]:
    x, y = anchor
    poles = _row_pole_positions(anchor, entity, last_x if last_x is not None else x)
    scaffold = ([
        {"action_type": "place_entity", "entity": "electric-energy-interface",
         "position": even_size_center(x - 8, y)},
    ] if include_energy_interface else []) + [
        {"action_type": "place_entity", "entity": "substation",
         "position": even_size_center(x - 4, y)},
    ]
    return scaffold + ([
        {"action_type": "place_ghost", "entity": ROW_POLE,
         "position": {"x": pole_x, "y": pole_y}}
        for pole_x, pole_y in poles
    ] if include_row_poles else [])


def mine_substation_positions(
    drill_columns: list[float], belt_y: float, *, below_rows: bool = False,
) -> list[tuple[float, float]]:
    """Return one connected substation per paired six-drill mine module."""
    columns = sorted(set(float(x) for x in drill_columns))
    if not columns:
        raise ValueError("Mine power grid needs at least one drill column")
    vertical_offset = 4.5 if below_rows else -5.5
    positions = []
    for index in range(0, len(columns), 3):
        group = columns[index:index + 3]
        positions.append((
            float(round(sum(group) / len(group))),
            float(round(belt_y + vertical_offset)),
        ))
    return positions


def _mine_substation_actions(
    drill_columns: list[float], belt_y: float, *, below_rows: bool = False,
) -> list[dict]:
    return [
        {"action_type": "place_entity", "entity": "substation",
         "position": even_size_center(x, y)}
        for x, y in mine_substation_positions(
            drill_columns, belt_y, below_rows=below_rows,
        )
    ]


def generate_coal_mine(
    drill_positions: list[tuple[float, float]],
    output_y: float,
    output_x: float,
    belt_type: str = "transport-belt",
) -> dict:
    if not drill_positions:
        raise ValueError("Coal mine needs supplied drill coordinates")
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if any(y + 2 != output_y for _, y in drill_positions):
        raise ValueError("Each south-facing drill must drop exactly two tiles onto output_y")
    first_x = min(x for x, _ in drill_positions)
    if output_x <= max(x for x, _ in drill_positions):
        raise ValueError("Coal output_x must lie east of every drill")
    belts = []
    x = first_x
    while x < output_x:
        belts.append({"action_type": "place_ghost", "entity": belt_type,
                      "position": {"x": x, "y": output_y}, "direction": "east"})
        x += 1
    drills = [
        {"action_type": "place_ghost", "entity": "electric-mining-drill",
         "position": {"x": x, "y": y}, "direction": "south"}
        for x, y in drill_positions
    ]
    plan = {"phases": [
        {"name": "coal_power", "actions": _power_scaffold(
            (first_x - 2, output_y - 4), "electric-mining-drill",
            max(x for x, _ in drill_positions),
        )},
        {"name": "coal_mining", "actions": drills + belts},
    ]}
    validate_build_plan(plan)
    return plan


def generate_direct_mining_to_chest(
    drill_positions: list[tuple[float, float]],
    output_chest: tuple[float, float],
    belt_type: str = "fast-transport-belt",
    inserter_type: str = "fast-inserter",
    *,
    output_side: str = "east",
    reserved_pair_columns: int = 0,
    prebuilt_pair_columns: int = 0,
    continuation_tiles: int = 0,
    include_side_tap: bool = True,
) -> dict:
    """Mine onto a continuous belt, optionally with a side-tapped steel chest.

    Drills are deliberately south-facing and share their output row. This is
    the smallest real-base raw-resource primitive: it uses no infinity source
    and no energy-interface, so the caller must connect its retained local
    poles to the actual electric grid.
    """
    if not drill_positions:
        raise ValueError("Direct mine needs supplied drill coordinates")
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if inserter_type not in {"inserter", "fast-inserter", "bulk-inserter", "stack-inserter"}:
        raise ValueError(f"Unknown inserter tier: {inserter_type}")
    if output_side not in {"east", "west"}:
        raise ValueError(f"Unknown mine output side: {output_side}")
    if reserved_pair_columns < 0 or prebuilt_pair_columns < 0:
        raise ValueError("reserved and prebuilt pair columns cannot be negative")
    if prebuilt_pair_columns > reserved_pair_columns:
        raise ValueError("prebuilt_pair_columns cannot exceed the reserved corridor")
    if continuation_tiles < 0:
        raise ValueError("continuation_tiles cannot be negative")

    drills = sorted(drill_positions)
    first_x, drill_y = drills[0]
    if any(not isclose(y, drill_y) for _, y in drills):
        raise ValueError("Direct mine drills must share one output row")
    belt_y = drill_y + 2
    chest_x, chest_y = output_chest
    if not isclose(chest_y, belt_y):
        raise ValueError("Output chest must sit on the drills' south output row")
    last_x = max(x for x, _ in drills)
    if output_side == "east":
        # The west tail matches the west design's span so a surveyed row's
        # minimum belt x is always the expansion anchor (first column - 4).
        # Starting at first_x instead made every post-build survey report the
        # first column as the anchor, skewing expansion columns and haul-head
        # math by +4 tiles (live run of 2026-08-24 08:14 aimed the stone
        # refinery feed 4 tiles past the real collector head).
        belt_start_x = first_x - 4
        belt_end_x = chest_x + continuation_tiles
        if belt_end_x < last_x:
            raise ValueError("Output tap needs a belt endpoint east of every drill")
        terminal_inserter_x, belt_direction = chest_x - 1, "east"
    else:
        belt_start_x = chest_x - 2
        # Only the affordable part of the reserved corridor is paved now.
        belt_end_x = last_x + 2 + 3 * prebuilt_pair_columns
        if belt_start_x > first_x:
            raise ValueError("Output tap needs a belt endpoint west of every drill")
        terminal_inserter_x, belt_direction = chest_x + 1, "west"
    belt_length = belt_end_x - belt_start_x
    if not isclose(belt_length, round(belt_length)):
        raise ValueError("Output chest must be tile-aligned with the drill belt")

    belts = [
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": belt_start_x + step, "y": belt_y},
         "direction": belt_direction}
        for step in range(round(belt_length) + 1)
    ]
    mine_actions = [
        {"action_type": "place_ghost", "entity": "electric-mining-drill",
         "position": {"x": x, "y": y}, "direction": "south"}
        for x, y in drills
    ] + belts
    if include_side_tap:
        mine_actions += [
            {"action_type": "place_ghost", "entity": inserter_type,
             "position": {"x": chest_x, "y": chest_y - 1},
             "direction": "south"},
            {"action_type": "place_ghost", "entity": "steel-chest",
             "position": {"x": chest_x, "y": chest_y - 2}},
        ]
    # The row poles cover the drill row. A side-tapped chest, when requested,
    # also gets a nearby pole so its loading inserter is powered; direct refinery
    # feeds omit that tap and keep the turn column entirely belt-only.
    anchor = (first_x - 2, belt_y - 4)
    row_poles = _row_pole_positions(
        anchor, "electric-mining-drill", max(x for x, _ in drills),
    )
    output_pole = None
    if include_side_tap:
        output_pole = (terminal_inserter_x, chest_y + 2)
        reach = min(
            ((output_pole[0] - pole_x) ** 2 + (output_pole[1] - pole_y) ** 2) ** 0.5
            for pole_x, pole_y in row_poles
        )
        if reach > POLE_SPECS[ROW_POLE]["wire"]:
            raise ValueError(
                f"Output-row pole at {output_pole} is {reach:.2f} tiles from the nearest "
                f"row pole, past {ROW_POLE}'s {POLE_SPECS[ROW_POLE]['wire']}-tile wire reach"
            )
    plan = {"phases": [
        {"name": "direct_mine_power", "actions": _power_scaffold(
            anchor, "electric-mining-drill", max(x for x, _ in drills),
            include_energy_interface=False,
        ) + ([
            {"action_type": "place_ghost", "entity": ROW_POLE,
             "position": {"x": output_pole[0], "y": output_pole[1]}},
        ] if output_pole is not None else [])},
        {"name": "direct_mine_output", "actions": mine_actions},
    ]}
    if output_side == "east" and continuation_tiles:
        future_first_x = first_x - 3 * reserved_pair_columns
        # Keep future drill bodies, their shared belt, and local pole clearance
        # unavailable to unrelated power/coverage work.  This is metadata, not
        # a ghost: the mine grows only when its next six-drill module is needed.
        plan["reserved_tiles"] = [
            [tile_x, tile_y]
            for tile_x in range(
                floor(future_first_x - 1.5), floor(last_x + 2.5),
            )
            for tile_y in range(
                floor(belt_y - 4.5), floor(belt_y + 4.5),
            )
        ]
        plan["collector_geometry"] = {
            "flow": "east",
            "collector_tail": (belt_start_x, belt_y),
            "collector_head": (belt_end_x, belt_y),
            "continuation_tiles": continuation_tiles,
        }
    validate_build_plan(plan)
    return plan


def generate_parallel_mining_row_expansion(
    drill_xs: list[float], source_belt_y: float, *,
    belt_type: str = "fast-transport-belt",
    parallel_belt_y: float | None = None,
    merge_x: float | None = None,
    splitter_type: str | None = None,
) -> dict:
    """Build a second paired collector row and merge it into the source lane.

    The parallel belt runs east and feeds the second splitter lane through a
    vertical underground crossover.  This keeps the two collector rows
    independent while making the merge explicit; a raw perpendicular T is
    never emitted.  ``merge_x`` is the tile immediately before the splitter
    centre and defaults to the row's east edge.
    """
    if not drill_xs:
        raise ValueError("Parallel mine expansion needs drill columns")
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if parallel_belt_y is None:
        parallel_belt_y = source_belt_y + 8
    if parallel_belt_y == source_belt_y:
        raise ValueError("Parallel collector row must have a distinct belt_y")
    columns = sorted(set(float(x) for x in drill_xs))
    if len(columns) != len(drill_xs):
        raise ValueError("Parallel mine expansion columns must be unique")
    merge_x = float(max(columns) + 4 if merge_x is None else merge_x)
    splitter_type = splitter_type or _SPLITTER_FOR_BELT[belt_type]
    if splitter_type not in {
        "splitter", "fast-splitter", "express-splitter", "turbo-splitter",
    }:
        raise ValueError(f"Unknown splitter tier: {splitter_type}")
    if merge_x <= max(columns) + 1:
        raise ValueError("Parallel merge must leave room east of the drill row")
    if not isclose(merge_x % 1, 0.5):
        raise ValueError("East-facing parallel merge must be centred on a belt tile")

    first_x = min(columns)
    # East/west splitters are centred on the belt's half-tile x and an integer
    # y between their two lanes. The previous extra half tile produced an
    # integer x, which is not the centre of an east-facing splitter.
    splitter_x = merge_x
    side_y = source_belt_y + 1
    crossover_x = merge_x - 2
    if abs(parallel_belt_y - side_y) < 1:
        raise ValueError("Parallel row is too close for a legal splitter crossover")
    crossover_span = abs(parallel_belt_y - side_y)
    if crossover_span > _UNDERGROUND_REACH[belt_type]:
        raise ValueError(
            f"{belt_type} underground reach { _UNDERGROUND_REACH[belt_type] } "
            f"cannot cross {crossover_span:g} tiles"
        )

    lower_start_x = first_x - 4
    lower_length = round(crossover_x - lower_start_x)
    lower_belts = [
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": lower_start_x + step, "y": parallel_belt_y},
         "direction": "east"}
        for step in range(lower_length)
    ]
    # Two underground endpoints cross the source belt without creating a
    # perpendicular belt overlap; the output then runs into splitter lane 2.
    if belt_type == "transport-belt":
        underground_name = "underground-belt"
    else:
        underground_name = belt_type.replace("transport-belt", "underground-belt")
    crossover = [
        {"action_type": "place_ghost", "entity": underground_name,
         "position": {"x": crossover_x, "y": parallel_belt_y},
         "direction": "north", "underground_type": "input"},
        {"action_type": "place_ghost", "entity": underground_name,
         "position": {"x": crossover_x, "y": side_y},
         "direction": "north", "underground_type": "output"},
    ]
    side_belt = {
        "action_type": "place_ghost", "entity": belt_type,
        "position": {"x": merge_x - 1, "y": side_y}, "direction": "east",
    }
    splitter = {
        "action_type": "place_ghost", "entity": splitter_type,
        "position": {"x": splitter_x, "y": source_belt_y + 0.5},
        "direction": "east", "input_priority": "right", "output_priority": "right",
    }
    replace_trunk_belt = {
        "action_type": "remove_entity", "entity": belt_type,
        "position": {"x": merge_x, "y": source_belt_y},
    }
    plan = {"atomic": True, "phases": [
        {"name": "parallel_mine_power", "actions": _mine_substation_actions(
            columns, parallel_belt_y, below_rows=True,
        )},
        {"name": "parallel_mine_row", "actions": [
            {"action_type": "place_ghost", "entity": "electric-mining-drill",
             "position": {"x": x, "y": y},
             "direction": direction}
            for x, y, direction in [
                *[(x, parallel_belt_y - 2, "south") for x in columns],
                *[(x, parallel_belt_y + 2, "north") for x in columns],
            ]
        ] + [replace_trunk_belt] + lower_belts + crossover + [side_belt, splitter]},
    ], "parallel_merge": {
        "source_belt_y": source_belt_y,
        "parallel_belt_y": parallel_belt_y,
        "splitter": (splitter_x, source_belt_y + 0.5),
    }}
    validate_build_plan(plan)
    return plan


def generate_direct_mine_row_expansion(
    drill_positions: list[tuple[float, float]],
    shared_belt_y: float,
    *, include_power: bool = True,
) -> dict:
    """Add a north-facing drill row that drops onto an existing belt."""
    if not drill_positions:
        raise ValueError("Mine expansion needs drill positions")
    drills = sorted(drill_positions)
    if any(not isclose(y - 2, shared_belt_y) for _, y in drills):
        raise ValueError("North-facing expansion drills must output onto shared_belt_y")
    first_x = min(x for x, _ in drills)
    last_x = max(x for x, _ in drills)
    anchor = (first_x - 2, drills[0][1] + 4)
    phases = ([
        {"name": "direct_mine_expansion_power", "actions": _power_scaffold(
            anchor, "electric-mining-drill", last_x,
            include_energy_interface=False,
        )},
    ] if include_power else []) + [
        {"name": "direct_mine_expansion", "actions": [
            {"action_type": "place_ghost", "entity": "electric-mining-drill",
             "position": {"x": x, "y": y}, "direction": "north"}
            for x, y in drills
        ]},
    ]
    plan = {"phases": phases}
    validate_build_plan(plan)
    return plan

def generate_shared_belt_column_expansion(
    drill_x: float,
    shared_belt_y: float,
    belt_type: str = "fast-transport-belt",
    *,
    include_belt: bool = True,
    belt_direction: str = "east",
) -> dict:
    """Add one north/south drill pair, optionally extending its shared belt."""
    if belt_type not in BELT_TIERS:
        raise ValueError(f"Unknown belt tier: {belt_type}")
    if belt_direction not in {"east", "west"}:
        raise ValueError(f"Unknown belt direction: {belt_direction}")
    belt_actions = [
        {"action_type": "place_ghost", "entity": belt_type,
         "position": {"x": drill_x + step, "y": shared_belt_y},
         "direction": belt_direction}
        for step in range(3)
    ] if include_belt else []
    plan = {"phases": [
        {"name": "shared_belt_column_power", "actions": [
            {"action_type": "place_ghost", "entity": ROW_POLE,
             "position": {"x": drill_x, "y": shared_belt_y - 4}},
            {"action_type": "place_ghost", "entity": ROW_POLE,
             "position": {"x": drill_x, "y": shared_belt_y + 4}},
        ]},
        {"name": "shared_belt_column", "actions": [
            {"action_type": "place_ghost", "entity": "electric-mining-drill",
             "position": {"x": drill_x, "y": shared_belt_y - 2}, "direction": "south"},
            {"action_type": "place_ghost", "entity": "electric-mining-drill",
             "position": {"x": drill_x, "y": shared_belt_y + 2}, "direction": "north"},
            *belt_actions,
        ]},
    ]}
    validate_build_plan(plan)
    return plan

def generate_shared_belt_batch_expansion(
    drill_xs: list[float], shared_belt_y: float, *,
    belt_direction: str = "west",
) -> dict:
    """Populate reserved drill columns and only their required belt tiles."""
    if not drill_xs:
        raise ValueError("Batch mine expansion needs at least one drill column")
    if belt_direction not in {"east", "west"}:
        raise ValueError(f"Unknown belt direction: {belt_direction}")
    columns = sorted(set(drill_xs))
    # Reserved corridors only ever grow EAST of the row, so each column's
    # three belt tiles extend eastward from its centre; belt_direction is
    # purely the flow the ore travels toward the haul head.
    plan = {"phases": [
        {"name": "shared_belt_batch_power", "actions": _mine_substation_actions(
            columns, shared_belt_y,
        )},
        {"name": "shared_belt_batch", "actions": [
            {"action_type": "place_ghost", "entity": "electric-mining-drill",
             "position": {"x": x, "y": shared_belt_y + dy},
             "direction": direction}
            for x in columns for dy, direction in ((-2, "south"), (2, "north"))
        ] + [
            {"action_type": "place_ghost", "entity": "fast-transport-belt",
             "position": {"x": x + offset, "y": shared_belt_y},
             "direction": belt_direction}
            for x in columns for offset in range(3)
        ]},
    ]}
    validate_build_plan(plan)
    return plan

def _fluid_resource_plan(
    kind: str,
    entity: str,
    sites: list[dict],
    pipe_tiles: list[tuple[float, float]],
) -> dict:
    if not sites or not pipe_tiles:
        raise ValueError(f"{kind} source needs supplied entity and pipe coordinates")
    outputs = {tuple(site["output"]) for site in sites}
    if not outputs <= set(map(tuple, pipe_tiles)):
        raise ValueError(f"{kind} pipe tiles must include every supplied output coordinate")
    if entity == "pumpjack":
        for site in sites:
            verified_output = verified_pumpjack_output_tile(site)
            if verified_output is None or tuple(site["output"]) != verified_output:
                raise ValueError(
                    "Pumpjack output must match its rotated connector tile "
                    f"{verified_output}, got {tuple(site['output'])}"
                )
    elif entity == "offshore-pump":
        for site in sites:
            verified_output = verified_offshore_pump_output_tile(site)
            if verified_output is None or tuple(site["output"]) != verified_output:
                raise ValueError(
                    "Offshore-pump output must be the adjacent land-side tile "
                    f"{verified_output}, got {tuple(site['output'])}"
                )
    entities = [
        {"action_type": "place_ghost", "entity": entity,
         "position": {"x": site["position"][0], "y": site["position"][1]},
         "direction": site.get("direction", "north")}
        for site in sites
    ]
    pipes = [
        {"action_type": "place_ghost", "entity": "pipe",
         "position": {"x": x + 0.5, "y": y + 0.5}}
        for x, y in pipe_tiles
    ]
    anchor = tuple(sites[0]["position"])
    if entity == "offshore-pump":
        # Base-game offshore pumps use void energy. A local substation is not
        # only wasteful; it can box in the one legal land-side pipe exit.
        power_actions = []
    else:
        power_anchor = (anchor[0] - 3, anchor[1] + 3)
        power_actions = _power_scaffold(
            power_anchor, entity,
            max(site["position"][0] for site in sites),
            include_row_poles=entity not in {"pumpjack"},
        )
    plan = {"phases": [
        {"name": f"{kind}_power", "actions": power_actions},
        {"name": f"{kind}_source", "actions": entities + pipes},
    ]}
    validate_build_plan(plan)
    return plan


def generate_pumpjack_source(sites: list[dict], pipe_tiles: list[tuple[float, float]]) -> dict:
    return _fluid_resource_plan("crude_oil", "pumpjack", sites, pipe_tiles)


def generate_offshore_pump_source(sites: list[dict], pipe_tiles: list[tuple[float, float]]) -> dict:
    return _fluid_resource_plan("water", "offshore-pump", sites, pipe_tiles)


def resource_fluid_segment(fluid: str, pipe_tiles: list[tuple[float, float]]) -> dict:
    return {"fluid": fluid, "separated_by_pump": False, "tiles": list(pipe_tiles)}
