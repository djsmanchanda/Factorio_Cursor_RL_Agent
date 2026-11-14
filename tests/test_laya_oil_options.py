# Path: tests/test_laya_oil_options.py | Purpose: Protect the clean runner's `reach`, `coal-feed` and `oil` families: planner outputs become scoped ghost candidates or typed blocks.

from __future__ import annotations

import pytest

from orchestrator import extraction_state, resource_patches, stage_chemical, stage_services
from orchestrator.reach_survey import MissingReachSurvey, ReachSurvey
from orchestrator.stage_chemical import (
    CoalMinePlan, CoalSurvey, MissingCoalSurvey, MissingOilSurvey, OilCellPlan, OilSurvey,
)
from orchestrator.stage_services import PowerBridgePlan, ReachStep
from tests.test_laya_clean_runner import facts as runner_facts, validator
from training.laya_clean_runner import _offer_oil, compile_menu
from training.laya_factory_status import compile_status
from planners.plan_validation import validate_build_plan
from tools.laya_oil_options import OilOptions, World, ghost_phases
from training.laya_saved_world import declared_scope_reason, ghost

CENTER = (297.0, 10.0)
OIL_FIELD = {"found": True, "anchor": {"x": 0, "y": 0}, "center": {"x": CENTER[0], "y": CENTER[1]},
             "anchor_source": "roboport", "search_radius": 512, "wells": 9, "water": {"found": True}}
BOARD = {"needs": [{"item": "plastic-bar", "condition": "shortfall"},
                   {"item": "crude-oil", "condition": "shortfall"}]}
HOP_SURVEY = ReachSurvey("area_clear", min_point=(40.0, -2.0), max_point=(44.0, 2.0))
CELL_SURVEY = OilSurvey("occupied", (280.0, 0.0), (320.0, 30.0))
COAL_SURVEY = CoalSurvey("origins", (240.0, 50.0), (270.0, 70.0), (312.0, 22.0))
COAL_PATCH = resource_patches.ResourcePatch((250.5, 50.5), (240.0, 50.0), (270.0, 70.0), 400_000)


def port(x, y, network="1"):
    return {"position": {"x": x, "y": y}, "network": network, "energy": 100,
            "construction_radius": 55, "logistic_radius": 25}


def world_facts(ports, oil_field=OIL_FIELD):
    return {"tick": 1000, "ports": ports, "networks": {"1": {"construction_robots": 50, "available_construction_robots": 50, "items": {"transport-belt": 500, "substation": 20, "medium-electric-pole": 20, "pumpjack": 10, "pipe": 500}, "reserved": {}}}, **({"oil_field": oil_field} if oil_field is not None else {})}


class FakeWorld:
    """Recorded read-only answers; ``reads`` lists every read in order."""

    def __init__(self, coal=(250.0, 40.0), coal_reason=None, coal_patch=COAL_PATCH, machines=None, isolated=()):
        self.coal, self.coal_reason, self.patch, self.reads = coal, coal_reason, coal_patch, []
        self.machines, self.isolated = machines or {}, list(isolated)

    def inspect_plan(self, phases):
        return [{"entity": a["entity"], "position": a["position"], "state": "missing"}
                for p in phases for a in p["actions"]]

    def outpost_power(self, center):
        self.reads.append("outpost_power")
        return {"machines": self.machines, "main_grid": 1, "isolated_substations": self.isolated}

    def oil_well(self, center):
        self.reads.append("oil_well")
        return (295.5, 9.5)

    def cell_site(self, oil):
        self.reads.append("cell_site")
        return (280.0, 0.0)

    def coal_source(self, near):
        self.reads.append("coal_source")
        return (None, self.coal_reason) if self.coal_reason else (self.coal, None)

    def coal_patch(self, near):
        self.reads.append(("coal_patch", near))
        return self.patch

    def read_coal_survey(self, survey):
        self.reads.append(("coal_survey", survey.kind))
        return [(250.0, 52.0)]

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


