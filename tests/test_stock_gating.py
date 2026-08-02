# Path: tests/test_stock_gating.py
# Purpose: Prove a mall cell stops crafting once the network holds its target, and that the gate is applied by the executor rather than silently skipped.

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planners.mall_layout import generate_paired_mall_layout  # noqa: E402
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


def test_only_a_construction_target_asks_for_a_gate() -> None:
    """The mall task path has a real count of finished goods; prep does not."""
    import inspect

    from orchestrator import autonomous_builder as builder

    served = inspect.getsource(builder._serve_mall_task)
    prepped = inspect.getsource(builder._prep_intermediate)

    assert "gate_on_stock=True" in served
    assert "gate_on_stock" not in prepped


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
