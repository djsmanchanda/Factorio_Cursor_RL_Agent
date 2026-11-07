# Path: tests/test_laya_oil_options.py | Purpose: Protect the clean runner's `reach` and `oil` families: planner outputs become scoped ghost candidates or typed blocks.

from __future__ import annotations

import pytest

from orchestrator import stage_chemical, stage_services
from orchestrator.reach_survey import MissingReachSurvey, ReachSurvey
from orchestrator.stage_chemical import MissingOilSurvey, OilCellPlan, OilSurvey
from orchestrator.stage_services import PowerBridgePlan, ReachStep
from tests.test_laya_clean_runner import facts as runner_facts, validator
from training.laya_clean_runner import _offer_oil, compile_menu
from training.laya_factory_status import compile_status
from tools.laya_oil_options import OilOptions
from training.laya_saved_world import declared_scope_reason, ghost

CENTER = (297.0, 10.0)
OIL_FIELD = {"found": True, "anchor": {"x": 0, "y": 0}, "center": {"x": CENTER[0], "y": CENTER[1]},
             "anchor_source": "roboport", "search_radius": 512, "wells": 9, "water": {"found": True}}
BOARD = {"needs": [{"item": "plastic-bar", "condition": "shortfall"},
                   {"item": "crude-oil", "condition": "shortfall"}]}
HOP_SURVEY = ReachSurvey("area_clear", min_point=(40.0, -2.0), max_point=(44.0, 2.0))
CELL_SURVEY = OilSurvey("occupied", (280.0, 0.0), (320.0, 30.0))


def port(x, y, network="1"):
    return {"position": {"x": x, "y": y}, "network": network, "energy": 100,
            "construction_radius": 55, "logistic_radius": 25}


def world_facts(ports, oil_field=OIL_FIELD):
    return {"tick": 1000, "ports": ports, **({"oil_field": oil_field} if oil_field is not None else {})}


class FakeWorld:
    """Recorded read-only answers; ``reads`` lists every read in order."""

    def __init__(self, coal=(250.0, 40.0), coal_reason=None):
        self.coal, self.coal_reason, self.reads = coal, coal_reason, []

    def oil_well(self, center):
        self.reads.append("oil_well")
        return (295.5, 9.5)

    def cell_site(self, oil):
        self.reads.append("cell_site")
        return (280.0, 0.0)

    def coal_source(self, near):
        self.reads.append("coal_source")
        return (None, self.coal_reason) if self.coal_reason else (self.coal, None)

    def plastic_site(self, centre, coal):
        self.reads.append("plastic_site")
        return (290.0, 30.0)

    def water_site(self, near):
        self.reads.append("water_site")
        return {"position": (310.5, 20.5), "output": (310, 18), "direction": "south"}

    def patch_tiles(self, oil):
        self.reads.append("patch_tiles")
        return [(295.5, 9.5)]

    def roboport_ghosts(self):
        self.reads.append("roboport_ghosts")
        return ()

    def read_oil_survey(self, survey):
        self.reads.append(("oil_survey", survey.kind))
        return set()

    def read_reach_survey(self, survey):
        self.reads.append(("reach_survey", survey.kind))
        return True


def cell_plan(extra=()):
    """A recorded opening-cell intent: backbone (direct substation), machines and a crude pipeline."""
    return OilCellPlan(intent={"packets": [
        ("chemical_coal_belt", {"phases": [{"name": "bridge_coal_to_plastic-bar",
                                            "actions": [ghost("transport-belt", 289.5, 31.5, "west")]}]}),
        ("chemical_power_backbone", {"phases": [{"name": "crude_oil_power", "actions": [
            {"action_type": "place_entity", "entity": "substation", "position": {"x": 300.0, "y": 14.0}}]}]}),
        ("chemical_refinery_and_plastic_machines", {"phases": [{"name": "crude_oil_source", "actions": [
            ghost("pumpjack", 295.5, 9.5), *extra]}]}),
        ("chemical_crude_pipeline", {"phases": [{"name": "fluid_link_crude-oil", "actions": [
            ghost("pipe", 297.5, 12.5)]}]}),
    ]})