def coal_plan():
    """A recorded belt coal mine: direct substation, one drill pair and an east-flow collector."""
    return CoalMinePlan(plan={"phases": [
        {"name": "direct_mine_power", "actions": [
            {"action_type": "place_entity", "entity": "substation", "position": {"x": 256.0, "y": 55.0}}]},
        {"name": "direct_mine_output", "actions": [
            {"action_type": "place_entity", "entity": "electric-mining-drill", "position": {"x": 251.5, "y": 50.5},
             "direction": "south"},
            ghost("transport-belt", 250.5, 52.5, "east"), ghost("transport-belt", 251.5, 52.5, "east"),
            ghost("transport-belt", 252.5, 52.5, "east"), ghost("transport-belt", 253.5, 52.5, "east")]},
        {"name": "direct_mine_expansion", "actions": [
            {"action_type": "place_entity", "entity": "electric-mining-drill", "position": {"x": 251.5, "y": 54.5},
             "direction": "north"}]},
    ]}, origin=(250.0, 52.0), count=1, output=(253.5, 52.5), output_side="east")


@pytest.fixture
def planners(monkeypatch):
    """Patch the pure planners with recorded outputs; each needs one survey first, like the real ones."""
    calls = {"reach": [], "oil": [], "bridge": [], "coal": []}

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
        return calls.get("bridge_plan") or PowerBridgePlan(
            "bridge", placements=(("medium-electric-pole", (292.5, 5.5)),))

    def coal(facts, surveys):
        calls["coal"].append(facts)
        if COAL_SURVEY not in surveys:
            raise MissingCoalSurvey(COAL_SURVEY)
        if calls.get("coal_error"):
            raise stage_services.StuckError(calls["coal_error"])
        return coal_plan()

    monkeypatch.setattr(stage_services, "plan_reach_step", reach)
    monkeypatch.setattr(stage_chemical, "plan_coal_mine_intent", coal)
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
    assert names == ["chemical_coal_belt:bridge_coal_to_plastic-bar"]
    assert all(a["action_type"] == "place_ghost" for p in cell["plan"]["phases"] for a in p["actions"])
    assert cell["oil_facts"]["bill"] == {"transport-belt": 1}
    assert cell["oil_facts"]["power_bridge"] == "bridge"
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
    assert offer["blocked"] == {"item:plastic-bar": {"needs:coal_source": 1, "needs:construction_coverage": 1}}
    assert [c["family"] for c in offer["candidates"]] == ["reach"]
    assert not planners["oil"] and "plastic_site" not in world.reads
    # The coal mine is planned near the cell centre and its land is kept free of the hop.
    assert planners["coal"][0].reference == (312.0, 22.0) and offer["facts"]["coal_planned"]
    assert planners["reach"][0].reserved_tiles


def test_missing_coal_source_offers_the_belt_coal_mine_once_covered(planners):
    world, facts = FakeWorld(coal_reason="needs:coal_source"), world_facts([port(0, 0), port(290, 10), port(250, 60)])
    offer = OilOptions().offer(world, facts, BOARD)
    [feed] = offer["candidates"]
    assert feed["family"] == "coal-feed" and feed["need_id"] == "item:plastic-bar" and feed["product"] == "coal"
    assert offer["blocked"] == {"item:plastic-bar": {"needs:coal_source": 1}}
    assert [p["name"] for p in feed["plan"]["phases"]] == [
        "coal_mine:direct_mine_power", "coal_power_bridge", "coal_mine:direct_mine_output",
        "coal_mine:direct_mine_expansion"]
    assert all(a["action_type"] == "place_ghost" for p in feed["plan"]["phases"] for a in p["actions"])
    assert planners["bridge"][0].near_position == (256.0, 55.0)
    assert feed["oil_facts"]["belt_source"] == [253.5, 52.5] and feed["oil_facts"]["bill"]["electric-mining-drill"] == 2
    assert feed["network_anchor"] == {"x": 0, "y": 0} and feed["own_power"]
    assert ("coal_survey", "origins") in world.reads and declared_scope_reason(feed, facts) is None


