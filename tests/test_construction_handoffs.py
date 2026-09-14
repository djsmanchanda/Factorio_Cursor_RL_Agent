# Path: tests/test_construction_handoffs.py
# Purpose: Construction events release optional work without priority aging.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder


def test_visible_splitter_ghosts_restore_demand_despite_old_craft_proof(monkeypatch, tmp_path):
    from orchestrator.priority_list import PriorityList
    ghost_bill = {"splitter": 2}
    stock = {}
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(builder, "_CRAFT_PROOF_CONSUMED", {})
    monkeypatch.setattr(builder, "_transferable_or_available_stock", lambda *_a: stock)
    monkeypatch.setattr(builder.live_base, "pending_construction_items", lambda *_a: ghost_bill)
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 100)
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [])
    monkeypatch.setattr(builder, "_loan_craft_proof_complete", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_loan_craft_proof_crafts", lambda *_a, **_k: 100)
    client = SimpleNamespace(command=lambda *_a: "")
    targets = {}
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    for _ in range(2):
        _, task = builder._survey_pass(client, "nauvis", "player", targets, priorities)
        assert targets == {"splitter": 2}
        assert task.item == "splitter" and task.base_rating == 100
    stock["splitter"] = 2
    builder._survey_pass(client, "nauvis", "player", targets, priorities)
    assert targets == {}
    # Bots spend the bill and remove the ghosts. No stale demand is rebuilt.
    stock.clear()
    ghost_bill.clear()
    _, task = builder._survey_pass(client, "nauvis", "player", targets, priorities)
    assert targets == {} and task is None


@pytest.mark.parametrize("capped,borrowed,ready", [(False,False,False),(True,True,False),(True,False,True)])
def test_stock_capped_core_capacity_is_not_chemical_production(monkeypatch, capped, borrowed, ready):
    point = (36.5, 44.5)
    monkeypatch.setattr(builder, "_production_started", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "stock_capped_mall_positions", lambda *_a: [point] if capped else [])
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [SimpleNamespace(machine_position=point)] if borrowed else [])
    client = SimpleNamespace(command=lambda *_a: "")
    assert builder._core_mall_producer_ready(client,"nauvis","player","fast-inserter") is ready
    assert not builder._power_generation_capability_started(client,"nauvis","player")


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
    monkeypatch.setattr(
        builder, "_prepare_core_mall_prerequisite",
        lambda *_a: pytest.fail("core promotion intercepted construction"),
    )
    assert not builder._prep_core_mall(
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


def test_completed_loan_release_parks_typed_chemical_handoff(monkeypatch) -> None:
    """Periodic loan release must not let a chemical handoff escape the loop."""
    loan = SimpleNamespace(
        target_item="chemical-plant", target_count=1, production_target=1,
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: [loan])
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock",
        lambda *_a: {"chemical-plant": 1},
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_products_finished", lambda *_a: 1,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_loan_minimum_fulfilled", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "_submit_bootstrap_loan",
        lambda *_a, **_k: (_ for _ in ()).throw(
            builder.ProductionPrerequisiteDeferred(
                "chemical ladder handoff", code="chemical_capability_handoff",
                state="supply_wait",
            )
        ),
    )
    messages: list[str] = []

    builder._release_completed_construction_loans(
        object(), object(), "nauvis", "player", (0, 0), messages.append,
    )

    assert any("retrying after re-observation" in message for message in messages)


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


@pytest.mark.parametrize('blocking', [set(), {'assembling-machine-2'}])
@pytest.mark.parametrize('held,finished,spares,restored', [(3, 40, 3, True), (0, 43, 3, True), (2, 42, 3, False), (5, 45, 5, True)])
def test_finite_completed_loan_restores_circuit_producer(monkeypatch, blocking, held, finished, spares, restored):
    """A retired splitter demand must not strand its borrowed circuit cell."""
    loan = builder.MallBootstrapLoan(
        original_recipe='electronic-circuit', target_item='splitter', target_count=3,
        spare_target_count=spares, side='right', requester_position=(50.5, 32.5),
        current_recipe='splitter', step_recipe='splitter', step_target_count=3,
        step_baseline_finished=40, step_required_crafts=3, step_minimum_crafts=3,
    )
    stock = {'splitter': held, 'electronic-circuit': 0}
    monkeypatch.setattr(builder, '_BLOCKING_MALL_ITEMS', blocking)
    monkeypatch.setattr(builder, 'active_bootstrap_loans', lambda *_: [loan])
    monkeypatch.setattr(builder, '_transferable_or_available_stock', lambda *_: stock)
    monkeypatch.setattr(builder, '_bootstrap_loan_stock', lambda *_: (dict(stock), dict(stock)))
    monkeypatch.setattr(builder, '_bootstrap_loan_products_finished', lambda *_: finished)
    monkeypatch.setattr(builder, 'mall_slot_uses_shared_provider', lambda *_: True)
    monkeypatch.setattr(builder, '_deliver_cell_ingredients', lambda *_a, **_k: pytest.fail('completion must restore, not relocate ingredients'))
    plans = []
    monkeypatch.setattr(builder, '_submit', lambda _c, _b, _s, plan, *_a, **_k: plans.append(plan))
    builder._release_completed_construction_loans(object(), object(), 'nauvis', 'player', (3, -1), lambda _: None)
    assert bool(plans) is restored
    if restored:
        actions = [a for p in plans for phase in p['phases'] for a in phase['actions']]
        machine = next(a for a in actions if a.get('recipe') == 'electronic-circuit')
        assert machine['action_type'] == 'configure_entity'
        assert machine['clear_logistic_condition'] is True
        requester = next(a for a in actions if 'clear_logistic_groups' in a)
        assert requester['clear_logistic_groups'] == [loan.group]
        assert all(a['action_type'] == 'configure_entity' for a in actions)


