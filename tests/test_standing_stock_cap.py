# Path: tests/test_standing_stock_cap.py
# Purpose: A standing mall cell caps its output at the demand-derived stock target instead of filling its provider or keeping the largest target it ever saw.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.live_base import LineState
from planners.plan_validation import actions
from planners.stock_gating import stock_gate


def _standing_plan(*, storage_limit: int = 200) -> SimpleNamespace:
    return SimpleNamespace(
        existing=SimpleNamespace(
            machine_positions=((10.5, 10.5), (16.5, 10.5)),
        ),
        spec={
            "machine": "assembling-machine-2",
            "ingredients": ["iron-gear-wheel", "iron-plate"],
            "amounts": [1, 1],
            "product_amount": 2,
        },
        production_target=200,
        mall_storage_limit=storage_limit,
        fill_provider=False,
    )


def _drive_refresh(monkeypatch, submitted, *, stock_gate_target: int) -> None:
    provider = (13.5, 9.5)
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(
        builder, "_submit",
        lambda _client, _bridge, _surface, plan, _name, _emit: submitted.append(plan),
    )
    builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "transport-belt",
        _standing_plan(), lambda _message: None,
        upgrade_bootstrap=False, stock_gate_target=stock_gate_target,
    )


def test_a_standing_gate_follows_current_demand_not_a_historical_max(
    monkeypatch,
) -> None:
    """A previous large bill (a one-off 600-cable/inserter batch) must not pin a
    standing cell to keep filling 600 after the demand falls back."""
    submitted: list[dict] = []
    _drive_refresh(monkeypatch, submitted, stock_gate_target=600)
    _drive_refresh(monkeypatch, submitted, stock_gate_target=50)

    gates = [
        action["logistic_condition"]
        for action in actions(submitted[-1])
        if "logistic_condition" in action
    ]

    assert gates == [stock_gate("transport-belt", 50)] * 2


def test_standing_gate_target_is_demand_plus_reserve_plus_wip() -> None:
    from orchestrator.parts_mall import standing_gate_target

    assert standing_gate_target(200, 50) == 250
    assert standing_gate_target(200, 50, wip_gap=40) == 290


def test_standing_gate_target_never_returns_a_nonpositive_cap() -> None:
    from orchestrator.parts_mall import standing_gate_target

    assert standing_gate_target(0) == 1


def test_the_effective_gate_folds_the_transferable_wip_gap(monkeypatch) -> None:
    """Requester-held WIP counts in the logistic network but a bill cannot
    spend it; folding the gap in makes the gate close on transferable stock."""
    monkeypatch.setattr(builder, "_startup_mall_item_cap", lambda *_a: None)
    monkeypatch.setattr(builder, "_rationed_mall_wip_gap", lambda *_a: 40)

    assert builder._effective_mall_stock_gate(
        object(), "nauvis", "player", "transport-belt",
        upgrade_bootstrap=False, requested=200,
    ) == 240


def test_a_bootstrap_gate_is_left_unreconciled() -> None:
    assert builder._effective_mall_stock_gate(
        object(), "nauvis", "player", "transport-belt",
        upgrade_bootstrap=True, requested=200,
    ) == 200


def test_standing_cell_without_explicit_gate_gets_current_bill_and_reserve(monkeypatch):
    from orchestrator.material_reservations import MaterialReservationLedger
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', SimpleNamespace(stock_targets=lambda: {'copper-cable': 80}))
    monkeypatch.setattr(builder, '_released_request_multiple', lambda _c,_s,_f,n: n)
    target = builder._standing_mall_stock_target(object(), 'nauvis', 'player', 'copper-cable', 1)
    assert target > 80 and target < 200
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', SimpleNamespace(stock_targets=lambda: {}))
    assert builder._standing_mall_stock_target(object(), 'nauvis', 'player', 'copper-cable', 1) < target


