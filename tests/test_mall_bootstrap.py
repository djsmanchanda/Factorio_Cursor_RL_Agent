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
    assert requester["logistic_sections"][0]["multiplier"] == 3
    # Bounded headroom: 2 step crafts ask for ceil(2 * 1.2), never the exact
    # count, so the machine never idles on a dry requester (2026-09-03).
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



def test_hovering_robot_is_not_parsed_as_the_borrowed_machine() -> None:
    """2026-09-03 run killer: a logistic robot flying over the mall pad at
    survey tick was parsed as the loan's machine, so the next submit tried
    to configure_entity a logistic-robot and died on configure_target_missing.
    Only assembler tiers are usable machine identities."""
    group = (
        "mall-bootstrap:v4:iron-gear-wheel:transport-belt:128:154:left:"
        "transport-belt:150:100:12:12:-"
    )
    reply = f"{group}|39.5|38.5|logistic-robot,transport-belt|-"

    class _Client:
        def command(self, _command: str) -> str:
            return reply

    (loan,) = active_bootstrap_loans(_Client(), "nauvis", "player")

    assert loan.machine_name == "assembling-machine-1"
    assert loan.current_recipe == "transport-belt"


def _provider_counts(plan: dict) -> list[int]:
    return [
        action["inventory_limit"]["count"]
        for phase in plan["phases"]
        for action in phase["actions"]
        if action.get("entity") == "passive-provider-chest"
        and isinstance(action.get("inventory_limit"), dict)
    ]


def test_restore_defaults_to_the_legacy_provider_count() -> None:
    plan = restore_bootstrap_loan_plan(_loan("copper-cable"))

    assert _provider_counts(plan) == [1]


def test_restore_applies_the_canonical_twin_limit() -> None:
    """Every live cell making the same item carries the same provider limit
    (user standard 2026-09-04): a restored twin must not reset to 1 while its
    sibling holds hundreds."""
    plan = restore_bootstrap_loan_plan(
        _loan("copper-cable"), provider_stock_target=240,
    )

    assert _provider_counts(plan) == [240]


def _install_self_supply_recipes(monkeypatch) -> None:
    """Assemblers and drills as catalog recipes: plates, gears, and circuits
    in, finished machines out -- everything a seed-powered base can supply."""
    monkeypatch.setitem(LINE_RECIPES, "assembling-machine-1", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-plate", "iron-gear-wheel"],
        "amounts": [9, 4], "product_amount": 1, "craft_time": 0.5,
    })
    monkeypatch.setitem(LINE_RECIPES, "electric-mining-drill", {
        "machine": "assembling-machine-1",
        "ingredients": ["iron-plate", "iron-gear-wheel", "electronic-circuit"],
        "amounts": [10, 5, 3], "product_amount": 1, "craft_time": 2.0,
    })


def test_drill_batch_is_admitted_on_seed_stock(monkeypatch) -> None:
    """5-drill start: seeds consume every gifted drill, so the first mine
    expansion drills must be mall batches made from seed plates, gears, and
    circuits -- with no external capability missing."""
    _install_self_supply_recipes(monkeypatch)
    stock = {
        "iron-plate": 100, "iron-gear-wheel": 30, "electronic-circuit": 18,
        "electric-mining-drill": 0,
    }

    step = next_bootstrap_step("electric-mining-drill", 6, stock, stock)
    external = bootstrap_external_shortages(
        "electric-mining-drill", 6, stock, stock,
    )

    assert step is not None
    assert step.recipe == "electric-mining-drill"
    assert external == ()


def test_drill_batch_makes_circuits_first_when_they_are_missing(monkeypatch) -> None:
    """Without circuits flowing, the loan ladder still has everything it
    needs: cable and plates stocked means no external capability is missing
    and the deepest step is circuits, ahead of the drills themselves."""
    _install_self_supply_recipes(monkeypatch)
    stock = {
        "iron-plate": 100, "iron-gear-wheel": 30, "copper-cable": 60,
        "electronic-circuit": 0,
    }

    step = next_bootstrap_step("electric-mining-drill", 6, stock, stock)
    external = bootstrap_external_shortages(
        "electric-mining-drill", 6, stock, stock,
    )

    assert step is not None
    assert step.recipe == "electronic-circuit"
    assert external == ()


