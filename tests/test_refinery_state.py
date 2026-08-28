# Path: tests/test_refinery_state.py
# Purpose: Prove live modular-refinery recovery and End/output removal authorization fail closed.

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import live_base, refinery_state  # noqa: E402
from planners.plan_validation import actions  # noqa: E402
from planners.smelter_block import (  # noqa: E402
    generate_managed_refinery_extension_plan,
    generate_managed_refinery_plan,
)

_DIRECTIONS = {"north": 0, "east": 4, "south": 8, "west": 12}


def _furnaces(plan: dict) -> tuple[tuple[float, float], ...]:
    return tuple(sorted(
        (action["position"]["x"], action["position"]["y"])
        for action in actions(plan) if action["entity"] == "electric-furnace"
    ))


def _signatures(plan: dict) -> dict:
    result = {}
    for action in actions(plan):
        position = (action["position"]["x"], action["position"]["y"])
        result[position] = {
            "name": action["entity"],
            "direction": _DIRECTIONS.get(action.get("direction", "north"), 0),
            "input_priority": action.get("input_priority"),
            "output_priority": action.get("output_priority"),
        }
    return result


def test_infers_one_complete_six_furnace_module() -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, origin_x=20, origin_y=-10)

    state = refinery_state.infer_refinery_state("iron-plate", _furnaces(plan))

    assert state.origin == (20.0, -10.0)
    assert (state.shape.columns, state.shape.middle_rows) == (1, 0)
    assert state.furnace_count == 6


def test_rejects_disconnected_or_partial_furnace_lattices() -> None:
    positions = ((3.5, 4.5), (9.5, 4.5), (3.5, 40.5), (9.5, 40.5))

    with pytest.raises(ValueError, match="complete six-furnace modules"):
        refinery_state.infer_refinery_state("iron-plate", positions)


def test_live_recovery_requires_the_blueprint_splitter_signature(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("copper-plate", 30)
    signatures = _signatures(plan)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )

    state = refinery_state.recover_managed_refinery(
        object(), "nauvis", "player", "copper-plate", _furnaces(plan),
    )

    assert state.shape.columns == 5


def test_live_recovery_tolerates_a_missing_retained_belt(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6)
    signatures = _signatures(plan)
    belt = next(
        action for action in actions(plan) if action["entity"] == "fast-transport-belt"
    )
    signatures.pop((belt["position"]["x"], belt["position"]["y"]))
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {
            position: signatures[position]
            for position in positions if position in signatures
        },
    )

    state = refinery_state.recover_managed_refinery(
        object(), "nauvis", "player", "iron-plate", _furnaces(plan),
    )

    assert state.furnace_count == 6


def test_live_recovery_rejects_a_rotated_splitter(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6)
    signatures = _signatures(plan)
    splitter = next(
        action for action in actions(plan) if action["entity"] == "fast-splitter"
    )
    signatures[(splitter["position"]["x"], splitter["position"]["y"])]["direction"] = 8
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )

    with pytest.raises(ValueError, match="not the planner-owned template"):
        refinery_state.recover_managed_refinery(
            object(), "nauvis", "player", "iron-plate", _furnaces(plan),
        )


def test_live_refinery_placements_include_only_matching_entities(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    state = refinery_state.infer_refinery_state(
        "iron-plate", _furnaces(plan), variant="basic",
    )
    signatures = _signatures(plan)
    belt = next(
        action for action in actions(plan) if action["entity"] == "transport-belt"
    )
    missing = (belt["position"]["x"], belt["position"]["y"])
    signatures.pop(missing)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {
            position: signatures[position]
            for position in positions if position in signatures
        },
    )

    owned = refinery_state.live_refinery_placements(
        _RconOutput(""), "nauvis", "player", state,
    )

    assert ("transport-belt", *missing) not in owned
    assert ("electric-furnace", *state.machine_positions[0]) in owned


def test_removal_authorization_checks_the_exact_old_end_and_output(monkeypatch) -> None:
    old = generate_managed_refinery_plan("iron-plate", 30)
    delta = generate_managed_refinery_extension_plan("iron-plate", 30, 31)
    signatures = _signatures(old)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )
    state = refinery_state.infer_refinery_state("iron-plate", _furnaces(old))

    refinery_state.assert_refinery_removals_owned(
        object(), "nauvis", "player", state, delta,
    )