@pytest.mark.parametrize("setup,reason", [
    (lambda calls, world: setattr(world, "patch", None), "no_coal_patch"),
    (lambda calls, world: calls.update(coal_error="No clear two-drill coal extraction site"), "coal_plan_failed"),
    (lambda calls, world: calls.update(bridge_plan=PowerBridgePlan("unpowered")), "coal_unpowered"),
])
def test_an_unplannable_coal_mine_is_a_typed_block(planners, setup, reason):
    world = FakeWorld(coal_reason="needs:coal_source")
    setup(planners, world)
    offer = OilOptions().offer(world, world_facts([port(0, 0), port(290, 10), port(250, 60)]), BOARD)
    assert offer["candidates"] == []
    assert offer["blocked"] == {"item:plastic-bar": {"needs:coal_source": 1, reason: 1}}


def test_an_incomplete_coal_mine_is_not_offered_again(planners):
    world = FakeWorld(coal_reason="coal_source_incomplete")
    offer = OilOptions().offer(world, world_facts([port(0, 0), port(290, 10), port(250, 60)]), BOARD)
    assert offer["candidates"] == [] and not planners["coal"]
    assert offer["blocked"] == {"item:plastic-bar": {"coal_source_incomplete": 1}}


def test_a_built_coal_feed_is_recognized_as_the_oil_cells_belt_source(monkeypatch):
    """Real planner geometry through the real mine classifier: ghosts read as incomplete, built as the source."""
    from tests.test_coal_mine_intent import _EAST_REFERENCE, _facts, _record_surveys
    mine = stage_chemical.plan_coal_mine_intent(_facts(_EAST_REFERENCE), _record_surveys(monkeypatch, _EAST_REFERENCE))
    entities = [(("drill" if a["entity"] == "electric-mining-drill" else "belt"), (a["position"]["x"], a["position"]["y"]))
                for p in mine.plan["phases"] for a in p["actions"]
                if a["entity"] in ("electric-mining-drill", "transport-belt")]
    phases, reason = ghost_phases([("coal_mine", mine.plan)])
    assert reason is None  # The deterministic geometry is a valid all-ghost Laya plan as it stands.
    validate_build_plan({"surface": "nauvis", "force": "player", "atomic": True, "phases": phases})
    lone_chest_drill = extraction_state.ExtractionEntity("drill", (-20.5, 40.5), True)  # A `mine` build.

    def entities_at(built):
        return lambda *_a: [lone_chest_drill, *(extraction_state.ExtractionEntity(k, p, built) for k, p in entities)]

    monkeypatch.setattr(stage_chemical.live_base, "transport_belt_direction_at", lambda *_a: "east")
    world = World(type("Client", (), {"command": lambda self, _t: ""})())
    monkeypatch.setattr(extraction_state, "_resource_mine_entities", entities_at(False))
    assert world.coal_source(_EAST_REFERENCE) == (None, "coal_source_incomplete")
    monkeypatch.setattr(extraction_state, "_resource_mine_entities", entities_at(True))
    assert world.coal_source(_EAST_REFERENCE) == (mine.output, None)
    # Drill-to-chest coal mines alone are not a belt source.
    monkeypatch.setattr(extraction_state, "_resource_mine_entities", lambda *_a: [lone_chest_drill])
    assert world.coal_source(_EAST_REFERENCE) == (None, "needs:coal_source")


def test_opening_geometry_is_pinned_across_packet_release_and_ttl(planners):
    options, facts = OilOptions(), world_facts([port(0, 0)])
    options.offer(FakeWorld(), facts, BOARD)
    planned = len(planners["oil"])
    options.offer(FakeWorld(), facts, BOARD)
    assert len(planners["oil"]) == planned
    options.offer(FakeWorld(), {**facts, "tick": facts["tick"] + options.plan_ttl_ticks}, BOARD)
    assert len(planners["oil"]) == planned


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


