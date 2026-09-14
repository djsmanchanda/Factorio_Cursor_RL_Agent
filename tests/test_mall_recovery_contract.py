# Path: tests/test_mall_recovery_contract.py
# Purpose: Prove paired-mall survey responses are classified by topology using fakes.

from __future__ import annotations

from types import SimpleNamespace

from orchestrator import autonomous_builder as builder
from orchestrator import mall_builder
import pytest
from planners.mall_layout import generate_promoted_mall_retirement_plan


def test_promoted_shared_mall_half_keeps_its_siblings_provider() -> None:
    plan = generate_promoted_mall_retirement_plan(
        "electronic-circuit", "assembling-machine-2", (36.5, 44.5),
        (39.5, 43.5), preserve_provider=True,
    )

    assert not any(
        action.get("action_type") == "remove_entity"
        and action.get("entity") == "passive-provider-chest"
        for action in plan["phases"][0]["actions"]
    )


def test_complete_matching_pair_migrates_to_two_request_sections(
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda *_args, **_kwargs: captured.setdefault("plan", _args[3]),
    )

    changed = mall_builder.refresh_paired_mall_requests(
        object(), object(), "nauvis", "player", "copper-cable",
        [(36.5, 32.5), (42.5, 32.5)], (3.0, -1.0),
        lambda _message: None,
    )

    assert changed is True
    actions = captured["plan"]["phases"][0]["actions"]
    assert len(actions) == 1, "both machines share one physical requester"
    sections = actions[0]["logistic_sections"]
    assert {section["group"] for section in sections} == {
        "mall:copper-cable:left", "mall:copper-cable:right",
    }
    assert sum(section["multiplier"] for section in sections) == 46


def test_pair_refresh_merges_stale_groups_from_both_sides(monkeypatch) -> None:
    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    captured = {}
    monkeypatch.setattr(
        mall_builder.live_base, "requester_logistic_groups",
        lambda *_a: (
            "mall:iron-gear-wheel:left",
            "mall:inserter:right",
        ),
    )
    monkeypatch.setattr(
        mall_builder, "_submit",
        lambda *_args, **_kwargs: captured.setdefault("plan", _args[3]),
    )

    mall_builder.refresh_paired_mall_requests(
        _LiveClient(), object(), "nauvis", "player", "copper-cable",
        [(36.5, 32.5), (42.5, 32.5)], (3.0, -1.0),
        lambda _message: None,
    )

    requester = captured["plan"]["phases"][0]["actions"][0]
    assert "mall:iron-gear-wheel:left" in requester["clear_logistic_groups"]
    assert "mall:inserter:right" in requester["clear_logistic_groups"]


def test_reassigned_half_clears_only_its_stale_recipe_group(monkeypatch) -> None:
    class _LiveClient:
        def command(self, _text: str) -> str:
            return ""

    monkeypatch.setattr(
        mall_builder.live_base, "requester_logistic_groups",
        lambda *_a: (
            "mall:iron-gear-wheel:left",
            "mall:copper-cable:left",
            "mall:electronic-circuit:right",
        ),
    )
    action = {
        "position": {"x": 39.5, "y": 32.5},
        "clear_logistic_groups": ["mall:copper-cable"],
    }

    mall_builder._clear_stale_side_requests(
        _LiveClient(), "nauvis", action, "copper-cable", "left",
    )

    assert action["clear_logistic_groups"] == [
        "mall:copper-cable", "mall:iron-gear-wheel:left",
    ]


