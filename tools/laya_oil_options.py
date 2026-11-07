# Path: tools/laya_oil_options.py
# Purpose: Clean-runner `reach`, `coal-feed` and `oil` option families: drive the deterministic pure planners and offer their placements as ghost candidates.

"""Reach, coal-feed and oil options for the clean runner (docs/rl/Laya_Clean_Runner.md, "Oil options").

Adapter only.  Geometry comes from the deterministic pure planners
(`orchestrator.stage_services.plan_reach_step`, `plan_power_bridge`,
`orchestrator.stage_chemical.plan_oil_cell_intent`, `plan_coal_mine_intent`); every survey they name is
answered with the deterministic read-only functions against the disposable
world.  It lives in tools/ (training/ may not import `orchestrator.stage_*`;
training/isolation.py) and the CLI injects it into the clean runner, like the
optimizer-block capability.  This module only turns their placements into ordinary atomic
`place_ghost` candidates with a declared scope, a need and observed facts, or
into typed blocked reasons.  It ranks nothing: the policy chooses.
"""

from __future__ import annotations

import copy
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from orchestrator.laya_operations import sha256_hash
from planners.plan_validation import validate_build_plan
from training.laya_saved_world import SAVED_WORLD_GHOST_FIELDS, ghost

FLUID_CHAIN = frozenset({"crude-oil", "petroleum-gas", "plastic-bar"})
# A hop serves the most upstream unmet item; the cell serves its product first.
REACH_NEED_ORDER = ("crude-oil", "petroleum-gas", "plastic-bar")
OIL_NEED_ORDER = ("plastic-bar", "petroleum-gas", "crude-oil")
# Tiles between the outermost action and the declared scope edge. The adapter requires at least
# SCOPE_EDGE (2) and at most SCOPE_EDGE + SCOPE_SLACK (6); see laya_saved_world.declared_scope_reason.
SCOPE_MARGIN = 4
MAX_SURVEYS = 96  # Bounded reads per planner resolution; exceeding it is a typed block, not a hang.
# The coal mine probes up to 300 origins (two reads each); past this many reads the site is too cluttered to offer.
COAL_MAX_SURVEYS = 128
# The cell intent is a frozen geometry; re-plan after this many game ticks so a stale one cannot linger.
PLAN_TTL_TICKS = 36000
# Nominal electric draw (kW) of the cell's consumers; an estimate, compared with no measured grid.
NOMINAL_DRAW_KW = {"pumpjack": 90, "oil-refinery": 420, "chemical-plant": 210, "offshore-pump": 0,
                   "electric-mining-drill": 90, "inserter": 13.2, "fast-inserter": 46.7}


class SurveyBudgetExceeded(RuntimeError):
    pass


def resolve(planner: Callable, facts: Any, reader: Callable, missing: type[LookupError],
            limit: int = MAX_SURVEYS) -> tuple[Any, dict]:
    """Run a pure planner, answering each survey it names with ``reader`` until it returns."""
    surveys: dict = {}
    for _ in range(limit):
        try:
            return planner(facts, surveys), surveys
        except missing as error:
            surveys[error.survey] = reader(error.survey)
    raise SurveyBudgetExceeded(f"planner needed more than {limit} surveys")