def test_the_coal_feed_may_stand_on_coal_land_only():
    from training.laya_site_plan import reservation_reason
    plan = {"zones": [{"purpose": "ore:coal", "rect": [240, 40, 270, 70]},
                      {"purpose": "ore:iron-ore", "rect": [30, -10, 60, 10]}]}

    def feed(x, y):
        return {"family": "coal-feed", "product": "coal", "plan": {"phases": [
            {"name": "all", "actions": [ghost("electric-mining-drill", x, y)]}]}}

    assert reservation_reason(plan, feed(251.5, 50.5)) is None
    assert reservation_reason(plan, feed(42.5, 0.5)) == "reserved:ore:iron-ore"


def test_an_option_over_already_submitted_ghosts_is_blocked_not_resubmitted():
    from tests.test_laya_factory_status import FLUID_RECIPES
    fluid = {**runner_facts(), "fluid_recipes": FLUID_RECIPES}
    board = compile_status(fluid, {"plastic-bar": 0.5})
    hop = {"option_id": "reach.abc", "family": "reach", "summary": "hop", "need_id": "item:crude-oil",
           "product": "crude-oil", "network": "1", "own_power": True,
           "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
               {"name": "reach", "actions": [ghost("roboport", 42, 0)]}]}}
    menu = compile_menu(fluid, board, validator(), seed=4, oil_offer={"candidates": [hop], "blocked": {}},
                        placed=[{"entity": "roboport", "position": {"x": 42, "y": 0}, "submitted": True}])
    assert menu["blocked"]["item:crude-oil"] == {"already_submitted": 1}  # a pure resubmission
    assert not any(o["option_id"] == "reach.abc" for o in menu["options"])


def test_an_option_sharing_an_attachment_entity_with_an_earlier_build_is_still_offered():
    """Live 2026-11-07 smoke: the oil cell repeats the coal-feed's belt end (reconciled as already present)."""
    from tests.test_laya_factory_status import FLUID_RECIPES
    fluid = {**runner_facts(), "fluid_recipes": FLUID_RECIPES}
    board = compile_status(fluid, {"plastic-bar": 0.5})
    cell = {"option_id": "reach.abc", "family": "reach", "summary": "cell", "need_id": "item:crude-oil",
            "product": "crude-oil", "network": "1", "own_power": True,
            "plan": {"surface": "nauvis", "force": "player", "atomic": True, "phases": [
                {"name": "coal", "actions": [ghost("transport-belt", 60.5, 0.5, "east"), ghost("transport-belt", 61.5, 0.5, "east")]}]}}
    def validate(candidates):
        return {c["option_id"]: {"reason": None, "bill": {"transport-belt": 2}} if c["family"] == "reach"
                else validator()([c])[c["option_id"]] for c in candidates}

    shared = [{"entity": "transport-belt", "position": {"x": 60.5, "y": 0.5}, "submitted": True}]
    menu = compile_menu(fluid, board, validate, seed=4, oil_offer={"candidates": [cell], "blocked": {}}, placed=shared)
    assert "already_submitted" not in menu["blocked"].get("item:crude-oil", {})
    refused = [{"entity": "transport-belt", "position": {"x": 60.5, "y": 0.5}}]  # a footprint the game refused
    menu = compile_menu(fluid, board, validate, seed=4, oil_offer={"candidates": [cell], "blocked": {}}, placed=refused)
    assert menu["blocked"]["item:crude-oil"] == {"already_submitted": 1}


def test_missing_stock_for_the_cell_is_a_shortfall_that_drives_the_mall(planners, monkeypatch):
    """Live 2026-11-07 smoke s1: the coal belt route needed 91 belts; it read as oil_options_error."""
    from orchestrator.parts_mall import MaterialShortage

    def short(facts, surveys):
        raise MaterialShortage("belt bridge for coal", {"transport-belt": 91}, {"transport-belt": 0})
    monkeypatch.setattr(stage_chemical, "plan_oil_cell_intent", short)
    offer = OilOptions().offer(FakeWorld(), world_facts([port(0, 0), port(290, 10)]), BOARD)
    assert offer["blocked"]["item:plastic-bar"] == {"item_shortfall:transport-belt": 1}
    assert offer["shortfalls"] == {"transport-belt": 91}
    from tests.test_laya_factory_status import FLUID_RECIPES
    base = runner_facts()
    belt = {"ingredients": [{"name": "iron-gear-wheel", "amount": 1}, {"name": "iron-plate", "amount": 1}],
            "product_amount": 2, "energy_ticks": 30}
    fluid = {**base, "recipes": {**base["recipes"], "transport-belt": belt}, "fluid_recipes": FLUID_RECIPES}
    menu = compile_menu(fluid, compile_status(fluid, {"plastic-bar": 0.5}), validator(), seed=4,
                        oil_offer={"candidates": [], "blocked": offer["blocked"], "shortfalls": offer["shortfalls"]})
    assert menu["unit_shortfalls"]["transport-belt"] >= 91 and "transport-belt" in menu["shortfalls"]