def test_drill_batch_names_raw_plate_when_cable_runs_dry(monkeypatch) -> None:
    """With no cable either, the walk bottoms out at the raw capability the
    controller must establish rather than borrowing a slot it cannot feed."""
    _install_self_supply_recipes(monkeypatch)
    stock = {
        "iron-plate": 100, "iron-gear-wheel": 30, "copper-cable": 0,
        "electronic-circuit": 0,
    }

    external = bootstrap_external_shortages(
        "electric-mining-drill", 6, stock, stock,
    )

    assert "copper-plate" in [shortage.item for shortage in external]


def test_seventh_assembler_is_a_mall_batch_on_seed_stock(monkeypatch) -> None:
    """6-AM1 start: the 6 standing cells consume every gifted assembler, so
    cells 7-8 (and the core producers) must be mall batches, not stock."""
    _install_self_supply_recipes(monkeypatch)
    stock = {
        "iron-plate": 40, "iron-gear-wheel": 20, "assembling-machine-1": 0,
    }

    step = next_bootstrap_step("assembling-machine-1", 1, stock, stock)
    external = bootstrap_external_shortages(
        "assembling-machine-1", 1, stock, stock,
    )

    assert step is not None
    assert step.recipe == "assembling-machine-1"
    assert external == ()


def _restore_plan(provider_fill_chest: bool) -> dict:
    return restore_bootstrap_loan_plan(
        _loan("copper-cable"), provider_stock_target=240,
        provider_fill_chest=provider_fill_chest,
    )


def test_restore_opens_shared_providers() -> None:
    """2026-09-04: resetting a count bar behind another recipe's stacks
    stranded every future output on a shared chest."""
    actions = [
        action for phase in _restore_plan(True)["phases"]
        for action in phase["actions"]
        if action.get("entity") == "passive-provider-chest"
    ]
    assert actions and all(
        action["inventory_limit"].get("fill_chest") for action in actions
    )


def test_restore_applies_canonical_count_to_dedicated() -> None:
    assert _provider_counts(_restore_plan(False)) == [240]


def _telemetry_loan(**overrides) -> MallBootstrapLoan:
    fields = {
        "original_recipe": "copper-cable", "target_item": "transport-belt",
        "target_count": 184, "side": "right",
        "requester_position": (39.5, 38.5),
        "current_recipe": "iron-gear-wheel",
        "step_recipe": "iron-gear-wheel", "step_target_count": 92,
        "step_baseline_finished": 40, "step_required_crafts": 52,
        "step_minimum_crafts": 52, "spare_target_count": 221,
    }
    fields.update(overrides)
    return MallBootstrapLoan(**fields)


def test_loan_cell_telemetry_names_missing_ingredient_pool_and_loans(
    monkeypatch,
) -> None:
    """2026-09-06 chemical stall: a bare LOAN WAIT cannot tell a starved
    requester from pool exhaustion, loan contention, or bill accounting."""
    from orchestrator import autonomous_builder as builder

    loan = _telemetry_loan()
    other = _telemetry_loan(
        target_item="splitter", target_count=12, side="left",
        requester_position=(50.5, 32.5), current_recipe="transport-belt",
        step_recipe="transport-belt", spare_target_count=50,
    )
    monkeypatch.setattr(
        builder.live_base, "chest_contents", lambda *_a: {},
    )
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 6)
    monkeypatch.setattr(
        builder, "active_bootstrap_loans", lambda *_a: (loan, other),
    )
    emitted: list[str] = []
    builder._emit_loan_cell_telemetry(
        object(), "nauvis", "player", loan,
        MallBootstrapStep("iron-gear-wheel", 92, 52),
        {"transport-belt": 2}, 44, emitted.append,
        reference_point=(3.0, -1.0),
    )

    assert len(emitted) == 1
    line = emitted[0]
    assert "LOAN CELL TELEMETRY" in line
    assert "bill 2/184+221" in line
    assert "crafts 4/52" in line
    assert "missing iron-plate" in line
    assert "pool 6/12 free" in line
    assert "transport-belt:iron-gear-wheel@(42.5,38.5)" in line
    assert "splitter:transport-belt@(47.5,32.5)" in line


