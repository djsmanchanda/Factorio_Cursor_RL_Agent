# Path: tests/test_silo_component_link.py | Purpose: Verify offline component-to-silo belt composition.

from fractions import Fraction
import copy
from dataclasses import replace
import hashlib
import json

import pytest

from core.block_flow import ExistingAllocation, Material, Provenance, SharedCapacity
from core.catalog_demand import plan_catalog_demand
from core.optimizer_artifact_binding import ArtifactBindingError, bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from planners.fluid_item_block import build_fluid_item_block
from planners.plan_validation import actions
from planners.rocket_silo_artifact import build_rocket_silo_artifact
from planners.silo_component_link import (
    _bundle_capacity_accounting, _bundle_feed_accounting, _check_local_ports, _reject_incoming_foreign_belts,
    build_silo_component_bundle, build_silo_component_link,
)
from tests.test_catalog_demand import _bytes, _catalog, _provenance, _recipe
from tests.test_fluid_item_block import scenario
from tests.test_rocket_silo_artifact import _capture, _feeding_capture


@pytest.fixture
def case():
    processing = _recipe("processing-unit", [("electronic-circuit", 20), ("advanced-circuit", 2),
                                               ("sulfuric-acid", 5, "fluid")], [("processing-unit", 1)])
    processing.update(category="crafting-with-fluid", energy_ticks=600)
    rocket_part = _recipe("rocket-part", [("rocket-fuel", 1), ("processing-unit", 1),
                                           ("low-density-structure", 1)], [("rocket-part", 1)])
    catalog = _catalog(processing, rocket_part, force="test-force")
    catalog["machines"] = [{"name": "assembling-machine-2", "type": "assembling-machine",
                            "ingredient_count": 3, "crafting_speed": 0.75,
                            "categories": ["crafting-with-fluid"]}]
    data = _bytes(catalog)
    provenance = _provenance(data, catalog, mods=[{"name": "base", "version": "synthetic"}])
    demand = plan_catalog_demand(data, provenance=provenance, demand={"processing-unit": Fraction(3, 8)},
                                 boundaries=[{"name": name, "cap": 100} for name in
                                             ("electronic-circuit", "advanced-circuit", "sulfuric-acid")])
    component = {"catalog_bytes": data, "recipe": "processing-unit",
                 "machine_by_recipe": {"processing-unit": "assembling-machine-2"},
                 "rows": [{"origin": [0, 0], "machines": 5}], "belt_type": "transport-belt",
                 "inserter_type": "fast-inserter", "capacities": {
                     "lane_items_per_second": Fraction(15, 2), "input_inserter_items_per_second": 4,
                     "output_inserter_items_per_second": 4},
                 "item_mapping": {"provenance": "synthetic fixture", "tiles": {},
                                  "entities": {name: name for name in (
                                      "pipe", "pipe-to-ground", "medium-electric-pole", "assembling-machine-2",
                                      "transport-belt", "fast-inserter")}}, "max_machines": 5}
    return demand, {"component_options": component, "silo_origin": [30, 15],
                    "placement_capture": _feeding_capture(), "row_index": 0,
                    "waypoints": [[20, 6], [20, 10], [33, 10]]}


def test_component_link_connects_and_bills_ghost_route(case):
    demand, options = case
    artifact = build_silo_component_link(demand, **options)
    route = next(phase for phase in artifact["plan"]["phases"] if phase["name"] == "item_route_component_to_silo")
    route_actions = route["actions"]
    assert route_actions[0]["position"] == {"x": 15.5, "y": 6.5}
    assert route_actions[0]["direction"] == "east"
    assert route_actions[-1]["position"] == {"x": 33.5, "y": 13.5}
    assert route_actions[-1]["direction"] == "east"
    assert route_actions[-2]["direction"] == "south"
    assert all(action["action_type"] == "place_ghost" for action in actions(artifact["plan"]))
    component = build_fluid_item_block(demand, **options["component_options"])["artifact"]
    assert artifact["declared_item_bill"]["transport-belt"] == (
        component["declared_item_bill"]["transport-belt"] + 3 + len(route_actions))
    assert "silo_input_delivery" in artifact["pending_checks"]
    assert artifact["executable"] is False
    scope = ConstructionScope("synthetic", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="link", throughput_output="rocket-part",
        throughput_target=1, throughput_unit="items/second")
    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]

    artifact["planning_context"]["data"]["item"] = "other"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="silo", transaction_id="link", throughput_output="rocket-part",
            throughput_target=1, throughput_unit="items/second", expected_plan_hash=bound.binding_sha256)


