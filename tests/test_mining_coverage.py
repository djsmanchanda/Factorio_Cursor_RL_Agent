# Path: tests/test_mining_coverage.py
# Purpose: Prove new mine blueprints obtain construction coverage for their complete footprint before submission.

from __future__ import annotations

from types import SimpleNamespace

from orchestrator import autonomous_builder


def test_new_mine_extends_coverage_to_full_blueprint_before_submit(monkeypatch) -> None:
    plan = {
        "phases": [
            {"name": "power", "actions": [
                {"action_type": "place_entity", "entity": "substation",
                 "position": {"x": 48.0, "y": -70.0}},
                {"action_type": "place_ghost", "entity": "medium-electric-pole",
                 "position": {"x": 48.0, "y": -66.0}},
            ]},
            {"name": "mine", "actions": [
                {"action_type": "place_ghost", "entity": "electric-mining-drill",
                 "position": {"x": 50.5, "y": -67.5}},
                {"action_type": "place_ghost", "entity": "electric-mining-drill",
                 "position": {"x": 53.5, "y": -67.5}},
                {"action_type": "place_ghost", "entity": "transport-belt",
                 "position": {"x": 49.5, "y": -65.5}},
            ]},
        ]
    }
    extraction = SimpleNamespace(
        mine_origin=(52.0, -66.0), build_plan=plan, ore="stone",
        ore_output=(49.5, -65.5),
    )
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(autonomous_builder, "_publish_output_chest", lambda _plan: None)
    monkeypatch.setattr(
        autonomous_builder, "extend_roboport_coverage",
        lambda *_args: events.append(("coverage", _args[4])) or False,
    )
    monkeypatch.setattr(
        autonomous_builder, "_submit",
        lambda *_args: events.append(("submit", None)) or {},
    )
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(autonomous_builder, "_diagnose_machines", lambda *_args, **_kwargs: None)

    client = SimpleNamespace(command=lambda _command: "")
    autonomous_builder._place_new_mine(
        client, object(), "nauvis", "player", extraction, lambda _message: None,
    )

    coverage_targets = {value for kind, value in events if kind == "coverage"}
    assert (48.0, -70.0) in coverage_targets
    assert (53.5, -65.5) in coverage_targets
    assert [kind for kind, _value in events].index("coverage") < [
        kind for kind, _value in events
    ].index("submit")