def test_loan_cell_telemetry_never_raises(monkeypatch) -> None:
    """Telemetry is zero-behavior: total probe failure still emits one
    skip marker instead of breaking the pass."""
    from orchestrator import autonomous_builder as builder

    def _boom(*_a, **_k) -> None:
        raise RuntimeError("no rcon")

    class _BadStock(dict):
        def get(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
            raise RuntimeError("no stock")

    monkeypatch.setattr(builder.live_base, "chest_contents", _boom)
    monkeypatch.setattr(builder, "mall_slot_count", _boom)
    monkeypatch.setattr(builder, "active_bootstrap_loans", _boom)
    emitted: list[str] = []
    builder._emit_loan_cell_telemetry(
        object(), "nauvis", "player", _telemetry_loan(),
        MallBootstrapStep("iron-gear-wheel", 92, 52),
        _BadStock(), 44, emitted.append,
    )

    assert len(emitted) == 1
    assert emitted[0].startswith("  LOAN CELL TELEMETRY skipped: ")


def test_stage_delivery_telemetry_counts_attempts_and_names_stock() -> None:
    """2026-09-06 oil terminal: one moved-0 pumpjack delivery, mall restock
    to 1, no further attempt logged. The attempt ordinal plus net /
    transferable / ghost-network counts distinguish "reconcile never
    re-ran" from "transfer cannot complete" from "stock locked in WIP"."""
    from orchestrator import autonomous_builder as builder

    builder._STAGE_DELIVERY_ATTEMPTS.clear()
    try:
        emitted: list[str] = []
        builder._emit_stage_delivery_telemetry(
            object(), "nauvis", "player", "crude-oil source",
            "pumpjack", 1, {"pumpjack": 0}, (-284.5, -88.5), emitted.append,
        )
        builder._emit_stage_delivery_telemetry(
            object(), "nauvis", "player", "crude-oil source",
            "pumpjack", 1, {"pumpjack": 1}, (-284.5, -88.5), emitted.append,
        )

        assert len(emitted) == 2
        # Live probes fail against a dummy client; counts fall back while
        # the attempt ordinal and net stock still discriminate the retry.
        assert (
            "STAGE DELIVERY TELEMETRY: crude-oil source pumpjack attempt 1 "
            "need 1 | net 0 " in emitted[0]
        )
        assert (
            "STAGE DELIVERY TELEMETRY: crude-oil source pumpjack attempt 2 "
            "need 1 | net 1 " in emitted[1]
        )
    finally:
        builder._STAGE_DELIVERY_ATTEMPTS.clear()


def test_stage_delivery_telemetry_never_raises(monkeypatch) -> None:
    """Telemetry is zero-behavior: total probe failure still emits one
    skip marker instead of breaking the pass."""
    from orchestrator import autonomous_builder as builder

    def _boom(*_a, **_k) -> None:
        raise RuntimeError("no rcon")

    class _BadStock(dict):
        def get(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
            raise RuntimeError("no stock")

    monkeypatch.setattr(builder.live_base, "transferable_item_count", _boom)
    monkeypatch.setattr(builder.live_base, "network_item_count", _boom)
    emitted: list[str] = []
    builder._emit_stage_delivery_telemetry(
        object(), "nauvis", "player", "crude-oil source",
        "pumpjack", 1, _BadStock(), (-284.5, -88.5), emitted.append,
    )

    assert len(emitted) == 1
    assert "STAGE DELIVERY TELEMETRY" in emitted[0]