@pytest.mark.parametrize("change,reason", [
    (lambda options: options.update(row_index=True), "row_index"),
    (lambda options: options.update(row_index=1), "row_index"),
    (lambda options: options.update(waypoints=[[20, 7]]), "axis-aligned"),
    (lambda options: options.update(waypoints=[[1_000_001, 6]]), "bounded"),
    (lambda options: options.update(waypoints=[[300, 6], [300, 13]]), "256 tiles"),
    (lambda options: options.update(waypoints=[[15, 10], [33, 10]]), "eastbound belt"),
    (lambda options: options.update(waypoints=[[20, 6], [20, 10], [32, 10], [32, 13]]),
     "another planned belt"),
    (lambda options: options["placement_capture"].update(surface="other"), "scope or mod"),
    (lambda options: options["component_options"]["item_mapping"]["entities"].update({"transport-belt": "other"}),
     "item mappings differ"),
])
def test_invalid_links_fail_closed(case, change, reason):
    demand, options = case
    options = copy.deepcopy(options)
    change(options)
    with pytest.raises(ValueError, match=reason):
        build_silo_component_link(demand, **options)


@pytest.mark.parametrize("damage", ["item", "belt", "inserter", "amount"])
def test_local_port_check_rejects_inconsistent_evidence(case, damage):
    demand, options = case
    component = build_fluid_item_block(demand, **options["component_options"])
    catalog_bytes = options["component_options"]["catalog_bytes"]
    silo = build_rocket_silo_artifact(origin=options["silo_origin"], placement_capture=options["placement_capture"],
                                      recipe_bytes=catalog_bytes, recipe_sha256=hashlib.sha256(catalog_bytes).hexdigest())
    component_port = copy.deepcopy(component["item_output_ports"][0])
    silo_port = copy.deepcopy(next(port for port in silo["planning_context"]["data"]["input_ports"]
                                   if port["item"] == "processing-unit"))
    if damage == "item":
        component_port["item"] = "other"
    elif damage == "belt":
        _action = next(action for action in actions(component["artifact"]["plan"])
                       if action.get("position") == component_port["belt_position"])
        _action["direction"] = "west"
    elif damage == "inserter":
        next(action for action in actions(silo["plan"]) if action.get("entity") == "fast-inserter"
             and action["position"]["x"] == silo_port["belt_position"]["x"])["direction"] = "south"
    else:
        silo_port["amount_per_part"] += 1
    recipe = next(entry for entry in json.loads(catalog_bytes)["recipes"] if entry["name"] == "rocket-part")
    with pytest.raises(ValueError):
        _check_local_ports(component["artifact"], silo, component_port, silo_port, "transport-belt", recipe["ingredients"])


def test_foreign_belt_feeding_route_is_rejected():
    def belt(x, y, direction):
        return {"action_type": "place_ghost", "entity": "transport-belt",
                "position": {"x": x + 0.5, "y": y + 0.5}, "direction": direction}
    route = {"phases": [{"name": "route", "actions": [belt(1, 0, "east")]}]}
    external = {"phases": [{"name": "other", "actions": [belt(1, -1, "south")]}]}
    with pytest.raises(ValueError, match="another planned belt"):
        _reject_incoming_foreign_belts(route, [external], (0, 0))


