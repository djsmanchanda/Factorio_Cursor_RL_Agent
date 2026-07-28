# Path: planners/line_layouts.py
# Purpose: Deterministic local production-line and mining-feed geometry.

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator

from planners.recipe_data import (
    BELT_TIERS,
    CHAINED_BELT_WEST,
    CHAINED_SUBSTATION_X,
    CHAIN_SOUTH_APPROACH_COL,
    FEEDER_RATES,
    FEED_HEADROOM,
    FEED_STYLES,
    INSERTER_TIERS,
    LINE_RECIPES,
    MACHINE_SPEEDS,
    MACHINE_WIDTH,
    SIDELOAD_NORTH_COL,
    SIDELOAD_SOUTH_COL,
    _reject_fuel_entities,
)


class LineLayoutMixin:
    def generate_line_layout(
        self,
        recipe: str,
        machine_count: int,
        origin_x: int = 0,
        origin_y: int = 0,
        mining_feed: bool = False,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        feed_style: str = "chest",
        chained_ingredients: "set | list | None" = None,
        terminal_collector: bool = True,
        flow_direction: str = "east",
    ) -> dict:
        """Deterministic single-recipe production line.

        Row layout (y offsets from origin, y grows south):
          0: input belt flowing east          4: (machine bottom row)
          1: input inserters + power poles    5: output inserters
          2: machine top row                  6: output belt flowing east
        Feed (infinity chest) at the west end, collection chest at the east end.
        Inserters face their pickup side; drop is the opposite tile.

        feed_style="chest" (default): each ingredient's infinity chest + inserter
        pairs sit directly on the input belt at the west extension - ingredient 0
        on the north side (inserter faces north, drops onto the far lane),
        ingredient 1 on the south side.

        feed_style="sideload": ingredients ride dedicated feeder BELT columns
        that T-junction into the input belt, which sustains higher throughput
        than chest+inserter feeding because the feeder belt buffers. Geometry
        (columns are tile indices, all WEST of x=0; N0/N1 = feeders_needed per
        ingredient, sized by demand exactly like the chest feeders):

          col:  -5    -4    -3      -2      -1  0  1 .. machines ->
                                    [belt  ]  input belt (row 0) flows east >>
          north (rows y<0, ingredient 0):
                            ins-> [Nbelt v]              feeder belt runs SOUTH,
                     chest  ins-> [Nbelt v]              its last tile (row -1)
                            ...   [Nbelt v]              sideloads input row 0.
          south (rows y>0, ingredient 1):
              chest  ins->        [Sbelt ^]              feeder belt runs NORTH,
              chest  ins->        [Sbelt ^]              its last tile (row 1)
              ...                 [Sbelt ^]              sideloads input row 0.

        North feeder belt occupies column -2, its loading inserters column -3
        (facing west: pick from the chest, drop east onto the belt) and chests
        column -4. South feeder belt occupies column -3, loaders column -4,
        chests column -5. The input belt extends west to include both junction
        tiles (-2 and, for two ingredients, -3). Power scaffolding is pushed
        further west of every feeder tile so nothing overlaps.

        feed_style="chained": ingredients listed in chained_ingredients (by
        index; default = all of them) arrive over a chain link from an upstream
        line and get NO local feeders at all. Any ingredient NOT in that set
        keeps its chest feeders. The input belt still extends west to
        CHAINED_BELT_WEST so generate_chain_link has junction tiles to land on,
        and power is pushed west of the south connector's approach column.
        """
        if recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for: {recipe}")
        if machine_count <= 0:
            raise ValueError("machine_count must be positive")
        if flow_direction not in {"east", "west"}:
            raise ValueError("Line flow direction must be east or west")

        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")
        if feed_style not in FEED_STYLES:
            raise ValueError(f"Unknown feed style: {feed_style}")
        if feed_style == "sideload" and not mining_feed:
            self._check_sideload_lane_capacity(recipe, machine_count, belt_type)

        spec = LINE_RECIPES[recipe]
        machine = spec["machine"]
        ingredients = spec["ingredients"]
        auxiliary_index = spec.get("auxiliary_ingredient_index")
        three_input = len(ingredients) == 3 and auxiliary_index == 1
        if not (1 <= len(ingredients) <= 2 or three_input):
            raise ValueError("Line layouts need at most two main-belt ingredients and one declared auxiliary")
        if three_input and feed_style == "sideload":
            raise ValueError("Three-input rows require declared chained or chest endpoints")

        # A chain delivers one lane, so chained ingredients face the same lane
        # ceiling as sideload feeding.
        if feed_style == "chained" and not mining_feed:
            chained = set(range(len(ingredients))) if chained_ingredients is None else set(chained_ingredients)
            if not chained:
                raise ValueError("feed_style='chained' requires at least one chained ingredient index")
            if any(index not in range(len(ingredients)) for index in chained):
                raise ValueError(f"chained_ingredients out of range for {recipe}: {sorted(chained)}")
            if len(chained) > (3 if three_input else 2):
                raise ValueError("Too many chained ingredients for the declared belt interfaces")
            self._check_sideload_lane_capacity(recipe, machine_count, belt_type, only_indices=chained)
        else:
            chained = set()
        length = machine_count * MACHINE_WIDTH
        ox, oy = origin_x, origin_y

        def at(x: float, y: float) -> dict:
            return {"x": ox + x, "y": oy + y}

        # Per-ingredient demand (items/s) sets how many feed points each
        # ingredient needs; the input belt extends west to host them.
        crafts_per_second = machine_count * MACHINE_SPEEDS[machine] / spec["craft_time"]
        feeder_rate = FEEDER_RATES[inserter_type]
        feeders_needed = self._feeders_needed(recipe, machine_count, inserter_type)
        feed_slots = max(feeders_needed) if not mining_feed else 0
        belt_west = self._belt_west(recipe, machine_count, feed_style, inserter_type, mining_feed)

        ghosts: List[dict] = []
        # Belts: input lane must cover every feeder inserter's drop tile.
        for x in range(belt_west, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(x + 0.5, 0.5), "direction": "east"})
        for x in range(0, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(x + 0.5, 6.5), "direction": "east"})
        if three_input:
            for x in range(belt_west, length):
                ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                               "position": at(x + 0.5, 7.5), "direction": "east"})

        set_recipe = spec.get("set_recipe", True)
        for i in range(machine_count):
            base = i * MACHINE_WIDTH
            center = base + 1.5
            machine_action = {"action_type": "place_ghost", "entity": machine, "position": at(center, 3.5)}
            if set_recipe:
                machine_action["recipe"] = recipe
            ghosts.append(machine_action)
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(center, 1.5), "direction": "north"})
            if three_input:
                ghosts.append({"action_type": "place_ghost", "entity": "long-handed-inserter",
                               "position": at(center, 5.5), "direction": "south"})
                ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                               "position": at(center + 1, 5.5), "direction": "north"})
            else:
                ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                               "position": at(center, 5.5), "direction": "north"})

        # Two pole rows: medium-pole supply is 7x7, so one row cannot reach
        # both the input inserters (y=1) and the output row (y=5).
        for x in range(0, length + 1, 6):
            ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                           "position": at(x + 0.5, 1.5)})
            ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                           "position": at(x + 0.5, 5.5)})

        # Feed and collection endpoints plus dedicated power are placed as real
        # entities: they are line scaffolding, not part of the planned build.
        # Feeders sit on opposite sides of the input belt so each ingredient
        # lands on its own lane (an inserter drops onto the far lane).
        if feed_style == "sideload" and not mining_feed:
            # Push power west of every feeder tile so its 2x2 footprint cannot
            # collide with a feeder belt, inserter or chest.
            westmost = (SIDELOAD_SOUTH_COL if len(ingredients) == 2 else SIDELOAD_NORTH_COL) - 2
            interface_pos = at(westmost - 6.0, 3.5)
            substation_pos = at(westmost - 3.0, 2.0)
        elif chained:
            # Clear of the south connector's approach column and its row +1 run,
            # but still within MEDIUM_POLE_WIRE_REACH of the first line pole
            # (x=0.5) - a substation 9.5 tiles away silently fails to connect,
            # which reads as no_power on every machine (found live).
            westmost = CHAIN_SOUTH_APPROACH_COL - 2
            interface_pos = at(westmost - 6.0, 3.5)
            substation_pos = at(CHAINED_SUBSTATION_X, 2.0)
        else:
            interface_pos = at(-7.5, 3.5)
            substation_pos = at(-4.0, 2.0)
        scaffolding: List[dict] = [
            {"action_type": "place_entity", "entity": "electric-energy-interface", "position": interface_pos},
            {"action_type": "place_entity", "entity": "substation", "position": substation_pos},
        ]
        if terminal_collector:
            scaffolding.extend([
                {"action_type": "place_entity", "entity": "steel-chest",
                 "position": at(length + 1.5, 6.5)},
                {"action_type": "place_entity", "entity": inserter_type,
                 "position": at(length + 0.5, 6.5), "direction": "west"},
            ])
        # Collectors scale with output demand just like feeders; extras drain
        # from the south side of the output belt into their own chests.
        extra_collectors = max(0, -(-int(crafts_per_second * spec["product_amount"] * FEED_HEADROOM * 100) // int(feeder_rate * 100)) - 1)
        if three_input and extra_collectors:
            raise ValueError("Advanced-circuit auxiliary corridor reserves the south collector rows")
        for i in range(extra_collectors if terminal_collector else 0):
            x = length - 1.5 - 2 * i
            scaffolding.extend([
                {"action_type": "place_entity", "entity": inserter_type,
                 "position": at(x, 7.5), "direction": "north"},
                {"action_type": "place_entity", "entity": "steel-chest", "position": at(x, 8.5)},
            ])
        if not mining_feed and feed_style in {"chest", "chained"}:
            # Ingredient 0 feeds from the north side, ingredient 1 from the
            # south; each west-extension tile hosts one feed point per side.
            # Chained ingredients are supplied by an upstream line instead, so
            # they emit no feeders at all.
            if 0 not in chained:
                for slot in range(feeders_needed[0]):
                    x = -1.5 - slot
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(x, -1.5), "infinity_filter": ingredients[0]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(x, -0.5), "direction": "north"},
                    ])
            south_index = 2 if three_input else 1
            if len(ingredients) >= 2 and south_index not in chained:
                for slot in range(feeders_needed[south_index]):
                    x = -1.5 - slot
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(x, 2.5), "infinity_filter": ingredients[south_index]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(x, 1.5), "direction": "south"},
                    ])
            if three_input and auxiliary_index not in chained:
                for slot in range(feeders_needed[auxiliary_index]):
                    x = -1.5 - slot
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(x, 9.5), "infinity_filter": ingredients[auxiliary_index]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(x, 8.5), "direction": "south"},
                    ])
        elif not mining_feed and feed_style == "sideload":
            # North feeder belt (ingredient 0): a column of belt ghosts running
            # SOUTH, its last tile (row -1) sideloading the input belt. Loaded
            # from the west by infinity-chest + inserter pairs (one per demand
            # feed point) so the buffered belt saturates the input.
            north_needed = feeders_needed[0]
            for row in range(-(north_needed + 1), 0):  # rows -(N+1)..-1
                ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                               "position": at(SIDELOAD_NORTH_COL + 0.5, row + 0.5),
                               "direction": "south"})
            for slot in range(north_needed):
                row = -(2 + slot)  # loading tiles sit above the junction (row -1)
                scaffolding.extend([
                    {"action_type": "place_entity", "entity": "infinity-chest",
                     "position": at(SIDELOAD_NORTH_COL - 1.5, row + 0.5),
                     "infinity_filter": ingredients[0]},
                    {"action_type": "place_entity", "entity": inserter_type,
                     "position": at(SIDELOAD_NORTH_COL - 0.5, row + 0.5), "direction": "west"},
                ])
            if len(ingredients) == 2:
                # South feeder belt (ingredient 1): column running NORTH, its
                # last tile (row 1) sideloading the input belt from the south.
                south_needed = feeders_needed[1]
                for row in range(1, south_needed + 2):  # rows 1..S+1
                    ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                                   "position": at(SIDELOAD_SOUTH_COL + 0.5, row + 0.5),
                                   "direction": "north"})
                for slot in range(south_needed):
                    row = 2 + slot  # loading tiles sit below the junction (row 1)
                    scaffolding.extend([
                        {"action_type": "place_entity", "entity": "infinity-chest",
                         "position": at(SIDELOAD_SOUTH_COL - 1.5, row + 0.5),
                         "infinity_filter": ingredients[1]},
                        {"action_type": "place_entity", "entity": inserter_type,
                         "position": at(SIDELOAD_SOUTH_COL - 0.5, row + 0.5), "direction": "west"},
                    ])

        phases = [
            {"name": "line_scaffolding", "actions": scaffolding},
            {"name": f"line_{recipe}", "actions": ghosts},
        ]
        if mining_feed:
            phases.append(self.generate_mining_feed(machine_count, origin_x, origin_y)["phases"][0])
        if flow_direction == "west":
            mirror_axis_twice = 2 * ox + length
            for phase in phases:
                for action in phase["actions"]:
                    action["position"]["x"] = (
                        mirror_axis_twice - action["position"]["x"]
                    )
                    if action.get("direction") == "east":
                        action["direction"] = "west"
                    elif action.get("direction") == "west":
                        action["direction"] = "east"
        plan = {"phases": phases}

        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)
        return plan

    def generate_mining_feed(
        self,
        machine_count: int,
        origin_x: int = 0,
        origin_y: int = 0,
    ) -> dict:
        """Miners north of the line's input belt, outputting directly onto it.

        Electric drills are 3x3; a drill at rows -3..-1 facing south drops its
        ore onto the y=0 input belt of the line at the same origin. Combined
        with a smelting line this is: ORE -> BELT -> FURNACE -> PLATE BELT,
        with no scripted item source. Requires an ore patch under the drills
        (seeded by sandbox scaffolding on the test surface).
        """
        if machine_count <= 0:
            raise ValueError("machine_count must be positive")
        ox, oy = origin_x, origin_y
        actions: List[dict] = []
        for i in range(machine_count):
            center = i * MACHINE_WIDTH + 1.5
            # Drill drop tile is 2 tiles south of center (footprint edge + 1),
            # so a center at oy-1.5 lands the ore exactly on the y=0 belt row.
            actions.append({
                "action_type": "place_ghost",
                "entity": "electric-mining-drill",
                "position": {"x": ox + center, "y": oy - 1.5},
                "direction": "south",
            })
        plan = {"phases": [{"name": "mining_feed", "actions": actions}]}

        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)
        return plan

    def _feeders_needed(self, recipe: str, machine_count: int, inserter_type: str) -> List[int]:
        """Per-ingredient feed-point count = ceil(demand * headroom / rate).

        Feed capacity is provisioned with FEED_HEADROOM (user standard: 20-25%
        buffer) so lines never run at the ragged edge of supply. Shared by
        generate_line_layout and generate_chain_link so both agree on how far
        west a line's input belt extends. Integer scaling by 100 keeps the
        arithmetic deterministic (no float division rounding drift).
        """
        spec = LINE_RECIPES[recipe]
        crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        feeder_rate = FEEDER_RATES[inserter_type]
        return [
            max(1, -(-int(amount * crafts_per_second * FEED_HEADROOM * 100) // int(feeder_rate * 100)))
            for amount in spec["amounts"]
        ]

    def _check_sideload_lane_capacity(self, recipe: str, machine_count: int, belt_type: str,
                                      only_indices: "set | None" = None) -> None:
        """Sideload gives each ingredient ONE lane; demand must fit it.

        Hard failure below raw demand (physically cannot keep up â€” measured
        live: express lane 22.5/s vs 27/s cable demand ran at 8.27/s of a
        9.6/s cap). The error names the cheapest tier meeting demand with
        FEED_HEADROOM so callers can upgrade or switch feed style.

        only_indices restricts the check to specific ingredient indices, used
        by chained lines where just some ingredients arrive one-lane.
        """
        spec = LINE_RECIPES[recipe]
        crafts_per_second = machine_count * MACHINE_SPEEDS[spec["machine"]] / spec["craft_time"]
        lane_rate = BELT_TIERS[belt_type] / 2
        for index, (ingredient, amount) in enumerate(zip(spec["ingredients"], spec["amounts"])):
            if only_indices is not None and index not in only_indices:
                continue
            demand = amount * crafts_per_second
            if lane_rate < demand:
                target = demand * FEED_HEADROOM
                adequate = sorted(
                    (name for name, rate in BELT_TIERS.items() if rate / 2 >= target),
                    key=lambda name: BELT_TIERS[name],
                )
                suggestion = adequate[0] if adequate else "no tier suffices; use chest feeding or a dedicated both-lane belt"
                raise ValueError(
                    f"Sideload lane rate {lane_rate}/s < {ingredient} demand {demand}/s "
                    f"for {machine_count} machines; smallest tier with {FEED_HEADROOM}x headroom: {suggestion}"
                )

    def _belt_west(self, recipe: str, machine_count: int, feed_style: str,
                   inserter_type: str, mining_feed: bool) -> int:
        """Westmost input-belt tile column for a line.

        Chest feeders push the belt one tile west per feed slot; sideload feeders
        only need the belt to reach their junction columns (-2, and -3 when a
        second ingredient feeds from the south).
        """
        if feed_style == "chained" and not mining_feed:
            return CHAINED_BELT_WEST
        if feed_style == "sideload" and not mining_feed:
            ingredients = LINE_RECIPES[recipe]["ingredients"]
            return SIDELOAD_SOUTH_COL if len(ingredients) == 2 else SIDELOAD_NORTH_COL
        feeders = self._feeders_needed(recipe, machine_count, inserter_type)
        feed_slots = max(feeders) if not mining_feed else 0
        return -(1 + max(1, feed_slots))

    def _validate_and_reject_fuel(self, plan: dict) -> None:
        """Schema-validate a BuildPlan then enforce the electric-only invariant."""
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schemas" / "build_plan.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        errors = list(Draft7Validator(schema).iter_errors(plan))
        if errors:
            messages = [f"- {self._format_error_path(e)}: {e.message}" for e in errors]
            raise ValueError("BuildPlan validation FAILED:\n" + "\n".join(messages))
        _reject_fuel_entities(plan)

