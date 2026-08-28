# Path: tests/test_recoverable_retirement.py
# Purpose: Prove planner-owned teardown uses bot deconstruction and exact restart-safe identity.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator import recoverable_retirement as retirement


def _plan() -> dict:
    return {"phases": [{"name": "retire", "actions": [
        {
            "action_type": "remove_entity",
            "entity": "electric-furnace",
            "position": {"x": 1.5, "y": 2.5},
        },
        {
            "action_type": "remove_entity",
            "entity": "passive-provider-chest",
            "position": {"x": 1.5, "y": 5.5},
        },
    ]}]}


def test_retirement_orders_bot_deconstruction_and_waits_for_recovery(
    monkeypatch,
) -> None:
    observations = iter((
        {
            (1.5, 2.5): {"name": "electric-furnace"},
            (1.5, 5.5): {"name": "passive-provider-chest"},
        },
        {
            (1.5, 2.5): {"name": "NONE"},
            (1.5, 5.5): {"name": "NONE"},
        },
    ))
    monkeypatch.setattr(
        retirement.live_base, "entity_signatures_at",
        lambda *_a, **_k: next(observations),
    )
    monkeypatch.setattr(retirement, "load_json", lambda report: report)
    calls: list[tuple[dict, dict, str, str]] = []

    def execute(authorization, plan, *, surface, force):
        calls.append((authorization, plan, surface, force))
        return {"actions": [
            {"status": "success"}, {"status": "success"},
        ]}

    messages: list[str] = []
    count = retirement.retire_entities_via_bots(
        object(), SimpleNamespace(execute_deconstruction=execute),
        "nauvis", "player", _plan(), "iron_starter", messages.append,
    )

    assert count == 2
    authorization, plan, surface, force = calls[0]
    assert authorization["approved_actions"] == ["apply_deconstruction"]
    assert authorization["scope_limits"]["max_count"] == 2
    assert surface == "nauvis" and force == "player"
    assert [action["action"] for action in plan["actions"]] == [
        "deconstruct_entity", "deconstruct_entity",
    ]
    assert {action["name"] for action in plan["actions"]} == {
        "electric-furnace", "passive-provider-chest",
    }
    assert "construction bots" in messages[0]


def test_retirement_skips_already_absent_entities_on_restart(monkeypatch) -> None:
    observations = iter((
        {
            (1.5, 2.5): {"name": "NONE"},
            (1.5, 5.5): {"name": "passive-provider-chest"},
        },
        {(1.5, 5.5): {"name": "NONE"}},
    ))
    monkeypatch.setattr(
        retirement.live_base, "entity_signatures_at",
        lambda *_a, **_k: next(observations),
    )
    monkeypatch.setattr(retirement, "load_json", lambda report: report)
    submitted: list[dict] = []

    def execute(_authorization, plan, **_scope):
        submitted.append(plan)
        return {"actions": [{"status": "success"}]}

    count = retirement.retire_entities_via_bots(
        object(), SimpleNamespace(execute_deconstruction=execute),
        "nauvis", "player", _plan(), "iron_starter", lambda _m: None,
    )

    assert count == 1
    assert [action["name"] for action in submitted[0]["actions"]] == [
        "passive-provider-chest",
    ]


def test_retirement_rejects_a_different_live_occupant(monkeypatch) -> None:
    monkeypatch.setattr(
        retirement.live_base, "entity_signatures_at",
        lambda *_a, **_k: {
            (1.5, 2.5): {"name": "assembling-machine-2"},
            (1.5, 5.5): {"name": "passive-provider-chest"},
        },
    )
    bridge = SimpleNamespace(
        execute_deconstruction=lambda *_a, **_k: pytest.fail(
            "ownership mismatch must fail before mutation"
        ),
    )

    with pytest.raises(
        retirement.RecoverableRetirementError,
        match="assembling-machine-2, expected planner-owned electric-furnace",
    ):
        retirement.retire_entities_via_bots(
            object(), bridge, "nauvis", "player", _plan(),
            "iron_starter", lambda _m: None,
        )
