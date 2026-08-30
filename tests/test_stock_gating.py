# Path: tests/test_stock_gating.py
# Purpose: Prove a mall cell stops crafting once the network holds its target, and that the gate is applied by the executor rather than silently skipped.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder as builder  # noqa: E402
from planners.mall_layout import (  # noqa: E402
    generate_mall_stock_gate_update, generate_paired_mall_layout,
)
from planners.plan_validation import actions, validate_build_plan  # noqa: E402
from planners.stock_gating import BELOW, gate_matches, stock_gate  # noqa: E402

_SCHEMA = json.loads((REPO_ROOT / "schemas" / "build_plan.schema.json").read_text())
_EXECUTOR = (REPO_ROOT / "factorio_mod" / "layout_executor.lua").read_text(encoding="utf-8")
_SECTIONS = (REPO_ROOT / "factorio_mod" / "logistic_sections.lua").read_text(encoding="utf-8")


def _cell(stock_target: int = 50, *, gated: bool = True) -> dict:
    plan = generate_paired_mall_layout(
        "transport-belt", "assembling-machine-2", ["iron-gear-wheel", "iron-plate"],
        [1, 1], (0, 0), "left", stock_target=stock_target, product_amount=2,
        craft_time=0.5,
        stock_gate_target=stock_target if gated else None,
    )
    plan["surface"], plan["force"] = "nauvis", "player"
    return plan


def test_the_gate_watches_the_item_the_cell_makes() -> None:
    gate = stock_gate("transport-belt", 4800)

    assert gate == {"signal": "transport-belt", "comparator": BELOW, "constant": 4800}


def test_the_comparator_is_strictly_below_not_at_or_below() -> None:
    """At-or-below would let a network already holding the target craft one
    more, and the cell would never settle."""
    assert stock_gate("pipe", 10)["comparator"] == "<"


def test_a_gate_needs_a_positive_target() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        stock_gate("pipe", 0)


def test_a_gate_needs_an_item() -> None:
    with pytest.raises(ValueError, match="needs an item"):
        stock_gate("", 10)


def test_a_matching_gate_is_recognised_so_a_correct_cell_is_left_alone() -> None:
    assert gate_matches(stock_gate("pipe", 10), "pipe", 10)
    assert not gate_matches(stock_gate("pipe", 10), "pipe", 20)
    assert not gate_matches(stock_gate("pipe", 10), "belt", 10)
    assert not gate_matches(None, "pipe", 10)


def test_a_mall_cell_gates_its_own_machine() -> None:
    machine = next(
        action for action in actions(_cell())
        if action["entity"] == "assembling-machine-2"
    )

    assert machine["logistic_condition"] == stock_gate("transport-belt", 50)


def test_the_gate_tracks_the_target_the_cell_was_built_for() -> None:
    """When the stock cap lifts, the rewritten cell must gate at the new figure
    -- otherwise the machine keeps stopping at the old one."""
    machine = next(
        action for action in actions(_cell(stock_target=4800))
        if action["entity"] == "assembling-machine-2"
    )

    assert machine["logistic_condition"]["constant"] == 4800


def test_a_gated_cell_is_still_a_valid_build_plan() -> None:
    validate_build_plan(_cell())


def test_only_the_machine_is_gated_not_the_chests() -> None:
    """A disabled requester would stop refilling and the cell could never
    restart cleanly; the machine is the right thing to stop."""
    gated = [
        action for action in actions(_cell()) if "logistic_condition" in action
    ]

    assert [action["entity"] for action in gated] == ["assembling-machine-2"]


def test_the_schema_accepts_the_gate() -> None:
    field = (
        _SCHEMA["properties"]["phases"]["items"]["properties"]["actions"]
        ["items"]["properties"]["logistic_condition"]
    )

    assert set(field["required"]) == {"signal", "comparator", "constant"}
    assert "<" in field["properties"]["comparator"]["enum"]


def test_the_executor_applies_the_gate() -> None:
    assert "action.logistic_condition" in _EXECUTOR
    assert "connect_to_logistic_network = true" in _EXECUTOR
    assert "logistic_condition = {" in _EXECUTOR