def test_roboports_still_needed_for_the_outpost_join_the_goal_reserve(planners):
    """Live 2026-11-08 smoke s1: coverage builds took the kit's roboports and the outpost stalled."""
    offer = OilOptions().offer(FakeWorld(), world_facts([port(0, 0)]), BOARD)
    assert offer["facts"]["roboports_to_outpost"] == 6  # (297.2 - 55) / 42, rounded up
    from tests.test_laya_factory_status import FLUID_RECIPES
    fluid = {**runner_facts(), "fluid_recipes": FLUID_RECIPES}
    menu = compile_menu(fluid, compile_status(fluid, {"plastic-bar": 0.5}), validator(), seed=4,
                        oil_offer={"candidates": [], "blocked": {}, "facts": offer["facts"]})
    assert menu["goal_reserve"]["roboport"] == 6


def test_the_provider_reads_the_adapters_own_world_and_routes_reach_around_reserved_land(planners, tmp_path):
    """eval-fixes-20261109b: with the mall between base and oil, the direct hop was rejected reserved:mall forever."""
    import json
    seen = []
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "site-plan.json").write_text(json.dumps({"zones": [
        {"zone_id": "mall", "purpose": "mall", "rect": [0, 0, 2, 2]},
        {"zone_id": "ore", "purpose": "ore:iron-ore", "rect": [10, 10, 20, 20]}]}))

    class Adapter:
        bridge, surface, force, root = "rcon-of-this-copy", "nauvis", "player", tmp_path

    options = OilOptions()
    options.offer = lambda world, facts, board, reach_avoid: seen.append(
        (world.client, world.surface, world.force, reach_avoid))
    options(Adapter(), {}, BOARD)
    assert seen == [("rcon-of-this-copy", "nauvis", "player", frozenset({(0, 0), (0, 1), (1, 0), (1, 1)}))]


def test_a_built_cell_is_not_replanned_and_its_off_grid_substation_gets_a_hookup(planners):
    """Live 2026-11-07: the cell built, but its refinery substations stayed on their own network."""
    world = FakeWorld(machines={"oil-refinery": 4, "chemical-plant": 2, "pumpjack": 3}, isolated=[(285.0, -20.0)])
    offer = OilOptions().offer(world, world_facts([port(0, 0), port(290, 10)]), BOARD)
    assert offer["facts"]["cell_built"] and offer["facts"]["isolated_substations"] == 1
    assert "oil_well" not in world.reads and not planners["oil"]  # nothing replanned over the built cell
    (hookup,) = offer["candidates"]
    assert hookup["family"] == "power-hookup" and hookup["oil_facts"]["substation"] == [285.0, -20.0]
    assert planners["bridge"][-1].near_position == (285.0, -20.0)
    assert declared_scope_reason(hookup, world_facts([port(0, 0), port(290, 10)])) is None


def test_a_powered_built_cell_offers_nothing_more(planners):
    world = FakeWorld(machines={"oil-refinery": 4, "chemical-plant": 2})
    offer = OilOptions().offer(world, world_facts([port(0, 0), port(290, 10)]), BOARD)
    assert offer["candidates"] == [] and offer["blocked"] == {} and offer["facts"]["cell_built"]


