# Path: tests/test_construction_handoffs.py
# Purpose: Construction events release optional work without priority aging.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder


def test_binding_bill_does_not_wait_for_spare_margin(monkeypatch):
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"transport-belt"})
    monkeypatch.setattr(
        builder, "_rationed_mall_spare_target",
        lambda *_a: pytest.fail("binding work must not consult optional reserve"),
    )
    assert builder._rationed_mall_completion_target(
        object(), "nauvis", "player", "transport-belt", 148,
    ) == 148


@pytest.mark.parametrize("targets", [{"electric-mining-drill": 6}, {}])
def test_binding_construction_bypasses_standing_topology(monkeypatch, targets):
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"electric-mining-drill"})
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: pytest.fail("standing prep intercepted construction"),
    )
    assert not builder._prep_intermediate(
        object(), object(), "nauvis", "player", set(),
        targets, (0, 0), lambda _m: None,
    )


@pytest.mark.parametrize("held,other_binding,finished,release", [
    (3, False, 43, True),
    (0, True, 43, True),
    (0, False, 43, False),
    (0, True, 42, False),
])
def test_completed_loan_hands_off_without_explicit_competitor(
    monkeypatch, held, other_binding, finished, release,
):
    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="splitter",
        target_count=3, spare_target_count=50, side="left",
        requester_position=(50.5, 32.5), current_recipe="splitter",
        step_recipe="splitter", step_baseline_finished=40,
        step_required_crafts=50, step_minimum_crafts=3,
    )
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", {"splitter"} | ({"electric-mining-drill"} if other_binding else set()))
    monkeypatch.setitem(builder.LINE_RECIPES, "splitter", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 1.0,
    })
    stock = {"splitter": held, "iron-plate": 100}
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock))
    monkeypatch.setattr(builder, "_bootstrap_loan_products_finished", lambda *_a: finished)
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: None)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(builder.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "entity_status_name", lambda *_a: "working")
    restored = []
    monkeypatch.setattr(builder, "_restore_bootstrap_loan", lambda *_a, **_k: restored.append(True))
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: stock)
    builder._release_completed_construction_loans(
        object(), object(), "nauvis", "player", (0, 0), lambda _m: None,
    )
    assert bool(restored) is release


@pytest.mark.parametrize("earmark", [True, False])
def test_existing_direct_mine_earmark_does_not_wait_before_refinery(monkeypatch, earmark):
    extraction = SimpleNamespace(
        build_plan=None, expansion_positions=(), ore="iron-ore",
        ore_output=(12.5, -1.5), shared_belt_y=-1.5, row_drill_count=3,
        drill_count=6, expansion_step=0,
    )
    monkeypatch.setattr(builder.live_base, "entity_at", lambda *_a: {"type": "transport-belt"})
    monkeypatch.setattr(builder, "existing_mine_service_geometry", lambda *_a, **_k: ((0, 0), ((0, 0), (20, 20)), (1, 1), [(2, 2)]))
    calls = []
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: calls.append("wait"))
    monkeypatch.setattr(builder, "_ensure_power_anchor_on_generated_network", lambda *_a, **_k: calls.append("power"))
    builder._submit_mining_plan(
        SimpleNamespace(command=lambda *_a: ""), object(), "nauvis", "player",
        extraction, extraction.ore_output, lambda _m: None,
        allow_unfunded_ghosts=earmark,
    )
    assert calls == (["power"] if earmark else ["wait"])
