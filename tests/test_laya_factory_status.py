# Path: tests/test_laya_factory_status.py | Purpose: Protect Laya's status board: goal demand, separated machine conditions, causal chain and unknowns.

import pytest

from orchestrator.laya_state import validate_state
from training.laya_factory_status import (
    FactoryStatusError, apply_to_state, bottleneck_chain, compile_status, goal_demand, goal_met, production_recipes,
)
from training.laya_saved_world import decision_state


def recipe(ingredients, ticks, amount=1):
    return {"ingredients": [{"name": n, "amount": a} for n, a in ingredients],
            "product_amount": amount, "energy_ticks": ticks}


RECIPES = {
    "iron-plate": recipe([("iron-ore", 1)], 192), "copper-plate": recipe([("copper-ore", 1)], 192),
    "copper-cable": recipe([("copper-plate", 1)], 30, 2),
    "electronic-circuit": recipe([("iron-plate", 1), ("copper-cable", 3)], 30),
    "iron-gear-wheel": recipe([("iron-plate", 2)], 30),
    "assembling-machine-2": recipe([("iron-plate", 20)], 30),
}


def machine(name, recipe_name, status, x=0):
    return {"name": name, "recipe": recipe_name, "status": status, "position": {"x": x, "y": 0}}


def world(machines, reserved=None, items=None):
    return {"tick": 600, "recipes": RECIPES, "machines": machines, "pending_ghosts": 1,
            "networks": {"1": {"items": items or {"iron-plate": 50}, "reserved": reserved or {},
                               "construction_robots": 50}},
            "ports": [{"position": {"x": 0, "y": 0}, "network": "1", "energy": 1, "construction_radius": 55,
                       "logistic_radius": 25}], "poles": [], "production": {}, "consumption": {}}


def flows(**rates):
    return {"window_ticks": 600, "production": {k.replace("_", "-"): {"per_second": v} for k, v in rates.items()},
            "consumption": {}}


def test_goal_demand_expands_recipe_ratios_to_raw_resources():
    demand = goal_demand({"electronic-circuit": 1.0}, RECIPES)
    assert demand["copper-cable"]["required_per_s"] == pytest.approx(3.0)
    assert demand["copper-plate"]["required_per_s"] == pytest.approx(1.5)
    assert demand["iron-ore"]["required_per_s"] == pytest.approx(1.0)
    assert demand["copper-cable"]["consumers"] == ["electronic-circuit"]
    with pytest.raises(FactoryStatusError, match="positive"):
        goal_demand({"electronic-circuit": 0}, RECIPES)


def test_board_separates_starved_undersized_blocked_unpowered_and_locked_needs():
    facts = world([
        machine("assembling-machine-1", "electronic-circuit", "item_ingredient_shortage"),
        machine("assembling-machine-1", "electronic-circuit", "item_ingredient_shortage", 4),
        machine("assembling-machine-1", "copper-cable", "working"),
        machine("electric-furnace", "copper-plate", "full_output"),
        machine("electric-furnace", "iron-plate", "no_power"),
    ])
    board = compile_status(facts, {"electronic-circuit": 1.0, "plastic-bar": 0.5},
                           flows(electronic_circuit=0.2, copper_cable=1.0, copper_plate=0.5))
    needs = {n["item"]: n for n in board["needs"]}
    assert needs["electronic-circuit"]["condition"] == "input_starved"
    assert needs["electronic-circuit"]["short_inputs"] == ["copper-cable", "iron-plate"]
    assert needs["copper-cable"]["condition"] == "under_capacity"
    assert needs["copper-cable"]["machines_needed"]["assembling-machine-1"] == 2
    assert needs["copper-plate"]["condition"] == "output_blocked"
    assert needs["iron-plate"]["condition"] == "power_limited"
    assert needs["plastic-bar"]["condition"] == "recipe_unavailable"
    assert needs["iron-ore"]["condition"] == "unmeasured" and needs["iron-ore"]["gap_per_s"] is None
    # Goal items come first; order is goal-tree depth, never gap size.
    assert [n["depth"] for n in board["needs"]] == sorted(n["depth"] for n in board["needs"])
    upgrade = needs["copper-cable"]["upgrade"]
    assert upgrade["to"] == "assembling-machine-2" and upgrade["target_recipe_enabled"]
    assert upgrade["gain_per_machine_per_s"] == pytest.approx(1.0)
    assert bottleneck_chain(board)[0].startswith("electronic-circuit:input_starved <- copper-cable:under_capacity")
    assert bottleneck_chain(board)[1] == "plastic-bar:recipe_unavailable"  # One chain per unmet goal.


