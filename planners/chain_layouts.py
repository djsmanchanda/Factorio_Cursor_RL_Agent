# Path: planners/chain_layouts.py
# Purpose: Deterministic line chaining, extension, lab-row, and material geometry.

from __future__ import annotations

from collections import Counter
import json
from typing import Dict, List

from planners.recipe_data import (
    BELT_TIERS,
    CHAINED_BELT_WEST,
    CHAINED_SUBSTATION_X,
    CHAIN_JUNCTION_SIDES,
    CHAIN_NORTH_JUNCTION_COL,
    CHAIN_SOUTH_APPROACH_COL,
    CHAIN_SOUTH_JUNCTION_COL,
    FEED_STYLES,
    INSERTER_TIERS,
    LINE_PITCH_Y,
    LINE_RECIPES,
    MACHINE_WIDTH,
)


class ChainLayoutMixin:
    def generate_chain_link(
        self,
        producer_origin: tuple,
        producer_recipe: str,
        producer_machines: int,
        consumer_origin: tuple,
        consumer_recipe: str,
        consumer_machines: int,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        consumer_feed_style: str = "chest",
        junction_side: str = "head_on",
    ) -> dict:
        """Route a producer line's OUTPUT belt into a consumer line's INPUT belt.

        A producer built by generate_line_layout dumps its output (row 6, flowing
        east) into a terminal collector (inserter at column length, chest at
        column length+1). This method emits one BuildPlan phase that reclaims the
        collector's tiles and lays a belt connector:

          1. remove the terminal collector inserter + chest (freeing two tiles);
          2. extend the output belt one tile east onto the freed inserter tile;
          3. corner SOUTH in a dedicated "turn column" (producer x + length + 1,
             the freed chest tile) and run straight down;
          4. corner EAST into the consumer's input-belt west extension.

        Geometry (absolute tiles; py/px = producer origin, cy/cx = consumer):
          out_row  = py + 6, turn_col = px + producer_len + 1
          row out_row:      .. [belt >][corner v]              (past the collector)
          rows out_row+1..cy-1:          [belt v]              (dedicated column)
          row cy (consumer input): [corner >]--> consumer input belt west end

        Deterministic x-offset RULE for the consumer: the connector corners east
        exactly one tile WEST of the consumer's input-belt west extension and
        feeds into it, so the consumer origin x is pinned by the producer:

            consumer_x == turn_col + 1 - consumer_belt_west

        where consumer_belt_west comes from the same demand math the consumer
        line uses. The method raises if consumer_origin's x does not match, and
        the error states the required value. Constraint: the consumer must sit at
        cy >= py + LINE_PITCH_Y so the vertical run has room and never overlaps
        the producer's own rows.

        junction_side selects how the connector enters the consumer, because
        output inserters drop onto ONE lane - so two producers feeding one
        consumer must enter from opposite edges to keep their items separated:

          "head_on" (default): corners east into the input belt's west end
              (pinned consumer_x = turn_col + 1 - consumer_belt_west).
          "north": the connector descends in the consumer's own
              CHAIN_NORTH_JUNCTION_COL and its last tile (row -1) faces south,
              sideloading the north lane (pinned consumer_x = turn_col + 1).
          "south": the connector descends in CHAIN_SOUTH_APPROACH_COL (west of
              the input belt so it passes row 0 safely), corners east along row
              +1, and its last tile at CHAIN_SOUTH_JUNCTION_COL faces north,
              sideloading the south lane (pinned consumer_x = turn_col + 4).
        """
        if producer_recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for producer: {producer_recipe}")
        if consumer_recipe not in LINE_RECIPES:
            raise ValueError(f"No line recipe knowledge for consumer: {consumer_recipe}")
        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")
        if consumer_feed_style not in FEED_STYLES:
            raise ValueError(f"Unknown feed style: {consumer_feed_style}")
        if junction_side not in CHAIN_JUNCTION_SIDES:
            raise ValueError(f"Unknown junction side: {junction_side}")
        if producer_machines <= 0 or consumer_machines <= 0:
            raise ValueError("machine counts must be positive")

        px, py = producer_origin
        cx, cy = consumer_origin
        if cy < py + LINE_PITCH_Y:
            raise ValueError(
                f"consumer must sit at y >= producer_y + {LINE_PITCH_Y} (got cy={cy}, py={py})"
            )

        producer_len = producer_machines * MACHINE_WIDTH
        out_row = py + 6
        in_row = cy  # consumer input belt is at offset 0 from its origin

        consumer_belt_west = self._belt_west(
            consumer_recipe, consumer_machines, consumer_feed_style, inserter_type, False
        )
        turn_col = px + producer_len + 1
        if junction_side == "head_on":
            required_cx = turn_col + 1 - consumer_belt_west
        elif junction_side == "north":
            required_cx = turn_col - CHAIN_NORTH_JUNCTION_COL
        else:  # south: the connector descends in the approach column
            required_cx = turn_col - CHAIN_SOUTH_APPROACH_COL
        if cx != required_cx:
            raise ValueError(
                f"consumer_origin x must be {required_cx} for junction_side='{junction_side}' "
                f"so the connector lands on the consumer input belt (got {cx}); "
                f"turn_col={turn_col}, consumer_belt_west={consumer_belt_west}"
            )

        actions: List[dict] = []
        # 1) Reclaim the producer's terminal collector so the belt can continue.
        actions.append({"action_type": "remove_entity", "entity": inserter_type,
                        "position": {"x": px + producer_len + 0.5, "y": out_row + 0.5}})
        actions.append({"action_type": "remove_entity", "entity": "steel-chest",
                        "position": {"x": px + producer_len + 1.5, "y": out_row + 0.5}})
        # 2) Extend the output belt east onto the freed inserter tile.
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": px + producer_len + 0.5, "y": out_row + 0.5},
                        "direction": "east"})
        # 3) Corner south into the dedicated turn column, then run straight down.
        #    "north" stops one row above the input belt and pushes into it;
        #    the others descend to (head_on) or past (south) the input row.
        # "south" descends THROUGH the input row to corner east below it; the
        # others stop one row short so the junction/corner tile is free.
        last_descent_row = in_row if junction_side == "south" else in_row - 1
        actions.append({"action_type": "place_ghost", "entity": belt_type,
                        "position": {"x": turn_col + 0.5, "y": out_row + 0.5},
                        "direction": "south"})
        for y in range(out_row + 1, last_descent_row + 1):
            actions.append({"action_type": "place_ghost", "entity": belt_type,
                            "position": {"x": turn_col + 0.5, "y": y + 0.5},
                            "direction": "south"})

        # 4) Enter the consumer.
        if junction_side == "north":
            # The last descending tile (row in_row-1, facing south) already
            # sideloads the input belt tile below it - nothing more to place.
            pass
        elif junction_side == "head_on":
            actions.append({"action_type": "place_ghost", "entity": belt_type,
                            "position": {"x": turn_col + 0.5, "y": in_row + 0.5},
                            "direction": "east"})
        else:  # south: corner east below the line, then push north into it
            junction_col = cx + CHAIN_SOUTH_JUNCTION_COL
            for x in range(turn_col, junction_col):
                actions.append({"action_type": "place_ghost", "entity": belt_type,
                                "position": {"x": x + 0.5, "y": in_row + 1.5},
                                "direction": "east"})
            actions.append({"action_type": "place_ghost", "entity": belt_type,
                            "position": {"x": junction_col + 0.5, "y": in_row + 1.5},
                            "direction": "north"})

        plan = {"phases": [{
            "name": f"chain_{producer_recipe}_to_{consumer_recipe}",
            "actions": actions,
        }]}
        self._validate_and_reject_fuel(plan)
        return plan

    def _terminal_collector_tiles(self, machine_count: int, origin_x: int, origin_y: int) -> list:
        """Absolute positions of a line's terminal collector inserter + chest."""
        length = machine_count * MACHINE_WIDTH
        return [
            {"x": origin_x + length + 0.5, "y": origin_y + 6.5},
            {"x": origin_x + length + 1.5, "y": origin_y + 6.5},
        ]

    @staticmethod
    def _action_key(action: dict) -> str:
        """Canonical identity of a placement (entity + tile + every field)."""
        return json.dumps(action, sort_keys=True)

    def generate_line_extension(
        self,
        recipe: str,
        current_machines: int,
        new_machines: int,
        origin_x: int = 0,
        origin_y: int = 0,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        feed_style: str = "chest",
        chained_ingredients: "set | list | None" = None,
        mining_feed: bool = False,
        has_terminal_collector: bool = True,
    ) -> dict:
        """Grow an existing line from current_machines to new_machines IN PLACE.

        This is the planner half of the "extend_line_x" catalog action: it emits
        only the DELTA, so an agent never rebuilds (or duplicates) what already
        stands. The delta is computed as the exact set difference between the
        full layout of the larger line and the full layout of the smaller one,
        both produced by generate_line_layout with identical parameters, so the
        world after applying this plan is byte-identical to a line built at
        new_machines from scratch.

        Delta rules (all follow from that difference; listed because callers
        cost them individually):

        * machines — new_machines - current_machines machine ghosts at their
          usual centers (3i+1.5, 3.5), each with its input inserter (row 1) and
          output inserter (row 5).
        * belts — input row 0 and output row 6 gain the tiles east of the old
          east end. If the extra demand also needs more feed slots, the input
          row additionally gains the tiles WEST of the old west end (chest
          feeding pushes _belt_west further west per feed slot). Existing belt
          tiles are never re-emitted.
        * poles — the x=6j pattern simply continues: poles are emitted only for
          the multiples of 6 that the old length did not already cover (rows 1
          and 5). No duplicates.
        * mining drills — with mining_feed, one more drill per added machine,
          continuing the same 3-tile pitch.
        * feed points — _feeders_needed is evaluated at BOTH counts; only the
          difference is emitted (infinity chest + loading inserter per new
          slot, chest or sideload geometry per feed_style). Because feed
          capacity is provisioned with FEED_HEADROOM, a modest growth step
          usually crosses no feeder boundary and adds nothing here.
        * drain collectors — same rule by count; the extra drain collectors are
          anchored to the east end, so the ones that must shift are emitted as
          a remove/place pair (the resulting count matches the full layout).
        * terminal collector — MOVES: remove_entity for the inserter at
          (length_old, row 6) and the steel chest at (length_old+1, row 6),
          place_entity for both at the new east end. Pass
          has_terminal_collector=False when the line feeds a chain link instead
          of a terminal collector: the pair is then neither removed nor placed.

        Phase order is reclaim -> scaffolding -> ghosts. Reclaim must run first:
        the new output-belt tiles land exactly on the old collector's tiles.

        Caller responsibility (NOT emitted here): when has_terminal_collector is
        False, the downstream chain link laid by generate_chain_link starts at
        the OLD east end and is invalidated by the growth — regenerate it for
        the new machine count after applying this plan.

        Raises ValueError if new_machines <= current_machines.
        """
        if current_machines <= 0:
            raise ValueError("current_machines must be positive")
        if new_machines <= current_machines:
            raise ValueError(
                f"extension must grow the line: new_machines={new_machines} "
                f"must exceed current_machines={current_machines}"
            )

        def full(count: int) -> List[dict]:
            plan = self.generate_line_layout(
                recipe, count, origin_x, origin_y, mining_feed=mining_feed,
                belt_type=belt_type, inserter_type=inserter_type,
                feed_style=feed_style, chained_ingredients=chained_ingredients,
            )
            actions = [a for phase in plan["phases"] for a in phase["actions"]]
            if has_terminal_collector:
                return actions
            terminal = self._terminal_collector_tiles(count, origin_x, origin_y)
            return [
                a for a in actions
                if not (a["action_type"] == "place_entity"
                        and a["entity"] in {inserter_type, "steel-chest"}
                        and a.get("position") in terminal)
            ]

        old_actions = full(current_machines)
        new_actions = full(new_machines)
        old_keys = {self._action_key(a) for a in old_actions}
        new_keys = {self._action_key(a) for a in new_actions}

        additions = [a for a in new_actions if self._action_key(a) not in old_keys]
        reclaim = [
            {"action_type": "remove_entity", "entity": a["entity"], "position": a["position"]}
            for a in old_actions if self._action_key(a) not in new_keys
        ]

        phases = [
            {"name": "extension_reclaim", "actions": reclaim},
            {"name": "extension_scaffolding",
             "actions": [a for a in additions if a["action_type"] == "place_entity"]},
            {"name": f"extend_line_{recipe}",
             "actions": [a for a in additions if a["action_type"] == "place_ghost"]},
        ]
        plan = {"phases": [phase for phase in phases if phase["actions"]]}
        self._validate_and_reject_fuel(plan)
        return plan

    def line_extension_cost(
        self,
        recipe: str,
        current_machines: int,
        new_machines: int,
        origin_x: int = 0,
        origin_y: int = 0,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
        feed_style: str = "chest",
        chained_ingredients: "set | list | None" = None,
        mining_feed: bool = False,
        has_terminal_collector: bool = True,
    ) -> dict:
        """Cost of the extend_line_x action, read off the generated delta plan.

        Returns:
          materials — items the extension consumes, keyed by entity name:
            ghost requirements (material_requirements) plus directly placed
            scaffolding, MINUS reclaimed entities (a moved collector returns its
            inserter and chest to inventory, so it nets to zero). Entities whose
            net is <= 0 are omitted.
          moves_collector — True when the terminal collector pair is relocated.
          adds_feeders — number of new feed points (one infinity chest each);
            0 when the extra demand still fits the existing feeders.
        """
        plan = self.generate_line_extension(
            recipe, current_machines, new_machines, origin_x, origin_y,
            belt_type, inserter_type, feed_style, chained_ingredients,
            mining_feed, has_terminal_collector,
        )
        materials = Counter(self.material_requirements(plan))
        for phase in plan["phases"]:
            for action in phase["actions"]:
                if action["action_type"] == "place_entity":
                    materials[action["entity"]] += 1
                elif action["action_type"] == "remove_entity":
                    materials[action["entity"]] -= 1

        terminal = self._terminal_collector_tiles(current_machines, origin_x, origin_y)
        moves_collector = any(
            action["action_type"] == "remove_entity" and action["position"] in terminal
            for phase in plan["phases"] for action in phase["actions"]
        )
        adds_feeders = sum(
            1
            for phase in plan["phases"] for action in phase["actions"]
            if action["action_type"] == "place_entity" and action["entity"] == "infinity-chest"
        )
        return {
            "materials": {name: count for name, count in sorted(materials.items()) if count > 0},
            "moves_collector": moves_collector,
            "adds_feeders": adds_feeders,
        }

    def generate_lab_row(
        self,
        lab_count: int,
        origin_x: int = 0,
        origin_y: int = 0,
        belt_type: str = "transport-belt",
        inserter_type: str = "fast-inserter",
    ) -> dict:
        """Row of labs fed science packs by a belt - the end of every chain.

        Rows (y offsets from origin, y grows south):
          0: input belt flowing east (extends west to CHAINED_BELT_WEST so a
             chain link can land on it)
          1: inserters facing north (pick from the belt, drop south into the
             lab) + medium poles at x=6j
          2-4: labs, 3x3 at 3-tile pitch, centered (3i+1.5, 3.5)

        Labs consume and produce nothing on a belt, so there is no output side
        and no collectors. Lab consumption is slow (one science pack per
        research unit over seconds), so one inserter per lab is ample.
        """
        if lab_count <= 0:
            raise ValueError("lab_count must be positive")
        if belt_type not in BELT_TIERS:
            raise ValueError(f"Unknown belt tier: {belt_type}")
        if inserter_type not in INSERTER_TIERS:
            raise ValueError(f"Unknown inserter tier: {inserter_type}")

        ox, oy = origin_x, origin_y
        length = lab_count * MACHINE_WIDTH

        def at(x: float, y: float) -> dict:
            return {"x": ox + x, "y": oy + y}

        ghosts: List[dict] = []
        for x in range(CHAINED_BELT_WEST, length):
            ghosts.append({"action_type": "place_ghost", "entity": belt_type,
                           "position": at(x + 0.5, 0.5), "direction": "east"})
        for i in range(lab_count):
            center = i * MACHINE_WIDTH + 1.5
            ghosts.append({"action_type": "place_ghost", "entity": "lab",
                           "position": at(center, 3.5)})
            ghosts.append({"action_type": "place_ghost", "entity": inserter_type,
                           "position": at(center, 1.5), "direction": "north"})
        for x in range(0, length + 1, 6):
            ghosts.append({"action_type": "place_ghost", "entity": "medium-electric-pole",
                           "position": at(x + 0.5, 1.5)})

        # Power sits west of the chain junction columns, like chained lines.
        westmost = CHAIN_SOUTH_APPROACH_COL - 2
        scaffolding = [
            {"action_type": "place_entity", "entity": "electric-energy-interface",
             "position": at(westmost - 6.0, 3.5)},
            {"action_type": "place_entity", "entity": "substation",
             "position": at(CHAINED_SUBSTATION_X, 2.0)},
        ]

        plan = {"phases": [
            {"name": "lab_scaffolding", "actions": scaffolding},
            {"name": "lab_row", "actions": ghosts},
        ]}
        self._validate_and_reject_fuel(plan)
        return plan

    @staticmethod
    def material_requirements(plan: dict) -> Dict[str, int]:
        """Items construction bots need: one per ghost, keyed by entity name."""
        needs: Dict[str, int] = {}
        for phase in plan.get("phases", []):
            for action in phase.get("actions", []):
                if action.get("action_type") == "place_ghost":
                    entity = action["entity"]
                    needs[entity] = needs.get(entity, 0) + 1
        return needs