def test_science_call_repairs_starved_paired_mall_transport(monkeypatch) -> None:
    """The live fault: two valid mall gear assemblers were passed to line
    recovery, which rejected their six-tile spacing as invalid line geometry."""
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    existing = SimpleNamespace(
        machine_count=2,
        working_count=0,
        machine_positions=machines,
        output_position=provider,
    )
    plan = SimpleNamespace(
        existing=existing,
        at_size=True,
        promote_to_line=False,
    )
    monkeypatch.setattr(builder, "_plan_line", lambda *_a, **_k: plan)
    monkeypatch.setattr(builder, "_refresh_mall_cell", lambda *_a, **_k: provider)
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder.live_base, "nearest_pole_on_other_network", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "_existing_stage_chests", lambda *_a, **_k: [])
    monkeypatch.setattr(
        builder.live_base,
        "entity_statuses",
        lambda *_a, **_k: {position: "item_ingredient_shortage" for position in machines},
    )
    repaired = []
    monkeypatch.setattr(
        builder, "repair_existing_ingredient_transport",
        lambda *_a, **_k: repaired.append(True) or True,
    )

    output = builder.ensure_produced(
        object(), object(), "nauvis", "player", "iron-gear-wheel",
        (3.0, -1.0), lambda _message: None, upgrade_bootstrap=True,
    )

    assert output == provider
    assert repaired == []


@pytest.mark.parametrize("second", ["assembling-machine-2", None, "entity-ghost"])
def test_stock_gate_uses_live_tiers_and_reobserves_upgrades(monkeypatch, second):
    machines = ((50.5, 32.5), (56.5, 32.5))
    names = dict(zip(machines, ["assembling-machine-1", second]))
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_positions=machines),
        spec={"machine": "assembling-machine-2"}, production_target=50,
        mall_storage_limit=50, fill_provider=False,
    )
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a: (53.5, 31.5))
    monkeypatch.setattr(builder, "mall_slot_uses_shared_provider", lambda *_a: False)
    monkeypatch.setattr(builder.live_base, "entity_names_at", lambda *_a: names)
    submitted = []
    monkeypatch.setattr(builder, "_submit", lambda *args: submitted.append(args[3]))

    def refresh():
        builder._refresh_mall_cell(
            SimpleNamespace(command=lambda *_a: ""), object(), "nauvis", "player",
            "transport-belt", plan, lambda _m: None, upgrade_bootstrap=False,
            stock_gate_target=50,
        )

    if second != "assembling-machine-2":
        with pytest.raises(builder.ProductionPrerequisiteDeferred) as error:
            refresh()
        assert error.value.code == "mall_stock_gate_target_pending"
        assert all("stock_gate" not in p["phases"][0]["name"] for p in submitted)
        return
    refresh()
    refresh()
    names[machines[0]] = "assembling-machine-2"
    refresh()
    gates = [p["phases"][0]["actions"] for p in submitted if "stock_gate" in p["phases"][0]["name"]]
    assert [[a["entity"] for a in actions] for actions in gates] == [
        ["assembling-machine-1", "assembling-machine-2"],
        ["assembling-machine-2", "assembling-machine-2"],
    ]
    assert all(a["action_type"] == "configure_entity" for actions in gates for a in actions)


def test_upgrade_call_still_detects_the_paired_mall_provider(monkeypatch) -> None:
    provider = (53.5, 31.5)
    existing = SimpleNamespace(machine_positions=((50.5, 32.5), (56.5, 32.5)))
    plan = SimpleNamespace(
        existing=existing, spec={}, production_target=1, mall_storage_limit=1,
        fill_provider=False,
    )
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a, **_k: provider)

    found = builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "iron-gear-wheel", plan,
        lambda _message: None, upgrade_bootstrap=True,
    )

    assert found == provider


def test_unchanged_mall_maintenance_is_submitted_once_per_run(monkeypatch) -> None:
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_positions=machines),
        spec={"machine": "assembling-machine-2"},
        production_target=50,
        mall_storage_limit=50,
        fill_provider=False,
        mall_request_multiplier=15,
    )
    refreshed = []
    submitted = []
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_mineable", lambda _item: False)
    monkeypatch.setattr(
        builder, "refresh_paired_mall_requests",
        lambda *_args, **_kwargs: refreshed.append(True) or True,
    )
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a: provider)
    monkeypatch.setattr(
        builder, "mall_slot_uses_shared_provider", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_args, **_kwargs: submitted.append(_args[4]),
    )

    for _ in range(2):
        builder._refresh_mall_cell(
            object(), object(), "nauvis", "player", "splitter", plan,
            lambda _message: None, upgrade_bootstrap=False,
            stock_gate_target=50,
        )

    assert refreshed == [True]
    assert submitted == ["mall_provider_limit_splitter", "mall_stock_gate_splitter"]