@pytest.fixture
def planners(monkeypatch):
    """Patch the pure planners with recorded outputs; each needs one survey first, like the real ones."""
    calls = {"reach": [], "oil": [], "bridge": []}

    def reach(facts, surveys):
        calls["reach"].append(facts)
        if HOP_SURVEY not in surveys:
            raise MissingReachSurvey(HOP_SURVEY)
        bridge = PowerBridgePlan("bridge", placements=(("medium-electric-pole", (20.5, 0.5)),
                                                      ("medium-electric-pole", (35.5, 0.5))))
        return ReachStep("hop", roboports=((42.0, 0.0),), power=(bridge,),
                         chain=((42.0, 0.0), (84.0, 1.0), (126.0, 2.0)))

    def oil(facts, surveys):
        calls["oil"].append(facts)
        if CELL_SURVEY not in surveys:
            raise MissingOilSurvey(CELL_SURVEY)
        return calls.get("plan") or cell_plan()

    def bridge(facts, surveys):
        calls["bridge"].append(facts)
        return PowerBridgePlan("bridge", placements=(("medium-electric-pole", (292.5, 5.5)),))

    monkeypatch.setattr(stage_services, "plan_reach_step", reach)
    monkeypatch.setattr(stage_chemical, "plan_oil_cell_intent", oil)
    monkeypatch.setattr(stage_services, "plan_power_bridge", bridge)
    return calls


def test_reach_hop_is_a_scoped_ghost_candidate_while_the_field_is_uncovered(planners):
    world, facts = FakeWorld(), world_facts([port(0, 0)])
    offer = OilOptions().offer(world, facts, BOARD)
    [hop] = offer["candidates"]
    assert hop["family"] == "reach" and hop["need_id"] == "item:crude-oil" and hop["product"] == "crude-oil"
    entities = [(a["entity"], a["action_type"]) for p in hop["plan"]["phases"] for a in p["actions"]]
    assert entities == [("medium-electric-pole", "place_ghost")] * 2 + [("roboport", "place_ghost")]
    assert hop["network"] == "1" and hop["network_anchor"] == {"x": 0, "y": 0}
    assert hop["oil_facts"]["hops_left"] == 3 and hop["oil_facts"]["scope"] == hop["scope_bounds"]
    assert declared_scope_reason(hop, facts) is None
    # The survey the planner named was answered by the live reader, then the planner re-ran.
    assert ("reach_survey", "area_clear") in world.reads and len(planners["reach"]) == 2
    assert planners["reach"][0].target == CENTER and planners["reach"][0].source == (0.0, 0.0)
    # The cell is planned (so hops avoid its footprint) but not offered until it is covered.
    assert planners["reach"][0].reserved_tiles
    assert offer["blocked"] == {"item:plastic-bar": {"needs:construction_coverage": 1}}
    assert offer["facts"]["cell_planned"] and offer["facts"]["power_capacity_verified"] is False


def test_oil_cell_is_offered_only_once_construction_coverage_reaches_it(planners):
    world, facts = FakeWorld(), world_facts([port(0, 0), port(290, 10)])
    offer = OilOptions().offer(world, facts, BOARD)
    [cell] = offer["candidates"]
    assert not planners["reach"] and offer["blocked"] == {}
    assert cell["family"] == "oil" and cell["need_id"] == "item:plastic-bar"
    names = [p["name"] for p in cell["plan"]["phases"]]
    # One atomic plan, packets in order, the grid hookup right after the backbone.
    assert names == ["chemical_coal_belt:bridge_coal_to_plastic-bar", "chemical_power_backbone:crude_oil_power",
                     "oil_power_bridge", "chemical_refinery_and_plastic_machines:crude_oil_source",
                     "chemical_crude_pipeline:fluid_link_crude-oil"]
    assert all(a["action_type"] == "place_ghost" for p in cell["plan"]["phases"] for a in p["actions"])
    assert cell["oil_facts"]["bill"]["substation"] == 1 and cell["oil_facts"]["power_bridge"] == "bridge"
    assert planners["bridge"][0].near_position == (300.0, 14.0)
    assert declared_scope_reason(cell, facts) is None