@pytest.mark.parametrize("pool_full", [False, True])
def test_waiting_core_promotion_releases_completed_cable_loan(
    monkeypatch, tmp_path, pool_full,
):
    """A core AM2 wait must let the queued circuit loan leave its capped cable step."""
    from orchestrator.priority_list import PriorityList

    loan = builder.MallBootstrapLoan(
        original_recipe="copper-cable", target_item="electronic-circuit",
        target_count=200, spare_target_count=200, side="left",
        requester_position=(50.5, 32.5), current_recipe="copper-cable",
        step_recipe="copper-cable", step_target_count=600,
        step_baseline_finished=507, step_required_crafts=206,
    )
    stock = {"copper-cable": 2571, "copper-plate": 4800, "iron-plate": 3000}
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(builder, "_core_mall_producer_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_prepare_core_mall_prerequisite", lambda *_a: None)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "mall_slot_count", lambda *_a: 12 if pool_full else 7)
    monkeypatch.setattr(builder, "_bootstrap_mall_slot_limit", lambda *_a: 12)

    def defer(*_a, **_kw):
        raise builder.ProductionPrerequisiteDeferred(
            "AM2 waits for its finite batch", code="temporary_mall_batch",
            state="supply_wait",
        )

    monkeypatch.setattr(builder, "ensure_produced", defer)
    monkeypatch.setattr(builder, "_rationed_mall_batch", defer)
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock))
    monkeypatch.setattr(builder, "_bootstrap_loan_products_finished", lambda *_a: 713)
    monkeypatch.setattr(builder, "_missing_chemical_ladder_predecessor", lambda *_a: None)
    monkeypatch.setattr(builder, "_maybe_announce_post_starter", lambda *_a: None)
    submitted = []
    monkeypatch.setattr(builder, "_submit", lambda _c, _b, _s, plan, *_a: submitted.append(plan))
    targets = {"electronic-circuit": 200}
    priorities = PriorityList(tmp_path / "priorities.json", 0)
    priorities.sync(targets, stock, 0)

    def serve(*args, **_kw):
        builder._submit_bootstrap_loan(
            args[0], args[1], args[2], args[3], loan, lambda _m: None,
        )
        targets.clear()

    monkeypatch.setattr(builder, "_serve_mall_task", serve)
    spent = builder._prep_core_mall(
        object(), object(), "nauvis", "player", set(), targets,
        (0, 0), lambda _m: None,
    )
    # Same control flow as run(): True bypasses the ready-task scheduler.
    if not spent:
        builder._serve_ready_pass(
            object(), object(), "nauvis", "player", priorities.next(targets, 0),
            0, targets, {}, priorities, (0, 0), "automation-science-pack",
            lambda _m: None,
        )
    assert len(submitted) == 1
    configured = [
        action for action in builder.plan_actions(submitted[0])
        if action.get("recipe") == "electronic-circuit"
    ]
    assert len(configured) == 1


@pytest.mark.parametrize("target", ["assembling-machine-2", "splitter"])
def test_core_wait_after_real_loan_transition_requires_reobservation(monkeypatch, target):
    """Promotion and restoration spend a pass without claiming new production."""
    monkeypatch.setitem(builder.LINE_RECIPES, "assembling-machine-2", {
        "machine": "assembling-machine-2", "ingredients": ["iron-plate"],
        "amounts": [1], "product_amount": 1, "craft_time": 0.5,
    })
    loan = builder.MallBootstrapLoan(
        original_recipe="iron-gear-wheel", target_item=target,
        target_count=1, side="left", requester_position=(39.5, 32.5),
        current_recipe=target,
    )
    stock = {target: 1}
    monkeypatch.setattr(builder, "_BLOCKING_MALL_ITEMS", set())
    monkeypatch.setattr(builder, "_core_mall_producer_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_prepare_core_mall_prerequisite", lambda *_a: None)
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
    monkeypatch.setattr(builder, "_bootstrap_loan_stock", lambda *_a: (stock, stock))
    monkeypatch.setattr(builder, "_BOOTSTRAP_LOAN_CONFIGURATION_REVISION", 0)
    monkeypatch.setattr(builder, "_BOOTSTRAP_LOAN_PROGRESS_REVISION", 0)
    submitted = []
    monkeypatch.setattr(builder, "_submit", lambda _c, _b, _s, _p, name, _e: submitted.append(name))

    def transition(*args, **_kw):
        builder._submit_bootstrap_loan(
            args[0], args[1], args[2], args[3], loan, lambda _m: None,
        )
        raise builder.ProductionPrerequisiteDeferred(
            "temporary batch changed configuration", code="temporary_mall_batch",
        )

    monkeypatch.setattr(builder, "ensure_produced", transition)
    assert builder._prep_core_mall(
        object(), object(), "nauvis", "player", set(), {},
        (0, 0), lambda _m: None,
    ) is True
    assert submitted == [
        "promote_bootstrap_loan_assembling-machine-2"
        if target == "assembling-machine-2" else "restore_bootstrap_loan_splitter"
    ]
    assert builder._BOOTSTRAP_LOAN_CONFIGURATION_REVISION == 1
    assert builder._BOOTSTRAP_LOAN_PROGRESS_REVISION == 0
