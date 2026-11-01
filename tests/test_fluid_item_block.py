# Path: tests/test_fluid_item_block.py
# Purpose: Exercise a silo component demand through bounded ghost-only fluid item rows.

from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.optimizer_artifact_binding import ArtifactBindingError, bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from core.rocket_silo_supply import plan_silo_supply
import planners.fluid_item_block as fluid_item_block
from planners.fluid_item_block import build_fluid_item_block
from tests.test_rocket_silo_observation import _available, _success
from tests.test_catalog_demand import _bytes, _catalog, _provenance, _recipe


@pytest.fixture
def scenario():
    path = Path("data/factorio-2.1.21/probes/deterministic/server/script-output/factorio_mod/recipe_catalogs/recipe_catalog_243688.json")
    if not path.is_file():
        pytest.skip("historical catalog unavailable")
    data = path.read_bytes()
    catalog = json.loads(data)
    mods = [{"name": "synthetic", "version": "0.0.0"}]
    provenance = {"catalog_sha256": hashlib.sha256(data).hexdigest(), "game_version": "0.0.0",
                  "force": catalog["force"], "surface": "synthetic", "export_tick": catalog["tick"],
                  "export_command": "/synthetic", "mods": mods, "mod_set_sha256": mod_set_sha256(mods)}
    observation = _success()
    observation.update(surface="synthetic", tick=catalog["tick"] + 1)
    observation["requested"]["unit_number"] = _available(7)
    observation["silos"][0]["recipe"] = _available("rocket-part")
    names = ("rocket-fuel", "processing-unit", "low-density-structure")
    boundaries = [{"name": name, "cap": 100} for name in (
        "light-oil", "solid-fuel", "advanced-circuit", "electronic-circuit",
        "sulfuric-acid", "copper-plate", "plastic-bar", "steel-plate")]
    supply = plan_silo_supply(data, provenance=provenance, observation=observation, unit_number=7,
                              horizon_ticks=3600, boundaries=boundaries,
                              preferences={name: name for name in names})
    args = {"catalog_bytes": data, "recipe": "processing-unit",
            "machine_by_recipe": {name: "assembling-machine-2" for name in names},
            "rows": [{"origin": [0, 0], "machines": 5}, {"origin": [0, 30], "machines": 5},
                     {"origin": [0, 60], "machines": 3}],
            "belt_type": "transport-belt", "inserter_type": "fast-inserter",
            "capacities": {"lane_items_per_second": Fraction(15, 2),
                           "input_inserter_items_per_second": 4, "output_inserter_items_per_second": 4},
            "item_mapping": {"provenance": "synthetic test mapping", "tiles": {},
                             "entities": {name: name for name in (
                                 "pipe", "pipe-to-ground", "medium-electric-pole", "assembling-machine-2",
                                 "transport-belt", "fast-inserter")}},
            "max_machines": 13}
    return supply.demand, args


def test_silo_demand_reaches_capacity_checked_recipe_bound_ghosts(scenario):
    demand, args = scenario
    candidate = build_fluid_item_block(demand, **args)
    assert candidate["machine_fact"].machine_count == 13
    assert [row["input_lanes_per_second"]["electronic-circuit"] for row in candidate["rows"]] == [
        "15/2", "15/2", "9/2"]
    assert len(candidate["fluid_ports"]) == 3
    outputs = candidate["item_output_ports"]
    assert [port["item"] for port in outputs] == ["processing-unit"] * 3
    assert [port["nominal_items_per_second"] for port in outputs] == ["3/8", "3/8", "9/40"]
    artifact = candidate["artifact"]
    assert artifact["planning_context"]["data"]["item_output_ports"] == outputs
    intent = artifact["planning_context"]["data"]["fluid_topology_intent"]
    assert intent["version"] == "fluid-topology-intent-v1"
    assert intent["permitted_attachments"] == []
    assert {segment["fluid"] for segment in intent["segments"]} == {"sulfuric-acid"}
    for port in outputs:
        belts = [action for phase in artifact["plan"]["phases"] for action in phase["actions"]
                 if action.get("entity") == args["belt_type"]
                 and action["position"]["y"] == port["belt_position"]["y"]]
        assert port["belt_position"]["x"] == max(action["position"]["x"] for action in belts)
        assert port["direction"] == "east" and port["lane"] == "unverified"
    assert artifact["executable"] is False
    assert artifact["declared_item_bill"] == {
        "assembling-machine-2": 13, "fast-inserter": 26, "medium-electric-pole": 26,
        "pipe": 39, "pipe-to-ground": 26, "transport-belt": 87,
    }
    scope = ConstructionScope("synthetic", "a" * 64, "synthetic", "player")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="synthetic", transaction_id="processing",
        throughput_output="processing-unit", throughput_target=Fraction(29, 30), throughput_unit="items/second",
    )
    assert len([entity for entity in bound.plan.required_entities if entity.recipe == "processing-unit"]) == 13
    artifact["planning_context"]["data"]["item_output_ports"][0]["belt_position"]["x"] += 1
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="synthetic", transaction_id="processing",
            throughput_output="processing-unit", throughput_target=Fraction(29, 30), throughput_unit="items/second",
            expected_plan_hash=bound.binding_sha256,
        )
    artifact["planning_context"]["data"]["item_output_ports"][0]["belt_position"]["x"] -= 1
    artifact["planning_context"]["data"]["lane_items_per_second"] = "999"
    with pytest.raises(ArtifactBindingError):
        bind_artifact_to_construction_plan(
            artifact, scope=scope, project_id="synthetic", transaction_id="processing",
            throughput_output="processing-unit", throughput_target=Fraction(29, 30), throughput_unit="items/second",
            expected_plan_hash=bound.binding_sha256,
        )