def test_route_feeding_another_belt_is_rejected():
    route = {"phases": [{"name": "route", "actions": [{"action_type": "place_ghost", "entity": "transport-belt",
             "position": {"x": 1.5, "y": 0.5}, "direction": "east"}]}]}
    external = {"phases": [{"name": "other", "actions": [{"action_type": "place_ghost", "entity": "transport-belt",
                "position": {"x": 2.5, "y": 0.5}, "direction": "north"}]}]}
    with pytest.raises(ValueError, match="feeds another planned belt"):
        _reject_incoming_foreign_belts(route, [external], (0, 0), (9, 9))


def test_historical_three_row_demand_has_one_partial_silo_link(scenario):
    demand, component_options = scenario
    capture = _feeding_capture()
    catalog = json.loads(capture["catalog_bytes"])
    catalog["provenance"]["scope"].update(surface="synthetic", force="player")
    catalog["provenance"]["active_mods"] = {"synthetic": "0.0.0"}
    capture = _capture(catalog)
    capture.update(surface="synthetic", force="player", active_mods={"synthetic": "0.0.0"})
    artifact = build_silo_component_link(
        demand, component_options=component_options, silo_origin=[30, 15], placement_capture=capture,
        row_index=0, waypoints=[[20, 6], [20, 10], [33, 10]],
    )
    context = artifact["planning_context"]["data"]
    assert context["item"] == "processing-unit"
    assert len(context["component"]["item_output_ports"]) == 3
    assert context["fluid_topology_intent"] == context["component"]["fluid_topology_intent"]
    assert {port["item"] for port in context["silo"]["input_ports"]} == {
        "processing-unit", "low-density-structure", "rocket-fuel"}
    assert "other inputs" in artifact["unknowns"]["silo_input_delivery"]
    assert artifact["executable"] is False


def test_historical_rocket_fuel_row_connects_to_its_dedicated_silo_feed(scenario):
    demand, component_options = scenario
    component_options = component_options | {
        "recipe": "rocket-fuel", "rows": [{"origin": [0, 0], "machines": 10},
                                           {"origin": [0, 30], "machines": 10}],
        "max_machines": 20,
    }
    capture = _feeding_capture()
    catalog = json.loads(capture["catalog_bytes"])
    catalog["provenance"]["scope"].update(surface="synthetic", force="player")
    catalog["provenance"]["active_mods"] = {"synthetic": "0.0.0"}
    capture = _capture(catalog)
    capture.update(surface="synthetic", force="player", active_mods={"synthetic": "0.0.0"})
    artifact = build_silo_component_link(
        demand, component_options=component_options, silo_origin=[55, 15], placement_capture=capture,
        row_index=0, waypoints=[[35, 6], [35, 10], [61, 10]],
    )
    context = artifact["planning_context"]["data"]
    assert context["item"] == "rocket-fuel"
    assert context["target_tile"] == [62, 13]
    assert len(context["component"]["item_output_ports"]) == 2
    assert "machine_fluid_connection" in artifact["pending_checks"]
    assert artifact["executable"] is False