def test_removal_authorization_uses_persisted_owned_actions(monkeypatch) -> None:
    old = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    delta = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic",
    )
    signatures = _signatures(old)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )
    state = replace(
        refinery_state.infer_refinery_state(
            "iron-plate", _furnaces(old), variant="basic",
        ),
        owned_actions=tuple(
            action for action in actions(old)
            if action["action_type"] in {"place_entity", "place_ghost"}
        ),
    )
    monkeypatch.setattr(
        refinery_state, "generate_managed_refinery_plan",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("persisted ownership must replace regenerated inference")
        ),
    )

    refinery_state.assert_refinery_removals_owned(
        object(), "nauvis", "player", state, delta,
    )


def test_removal_authorization_allows_an_absent_old_output_adapter(monkeypatch) -> None:
    """An interrupted build may leave the old End chest absent.

    The executor's exact removal is then a no-op; the extension must be able
    to install the new End/output adapter instead of abandoning iron growth.
    """
    old = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    delta = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic",
    )
    signatures = _signatures(old)
    missing_positions = {
        (action["position"]["x"], action["position"]["y"])
        for action in actions(delta) if action["action_type"] == "remove_entity"
    }
    for position in missing_positions:
        signatures.pop(position)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {
            position: signatures.get(position, {
                "name": "NONE", "direction": None,
                "input_priority": None, "output_priority": None,
            })
            for position in positions
        },
    )
    state = refinery_state.infer_refinery_state(
        "iron-plate", _furnaces(old), variant="basic",
    )

    refinery_state.assert_refinery_removals_owned(
        object(), "nauvis", "player", state, delta,
    )


def test_removal_authorization_rejects_a_different_live_end_occupant(monkeypatch) -> None:
    old = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    delta = generate_managed_refinery_extension_plan(
        "iron-plate", 6, 12, current_variant="basic",
    )
    signatures = _signatures(old)
    target = next(
        action for action in actions(delta) if action["action_type"] == "remove_entity"
    )
    position = (target["position"]["x"], target["position"]["y"])
    signatures[position] = {
        "name": "steel-chest", "direction": 0,
        "input_priority": None, "output_priority": None,
    }
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )
    state = refinery_state.infer_refinery_state(
        "iron-plate", _furnaces(old), variant="basic",
    )

    with pytest.raises(ValueError, match="not the planner-owned template"):
        refinery_state.assert_refinery_removals_owned(
            object(), "nauvis", "player", state, delta,
        )


class _RconOutput:
    def __init__(self, output: str):
        self.output = output
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.output


def test_batch_signature_survey_parses_entities_and_missing_slots() -> None:
    client = _RconOutput("1|2|fast-splitter|4|left|right;3|4|NONE|-|-|-")

    found = live_base.entity_signatures_at(
        client, "nauvis", "player", [(1, 2), (3, 4)],
    )

    assert found[(1.0, 2.0)]["input_priority"] == "left"
    assert found[(1.0, 2.0)]["direction"] == 4
    assert found[(3.0, 4.0)]["name"] == "NONE"
    assert "math.abs(candidate.position.x-p[1])<0.01" in client.commands[0]
    assert "candidate.type~='logistic-robot'" in client.commands[0]
    assert "e=e or ghost" in client.commands[0]


def test_live_recovery_accepts_regular_belt_bootstrap_variant(monkeypatch) -> None:
    plan = generate_managed_refinery_plan("iron-plate", 6, variant="basic")
    signatures = _signatures(plan)
    monkeypatch.setattr(
        live_base, "entity_signatures_at",
        lambda _c, _s, _f, positions: {position: signatures[position] for position in positions},
    )

    state = refinery_state.recover_managed_refinery(
        object(), "nauvis", "player", "iron-plate", _furnaces(plan),
    )

    assert state.variant == "basic"
    assert state.interfaces.ore_inputs == ((-0.5, 1.5),)
    assert state.interfaces.plate_outputs == ((14.5, 12.5),)
