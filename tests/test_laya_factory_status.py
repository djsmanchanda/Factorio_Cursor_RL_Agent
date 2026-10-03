# Path: tests/test_laya_factory_status.py | Purpose: Protect Laya's status board: goal demand, separated machine conditions, causal chain and unknowns.

import pytest

from orchestrator.laya_state import validate_state
from training.laya_factory_status import (
    FactoryStatusError, apply_to_state, bottleneck_chain, compile_status, goal_demand,
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