def test_historical_low_density_structure_row_connects_to_silo(scenario):
    _, fluid_options = scenario
    data = fluid_options["catalog_bytes"]
    catalog = json.loads(data)
    from core.catalog_demand import mod_set_sha256

    mods = [{"name": "synthetic", "version": "0.0.0"}]
    provenance = {"catalog_sha256": hashlib.sha256(data).hexdigest(), "game_version": "0.0.0",
                  "force": catalog["force"], "surface": "synthetic", "export_tick": catalog["tick"],
                  "export_command": "/synthetic", "mods": mods, "mod_set_sha256": mod_set_sha256(mods)}
    demand = plan_catalog_demand(
        data, provenance=provenance, demand={"low-density-structure": Fraction(1, 4)},
        boundaries=[{"name": name, "cap": 100} for name in ("copper-plate", "plastic-bar", "steel-plate")],
        preferences={"low-density-structure": "low-density-structure"},
    )
    options = {"catalog_bytes": data, "recipe": "low-density-structure",
               "machine_by_recipe": {"low-density-structure": "assembling-machine-2"},
               "line_spec": {"recipe": "low-density-structure", "machine_count": 5, "origin_x": 0,
                             "origin_y": 0, "belt_type": "transport-belt", "inserter_type": "fast-inserter",
                             "mining_feed": False, "feed_style": "chained", "direct_bus_ingredients": [],
                             "chained_ingredients": [0, 1, 2], "terminal_collector": False},
               "item_mapping": {"provenance": "synthetic", "tiles": {}, "entities": {name: name for name in (
                   "assembling-machine-2", "medium-electric-pole", "fast-inserter", "long-handed-inserter",
                   "transport-belt")}}}
    capture = _feeding_capture()
    capture_catalog = json.loads(capture["catalog_bytes"])
    capture_catalog["provenance"]["scope"].update(surface="synthetic", force="player")
    capture_catalog["provenance"]["active_mods"] = {"synthetic": "0.0.0"}
    capture = _capture(capture_catalog)
    capture.update(surface="synthetic", force="player", active_mods={"synthetic": "0.0.0"})
    artifact = build_silo_component_link(
        demand, component_kind="solid", component_options=options, silo_origin=[30, 15],
        placement_capture=capture, row_index=0, waypoints=[[20, 6], [20, 10], [30, 10]],
    )
    context = artifact["planning_context"]["data"]
    assert context["item"] == "low-density-structure"
    assert context["component"]["item_output_ports"][0]["nominal_items_per_second"] == "1/4"
    assert artifact["declared_item_bill"]["long-handed-inserter"] == 5
    assert artifact["executable"] is False
    assert "item_lane_supply" in artifact["pending_checks"]
    scope = ConstructionScope("synthetic", "a" * 64, "synthetic", "player")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="solid-link", throughput_output="rocket-part",
        throughput_target=1, throughput_unit="items/second")
    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]
    undersized = copy.deepcopy(options)
    undersized["line_spec"]["machine_count"] = 4
    with pytest.raises(ValueError, match="fewer machines"):
        build_silo_component_link(demand, component_kind="solid", component_options=undersized,
                                  silo_origin=[30, 15], placement_capture=capture, row_index=0,
                                  waypoints=[[20, 6], [20, 10], [30, 10]])
    incomplete = copy.deepcopy(options)
    incomplete["line_spec"]["chained_ingredients"] = [0, 1]
    with pytest.raises(ValueError, match="fully chained"):
        build_silo_component_link(demand, component_kind="solid", component_options=incomplete,
                                  silo_origin=[30, 15], placement_capture=capture, row_index=0,
                                  waypoints=[[20, 6], [20, 10], [30, 10]])