def test_the_gate_is_a_reapplied_setting_not_a_creation_time_field() -> None:
    """A cell that already exists must still get its gate updated when the
    target changes. Omitting it here is the silent skip: the entity reports
    already_present, nothing fails, and the gate never lands."""
    assert '"logistic_condition"' in _SECTIONS


def test_a_gate_that_does_not_land_is_reported_rather_than_swallowed() -> None:
    """A machine whose gate silently failed looks exactly like one nobody asked
    to gate -- and it would quietly keep over-producing."""
    for failure in (
        "logistic_condition_unsupported",
        "logistic_condition_set_failed",
        "logistic_condition_not_applied",
    ):
        assert failure in _EXECUTOR


def test_the_gate_is_verified_after_it_is_written() -> None:
    """pcall succeeding is not proof the property took."""
    block = _EXECUTOR[_EXECUTOR.index("if action.logistic_condition then"):]
    block = block[:block.index("if action.clear_logistic_groups then")]

    assert "behavior.connect_to_logistic_network ~= true" in block


def test_a_prep_cell_is_not_gated() -> None:
    """An intermediate feeding other machines is throttled by its own provider
    chest filling up. Gating one on a network count stops the chain behind it --
    prep passed the MACHINE COUNT as the target and produced a copper-cable cell
    that refused to craft above two cables."""
    machine = next(
        action for action in actions(_cell(gated=False))
        if action["entity"] == "assembling-machine-2"
    )

    assert "logistic_condition" not in machine


def test_real_build_uses_the_reserve_for_both_the_bar_and_gate(monkeypatch) -> None:
    """The mission can proceed at 200, while the mall keeps prebuilding to 1000."""
    observed = {}
    plan = SimpleNamespace(
        existing=None,
        spec={
            "machine": "assembling-machine-2",
            "ingredients": ["iron-gear-wheel", "iron-plate"],
            "amounts": [1, 1],
            "product_amount": 2,
            "craft_time": 0.5,
        },
        production_target=200,
        mall_storage_limit=1000,
        promote_to_line=False,
        fill_provider=False,
        mall_request_multiplier=None,
        promoted_count=None,
    )
    monkeypatch.setattr(builder, "_ingredient_sources", lambda *_a, **_k: {})

    def capture(*_args, **kwargs) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(builder, "build_compact_mall_stage", capture)

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "transport-belt",
        (0.0, 0.0), lambda _message: None, plan, None,
        upgrade_bootstrap=False, stock_gate_target=1000,
    )

    assert observed["stock_target"] == 1000
    assert observed["stock_gate_target"] == 1000


def test_ensure_produced_preserves_requirement_and_capacity_to_the_build(
    monkeypatch,
) -> None:
    """Regression for the real call chain: gate_on_stock used to stop at
    ensure_produced and never reached the compact-mall layout."""
    plan = SimpleNamespace(existing=None)
    observed = {}

    def planned(*_args, **kwargs):
        observed["planned_storage_limit"] = kwargs["storage_limit"]
        return plan

    def refreshed(*_args, **kwargs):
        observed["refreshed_gate"] = kwargs["stock_gate_target"]
        return None

    def built(*_args, **kwargs) -> None:
        observed["built_gate"] = kwargs["stock_gate_target"]

    monkeypatch.setattr(builder, "_plan_line", planned)
    monkeypatch.setattr(builder, "_refresh_mall_cell", refreshed)
    monkeypatch.setattr(builder, "_build_assembled_stage", built)

    result = builder.ensure_produced(
        object(), object(), "nauvis", "player", "transport-belt",
        (0.0, 0.0), lambda _message: None,
        upgrade_bootstrap=False,
        stock_target=200,
        stock_gate_target=1000,
        storage_limit=1000,
    )

    assert result is None
    assert observed == {
        "planned_storage_limit": 1000,
        "refreshed_gate": 1000,
        "built_gate": 1000,
    }