def test_mall_reserve_and_gate_do_not_shrink_later_in_the_run(monkeypatch) -> None:
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    submitted = []
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_MALL_PROVIDER_CAPACITY_FLOORS", {})
    monkeypatch.setattr(builder, "_MALL_STOCK_GATE_FLOORS", {})
    monkeypatch.setattr(builder, "_mineable", lambda _item: False)
    monkeypatch.setattr(builder, "refresh_paired_mall_requests", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a: provider)
    monkeypatch.setattr(builder, "mall_slot_uses_shared_provider", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_args, **_kwargs: submitted.append(_args[3]),
    )

    def refresh(target: int) -> None:
        plan = SimpleNamespace(
            existing=SimpleNamespace(machine_positions=machines),
            spec={"machine": "assembling-machine-2"},
            production_target=target,
            mall_storage_limit=target,
            fill_provider=False,
            mall_request_multiplier=15,
        )
        builder._refresh_mall_cell(
            object(), object(), "nauvis", "player", "transport-belt", plan,
            lambda _message: None, upgrade_bootstrap=False,
            stock_gate_target=target,
        )

    refresh(400)
    refresh(5)

    provider_updates = [
        action for plan in submitted for phase in plan["phases"]
        for action in phase["actions"]
        if action["entity"] == "passive-provider-chest"
    ]
    gate_updates = [
        action for plan in submitted for phase in plan["phases"]
        for action in phase["actions"]
        if "logistic_condition" in action
    ]
    assert len(provider_updates) == 1
    assert len(gate_updates) == len(machines)
    assert {
        action["logistic_condition"]["constant"] for action in gate_updates
    } == {400}


def test_long_blocking_reserve_can_fund_a_second_bootstrap_producer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(machine_count=1),
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"electronic-circuit": 0},
    )
    monkeypatch.setattr(builder, "backlog_seconds", lambda *_a: 180.0)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (True, {}),
    )

    wanted = builder._bootstrap_reserve_machine_target(
        object(), "nauvis", "player", "electronic-circuit", 200,
        (3.0, -1.0), lambda _message: None, background=False,
    )

    assert wanted == 2
    assert "electronic-circuit" in builder._BOOTSTRAP_SHARED_PROVIDER_ITEMS


def test_anchor_backlog_can_fund_one_bounded_temporary_slot(monkeypatch) -> None:
    """Gear/cable keep an anchor while large construction demand may add one."""
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(machine_count=2),
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"iron-gear-wheel": 0},
    )
    monkeypatch.setattr(builder, "backlog_seconds", lambda *_a: 180.0)
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (True, {}),
    )
    messages: list[str] = []

    wanted = builder._bootstrap_reserve_machine_target(
        object(), "nauvis", "player", "iron-gear-wheel", 400,
        (3.0, -1.0), messages.append, background=False,
    )

    assert wanted == 3
    assert any("temporary producers" in message for message in messages)


def test_new_compact_cell_cannot_escape_the_global_bootstrap_slot_cap(monkeypatch) -> None:
    """Core-mall promotion may not bypass the pre-plastic shared budget."""
    plan = builder._LinePlan(
        existing=None, spec=builder.LINE_RECIPES["transport-belt"],
        production_target=1, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, demand=0.0, saturated=False,
        promoted_count=None, promote_to_line=False, at_size=True,
    )
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "mall_slot_count", lambda *_a: builder.BOOTSTRAP_MALL_SLOT_TARGET,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_a, **_k: pytest.fail("must not spend inputs"),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as raised:
        builder._build_assembled_stage(
            object(), object(), "nauvis", "player", "transport-belt",
            (3.0, -1.0), lambda _message: None, plan, None,
            upgrade_bootstrap=False,
        )

    assert raised.value.code == "bootstrap_mall_slot_cap"