@pytest.fixture
def bundle_case():
    recipes = [
        _recipe("processing-unit", [("electronic-circuit", 20), ("advanced-circuit", 2),
                                    ("sulfuric-acid", 5, "fluid")], [("processing-unit", 1)]),
        _recipe("rocket-fuel", [("solid-fuel", 10), ("light-oil", 10, "fluid")], [("rocket-fuel", 1)]),
        _recipe("low-density-structure", [("copper-plate", 20), ("plastic-bar", 5), ("steel-plate", 2)],
                [("low-density-structure", 1)]),
        _recipe("rocket-part", [("rocket-fuel", 1), ("processing-unit", 1), ("low-density-structure", 1)],
                [("rocket-part", 1)]),
    ]
    recipes[0].update(category="crafting-with-fluid", energy_ticks=600)
    recipes[1].update(category="crafting-with-fluid", energy_ticks=900)
    recipes[2].update(energy_ticks=900)
    catalog = _catalog(*recipes, force="test-force")
    catalog["machines"] = [{"name": "assembling-machine-2", "type": "assembling-machine", "ingredient_count": 3,
                            "crafting_speed": 0.75, "categories": ["crafting", "crafting-with-fluid"]}]
    data = _bytes(catalog)
    provenance = _provenance(data, catalog, mods=[{"name": "base", "version": "synthetic"}])
    item_mapping = {"provenance": "synthetic", "tiles": {}, "entities": {name: name for name in (
        "pipe", "pipe-to-ground", "medium-electric-pole", "assembling-machine-2", "transport-belt",
        "fast-inserter", "long-handed-inserter")}}
    capacities = {"lane_items_per_second": Fraction(15, 2), "input_inserter_items_per_second": 4,
                  "output_inserter_items_per_second": 4}
    requests = []
    for name, origin, boundaries, approach in [
        ("rocket-fuel", 0, ["solid-fuel", "light-oil"], 66),
        ("processing-unit", 30, ["electronic-circuit", "advanced-circuit", "sulfuric-acid"], 63),
        ("low-density-structure", 60, ["copper-plate", "plastic-bar", "steel-plate"], 60),
    ]:
        rate = Fraction(3, 8) if name == "processing-unit" else Fraction(1, 4)
        demand = plan_catalog_demand(data, provenance=provenance, demand={name: rate},
                                     boundaries=[{"name": item, "cap": 100} for item in boundaries])
        if name == "low-density-structure":
            options = {"catalog_bytes": data, "recipe": name, "machine_by_recipe": {name: "assembling-machine-2"},
                       "item_mapping": item_mapping, "line_spec": {
                           "recipe": name, "machine_count": 5, "origin_x": 0, "origin_y": origin,
                           "belt_type": "transport-belt", "inserter_type": "fast-inserter", "mining_feed": False,
                           "feed_style": "chained", "direct_bus_ingredients": [], "chained_ingredients": [0, 1, 2],
                           "terminal_collector": False}}
            kind = "solid"
        else:
            options = {"catalog_bytes": data, "recipe": name, "machine_by_recipe": {name: "assembling-machine-2"},
                       "rows": [{"origin": [0, origin], "machines": 5}], "belt_type": "transport-belt",
                       "inserter_type": "fast-inserter", "capacities": capacities, "item_mapping": item_mapping,
                       "max_machines": 5}
            kind = "fluid"
        requests.append({"demand": demand, "component_options": options, "component_kind": kind,
                         "row_index": 0, "waypoints": [[approach, origin + 6]]})
    return {"components": requests, "silo_origin": [60, 100], "placement_capture": _feeding_capture()}


def test_three_components_share_one_silo_and_bill(bundle_case):
    artifact = build_silo_component_bundle(**bundle_case)
    context = artifact["planning_context"]["data"]
    assert {link["item"] for link in context["links"]} == {"rocket-fuel", "processing-unit", "low-density-structure"}
    assert sum(action["entity"] == "rocket-silo" for action in actions(artifact["plan"])) == 1
    assert sum(action["entity"] == "assembling-machine-2" for action in actions(artifact["plan"])) == 15
    assert artifact["executable"] is False
    assert "shared capacity" in artifact["unknowns"]["silo_input_delivery"]
    assert context["input_capacity"]["status"] == "conditional"
    assert context["input_capacity"]["unaccounted_ports"]
    assert context["feed_rates"]["status"] == "target_unknown"
    intent = context["fluid_topology_intent"]
    component_intents = [link["fluid_topology_intent"] for link in context["links"] if "fluid_topology_intent" in link]
    assert intent["segments"] == [segment for part in component_intents for segment in part["segments"]]
    assert intent["permitted_attachments"] == []
    assert context["feed_rates"]["connected_nominal_part_rate"] == "1/4"
    scope = ConstructionScope("synthetic", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="bundle", throughput_output="rocket-part",
        throughput_target=1, throughput_unit="items/second")
    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]
    artifact["planning_context"]["data"]["fluid_topology_intent"]["segments"][0]["fluid"] = "changed"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="silo", transaction_id="bundle", throughput_output="rocket-part",
            throughput_target=1, throughput_unit="items/second", expected_plan_hash=bound.binding_sha256)