def test_unbuilt_and_satisfied_needs_and_unknown_measurement_stays_unknown():
    facts = world([machine("assembling-machine-2", "iron-gear-wheel", "working")])
    assert compile_status(facts, {"iron-gear-wheel": 1.0}, flows(iron_gear_wheel=1.0))["needs"][0]["condition"] == "satisfied"
    board = compile_status(world([]), {"iron-gear-wheel": 1.0})
    gear = board["needs"][0]
    assert gear["condition"] == "unbuilt" and gear["measured_per_s"] is None and gear["capacity_per_s"] is None
    assert gear["upgrade"] is None


def test_board_fills_a_valid_decision_state_without_inventing_rates():
    facts = world([machine("assembling-machine-1", "iron-gear-wheel", "working")],
                  reserved={"inserter": 4}, items={"iron-plate": 50, "inserter": 1})
    state = decision_state(facts, [{"option_id": "wait.300", "family": "wait", "summary": "Wait", "wait_ticks": 300}],
                           "test", {"model_id": "test", "model_hash": "sha256:" + "0" * 64}, "Make gears")
    unmeasured = apply_to_state(state, compile_status(facts, {"iron-gear-wheel": 2.0}))
    validate_state(unmeasured)
    assert unmeasured["acceptance_gap"]["current"] is None and unmeasured["goal_stack"][0]["gap"] is None
    assert unmeasured["state_digest"]["stock"]["top_shortages"] == [{"item": "inserter", "amount": 3.0}]

    measured = apply_to_state(state, compile_status(facts, {"iron-gear-wheel": 2.0}, flows(iron_gear_wheel=0.5)))
    validate_state(measured)
    assert measured["acceptance_gap"]["remaining"] == pytest.approx(1.5)
    assert measured["state_digest"]["production"]["bottleneck_hint"].startswith("iron-gear-wheel:under_capacity")
    assert measured["observation_digest"]["factory_status"]["needs"][0]["item"] == "iron-gear-wheel"


def test_runner_built_machines_that_never_ran_are_idle_not_unbuilt():
    board = compile_status(world([]), {"iron-gear-wheel": 1.0}, built={"iron-gear-wheel": 2})
    gear = board["needs"][0]
    assert gear["condition"] == "built_idle" and gear["built"] == 2
    assert compile_status(world([]), {"iron-gear-wheel": 1.0})["needs"][0]["condition"] == "unbuilt"


def test_short_construction_items_expose_their_input_chain_without_becoming_goals():
    recipes = {**RECIPES, "medium-electric-pole": recipe([("steel-plate", 2), ("iron-plate", 2)], 30),
               "steel-plate": recipe([("iron-plate", 5)], 960)}
    facts = {**world([]), "recipes": recipes}
    board = compile_status(facts, {"iron-gear-wheel": 1.0}, stock={"medium-electric-pole": 0.05})
    needs = {n["item"]: n for n in board["needs"]}
    assert board["targets"] == {"iron-gear-wheel": 1.0} and "steel-plate" in needs
    assert any(chain.startswith("medium-electric-pole:unbuilt") for chain in bottleneck_chain(board))


def test_stock_demand_never_raises_the_phase_goal():
    from training.laya_mission import phase_reached
    recipes = {**RECIPES, "medium-electric-pole": recipe([("steel-plate", 2), ("iron-plate", 2)], 30),
               "steel-plate": recipe([("iron-plate", 5)], 960)}
    facts = {**world([machine("electric-furnace", "iron-plate", "working")]), "recipes": recipes}
    measured = flows(iron_plate=0.6)
    board = compile_status(facts, {"iron-plate": 0.5}, measured, stock={"medium-electric-pole": 0.5})
    iron = next(n for n in board["needs"] if n["item"] == "iron-plate")
    assert iron["required_per_s"] > 1.0 and iron["goal_required_per_s"] == 0.5
    assert phase_reached(board)  # 0.6/s meets the 0.5/s goal even though stock wants more iron.