def test_existing_mall_cell_gets_the_reserve_gate_and_storage_limit(
    monkeypatch,
) -> None:
    machines = ((10.5, 10.5), (16.5, 10.5))
    provider = (13.5, 9.5)
    existing = SimpleNamespace(machine_positions=machines)
    plan = SimpleNamespace(
        existing=existing,
        spec={
            "machine": "assembling-machine-2",
            "ingredients": ["iron-gear-wheel", "iron-plate"],
            "amounts": [1, 1],
            "product_amount": 2,
        },
        production_target=200,
        mall_storage_limit=1000,
        fill_provider=False,
    )
    submitted = []
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(
        builder, "_submit",
        lambda _client, _bridge, _surface, plan, _name, _emit: submitted.append(plan),
    )

    builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "transport-belt", plan,
        lambda _message: None, upgrade_bootstrap=False, stock_gate_target=1000,
    )

    limit = next(actions(submitted[0]))["inventory_limit"]
    gates = [
        action["logistic_condition"]
        for action in actions(submitted[1])
    ]
    assert limit["count"] == 1000
    assert gates == [stock_gate("transport-belt", 1000)] * 2


def test_existing_gate_update_is_a_valid_reconfiguration_plan() -> None:
    plan = generate_mall_stock_gate_update(
        "transport-belt", "assembling-machine-2", [(10.5, 10.5)], 200,
    )
    plan["surface"], plan["force"] = "nauvis", "player"

    validate_build_plan(plan)


def test_only_a_construction_target_asks_for_a_gate() -> None:
    """The mall task path has a real count of finished goods; prep does not."""
    import inspect

    from orchestrator import autonomous_builder as builder

    ensured = inspect.getsource(builder._ensure_mall_item)
    prepped = inspect.getsource(builder._prep_intermediate)

    assert "stock_gate_target=reserve.gate_target" in ensured
    assert "stock_gate_target" not in prepped


def test_a_smelted_recipe_is_refused_a_mall_cell() -> None:
    """It could never be counted again, so it would be rebuilt every pass."""
    import inspect

    from orchestrator.mall_builder import build_compact_mall_stage

    source = inspect.getsource(build_compact_mall_stage)

    assert 'spec.get("set_recipe", True)' in source
    assert "needs a smelting stage" in source


def test_a_full_chest_target_clears_the_bar_rather_than_setting_one() -> None:
    """set_bar() with no argument clears the limit; there is no clear_bar().
    The clearing branch only runs when the target needs the whole chest, so it
    went unexercised until a stock cap lifted to 4800 belts -- and the first
    plan that reached it died on a nil method."""
    assert "inventory.set_bar()" in _EXECUTOR
    assert "clear_bar" not in _EXECUTOR.replace(
        "-- set_bar() with no argument CLEARS the limit; there is no clear_bar().", ""
    )


def test_the_bar_is_only_touched_on_inventories_that_support_one() -> None:
    assert "supports_bar()" in _EXECUTOR


def test_bootstrap_inventory_limits_are_exact_stack_counts() -> None:
    assert "local usable_slots = math.max(minimum_stacks, target_stacks)" in _EXECUTOR
    assert "growth_stacks" not in _EXECUTOR


def test_a_mature_cell_explicitly_fills_the_provider_chest() -> None:
    plan = generate_paired_mall_layout(
        "transport-belt", "assembling-machine-2",
        ["iron-gear-wheel", "iron-plate"], [1, 1], (0, 0), "left",
        stock_target=250, product_amount=2, craft_time=0.5,
        fill_chest=True,
    )
    provider = next(
        action for action in actions(plan)
        if action["entity"] == "passive-provider-chest"
    )

    assert provider["inventory_limit"]["fill_chest"] is True


def test_a_mature_cell_clears_its_old_bootstrap_gate() -> None:
    plan = generate_mall_stock_gate_update(
        "transport-belt", "assembling-machine-2", [(10.5, 10.5)], None,
    )
    plan["surface"], plan["force"] = "nauvis", "player"

    validate_build_plan(plan)
    assert next(actions(plan))["clear_logistic_condition"] is True


def test_the_executor_clears_and_verifies_an_old_gate() -> None:
    assert "behavior.connect_to_logistic_network = false" in _EXECUTOR
    assert "logistic_condition_not_cleared" in _EXECUTOR
    assert "logistic_condition_clear_failed" in _EXECUTOR


def test_the_schema_distinguishes_exact_reserve_from_full_chest() -> None:
    fields = (
        _SCHEMA["properties"]["phases"]["items"]["properties"]["actions"]
        ["items"]["properties"]
    )

    assert fields["inventory_limit"]["properties"]["fill_chest"]["type"] == "boolean"
    assert fields["clear_logistic_condition"]["type"] == "boolean"
