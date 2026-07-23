# Path: tests/test_electronics_radial_execution.py
# Purpose: Fake-only regression tests for atomic infrastructure plus center-out
#          production construction of the real composed action shape.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.electronics_radial_execution import (
    RadialExecutionError,
    execute_electronics_bundle_radial,
    partition_actions_into_rings,
)


def _action(entity: str, x: float, y: float, **extra) -> dict:
    return {
        "action_type": "place_ghost",
        "entity": entity,
        "position": {"x": x, "y": y},
        **extra,
    }


def _layout_report(plan: dict) -> dict:
    actions = [action for phase in plan["phases"] for action in phase["actions"]]
    ghosts = sum(action["action_type"] == "place_ghost" for action in actions)
    entities = sum(action["action_type"] == "place_entity" for action in actions)
    return {
        "ok": True,
        "attempted_ghosts": ghosts,
        "placed_ghosts": ghosts,
        "already_present_ghosts": 0,
        "failed_ghosts": 0,
        "attempted_entities": entities,
        "placed_entities": entities,
        "already_present_entities": 0,
        "failed_entities": 0,
        "attempted_placements": len(actions),
        "succeeded_placements": len(actions),
        "already_present_placements": 0,
        "failed_placements": 0,
    }


def _bundle() -> dict:
    return {
        "infrastructure": [(
            "infrastructure",
            {"phases": [{"name": "infra", "actions": [{
                "action_type": "place_entity",
                "entity": "roboport",
                "position": {"x": -128.0, "y": -128.0},
            }]}]},
        )],
        "plans": [(
            "production",
            {"phases": [{"name": "machines", "actions": [
                _action("small-electric-pole", -128.0, -128.0),
                _action("small-electric-pole", -82.0, -128.0),
                _action("small-electric-pole", -42.0, -128.0),
            ]}]},
        )],
        "scaffolding": {"anchors": [{"x": -128.0, "y": -128.0}]},
    }


class FakeBridge:
    def __init__(self, events: list[str]):
        self.events = events
        self.build_layout_calls: list[dict] = []

    def build_layout(self, _authorization: dict, plan: dict) -> dict:
        name = plan["phases"][0]["name"]
        self.events.append(f"build:{name}")
        self.build_layout_calls.append(plan)
        return _layout_report(plan)

    def ensure_scaffolding(self, _payload: dict) -> dict:
        self.events.append("scaffold")
        return {"ok": True, "created_entities": 1, "bots_inserted": 1, "inserted": {}}

    def verify_electronics_execution(self) -> dict:
        self.events.append("verify")
        return {"ok": True}

    def save_game(self) -> str:
        self.events.append("save")
        return "ok"


class FakeRcon:
    def __init__(self, events: list[str], *, electric_networks: int = 1, roboport_networks: int = 1):
        self.events = events
        self.electric_networks = electric_networks
        self.roboport_networks = roboport_networks

    def command(self, _text: str) -> str:
        self.events.append("network")
        return (
            "{\"electric_poles\":3,\"electric_networks\":"
            f"{self.electric_networks},\"roboports\":2,\"roboport_networks\":"
            f"{self.roboport_networks},\"roboports_without_network\":0}}"
        )


def _patch_execution_dependencies(monkeypatch, events: list[str], *, fail_ring: int | None = None) -> None:
    import tools.electronics_radial_execution as radial

    monkeypatch.setattr(radial, "prepare_existing_topology", lambda *_: events.append("prepare"))
    monkeypatch.setattr(
        radial, "_seed_ore", lambda *_: events.append("seed") or {"seeded_ore_tiles": 1},
    )
    monkeypatch.setattr(radial, "production_materials", lambda _: {"small-electric-pole": 10})
    monkeypatch.setattr(radial, "spawn_spidertron", lambda *_args, **_kwargs: events.append("spawn") or 7)
    monkeypatch.setattr(radial, "read_construction_radius", lambda *_: events.append("radius") or 40.0)
    monkeypatch.setattr(radial, "cleanup_spidertron", lambda *_: events.append("cleanup"))
    monkeypatch.setattr(radial, "wait_for_game_ticks", lambda *_args, **_kwargs: events.append("settle"))
    monkeypatch.setattr(radial, "validate_live_report", lambda report: report)

    def build_ring(_client, _surface, ring, **_kwargs):
        index = len([event for event in events if event.startswith("tour:")])
        events.append(f"tour:{index}:{len(ring)}")
        if index == fail_ring:
            raise RuntimeError("ring tour failed")
        return True

    monkeypatch.setattr(radial, "build_ring", build_ring)


def test_partition_actions_into_rings_preserves_full_mixed_actions():
    actions = [
        _action("assembling-machine-3", 43, 0, direction="east", recipe="processing-unit"),
        _action("underground-belt", 0, 0, direction="north", underground_type="input"),
        _action("infinity-pipe", 83, 0, infinity_filter="water", fill_percentage=0.5),
        _action("underground-belt", 0, 1, direction="north", underground_type="output"),
    ]

    rings = partition_actions_into_rings(actions, (0.0, 0.0), 40.0)

    assert [[action["position"]["x"] for action in ring] for ring in rings] == [[0, 0], [43], [83]]
    assert [action for ring in rings for action in ring] == [actions[1], actions[3], actions[0], actions[2]]
    assert rings[1][0]["recipe"] == "processing-unit"
    assert rings[2][0]["infinity_filter"] == "water"
    assert rings[0][1]["underground_type"] == "output"


def test_fragmented_infrastructure_stops_before_production_placement(monkeypatch):
    events: list[str] = []
    _patch_execution_dependencies(monkeypatch, events)
    bridge = FakeBridge(events)
    rcon = FakeRcon(events, electric_networks=2)

    with pytest.raises(RadialExecutionError, match="fragmented"):
        execute_electronics_bundle_radial(
            bridge, rcon, _bundle(), world=SimpleNamespace(), existing_topology="refuse",
            settle_ticks=1, settle_timeout_seconds=1,
        )

    assert [plan["phases"][0]["name"] for plan in bridge.build_layout_calls] == ["infrastructure/infra"]
    assert "spawn" not in events


def test_radial_execution_orders_atomic_infrastructure_then_ascending_rings(monkeypatch):
    events: list[str] = []
    _patch_execution_dependencies(monkeypatch, events)
    result = execute_electronics_bundle_radial(
        FakeBridge(events), FakeRcon(events), _bundle(), world=SimpleNamespace(),
        existing_topology="refuse", settle_ticks=60, settle_timeout_seconds=1,
    )

    assert result["live"] == {"ok": True}
    assert events == [
        "prepare", "seed", "build:infrastructure/infra", "network", "spawn", "radius",
        "build:ring_0", "tour:0:1", "build:ring_1", "tour:1:1", "build:ring_2", "tour:2:1",
        "scaffold", "settle", "verify", "cleanup", "save",
    ]


def test_radial_execution_cleans_up_spidertron_after_mid_ring_failure(monkeypatch):
    events: list[str] = []
    _patch_execution_dependencies(monkeypatch, events, fail_ring=1)

    with pytest.raises(RuntimeError, match="ring tour failed"):
        execute_electronics_bundle_radial(
            FakeBridge(events), FakeRcon(events), _bundle(), world=SimpleNamespace(),
            existing_topology="refuse", settle_ticks=1, settle_timeout_seconds=1,
        )

    assert events[-2:] == ["cleanup", "save"]