def test_compact_rows_mark_stock_chain_items_with_a_zero_goal():
    from training.laya_factory_status import compact_status
    recipes = {**RECIPES, "medium-electric-pole": recipe([("steel-plate", 2), ("iron-plate", 2)], 30),
               "steel-plate": recipe([("iron-plate", 5)], 960)}
    board = compile_status({**world([]), "recipes": recipes}, {"iron-plate": 0.5}, stock={"medium-electric-pole": 0.5})
    rows = {r["item"]: r for r in compact_status(board, limit=20)["needs"]}
    assert rows["steel-plate"]["goal"] == 0.0 and rows["iron-plate"]["goal"] == 0.5


def test_full_mall_with_stock_is_satisfied_but_empty_blocked_mall_is_not():
    blocked = [machine("electric-furnace", "copper-plate", "full_output")]
    full = compile_status(world(blocked, items={"copper-plate": 4800}), {"copper-plate": 0.25}, flows(copper_plate=0.0))
    empty = compile_status(world(blocked, items={"copper-plate": 10}), {"copper-plate": 0.25}, flows(copper_plate=0.0))
    assert full["needs"][0]["condition"] == "satisfied"
    assert empty["needs"][0]["condition"] == "output_blocked"
    assert bottleneck_chain(full) == []
    assert not goal_met(full["needs"][0])  # Phase goals still judge the measured rate.


def test_a_cell_held_off_by_its_own_gate_reads_as_stocked_not_idle():
    # A capped gear chest switches its assembler off; that is a full mall, not a broken one.
    gated = [machine("assembling-machine-1", "iron-gear-wheel", "disabled_by_control_behavior")]
    board = compile_status(world(gated, items={"iron-gear-wheel": 400}), {"iron-gear-wheel": 0.25},
                           flows(iron_gear_wheel=0.0))
    assert board["needs"][0]["condition"] == "satisfied"


def fluid_recipe(ingredients, products, ticks, machine):
    """facts["fluid_recipes"] entry as the saved-world survey exports it (vanilla 2.0 amounts)."""
    def parts(entries):
        return [{"name": n, "type": t, "amount": a} for n, t, a in entries]
    return {"ingredients": parts(ingredients), "products": parts(products), "energy_ticks": ticks,
            "machine_speeds": {machine: 1.0}}


FLUID_RECIPES = {
    "plastic-bar": fluid_recipe([("petroleum-gas", "fluid", 20), ("coal", "item", 1)], [("plastic-bar", "item", 2)],
                                60, "chemical-plant"),
    "basic-oil-processing": fluid_recipe([("crude-oil", "fluid", 100)], [("petroleum-gas", "fluid", 45)],
                                         300, "oil-refinery"),
    "advanced-oil-processing": fluid_recipe(
        [("crude-oil", "fluid", 100), ("water", "fluid", 50)],
        [("heavy-oil", "fluid", 25), ("light-oil", "fluid", 45), ("petroleum-gas", "fluid", 55)], 300, "oil-refinery"),
    "light-oil-cracking": fluid_recipe([("light-oil", "fluid", 30), ("water", "fluid", 30)],
                                       [("petroleum-gas", "fluid", 20)], 120, "chemical-plant"),
    # Catalyst recipe whose steam no recipe makes: must neither be chosen nor raise a cycle.
    "coal-liquefaction": fluid_recipe(
        [("coal", "item", 10), ("heavy-oil", "fluid", 25), ("steam", "fluid", 50)],
        [("heavy-oil", "fluid", 90), ("light-oil", "fluid", 20), ("petroleum-gas", "fluid", 10)], 300, "oil-refinery"),
}