def scope_bounds(points: list[tuple[float, float]]) -> dict[str, int]:
    """Tight declared scope around action positions (SCOPE_MARGIN tiles on every side)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return {"x_min": math.floor(min(xs)) - SCOPE_MARGIN, "y_min": math.floor(min(ys)) - SCOPE_MARGIN,
            "x_max_exclusive": math.floor(max(xs)) + 1 + SCOPE_MARGIN,
            "y_max_exclusive": math.floor(max(ys)) + 1 + SCOPE_MARGIN}


def phases_bill(phases: list[Mapping]) -> dict[str, int]:
    """Items the submitted ghosts consume: one per ghost, including inserted power-bridge phases."""
    return dict(Counter(a["entity"] for phase in phases for a in phase["actions"]))


def plan_points(plan: Mapping) -> list[tuple[float, float]]:
    return [(a["position"]["x"], a["position"]["y"]) for phase in plan["phases"] for a in phase["actions"]]


def ghost_phases(packets: list[tuple[str, Mapping]]) -> tuple[list[dict] | None, str | None]:
    """Ordered ghost phases for the intent's packets, or the typed reason they cannot be one ghost plan.

    Direct placements are normalized to funded ghosts exactly as the deterministic `_submit`
    boundary does (`_ghostify_direct_infrastructure`); tile ghosts (landfill) and other actions
    are not saved-world ghosts.
    """
    from orchestrator.stage_services import _ghostify_direct_infrastructure
    phases = []
    for name, packet in packets:
        packet = copy.deepcopy(dict(packet))
        _ghostify_direct_infrastructure(packet)
        for phase in packet["phases"]:
            if not phase["actions"]:
                continue
            for action in phase["actions"]:
                kind = action.get("action_type")
                if kind == "place_tile_ghost":
                    return None, "needs_landfill"
                if kind != "place_ghost":
                    return None, "non_ghost_action"
                if set(action) - SAVED_WORLD_GHOST_FIELDS:
                    return None, "unsupported_action_field"
            phases.append({"name": f"{name}:{phase['name']}", "actions": phase["actions"]})
    return phases, None


def _first_unmet(order: tuple[str, ...], unmet: set[str]) -> str:
    return next(item for item in order if item in unmet)


def _xy(position: Mapping) -> tuple[float, float]:
    return (float(position["x"]), float(position["y"]))


@dataclass
class World:
    """Read-only access the families need; tests replace these with recorded answers."""

    client: Any
    surface: str = "nauvis"
    force: str = "player"

    def oil_well(self, center):
        from orchestrator import live_base
        found = live_base.nearest_resource(self.client, self.surface, "crude-oil", center, search_radius=64.0)
        return found[0] if found else None

    def cell_site(self, oil):
        from orchestrator.stage_chemical import _find_oil_cell_site
        return _find_oil_cell_site(self.client, self.surface, oil)

    def coal_source(self, near):
        """Belt source of the nearest planner-managed coal mine: (point, None) or (None, reason).

        Recognized by layout (`extraction_state`): drills beside a belt row, ghosts included, so a
        `coal-feed` build reads as incomplete until built and then as the source. Lone drills
        (drill-to-chest `mine` builds) are not belt sources and do not count.
        """
        from orchestrator import extraction_state
        from orchestrator.stage_chemical import _coal_belt_source
        mine = extraction_state.find_resource_mine(self.client, self.surface, self.force, "coal", near,
                                                   include_unclassified=False)
        if mine is None:
            return None, "needs:coal_source"
        if mine.pending:
            return None, "coal_source_incomplete"
        return _coal_belt_source(self.client, self.surface, mine), None

    def coal_patch(self, near):
        from orchestrator import resource_patches
        return resource_patches.nearest_viable_patch(self.client, self.surface, "coal", near)

    def read_coal_survey(self, survey):
        from orchestrator.stage_chemical import _read_coal_survey
        return _read_coal_survey(self.client, self.surface, survey)

    def plastic_site(self, refinery_centre, coal):
        from orchestrator.stage_chemical import _find_plastic_site
        return _find_plastic_site(self.client, self.surface, refinery_centre, coal)

    def water_site(self, near):
        from orchestrator import chemical_survey
        return chemical_survey.nearest_offshore_pump_site(self.client, self.surface, near)

    def patch_tiles(self, oil):
        from orchestrator.stage_chemical import _crude_patch_tiles
        try:
            return _crude_patch_tiles(self.client, self.surface, oil)
        except Exception:
            return []

    def roboport_ghosts(self):
        from orchestrator import live_base
        return tuple(live_base.roboport_ghost_positions(self.client, self.surface, self.force))

    def read_oil_survey(self, survey):
        from orchestrator.stage_chemical import _read_oil_survey
        return _read_oil_survey(self.client, self.surface, self.force, survey)

    def read_reach_survey(self, survey):
        from orchestrator.stage_services import _answer_live_reach_survey
        return _answer_live_reach_survey(self.client, self.surface, self.force, survey)


@dataclass
class OilOptions:
    """Per-run provider: ``offer(world, facts, board)`` -> candidates, blocked reasons and facts.

    Holds only a performance cache of the frozen cell intent (keyed by its observed inputs,
    refreshed after PLAN_TTL_TICKS); every offer re-reads coverage, ghosts and power.
    """

    plan_ttl_ticks: int = PLAN_TTL_TICKS
    _cache: dict = field(default_factory=dict)

    def __call__(self, adapter: Any, facts: Mapping, board: Mapping) -> dict | None:
        """The clean runner's hook: read the adapter's own disposable world."""
        return self.offer(World(adapter.bridge, adapter.surface, adapter.force), facts, board)

    def offer(self, world: World, facts: Mapping, board: Mapping) -> dict | None:
        field_facts = facts.get("oil_field") or {}
        unmet = {n["item"] for n in board["needs"] if n["condition"] != "satisfied"} & FLUID_CHAIN
        if not field_facts.get("found") or not field_facts.get("center") or not unmet:
            return None  # Menu unchanged: no surveyed oil, or nothing in the oil chain is needed.
        center = _xy(field_facts["center"])
        ports = [p for p in facts["ports"] if p["energy"] > 0]
        result: dict = {"candidates": [], "blocked": {}, "facts": {"oil_center": list(center)}}
        reach_need = f"item:{_first_unmet(REACH_NEED_ORDER, unmet)}"
        oil_need = f"item:{_first_unmet(OIL_NEED_ORDER, unmet)}"

        def block(need: str, reason: str) -> None:
            result["blocked"].setdefault(need, Counter())[reason] += 1

        if not ports:
            block(reach_need, "reach_no_source")
            return result
        source_port = min(ports, key=lambda p: math.dist(_xy(p["position"]), center))
        result["facts"]["distance_to_oil"] = round(math.dist(_xy(source_port["position"]), center), 1)

        def covered(point) -> bool:  # Euclidean: conservative for the square construction area.
            return any(math.dist(_xy(p["position"]), point) <= p["construction_radius"] for p in ports)

        intent, reason, cell_centre = self._intent(world, facts, center)
        if reason:
            block(oil_need, reason)
        # Without a belt coal source the cell cannot be planned; the next build is the coal mine it needs.
        coal = None
        if reason == "needs:coal_source":
            coal, coal_reason = self._coal(world, facts, cell_centre)
            if coal_reason:
                block(oil_need, coal_reason)
            result["facts"]["coal_planned"] = coal is not None
            if coal:
                result["facts"].update(coal_bill=coal["bill"], coal_belt_source=coal["source"])
        build = intent or coal
        points = plan_points({"phases": intent["phases"]}) if intent else []
        uncovered = [p for p in points if not covered(p)]
        result["facts"].update(cell_planned=intent is not None, cell_actions=len(points),
                               cell_uncovered_actions=len(uncovered))
        if coal:
            uncovered = [p for p in plan_points({"phases": coal["phases"]}) if not covered(p)]
        if intent:
            result["facts"].update(cell_bill=intent["bill"], cell_draw_kw=intent["draw_kw"],
                                   grid_capacity_kw=None, power_capacity_verified=False,
                                   plan_tick=intent["tick"])
        # Reach toward the oil field until it is covered, then toward the cell's (or coal mine's) remaining actions.
        targets = [center] if not covered(center) else []
        if not targets and uncovered:
            targets = [min(uncovered, key=lambda p: min(math.dist(_xy(q["position"]), p) for q in ports))]
        if targets:
            option = self._reach(world, ports, targets[0], center, build, reach_need, block)
            if build:
                block(oil_need, "needs:construction_coverage")
        elif intent:
            option = self._oil(world, ports, intent, center, oil_need, block)
        else:
            option = self._coal_feed(world, ports, coal, center, oil_need, block) if coal else None
        if option:
            try:
                validate_build_plan(option["plan"])
            except ValueError:
                block(option["need_id"], f"{option['family']}_plan_invalid")
            else:
                result["candidates"].append(option)
        result["blocked"] = {need: dict(counts) for need, counts in result["blocked"].items()}
        return result

    def _intent(self, world: World, facts: Mapping, center) -> tuple[dict | None, str | None, Any]:
        """The frozen opening cell (ghost phases, bill, draw) or the typed reason it cannot be planned,
        plus the cell centre once a cell site is known (the coal mine's reference point)."""
        oil = world.oil_well(center)
        if oil is None:
            return None, "oil_well_unreadable", None
        cell = world.cell_site(oil)
        if cell is None:
            return None, "no_cell_site", None
        cell_centre = (round(cell[0]) + 32.0, round(cell[1]) + 22.0)
        coal, reason = world.coal_source(cell_centre)
        if reason:
            return None, reason, cell_centre
        intent, reason = self._cell(world, facts, oil, cell, cell_centre, coal)
        return intent, reason, cell_centre

    def _cell(self, world, facts, oil, cell, cell_centre, coal) -> tuple[dict | None, str | None]:
        from orchestrator.stage_chemical import MissingOilSurvey, OilCellFacts, plan_oil_cell_intent
        from orchestrator.stage_services import StuckError
        key = (tuple(oil), tuple(cell), tuple(coal))
        cached = self._cache.get(key)
        if cached and facts["tick"] - cached["tick"] < self.plan_ttl_ticks:
            return (None, cached["reason"]) if "reason" in cached else (cached, None)
        self._cache.clear()
        plastic = world.plastic_site(cell_centre, coal)
        water = world.water_site(cell_centre) if plastic is not None else None
        if plastic is None or water is None:
            return None, "no_plastic_site" if plastic is None else "no_shoreline"
        cell_facts = OilCellFacts(target_output="plastic-bar", oil_position=tuple(oil), cell_origin=tuple(cell),
                                  coal=tuple(coal), plastic_site=tuple(plastic), water=water,
                                  patch_tiles=tuple(world.patch_tiles(oil)))
        try:
            plan, _surveys = resolve(plan_oil_cell_intent, cell_facts, world.read_oil_survey, MissingOilSurvey)
        except (StuckError, SurveyBudgetExceeded) as error:
            self._cache[key] = {"tick": facts["tick"], "reason": "oil_plan_failed", "error": str(error)[:200]}
            return None, "oil_plan_failed"
        phases, reason = ghost_phases(plan.packets)
        if reason:
            self._cache[key] = {"tick": facts["tick"], "reason": reason}
            return None, reason
        bill = Counter(a["entity"] for phase in phases for a in phase["actions"])
        intent = {"tick": facts["tick"], "phases": phases, "packets": [name for name, _ in plan.packets],
                  "bill": dict(bill), "footprint": frozenset(plan.footprint_tiles),
                  "substations": sorted({(a["position"]["x"], a["position"]["y"]) for phase in phases
                                         for a in phase["actions"] if a["entity"] == "substation"}),
                  "draw_kw": round(sum(NOMINAL_DRAW_KW.get(name, 0) * n for name, n in bill.items()), 1)}
        self._cache[key] = intent
        return intent, None

    def _coal(self, world: World, facts: Mapping, cell_centre) -> tuple[dict | None, str | None]:
        """The belt coal mine `ensure_coal_mine` would build for this cell (ghost phases, bill, belt
        source), or the typed reason it cannot be planned. Cached like the cell intent."""
        from orchestrator.stage_chemical import CoalMineFacts, MissingCoalSurvey, plan_coal_mine_intent
        from orchestrator.stage_services import StuckError
        patch = world.coal_patch(cell_centre)
        if patch is None:
            return None, "no_coal_patch"
        key = ("coal", tuple(cell_centre), tuple(patch.minimum), tuple(patch.maximum))
        cached = self._cache.get(key)
        if cached and facts["tick"] - cached["tick"] < self.plan_ttl_ticks:
            return (None, cached["reason"]) if "reason" in cached else (cached, None)
        self._cache.clear()
        mine_facts = CoalMineFacts(tuple(cell_centre), tuple(patch.minimum), tuple(patch.maximum), patch.amount)
        try:
            mine, _surveys = resolve(plan_coal_mine_intent, mine_facts, world.read_coal_survey, MissingCoalSurvey,
                                     limit=COAL_MAX_SURVEYS)
        except (StuckError, SurveyBudgetExceeded) as error:
            self._cache[key] = {"tick": facts["tick"], "reason": "coal_plan_failed", "error": str(error)[:200]}
            return None, "coal_plan_failed"
        phases, reason = ghost_phases([("coal_mine", mine.plan)])
        if reason:
            self._cache[key] = {"tick": facts["tick"], "reason": f"coal_{reason}"}
            return None, f"coal_{reason}"
        bill = Counter(a["entity"] for phase in phases for a in phase["actions"])
        coal = {"tick": facts["tick"], "phases": phases, "bill": dict(bill), "footprint": frozenset(mine.footprint_tiles),
                "substations": sorted({(a["position"]["x"], a["position"]["y"]) for phase in phases
                                       for a in phase["actions"] if a["entity"] == "substation"}),
                "source": list(mine.output), "output_side": mine.output_side,
                "patch": {"min": list(patch.minimum), "max": list(patch.maximum), "amount": patch.amount},
                "draw_kw": round(sum(NOMINAL_DRAW_KW.get(name, 0) * n for name, n in bill.items()), 1)}
        self._cache[key] = coal
        return coal, None

    def _bridge(self, world, build, phases, after, family, need, block) -> dict | None:
        """Hook the build's first substation to the grid (`plan_power_bridge`), inserting the poles after
        phase index ``after``; the facts, or None when blocked."""
        from orchestrator.reach_survey import MissingReachSurvey
        from orchestrator.stage_services import PowerBridgeFacts, StuckError, plan_power_bridge
        if not build["substations"]:
            return {"power_bridge": "none"}
        # One hookup from the first substation (the build wires the rest); the
        # deterministic runtime bridges each after the previous is built.
        try:
            bridge, _surveys = resolve(plan_power_bridge, PowerBridgeFacts(build["substations"][0],
                                       build["footprint"]), world.read_reach_survey, MissingReachSurvey)
        except (StuckError, SurveyBudgetExceeded):
            block(need, f"{family}_power_plan_failed")
            return None
        if bridge.outcome == "unpowered":
            block(need, f"{family}_unpowered")
            return None
        if bridge.placements:
            phases.insert(after, {"name": f"{family}_power_bridge",
                                  "actions": [ghost(name, x, y) for name, (x, y) in bridge.placements]})
        return {"power_bridge": bridge.outcome, "power_bridge_poles": len(bridge.placements),
                "power_bridged_substations": 1, "substations": len(build["substations"])}

    def _coal_feed(self, world, ports, coal, center, need, block) -> dict | None:
        phases = copy.deepcopy(coal["phases"])
        first = coal["substations"][0] if coal["substations"] else None
        after = next((i + 1 for i, p in enumerate(phases) for a in p["actions"]
                      if (a["position"]["x"], a["position"]["y"]) == first), 0)
        bridge_facts = self._bridge(world, coal, phases, after, "coal", need, block)
        if bridge_facts is None:
            return None
        source = tuple(coal["source"])
        port = min(ports, key=lambda p: math.dist(_xy(p["position"]), source))
        oil_facts = {"kind": "coal-feed", "bill": phases_bill(phases), "belt_source": coal["source"],
                     "output_side": coal["output_side"], "patch": coal["patch"], "draw_kw": coal["draw_kw"],
                     "power_capacity_verified": False, **bridge_facts}
        return self._candidate("coal-feed", need, f"Build the oil cell's belt coal mine ({coal['bill'].get('electric-mining-drill', 0)} "
                               f"drills, belt source at {source}, {coal['output_side']} flow)",
                               phases, port, ports, oil_facts, product="coal")

    def _reach(self, world, ports, target, center, intent, need, block) -> dict | None:
        from orchestrator.reach_survey import MissingReachSurvey
        from orchestrator.stage_services import ReachFacts, StuckError, plan_reach_step
        source = min(ports, key=lambda p: math.dist(_xy(p["position"]), target))
        reach_facts = ReachFacts(target=target, source=_xy(source["position"]),
                                 roboport_ghosts=world.roboport_ghosts(),
                                 reserved_tiles=intent["footprint"] if intent else frozenset())
        try:
            step, _surveys = resolve(plan_reach_step, reach_facts, world.read_reach_survey, MissingReachSurvey)
        except (StuckError, SurveyBudgetExceeded):
            block(need, "reach_plan_failed")
            return None
        if step.outcome != "hop":
            if step.outcome != "covered":
                block(need, f"reach_{step.outcome}")
            return None
        actions = [ghost(name, x, y) for name, (x, y) in step.placements]
        port = step.roboports[-1]
        oil_facts = {"kind": "reach", "target": list(target), "distance_to_oil": round(math.dist(port, center), 1),
                     "hops_left": len(step.chain), "bill": step.bill_of_materials}
        return self._candidate("reach", need, f"Carry bot coverage and power toward oil: roboport at {port} "
                               f"({len(step.chain)} hop(s) to target, {oil_facts['distance_to_oil']:.0f} tiles to oil)",
                               [{"name": "reach_power_and_roboport", "actions": actions}], source, ports, oil_facts)

    def _oil(self, world, ports, intent, center, need, block) -> dict | None:
        phases = copy.deepcopy(intent["phases"])
        after = next((i + 1 for i, p in enumerate(phases) if p["name"].startswith("chemical_power_backbone")), 0)
        bridge_facts = self._bridge(world, intent, phases, after, "oil", need, block)
        if bridge_facts is None:
            return None
        port = min(ports, key=lambda p: math.dist(_xy(p["position"]), center))
        oil_facts = {"kind": "oil", "packets": intent["packets"], "bill": phases_bill(phases),
                     "cell_draw_kw": intent["draw_kw"], "power_capacity_verified": False, **bridge_facts}
        return self._candidate("oil", need, f"Build the opening oil -> plastic cell ({len(intent['packets'])} packets, "
                               f"{sum(len(p['actions']) for p in phases)} ghosts, ~{intent['draw_kw'] / 1000:.1f} MW)",
                               phases, port, ports, oil_facts)

    @staticmethod
    def _candidate(family, need, summary, phases, port, ports, oil_facts, product=None) -> dict:
        plan = {"surface": "nauvis", "force": "player", "atomic": True, "phases": phases}
        network = port["network"]
        anchor = min((p["position"] for p in ports if p["network"] == network), key=lambda p: (p["x"], p["y"]))
        scope = scope_bounds(plan_points(plan))
        digest = sha256_hash({"plan": plan, "network_anchor": anchor})
        return {"option_id": f"{family}.{digest[7:19]}", "family": family, "summary": summary, "plan": plan,
                "network": network, "network_anchor": anchor, "product": product or need.split(":", 1)[1], "need_id": need,
                "scope_bounds": scope, "own_power": True, "oil_facts": {**oil_facts, "scope": scope}}