def test_bundle_rejects_missing_or_duplicate_ingredient(bundle_case):
    with pytest.raises(ValueError, match="one component per silo ingredient"):
        build_silo_component_bundle(**(bundle_case | {"components": bundle_case["components"][:2]}))
    duplicate = [bundle_case["components"][0]] * 3
    with pytest.raises(ValueError, match="one component per silo ingredient"):
        build_silo_component_bundle(**(bundle_case | {"components": duplicate}))


def test_bundle_rejects_route_collisions(bundle_case):
    options = copy.deepcopy(bundle_case)
    options["components"][1]["component_options"]["rows"][0]["origin"] = [0, 0]
    options["components"][1]["waypoints"] = [[63, 6]]
    with pytest.raises(ValueError, match="collision"):
        build_silo_component_bundle(**options)


def test_bundle_rejects_cross_capture_provenance_and_item_mapping(bundle_case):
    options = copy.deepcopy(bundle_case)
    demand = options["components"][1]["demand"]
    options["components"][1]["demand"] = replace(
        demand, provenance=replace(demand.provenance, export_command="/different"))
    with pytest.raises(ValueError, match="share catalog provenance"):
        build_silo_component_bundle(**options)
    options = copy.deepcopy(bundle_case)
    options["components"][1]["component_options"]["item_mapping"]["entities"]["transport-belt"] = "other"
    with pytest.raises(ValueError, match="item mappings differ"):
        build_silo_component_bundle(**options)


def test_bundle_rejects_untracked_fluid_inlet_routing(bundle_case):
    options = copy.deepcopy(bundle_case)
    options["components"][0]["component_options"]["fluid_inlets"] = {"light-oil": [-10, -2]}
    with pytest.raises(ValueError, match="fluid inlet links are not supported"):
        build_silo_component_bundle(**options)


def test_shared_input_capacity_counts_each_row_and_existing_allocations(bundle_case):
    processing = bundle_case["components"][1]
    provenance = Provenance("synthetic-observation")
    material = Material("electronic-circuit", "item")
    capacity = SharedCapacity("shared-circuit", material, Fraction(16),
                              (ExistingAllocation("prior", Fraction(2), provenance),), provenance)
    result = _bundle_capacity_accounting([processing, processing], [capacity])
    circuit = next(source for source in result["sources"] if source["source_id"] == "shared-circuit")
    assert circuit["proposed_demand"] == "15"
    assert circuit["shortage"] == "1" and circuit["status"] == "shortage"
    assert result["status"] == "shortage"
    assert len([port for port in result["unaccounted_ports"] if "electronic-circuit" in port]) == 0
    two_rows = copy.deepcopy(processing)
    two_rows["component_options"]["rows"].append({"origin": [0, 90], "machines": 5})
    unknown_allocations = SharedCapacity("shared-circuit", material, Fraction(20), None, provenance)
    result = _bundle_capacity_accounting([two_rows], [unknown_allocations])
    assert result["sources"][0]["proposed_demand"] == "15"
    assert result["sources"][0]["status"] == "conditional"
    assert result["sources"][0]["existing_allocations"] is None


def test_bundle_capacity_is_conditional_or_short_and_hash_bound(bundle_case):
    provenance = Provenance("synthetic-observation")
    material = Material("electronic-circuit", "item")
    unknown = SharedCapacity("circuits", material, None, (), provenance)
    artifact = build_silo_component_bundle(**bundle_case, capacities=[unknown])
    capacity = artifact["planning_context"]["data"]["input_capacity"]
    assert capacity["status"] == "conditional"
    assert capacity["sources"][0]["proposed_demand"] == "15/2"
    assert capacity["sources"][0]["shortage"] is None
    short = SharedCapacity("circuits", material, 7, (), provenance)
    artifact = build_silo_component_bundle(**bundle_case, capacities=[short])
    capacity = artifact["planning_context"]["data"]["input_capacity"]
    assert capacity["status"] == "shortage" and capacity["sources"][0]["shortage"] == "1/2"
    assert artifact["executable"] is False
    scope = ConstructionScope("synthetic", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="capacity", throughput_output="rocket-part",
        throughput_target=1, throughput_unit="items/second")
    capacity["sources"][0]["shortage"] = "0"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="silo", transaction_id="capacity", throughput_output="rocket-part",
            throughput_target=1, throughput_unit="items/second", expected_plan_hash=bound.binding_sha256)