def test_fluid_recipes_choose_the_shortest_chain_to_raw_and_keep_solid_recipes():
    facts = {**world([]), "fluid_recipes": FLUID_RECIPES}
    chosen = production_recipes(facts)
    assert {k: chosen[k] for k in RECIPES} == RECIPES  # Solid recipes pass through untouched.
    assert chosen["petroleum-gas"]["recipe"] == "basic-oil-processing"
    assert chosen["heavy-oil"]["recipe"] == chosen["light-oil"]["recipe"] == "advanced-oil-processing"
    assert "crude-oil" not in chosen and "steam" not in chosen
    only_advanced = {k: v for k, v in FLUID_RECIPES.items() if k != "basic-oil-processing"}
    assert production_recipes({**facts, "fluid_recipes": only_advanced})["petroleum-gas"]["recipe"] == "advanced-oil-processing"
    running = {**facts, "machines": [machine("oil-refinery", "advanced-oil-processing", "working")]}
    assert production_recipes(running)["petroleum-gas"]["recipe"] == "advanced-oil-processing"


def test_plastic_goal_expands_to_petroleum_gas_crude_and_coal_with_machine_counts():
    oil = {"found": True, "wells": 4, "max_crude_per_s": 40.0}
    facts = {**world([]), "fluid_recipes": FLUID_RECIPES, "oil_field": oil}
    board = compile_status(facts, {"plastic-bar": 0.5}, flows(coal=1.0))
    needs = {n["item"]: n for n in board["needs"]}
    assert set(needs) == {"plastic-bar", "petroleum-gas", "coal", "crude-oil"}
    plastic, gas, crude = needs["plastic-bar"], needs["petroleum-gas"], needs["crude-oil"]
    assert plastic["condition"] == "unbuilt" and plastic["machines_needed"] == {"chemical-plant": 1}
    assert gas["required_per_s"] == pytest.approx(5.0) and gas["machines_needed"] == {"oil-refinery": 1}
    assert crude["required_per_s"] == pytest.approx(100 / 45 * 5) and crude["machines_needed"] == {"pumpjack": 2}
    assert needs["coal"]["required_per_s"] == pytest.approx(0.25) and needs["coal"]["condition"] == "satisfied"
    assert bottleneck_chain(board) == ["plastic-bar:unbuilt <- petroleum-gas:unbuilt <- crude-oil:unmeasured"]
    # Machines count under the recipe they run, not the product name.
    built = {**facts, "machines": [machine("oil-refinery", "basic-oil-processing", "working")]}
    gas = next(n for n in compile_status(built, {"plastic-bar": 0.5})["needs"] if n["item"] == "petroleum-gas")
    assert gas["machines"] == {"oil-refinery": 1} and gas["capacity_per_s"] == pytest.approx(9.0)


def test_without_fluid_facts_plastic_stays_unavailable_and_solid_boards_are_unchanged():
    assert next(n for n in compile_status(world([]), {"plastic-bar": 0.5})["needs"])["condition"] == "recipe_unavailable"
    solid = compile_status(world([]), {"electronic-circuit": 1.0})
    assert compile_status({**world([]), "fluid_recipes": FLUID_RECIPES}, {"electronic-circuit": 1.0}) == solid


CHEST_CHAIN = {"passive-provider-chest": recipe([("steel-chest", 1), ("electronic-circuit", 3), ("advanced-circuit", 1)], 30),
               "steel-chest": recipe([("steel-plate", 8)], 30),
               "advanced-circuit": recipe([("plastic-bar", 2), ("electronic-circuit", 2), ("copper-cable", 4)], 360),
               "electronic-circuit": recipe([("iron-plate", 1), ("copper-cable", 3)], 30),
               "copper-cable": recipe([("copper-plate", 1)], 30, 2)}


def test_kit_chests_in_stock_are_no_route_to_more_chests_until_plastic_exists():
    """User 2026-11-08: before plastic, no chests may be ordered or slotted; the item's own stock never counts."""
    from training.laya_factory_status import missing_input
    have = {"passive-provider-chest", "steel-plate", "iron-plate", "copper-plate"}
    assert missing_input("passive-provider-chest", CHEST_CHAIN, have) == "plastic-bar"
    assert missing_input("passive-provider-chest", CHEST_CHAIN, have | {"plastic-bar"}) is None
    assert missing_input("electronic-circuit", CHEST_CHAIN, set()) == "iron-plate"  # A plate not on hand blocks too.