def test_landfill_plans_are_blocked_not_offered(planners):
    planners["plan"] = cell_plan(extra=[{"action_type": "place_tile_ghost", "tile": "landfill",
                                         "position": {"x": 296.5, "y": 20.5}}])
    offer = OilOptions().offer(FakeWorld(), world_facts([port(0, 0), port(290, 10)]), BOARD)
    assert offer["candidates"] == []
    assert offer["blocked"] == {"item:plastic-bar": {"needs_landfill": 1}}


def test_missing_coal_source_blocks_oil_and_reach_still_targets_the_field(planners):
    world = FakeWorld(coal_reason="needs:coal_source")
    offer = OilOptions().offer(world, world_facts([port(0, 0)]), BOARD)
    assert offer["blocked"] == {"item:plastic-bar": {"needs:coal_source": 1}}
    assert [c["family"] for c in offer["candidates"]] == ["reach"]
    assert not planners["oil"] and "plastic_site" not in world.reads
    assert planners["reach"][0].reserved_tiles == frozenset()


def test_cell_intent_is_cached_until_its_inputs_or_ttl_change(planners):
    options, facts = OilOptions(), world_facts([port(0, 0)])
    options.offer(FakeWorld(), facts, BOARD)
    planned = len(planners["oil"])
    options.offer(FakeWorld(), facts, BOARD)
    assert len(planners["oil"]) == planned
    options.offer(FakeWorld(), {**facts, "tick": facts["tick"] + options.plan_ttl_ticks}, BOARD)
    assert len(planners["oil"]) > planned


@pytest.mark.parametrize("oil_field", [None, {**OIL_FIELD, "found": False}])
def test_no_surveyed_oil_reads_nothing_and_leaves_the_menu_unchanged(planners, oil_field):
    world = FakeWorld()
    assert OilOptions().offer(world, world_facts([port(0, 0)], oil_field), BOARD) is None
    assert world.reads == []
    satisfied = {"needs": [{"item": "plastic-bar", "condition": "satisfied"}]}
    assert OilOptions().offer(world, world_facts([port(0, 0)]), satisfied) is None


def test_menu_offers_oil_options_under_their_need_and_journals_blocks():
    from tests.test_laya_factory_status import FLUID_RECIPES
    fluid = {**runner_facts(), "fluid_recipes": FLUID_RECIPES}
    board = compile_status(fluid, {"plastic-bar": 0.5})
    plain = compile_menu(fluid, board, validator(), seed=4)
    assert compile_menu(fluid, board, validator(), seed=4, oil_offer=None) == plain
    hop = {"option_id": "reach.abc", "family": "reach", "summary": "hop", "need_id": "item:crude-oil",
           "product": "crude-oil", "network": "1", "network_anchor": {"x": 0, "y": 0}, "own_power": True,
           "scope_bounds": {"x_min": 16, "y_min": -4, "x_max_exclusive": 47, "y_max_exclusive": 5},
           "oil_facts": {"kind": "reach", "hops_left": 3},
           "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
               {"name": "reach", "actions": [ghost("medium-electric-pole", 20.5, 0.5), ghost("roboport", 42, 0)]}]}}
    offer = {"candidates": [hop], "blocked": {"item:plastic-bar": {"needs:coal_source": 1}}, "facts": {"x": 1}}

    def validate(candidates):
        return {c["option_id"]: {"reason": None, "bill": {"roboport": 1}} if c["family"] == "reach"
                else validator()([c])[c["option_id"]] for c in candidates}

    menu = compile_menu(fluid, board, validate, seed=4, oil_offer=offer)
    needs = {n["need_id"]: n["options"] for n in menu["needs"]}
    assert needs["item:crude-oil"] == ["reach.abc"]
    chosen = next(o for o in menu["options"] if o["option_id"] == "reach.abc")
    assert chosen["plan"] == hop["plan"]  # Its own power: no relay variants were added.
    assert menu["blocked"]["item:plastic-bar"] == {"needs:coal_source": 1}
    assert menu["blocked"]["item:petroleum-gas"] == {"no_candidate": 1}
    assert menu["oil_options"]["candidates"] == [{"option_id": "reach.abc", "family": "reach",
                                                  "need_id": "item:crude-oil", "kind": "reach", "hops_left": 3}]
    # An item shortfall on an oil option funds the mall like any other build.
    short = compile_menu(fluid, board, lambda cs: {c["option_id"]: {"reason": "item_shortfall:roboport",
                                                                     "missing": {"roboport": 1}} for c in cs},
                         seed=4, oil_offer=offer)
    assert short["blocked"]["item:crude-oil"] == {"item_shortfall:roboport": 1}
    assert short["shortfalls"]["roboport"] >= 1 and short["unit_shortfalls"]["roboport"] == 1