def test_bundle_capacity_rejects_ambiguous_and_unused_sources(bundle_case):
    provenance = Provenance("synthetic")
    material = Material("electronic-circuit", "item")
    source = SharedCapacity("first", material, 20, (), provenance)
    another = SharedCapacity("second", material, 20, (), provenance)
    with pytest.raises(ValueError, match="one explicit shared source"):
        build_silo_component_bundle(**bundle_case, capacities=[source, another])
    unused = SharedCapacity("unused", Material("water", "fluid"), 20, (), provenance)
    with pytest.raises(ValueError, match="unused"):
        build_silo_component_bundle(**bundle_case, capacities=[unused])


def test_feed_target_reports_component_shortages_without_claiming_delivery(bundle_case):
    artifact = build_silo_component_bundle(**bundle_case, target_parts_per_second=Fraction(3, 10))
    result = artifact["planning_context"]["data"]["feed_rates"]
    feeds = {feed["item"]: feed for feed in result["feeds"]}
    assert result["status"] == "below_target"
    assert result["connected_nominal_part_rate"] == "1/4"
    assert feeds["rocket-fuel"]["nominal_shortfall_per_second"] == "1/20"
    assert feeds["low-density-structure"]["nominal_shortfall_per_second"] == "1/20"
    assert feeds["processing-unit"]["nominal_shortfall_per_second"] == "0"
    assert "silo_feed_throughput" in artifact["pending_checks"]
    assert artifact["executable"] is False
    result = build_silo_component_bundle(**bundle_case, target_parts_per_second=Fraction(1, 4))["planning_context"]["data"]["feed_rates"]
    assert result["status"] == "nominal_meets_target"
    assert "not measured throughput" in result["basis"]


def test_feed_rates_do_not_credit_unconnected_rows(bundle_case):
    artifact = build_silo_component_bundle(**bundle_case)
    contexts = artifact["planning_context"]["data"]["links"]
    fuel = next(context for context in contexts if context["item"] == "rocket-fuel")
    extra = copy.deepcopy(fuel["component"]["item_output_ports"][0])
    extra["nominal_items_per_second"] = "3"
    fuel["component"]["item_output_ports"].append(extra)
    result = _bundle_feed_accounting(contexts, Fraction(3, 10))
    fuel_feed = next(feed for feed in result["feeds"] if feed["item"] == "rocket-fuel")
    assert fuel_feed["connected_nominal_per_second"] == "1/4"
    assert fuel_feed["unconnected_row_count"] == 1 and fuel_feed["unconnected_nominal_per_second"] == "3"
    assert fuel_feed["nominal_shortfall_per_second"] == "1/20"


@pytest.mark.parametrize("target", [True, 0, -1, float("nan"), "1"])
def test_invalid_silo_feed_target_fails_closed(bundle_case, target):
    with pytest.raises(ValueError, match="rocket-part target"):
        build_silo_component_bundle(**bundle_case, target_parts_per_second=target)


def test_silo_feed_target_is_bound(bundle_case):
    artifact = build_silo_component_bundle(**bundle_case, target_parts_per_second=Fraction(1, 4))
    scope = ConstructionScope("synthetic", "a" * 64, "test-surface", "test-force")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="silo", transaction_id="feed-target", throughput_output="rocket-part",
        throughput_target=Fraction(1, 4), throughput_unit="items/second")
    artifact["planning_context"]["data"]["feed_rates"]["target_rocket_parts_per_second"] = "1/2"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="silo", transaction_id="feed-target", throughput_output="rocket-part",
            throughput_target=Fraction(1, 4), throughput_unit="items/second", expected_plan_hash=bound.binding_sha256)