def test_cached_standing_source_updates_gate_when_demand_changes(monkeypatch):
    captured = []
    monkeypatch.setattr(builder, 'MANAGED_INTERMEDIATE_SOURCES', {'copper-cable': (10.5,10.5)})
    monkeypatch.setattr(builder, '_complete_material_producer', lambda *_: None)
    monkeypatch.setattr(builder, '_refresh_owned_standing_gates', lambda *_a, **k: captured.append(k))
    output = builder.ensure_produced(object(), object(), 'nauvis', 'player', 'copper-cable', (0,0), lambda _:None,
                                    upgrade_bootstrap=False, stock_target=80)
    assert output == (10.5,10.5)
    assert captured and captured[0]['requested'] == {'copper-cable': 80}


def test_gate_reapplies_a_previous_target_after_intervening_demand(monkeypatch):
    submitted = []
    _drive_refresh(monkeypatch, submitted, stock_gate_target=50)
    _drive_refresh(monkeypatch, submitted, stock_gate_target=80)
    _drive_refresh(monkeypatch, submitted, stock_gate_target=50)
    gates = [p for p in submitted if any("logistic_condition" in a for a in actions(p))]
    assert len(gates) == 3
    assert next(a['logistic_condition'] for a in actions(submitted[-1]) if 'logistic_condition' in a) == stock_gate('transport-belt', 50)


def test_registered_owned_cell_follows_open_bill_changes_without_new_build(monkeypatch):
    demanded = {'copper-cable': 80}
    monkeypatch.setattr(builder, '_MATERIAL_RESERVATION_LEDGER', SimpleNamespace(stock_targets=lambda: demanded))
    monkeypatch.setattr(builder, '_released_request_multiple', lambda _c,_s,_f,n: n)
    monkeypatch.setattr(builder, '_rationed_mall_wip_gap', lambda *_:0)
    monkeypatch.setattr(builder, '_MALL_STOCK_GATE_FLOORS', {('nauvis','player','copper-cable'):600})
    machine = (10.5,10.5)
    monkeypatch.setattr(builder.live_base, 'find_line', lambda *_: SimpleNamespace(machine_positions=(machine,)))
    monkeypatch.setattr(builder.live_base, 'entity_names_at', lambda *_: {machine:'assembling-machine-1'})
    submitted = []
    monkeypatch.setattr(builder, '_submit', lambda _c,_b,_s,p,_n,_e:submitted.append(p))
    client = SimpleNamespace(command=lambda _: '')
    for needed in [80, 0, 80]:
        demanded['copper-cable'] = needed
        builder._refresh_owned_standing_gates(client, object(), 'nauvis','player',lambda _:None)
    targets = [next(a['logistic_condition']['constant'] for a in actions(p) if 'logistic_condition' in a) for p in submitted]
    assert len(targets) == 3 and targets[0] == targets[2] > targets[1]


def _standing_refresh_over_line(monkeypatch, positions, *, borrowed_at) -> list[dict]:
    """Run the standing refresh over one electronic-circuit line, some cells on loan."""
    submitted: list[dict] = []
    line = LineState(
        recipe="electronic-circuit", machine_count=len(positions), working_count=0,
        output_position=None, machine_positions=tuple(positions),
    )
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)
    monkeypatch.setattr(builder, "_released_request_multiple", lambda *_a: 1)
    monkeypatch.setattr(builder, "_rationed_mall_wip_gap", lambda *_a: 0)
    monkeypatch.setattr(builder, "_startup_mall_item_cap", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: line)
    monkeypatch.setattr(
        builder.live_base, "entity_names_at",
        lambda _client, _surface, points: {point: "assembling-machine-1" for point in points},
    )
    monkeypatch.setattr(
        builder, "active_bootstrap_loans",
        lambda *_a: [SimpleNamespace(machine_position=point) for point in borrowed_at],
    )
    monkeypatch.setattr(
        builder, "_submit",
        lambda _client, _bridge, _surface, plan, _name, _emit: submitted.append(plan),
    )
    builder._refresh_owned_standing_gates(
        SimpleNamespace(command=lambda _: ""), object(), "nauvis", "player",
        lambda _message: None, requested={"electronic-circuit": 1},
    )
    return submitted