def test_a_failed_read_blocks_the_families_not_the_decision():
    class Broken:
        def __call__(self, *_a):
            raise TimeoutError("rcon")

    class Adapter:
        bridge, surface, force = object(), "nauvis", "player"

    emitted = []
    offer = _offer_oil(Broken(), Adapter(), {}, BOARD, emitted.append)
    assert offer["blocked"] == {"item:crude-oil": {"oil_options_error": 1}} and emitted
    assert _offer_oil(None, Adapter(), {}, BOARD, emitted.append) is None


def test_reach_hops_cross_ore_and_oil_land_like_coverage_but_not_the_mall():
    from training.laya_site_plan import reservation_reason
    plan = {"zones": [{"purpose": "ore:iron-ore", "rect": [30, -10, 60, 10]},
                      {"purpose": "oil", "rect": [100, -10, 130, 10]},
                      {"purpose": "mall", "rect": [-30, -30, -10, -10]}]}

    def hop(x, y):
        return {"family": "reach", "product": "crude-oil", "plan": {"phases": [
            {"name": "reach", "actions": [ghost("roboport", x, y)]}]}}

    assert reservation_reason(plan, hop(42, 0)) is None
    assert reservation_reason(plan, hop(115, 0)) is None
    assert reservation_reason(plan, hop(-20, -20)) == "reserved:mall"


def test_an_option_over_already_submitted_ghosts_is_blocked_not_resubmitted():
    from tests.test_laya_factory_status import FLUID_RECIPES
    fluid = {**runner_facts(), "fluid_recipes": FLUID_RECIPES}
    board = compile_status(fluid, {"plastic-bar": 0.5})
    hop = {"option_id": "reach.abc", "family": "reach", "summary": "hop", "need_id": "item:crude-oil",
           "product": "crude-oil", "network": "1", "own_power": True,
           "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
               {"name": "reach", "actions": [ghost("roboport", 42, 0)]}]}}
    menu = compile_menu(fluid, board, validator(), seed=4, oil_offer={"candidates": [hop], "blocked": {}},
                        placed=[{"entity": "roboport", "position": {"x": 42, "y": 0}}])
    assert menu["blocked"]["item:crude-oil"] == {"already_submitted": 1}
    assert not any(o["option_id"] == "reach.abc" for o in menu["options"])


def test_the_provider_reads_the_adapters_own_world(planners):
    seen = []

    class Adapter:
        bridge, surface, force = "rcon-of-this-copy", "nauvis", "player"

    options = OilOptions()
    options.offer = lambda world, facts, board: seen.append((world.client, world.surface, world.force))
    options(Adapter(), {}, BOARD)
    assert seen == [("rcon-of-this-copy", "nauvis", "player")]