def test_a_cell_in_construction_range_but_not_logistic_range_reaches_for_logistic_coverage(planners):
    """eval-fixes-20261109c s0: the planned cell was in construction range, but its output chest was not in
    logistic range; the cell was rejected logistic_coverage for 230 decisions and nothing extended coverage."""
    planners["plan"] = cell_plan(extra=[ghost("passive-provider-chest", 296.5, 12.5)])
    world, facts = FakeWorld(), world_facts([port(0, 0), port(250, 10)])
    offer = OilOptions().offer(world, facts, BOARD)
    assert {c["family"] for c in offer["candidates"]} == {"reach", "oil"}
    partial = next(c for c in offer["candidates"] if c["family"] == "oil")
    assert not any(a["entity"] == "passive-provider-chest" for p in partial["plan"]["phases"] for a in p["actions"])
    assert planners["reach"][-1].purpose == "logistic"
    assert offer["facts"]["reach_purpose"] == "logistic"
    assert offer["blocked"]["item:plastic-bar"] == {"needs:logistic_coverage": 1}


def test_a_reserved_block_across_the_line_yields_corner_detours_shortest_first():
    from tools.laya_oil_options import DETOUR_MARGIN, detour_waypoints
    mall = (-54.0, -19.0, -14.0, 21.0)  # eval-fixes-20261109c s1: west of the base, on the way to oil
    points = detour_waypoints((0.0, 0.0), (-280.0, -91.0), [mall, (100.0, 100.0, 110.0, 110.0)])
    assert len(points) == 4  # only the zone the line crosses
    assert points[0] == (-14.0 + DETOUR_MARGIN, -19.0 - DETOUR_MARGIN) or points[0][1] == -19.0 - DETOUR_MARGIN
    assert detour_waypoints((0.0, 0.0), (-280.0, -91.0), [(100.0, 100.0, 110.0, 110.0)]) == []


def test_packet_progress_survives_restart_without_replanning_or_resubmission(tmp_path, planners, monkeypatch):
    import tools.laya_oil_options as module
    from types import SimpleNamespace
    world = FakeWorld()
    states = {}
    def inspect(phases):
        return [{'entity': a['entity'], 'position': a['position'],
                 'state': states.get((a['entity'], a['position']['x'], a['position']['y']), 'missing')}
                for phase in phases for a in phase['actions']]
    world.inspect_plan = inspect
    monkeypatch.setattr(module, 'World', lambda *args: world)
    (tmp_path / 'evidence').mkdir()
    adapter = SimpleNamespace(root=tmp_path, bridge=object(), surface='nauvis', force='player')
    facts = world_facts([port(0, 0), port(290, 10)])
    first = OilOptions()(adapter, facts, BOARD)['candidates'][0]
    for phase in first['plan']['phases']:
        for a in phase['actions']:
            states[(a['entity'], a['position']['x'], a['position']['y'])] = 'built'
    calls = len(planners['oil'])
    second = OilOptions()(adapter, {**facts, 'tick': facts['tick'] + 100_000}, BOARD)['candidates'][0]
    assert len(planners['oil']) == calls
    assert second['plan']['phases'][0]['name'].startswith('chemical_power_backbone')
    assert first['option_id'] != second['option_id']


def test_covered_substructure_releases_while_future_packet_needs_coverage(planners):
    # Put a future pipe outside today's frontier; the nearby funded coal belt
    # remains useful work and must not wait for the full district's coverage.
    plan = cell_plan()
    plan.intent['packets'][-1][1]['phases'][0]['actions'] = [ghost('pipe', 380.5, 12.5)]
    planners['plan'] = plan
    offer = OilOptions().offer(FakeWorld(), world_facts([port(0, 0), port(290, 10)]), BOARD)
    assert {'reach', 'oil'} <= {c['family'] for c in offer['candidates']}
    oil = next(c for c in offer['candidates'] if c['family'] == 'oil')
    assert oil['plan']['phases'][0]['name'].startswith('chemical_coal_belt')
    assert all(a['position']['x'] < 345 for p in oil['plan']['phases'] for a in p['actions'])
