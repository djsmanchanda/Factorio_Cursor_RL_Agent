# Path: tests/test_mining_coverage.py
# Purpose: Prove new mine blueprints obtain construction coverage for their complete footprint before submission.

from __future__ import annotations

from types import SimpleNamespace

from orchestrator import autonomous_builder, live_base


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
    # One distant hub exists; every blueprint position is outside its reach.
    ports: list[list[tuple[float, float]]] = [[(0.0, 0.0)]]
    monkeypatch.setattr(autonomous_builder, "_publish_output_chest", lambda _plan: None)
    monkeypatch.setattr(
        live_base, "roboport_positions",
        lambda *_args: list(ports[0]),
    )

    def fake_extend(_client, _bridge, _surface, _force, target, _emit, *, reserved_tiles=None):
        events.append(("coverage", target))
        ports[0].append((target[0], target[1] - 10))
        return True

    monkeypatch.setattr(autonomous_builder, "extend_roboport_coverage", fake_extend)

    def fake_submit(_client, _bridge, _surface, _plan, name, _emit, *, stage_coverage=None):
        events.append(("affordable", name))
        if stage_coverage is not None:
            stage_coverage()
        events.append(("submit", name))
        return {}

    monkeypatch.setattr(autonomous_builder, "_submit", fake_submit)
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(autonomous_builder, "_diagnose_machines", lambda *_args, **_kwargs: None)

    client = SimpleNamespace(command=lambda _command: "")
    autonomous_builder._place_new_mine(
        client, object(), "nauvis", "player", extraction, lambda _message: None,
    )

    # The whole cluster sits within one chained port's radius, so a single
    # extension serves it -- coverage follows POSITIONS, not box corners.
    assert [value for kind, value in events if kind == "coverage"] == [(48.0, -70.0)]
    kinds = [kind for kind, _value in events]
    assert kinds.index("affordable") < kinds.index("coverage") < kinds.index("submit")
