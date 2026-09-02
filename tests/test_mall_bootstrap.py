# Path: tests/test_mall_bootstrap.py
# Purpose: Prove finite mall recipe loans make prerequisites, request inputs, and restore safely.

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator

from orchestrator.mall_bootstrap import (
    MallBootstrapExternalShortage,
    MallBootstrapLoan,
    MallBootstrapStep,
    active_bootstrap_loans,
    bootstrap_external_shortages,
    bootstrap_loan_plan,
    next_bootstrap_step,
    promote_bootstrap_loan_plan,
    restore_bootstrap_loan_plan,
)
from planners.recipe_data import LINE_RECIPES


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Draft7Validator(json.loads(
    (ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8")
))


def _install_seed_recipes(monkeypatch) -> None:
    monkeypatch.setitem(LINE_RECIPES, "requester-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["advanced-circuit", "electronic-circuit", "steel-chest"],
        "amounts": [1, 3, 1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(LINE_RECIPES, "steel-chest", {
        "machine": "assembling-machine-2",
        "ingredients": ["steel-plate"],
        "amounts": [8], "product_amount": 1, "craft_time": 0.5,
    })


def _loan(current: str = "copper-cable") -> MallBootstrapLoan:
    return MallBootstrapLoan(
        original_recipe="copper-cable", target_item="requester-chest",
        target_count=2, side="right", requester_position=(39.5, 32.5),
        current_recipe=current,
    )


def test_missing_prerequisite_is_made_before_two_requester_chests(monkeypatch) -> None:
    _install_seed_recipes(monkeypatch)
    stock = {
        "advanced-circuit": 0, "electronic-circuit": 20,
        "steel-chest": 2, "plastic-bar": 10, "copper-cable": 20,
    }

    step = next_bootstrap_step("requester-chest", 2, stock, stock)

    assert step == MallBootstrapStep("advanced-circuit", 2, 2)


def test_root_step_targets_exactly_two_seed_chests(monkeypatch) -> None:
    _install_seed_recipes(monkeypatch)
    stock = {"advanced-circuit": 2, "electronic-circuit": 6, "steel-chest": 2}

    assert next_bootstrap_step("requester-chest", 2, stock, stock) == (
        MallBootstrapStep("requester-chest", 2, 2)
    )
    assert next_bootstrap_step(
        "requester-chest", 2, stock, {**stock, "requester-chest": 2},
    ) is None


def test_reserved_prerequisite_causes_extra_production(monkeypatch) -> None:
    _install_seed_recipes(monkeypatch)
    usable = {
        "advanced-circuit": 2, "electronic-circuit": 2,
        "steel-chest": 2, "iron-plate": 50, "copper-cable": 20,
    }
    actual = {**usable, "electronic-circuit": 6}

    step = next_bootstrap_step("requester-chest", 2, usable, actual)

    assert step == MallBootstrapStep("electronic-circuit", 10, 4)


def test_external_shortage_is_exposed_instead_of_ignored(monkeypatch) -> None:
    monkeypatch.setitem(LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2",
        "ingredients": ["assembling-machine-1", "steel-plate"],
        "amounts": [1, 2], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(LINE_RECIPES, "assembling-machine-1", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [9], "product_amount": 1, "craft_time": 0.5,
    })
    stock = {
        "assembling-machine-1": 1, "iron-plate": 100, "steel-plate": 0,
        "assembling-machine-2": 0,
    }

    assert bootstrap_external_shortages(
        "assembling-machine-2", 1, stock, stock,
    ) == (MallBootstrapExternalShortage("steel-plate", 2),)


def test_craftable_shortage_stays_inside_the_rotating_assembler(monkeypatch) -> None:
    monkeypatch.setitem(LINE_RECIPES, "fast-inserter", {
        "machine": "assembling-machine-2", "ingredients": ["inserter"],
        "amounts": [1], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(LINE_RECIPES, "inserter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 0.5,
    })
    stock = {"fast-inserter": 0, "inserter": 0, "iron-plate": 10}

    assert bootstrap_external_shortages(
        "fast-inserter", 1, stock, stock,
    ) == ()
    assert next_bootstrap_step(
        "fast-inserter", 1, stock, stock,
    ) == MallBootstrapStep("inserter", 1, 1)


def test_loan_configures_only_existing_entities_and_requests_step_inputs(
    monkeypatch,
) -> None:
    _install_seed_recipes(monkeypatch)
    plan = bootstrap_loan_plan(
        _loan(), MallBootstrapStep("advanced-circuit", 2, 2),
    )
    actions = plan["phases"][0]["actions"]
    requester = next(action for action in actions if action["entity"] == "requester-chest")

    assert {action["action_type"] for action in actions} == {"configure_entity"}
    assert requester["logistic_sections"][0]["requests"] == [
        {"name": "plastic-bar", "count": 2},
        {"name": "copper-cable", "count": 4},
        {"name": "electronic-circuit", "count": 2},
    ]
    assert requester["logistic_sections"][0]["multiplier"] == 2
    assert requester["clear_logistic_groups"] == [
        "mall:copper-cable",
        "mall:copper-cable:right",
        _loan().group,
    ]
    assert actions.index(requester) < actions.index(next(
        action for action in actions if action["entity"] == "assembling-machine-1"
    ))
    assert requester["logistic_sections"][0]["group"].startswith(
        "mall-bootstrap:v4:"
    )
    provider = next(
        action for action in actions
        if action["entity"] == "passive-provider-chest"
    )
    assert provider["inventory_limit"]["fill_chest"] is True
    assert not list(SCHEMA.iter_errors(plan))


def test_restore_clears_only_unique_loan_group(monkeypatch) -> None:
    _install_seed_recipes(monkeypatch)
    plan = restore_bootstrap_loan_plan(_loan("requester-chest"))
    actions = plan["phases"][0]["actions"]
    requester = next(action for action in actions if action["entity"] == "requester-chest")
    machine = next(action for action in actions if action["entity"] == "assembling-machine-1")

    assert requester["clear_logistic_groups"] == [_loan().group]
    assert requester["logistic_sections"][0]["group"] == "mall:copper-cable:right"
    assert actions.index(requester) < actions.index(machine)
    assert machine["recipe"] == "copper-cable"
    assert machine["clear_logistic_condition"] is True
    provider = next(
        action for action in actions
        if action["entity"] == "passive-provider-chest"
    )
    assert "fill_chest" not in provider["inventory_limit"]
    assert not list(SCHEMA.iter_errors(plan))


def test_promotion_reuses_the_borrowed_cell_as_permanent_pipe_mall() -> None:
    loan = MallBootstrapLoan(
        original_recipe="splitter", target_item="pipe", target_count=100,
        side="left", requester_position=(39.5, 32.5), current_recipe="pipe",
    )

    plan = promote_bootstrap_loan_plan(loan, stock_target=100)
    actions = plan["phases"][0]["actions"]
    requester = next(
        action for action in actions if action["entity"] == "requester-chest"
    )
    machine = next(
        action for action in actions if action["entity"] == "assembling-machine-1"
    )
    provider = next(
        action for action in actions
        if action["entity"] == "passive-provider-chest"
    )

    assert {action["action_type"] for action in actions} == {"configure_entity"}
    assert requester["clear_logistic_groups"] == [loan.group]
    assert requester["logistic_sections"][0]["group"] == "mall:pipe:left"
    assert requester["logistic_sections"][0]["requests"] == [
        {"name": "iron-plate", "count": 1},
    ]
    assert machine["recipe"] == "pipe"
    assert machine["logistic_condition"]["constant"] == 100
    assert provider["inventory_limit"]["fill_chest"] is True
    assert not list(SCHEMA.iter_errors(plan))


def test_active_loan_is_recovered_from_requester_tag() -> None:
    class Client:
        def command(self, _command: str) -> str:
            return (
                "mall-bootstrap:v1:copper-cable:requester-chest:2:right|"
                "39.5|32.5|assembling-machine-1,iron-gear-wheel|"
                "assembling-machine-1,advanced-circuit"
            )

    assert active_bootstrap_loans(Client(), "nauvis", "player") == (
        MallBootstrapLoan(
            original_recipe="copper-cable", target_item="requester-chest",
            target_count=2, side="right", requester_position=(39.5, 32.5),
            current_recipe="advanced-circuit",
        ),
    )


def test_active_v2_loan_recovers_step_baseline_for_consumed_output() -> None:
    class Client:
        def command(self, _command: str) -> str:
            return (
                "mall-bootstrap:v2:copper-cable:splitter:3:left:splitter:41:3|"
                "39.5|38.5|splitter|transport-belt"
            )

    assert active_bootstrap_loans(Client(), "nauvis", "player") == (
        MallBootstrapLoan(
            original_recipe="copper-cable", target_item="splitter",
            target_count=3, side="left", requester_position=(39.5, 38.5),
            current_recipe="splitter", step_recipe="splitter",
            step_baseline_finished=41, step_required_crafts=3,
        ),
    )


def test_active_v3_loan_recovers_required_and_spare_thresholds() -> None:
    class Client:
        def command(self, _command: str) -> str:
            return (
                "mall-bootstrap:v3:copper-cable:splitter:3:50:left:splitter:"
                "41:50:3|39.5|38.5|splitter|transport-belt"
            )

    loan = active_bootstrap_loans(Client(), "nauvis", "player")[0]

    assert loan.target_count == 3
    assert loan.production_target == 50
    assert loan.step_required_crafts == 50
    assert loan.step_minimum_crafts == 3


def test_active_v4_loan_recovers_step_target_and_completed_prerequisites() -> None:
    class Client:
        def command(self, _command: str) -> str:
            return (
                "mall-bootstrap:v4:electronic-circuit:electric-mining-drill:"
                "6:8:right:electronic-circuit:18:349:18:18:"
                "iron-gear-wheel=25|50.5|32.5|splitter|electronic-circuit"
            )

    loan = active_bootstrap_loans(Client(), "nauvis", "player")[0]

    assert loan.step_target_count == 18
    assert loan.step_baseline_finished == 349
    assert loan.completed_step_targets == (("iron-gear-wheel", 25),)


def test_active_loan_recovers_when_opposite_side_machine_is_missing() -> None:
    class Client:
        def command(self, _command: str) -> str:
            return (
                "mall-bootstrap:v4:copper-cable:splitter:3:3:left:electronic-circuit:18:111:2:2:-|"
                "39.5|38.5|assembling-machine-1,electronic-circuit|-"
            )

    loans = active_bootstrap_loans(Client(), "nauvis", "player")
    assert len(loans) == 1
    loan = loans[0]
    assert loan.current_recipe == "electronic-circuit"
    assert loan.machine_name == "assembling-machine-1"
    assert loan.step_recipe == "electronic-circuit"