def test_standing_refresh_leaves_a_loan_owned_cell_to_its_loan(monkeypatch) -> None:
    """2026-10-10 C4: a splitter loan's electronic-circuit step (target 5) sat
    disabled at 3 for 15 decisions because the standing refresh re-capped the
    borrowed cell every decision, overriding the loan gate."""
    submitted = _standing_refresh_over_line(
        monkeypatch, [(45.5, 38.5)], borrowed_at=[(45.5, 38.5)],
    )

    assert submitted == []


def test_standing_refresh_still_gates_owned_cells_beside_a_loan(monkeypatch) -> None:
    """Only the borrowed cell is released; a free twin keeps its standing cap."""
    submitted = _standing_refresh_over_line(
        monkeypatch, [(45.5, 38.5), (42.5, 30.5)], borrowed_at=[(45.5, 38.5)],
    )
    gated = {
        (action["position"]["x"], action["position"]["y"])
        for plan in submitted for action in plan["phases"][0]["actions"]
    }

    assert gated == {(42.5, 30.5)}


def test_measured_standing_twins_ungate_then_rejoin_current_demand(monkeypatch):
    positions = ((10.5, 10.5), (16.5, 10.5), (22.5, 10.5))
    demanded = {"electronic-circuit": 100}
    submitted = []
    monkeypatch.setattr(builder, "_MALL_STOCK_GATE_FLOORS", {})
    monkeypatch.setattr(builder, "_MALL_REFRESH_SIGNATURES", set())
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(stock_targets=lambda: demanded))
    monkeypatch.setattr(builder, "_released_request_multiple", lambda *_: 1)
    monkeypatch.setattr(builder, "_rationed_mall_wip_gap", lambda *_: 0)
    monkeypatch.setattr(builder, "_startup_mall_item_cap", lambda *_: None)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_: LineState(
        recipe="electronic-circuit", machine_count=len(positions), working_count=0,
        output_position=None, machine_positions=positions))
    monkeypatch.setattr(builder.live_base, "entity_names_at",
                        lambda _c, _s, points: {p: "assembling-machine-1" for p in points})
    loans = [SimpleNamespace(machine_position=positions[-1])]
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_: loans)
    monkeypatch.setattr(builder, "_submit", lambda _c, _b, _s, plan, _n, _e: submitted.append(plan))
    client = SimpleNamespace(command=lambda _: "")
    def refresh(rates):
        builder._refresh_owned_standing_gates(client, object(), "nauvis", "player", lambda _: None,
                                             rate_targets=rates)
        return list(actions(submitted[-1]))
    ungated = refresh({"electronic-circuit": .5})
    assert len(ungated) == 2 and all(a["clear_logistic_condition"] for a in ungated)
    gated = refresh({})
    assert [a["logistic_condition"]["constant"] for a in gated] == [101, 101]
    demanded.clear()
    assert [a["logistic_condition"]["constant"] for a in refresh({})] == [2, 2]
    loans.clear()  # A restored cell rejoins the same canonical target.
    assert len(refresh({})) == 3
    assert all(a["logistic_condition"]["constant"] == 2 for a in actions(submitted[-1]))


def test_open_bill_overrides_scarce_metal_cap_and_shrinks_when_closed(monkeypatch):
    demanded = {"electronic-circuit": 100}
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(stock_targets=lambda: demanded))
    monkeypatch.setattr(builder, "_startup_mall_item_cap", lambda *_: 5)
    monkeypatch.setattr(builder, "_rationed_mall_wip_gap", lambda *_: 40)
    def target():
        return builder._effective_mall_stock_gate(object(), "nauvis", "player", "electronic-circuit",
                                                 upgrade_bootstrap=False, requested=100)
    assert target() == 140  # One aggregate bill, including WIP a builder cannot spend.
    demanded.clear()
    assert target() == 5  # Optional reserves alone do not bypass starter rationing.