def test_declared_inlet_routes_all_rows_and_is_bound(scenario):
    demand, args = scenario
    candidate = build_fluid_item_block(demand, **args, fluid_inlets={"sulfuric-acid": [-10, -3]})
    artifact = candidate["artifact"]
    route = next(phase for phase in artifact["plan"]["phases"] if phase["name"] == "fluid_link_sulfuric-acid")
    tiles = {(int(action["position"]["x"] - 0.5), int(action["position"]["y"] - 0.5))
             for action in route["actions"]}
    pending = [(-10, -3)]
    reachable = set()
    while pending:
        point = pending.pop()
        if point in reachable or point not in tiles:
            continue
        reachable.add(point)
        pending.extend((point[0] + dx, point[1] + dy) for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))
    assert {tuple(port["attachment"]["attach"]) for port in candidate["fluid_ports"]} <= reachable
    assert artifact["declared_item_bill"]["pipe"] > 39
    assert "source identity and capacity are unverified" in artifact["unknowns"]["fluid_supply"]
    scope = ConstructionScope("synthetic", "a" * 64, "synthetic", "player")
    bound = bind_artifact_to_construction_plan(
        artifact, scope=scope, project_id="synthetic", transaction_id="processing",
        throughput_output="processing-unit", throughput_target=Fraction(29, 30), throughput_unit="items/second",
    )
    assert dict(bound.plan.required_materials)["pipe"] == artifact["declared_item_bill"]["pipe"]


@pytest.mark.parametrize("damage", ["missing", "missing_tail", "direction"])
def test_malformed_output_belt_cannot_be_exposed_as_port(scenario, monkeypatch, damage):
    demand, args = scenario
    generate = fluid_item_block.generate_fluid_machine_row

    def damaged_row(*row_args, **row_kwargs):
        plan = generate(*row_args, **row_kwargs)
        actions = plan["phases"][1]["actions"]
        output_belts = [action for action in actions if action.get("entity") == "transport-belt"
                        and action["position"]["y"] == row_args[3] + 6.5]
        if damage == "missing":
            actions.remove(output_belts[1])
        elif damage == "missing_tail":
            actions.remove(output_belts[-1])
        else:
            output_belts[0]["direction"] = "west"
        return plan

    monkeypatch.setattr(fluid_item_block, "generate_fluid_machine_row", damaged_row)
    with pytest.raises(ValueError, match="contiguous eastbound"):
        build_fluid_item_block(demand, **args)


@pytest.mark.parametrize("inlets, message", [
    ({}, "every required fluid"),
    ({"water": [0, 0]}, "every required fluid"),
    ({"sulfuric-acid": [True, 0]}, "bounded integers"),
    ({"sulfuric-acid": [-200, 0]}, "span"),
    ({"sulfuric-acid": [2, 2]}, "overlaps"),
])
def test_invalid_fluid_inlets_fail_closed(scenario, inlets, message):
    demand, args = scenario
    with pytest.raises(ValueError, match=message):
        build_fluid_item_block(demand, **args, fluid_inlets=inlets)


@pytest.mark.parametrize("rows, message", [
    ([{"origin": [0, 0], "machines": 13}], "lane capacity"),
    ([{"origin": [0, 0], "machines": 5}, {"origin": [0, 30], "machines": 5}], "fewer machines"),
    ([{"origin": [0, 0], "machines": 5}, {"origin": [0, 0], "machines": 5},
      {"origin": [0, 60], "machines": 3}], "collision"),
    ([{"origin": [0, 0], "machines": 5}, {"origin": [0, 30], "machines": 5},
      {"origin": [0, 60], "machines": 4}], "max_machines"),
])
def test_unsafe_row_configurations_fail_closed(scenario, rows, message):
    demand, args = scenario
    with pytest.raises(ValueError, match=message):
        build_fluid_item_block(demand, **(args | {"rows": rows}))


@pytest.mark.parametrize("field,value", [
    ("lane_items_per_second", 0), ("lane_items_per_second", True),
    ("lane_items_per_second", 8),
    ("input_inserter_items_per_second", 1), ("output_inserter_items_per_second", Fraction(1, 100)),
])
def test_missing_or_insufficient_throughput_fails_closed(scenario, field, value):
    demand, args = scenario
    capacities = dict(args["capacities"], **{field: value})
    with pytest.raises(ValueError, match="capacity"):
        build_fluid_item_block(demand, **(args | {"capacities": capacities}))