def test_post_plastic_per_item_cell_bypasses_demand_bank_cap(monkeypatch) -> None:
    plan = builder._LinePlan(
        existing=None, spec=builder.LINE_RECIPES["transport-belt"],
        production_target=1, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, demand=0.0, saturated=False,
        promoted_count=None, promote_to_line=False, at_size=True,
        demand_slot=False,
    )
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
    monkeypatch.setattr(
        builder, "mall_demand_slot_count",
        lambda *_a: builder.DEMAND_MALL_SLOT_TARGET,
    )
    monkeypatch.setattr(builder, "_ingredient_sources", lambda *_a, **_k: None)

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "transport-belt",
        (3.0, -1.0), lambda _message: None, plan, None,
        upgrade_bootstrap=False,
    )


def test_post_advanced_demand_cell_has_no_slot_cap(monkeypatch) -> None:
    plan = builder._LinePlan(
        existing=SimpleNamespace(machine_count=1),
        spec=builder.LINE_RECIPES["transport-belt"],
        production_target=1, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, demand=0.0, saturated=False,
        promoted_count=None, promote_to_line=False, at_size=False,
        demand_slot=True,
    )
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
    monkeypatch.setattr(
        builder, "mall_demand_slot_count",
        lambda *_a: builder.DEMAND_MALL_SLOT_TARGET,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        builder, "build_compact_mall_stage", lambda *_a, **_k: (1.0, 1.0),
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "transport-belt",
        (3.0, -1.0), lambda _message: None, plan, None,
        upgrade_bootstrap=False,
    )


def test_under_sized_existing_line_cannot_escape_the_global_slot_cap(monkeypatch) -> None:
    """Adding a temporary anchor half is also a new compact slot."""
    plan = builder._LinePlan(
        existing=SimpleNamespace(machine_count=2),
        spec=builder.LINE_RECIPES["transport-belt"],
        production_target=1, mall_storage_limit=1, fill_provider=False,
        mall_request_multiplier=None, demand=0.0, saturated=False,
        promoted_count=None, promote_to_line=False, at_size=False,
    )
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(
        builder, "mall_slot_count", lambda *_a: builder.BOOTSTRAP_MALL_SLOT_TARGET,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_a, **_k: pytest.fail("must not spend inputs"),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as raised:
        builder._build_assembled_stage(
            object(), object(), "nauvis", "player", "transport-belt",
            (3.0, -1.0), lambda _message: None, plan, None,
            upgrade_bootstrap=False,
        )

    assert raised.value.code == "bootstrap_mall_slot_cap"


def test_circuit_is_a_rotational_precore_batch(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    assert "electronic-circuit" not in builder._MALL_RECIPE_ANCHORS
    assert builder._is_pre_core_temporary_mall_item(
        object(), "nauvis", "player", "electronic-circuit",
    )


def test_tier1_assembler_scales_circuit_producer_at_lower_backlog(monkeypatch) -> None:
    monkeypatch.setattr(builder, "_BOOTSTRAP_SHARED_PROVIDER_ITEMS", set())
    monkeypatch.setattr(builder, "_core_mall_ready", lambda *_a: False)
    monkeypatch.setattr(builder, "_metal_starter_transition_complete", lambda *_a: False)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(machine_count=1),
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"electronic-circuit": 0},
    )
    monkeypatch.setattr(builder, "mall_machine", lambda *_a: "assembling-machine-1")
    monkeypatch.setattr(
        builder, "_bootstrap_demand_cell_affordable", lambda *_a: (True, {}),
    )

    wanted = builder._bootstrap_reserve_machine_target(
        object(), "nauvis", "player", "electronic-circuit", 40,
        (3.0, -1.0), lambda _message: None, background=False,
    )

    assert wanted == 2
    assert "electronic-circuit" in builder._BOOTSTRAP_SHARED_PROVIDER_ITEMS