def test_forged_balance_and_changed_catalog_fail_closed(scenario):
    demand, args = scenario
    balance = replace(demand.balance, rates=tuple(rate * 2 for rate in demand.balance.rates))
    with pytest.raises(ValueError, match="independently recomputed"):
        build_fluid_item_block(replace(demand, balance=balance), **args)
    with pytest.raises(ValueError, match="catalog bytes"):
        build_fluid_item_block(demand, **(args | {"catalog_bytes": args["catalog_bytes"] + b" "}))


def test_wrong_machine_and_unsupported_geometry_fail_closed(scenario):
    demand, args = scenario
    mapping = dict(args["machine_by_recipe"], **{"processing-unit": "assembling-machine-3"})
    with pytest.raises(ValueError, match="geometry"):
        build_fluid_item_block(demand, **(args | {"machine_by_recipe": mapping}))
    with pytest.raises(ValueError, match="geometry"):
        build_fluid_item_block(demand, **(args | {"recipe": "unsupported-geometry"}))


def test_historical_rocket_fuel_demand_builds_fluid_item_rows(scenario):
    demand, args = scenario
    candidate = build_fluid_item_block(demand, **(args | {
        "recipe": "rocket-fuel",
        "rows": [{"origin": [0, 0], "machines": 10}, {"origin": [0, 30], "machines": 10}],
        "max_machines": 20,
    }))
    assert candidate["machine_fact"].machine_count == 20
    assert [port["item"] for port in candidate["item_output_ports"]] == ["rocket-fuel", "rocket-fuel"]
    assert [row["input_lanes_per_second"]["solid-fuel"] for row in candidate["rows"]] == ["5", "5"]
    assert all(port["fluid"] == "light-oil" for port in candidate["fluid_ports"])
    assert "machine_fluid_connection" in candidate["artifact"]["pending_checks"]


def test_synthetic_catalog_does_not_require_historical_fixture():
    recipe = _recipe("processing-unit", [("electronic-circuit", 20), ("advanced-circuit", 2),
                                         ("sulfuric-acid", 5, "fluid")], [("processing-unit", 1)])
    recipe.update(category="crafting-with-fluid", energy_ticks=600)
    catalog = _catalog(recipe)
    catalog["machines"] = [{"name": "assembling-machine-2", "type": "assembling-machine",
                            "ingredient_count": 3, "crafting_speed": 0.75,
                            "categories": ["crafting-with-fluid"]}]
    data = _bytes(catalog)
    boundaries = [{"name": name, "cap": 100} for name in
                  ("electronic-circuit", "advanced-circuit", "sulfuric-acid")]
    demand = plan_catalog_demand(data, provenance=_provenance(data, catalog),
                                 demand={"processing-unit": Fraction(3, 4)}, boundaries=boundaries)
    result = build_fluid_item_block(
        demand, catalog_bytes=data, recipe="processing-unit", machine_by_recipe={"processing-unit": "assembling-machine-2"},
        rows=[{"origin": [0, 0], "machines": 5}, {"origin": [0, 30], "machines": 5}],
        belt_type="transport-belt", inserter_type="fast-inserter",
        capacities={"lane_items_per_second": 7.5, "input_inserter_items_per_second": 4,
                    "output_inserter_items_per_second": 4},
        item_mapping={"provenance": "synthetic", "tiles": {}, "entities": {name: name for name in (
            "pipe", "pipe-to-ground", "medium-electric-pole", "assembling-machine-2", "transport-belt", "fast-inserter")}},
        max_machines=10,
    )
    assert result["artifact"]["declared_item_bill"]["assembling-machine-2"] == 10
    assert [port["belt_position"] for port in result["item_output_ports"]] == [
        {"x": 14.5, "y": 6.5}, {"x": 14.5, "y": 36.5}]
    assert [port["nominal_items_per_second"] for port in result["item_output_ports"]] == ["3/8", "3/8"]


def test_catalog_recipe_mismatch_is_rejected_even_with_recomputed_balance(scenario):
    demand, args = scenario
    catalog = json.loads(args["catalog_bytes"])
    recipe = next(entry for entry in catalog["recipes"] if entry["name"] == "processing-unit")
    recipe["ingredients"][0]["amount"] += 1
    data = json.dumps(catalog, sort_keys=True).encode()
    provenance = asdict(demand.provenance)
    provenance["catalog_sha256"] = hashlib.sha256(data).hexdigest()
    boundaries = [{"name": entry.name, **({"rate": entry.rate} if entry.rate is not None else {"cap": entry.cap})}
                  for entry in demand.boundaries]
    preferences = {entry.material: entry.recipe for entry in demand.selections}
    changed = plan_catalog_demand(data, provenance=provenance, demand=dict(demand.demand),
                                  boundaries=boundaries, preferences=preferences)
    with pytest.raises(ValueError, match="differs from the fluid row geometry"):
        build_fluid_item_block(changed, **(args | {"catalog_bytes": data}))
