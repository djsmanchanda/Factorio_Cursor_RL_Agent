# Path: tests/test_starter_provider_retention.py
# Purpose: Explicit starter retirement persists its exact teardown, retains a stocked provider, and resumes pending bots.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from orchestrator import autonomous_builder as builder
from orchestrator import recoverable_retirement as retirement
from orchestrator.bootstrap_district import (
    BootstrapDistrictLedger, BootstrapDistrictState, BootstrapLifecycleError,
    REQUIRED_RESERVATION_ROLES,
)
from planners.bootstrap_smelting import generate_direct_smelter
from planners.smelter_block import refinery_interfaces

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "bootstrap_district_state.schema.json").read_text())
CHEST = "passive-provider-chest"
STARTER = builder.live_base.DirectPlateStarter((1.5, 2.5), "north", 1)
PROVIDER = refinery_interfaces(6, origin_x=20, origin_y=30, variant="basic").provider


def _ghost(entity: str, x: float, y: float) -> dict:
    return {"action_type": "place_ghost", "entity": entity, "position": {"x": x, "y": y}}


def _starter_world() -> dict[tuple[float, float], str]:
    actions = generate_direct_smelter("iron-plate", "iron-ore", (1.5, 2.5), "north")
    return {(a["position"]["x"], a["position"]["y"]): a["entity"]
            for a in actions["phases"][0]["actions"]}


def _starter_chest() -> tuple[float, float]:
    return next(p for p, name in _starter_world().items() if name == CHEST)


def _ledger(tmp_path: Path) -> BootstrapDistrictLedger:
    ledger = BootstrapDistrictLedger(
        tmp_path / "script-output" / "factorio_cursor_rl", episode_id="episode-1",
        surface="nauvis", force="player", bootstrap_profile="reduced-v1",
    )
    ledger.record_pioneer("iron-plate", "iron-ore", [_ghost("electric-furnace", 1.5, 5.5)])
    ledger.provision(
        "iron-plate",
        reservations={role: frozenset({(i, 10)}) for i, role in enumerate(sorted(REQUIRED_RESERVATION_ROLES))},
        replacement_origin=(20.0, 30.0), replacement_provider=PROVIDER, replacement_furnaces=6,
        replacement_actions=[_ghost("electric-furnace", 20.5 + 3 * i, 30.5) for i in range(6)]
        + [_ghost(CHEST, *PROVIDER)],
        transport_source=(10.5, 10.5), transport_actions=[_ghost("transport-belt", 10.5, 10.5)],
    )
    return ledger


class _Client:
    def command(self, _text: str) -> str:
        return ""


class _World:
    """Exact entity occupancy; bots clear ordered machinery but cannot empty a full chest."""

    def __init__(self, tmp_path: Path, *, slow: frozenset = frozenset()) -> None:
        self.entities = _starter_world()
        self.ordered: list[set[str]] = []
        self.slow = set(slow)
        self.tmp_path = tmp_path

    def signatures(self, _c, _s, _f, positions):
        return {p: {"name": self.entities.get(p, "NONE")} for p in positions}

    def execute_deconstruction(self, _auth, plan, **_k):
        self.ordered.append({a["name"] for a in plan["actions"]})
        for action in plan["actions"]:
            point = (action["position"]["x"], action["position"]["y"])
            if action["name"] == CHEST:
                continue  # a full chest: every bot is still hauling plates
            if action["name"] in self.slow:
                self.slow.discard(action["name"])  # recovered only after this wait
                continue
            self.entities.pop(point, None)
        report = self.tmp_path / f"report-{len(self.ordered)}.json"
        report.write_text(json.dumps({"actions": [{"status": "success"} for _ in plan["actions"]]}))
        return report


def _live(monkeypatch, world: _World, *, output: float = 1000.0) -> dict[str, int]:
    clock = iter(range(0, 10**6, 200))
    monkeypatch.setattr(retirement.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(retirement.time, "sleep", lambda _s: None)
    monkeypatch.setattr(builder.live_base, "entity_signatures_at", world.signatures)
    surveys = iter((STARTER,))
    monkeypatch.setattr(builder.live_base, "direct_plate_starter", lambda *_a, **_k: next(surveys, None))
    # Only one of the six replacement furnaces has produced: partial operation suffices.
    monkeypatch.setattr(builder.live_base, "progress_counters", lambda *_a, **_k: {(20.5, 30.5): output})
    monkeypatch.setattr(builder.live_base, "chest_contents", lambda *_a: {"iron-plate": 1})
    monkeypatch.setattr(builder.live_base, "bootstrap_cell_origins", lambda *_a, **_k: [])
    monkeypatch.setattr(builder.live_base, "teardown_storage_room", lambda *_a: (1, 5))
    monkeypatch.setattr(builder, "_retire_unused_starter_power_branch", lambda *_a: 0)
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    calls = {"limits": 0}
    monkeypatch.setattr(builder, "_release_metal_starter_limits_if_complete",
                        lambda *_a: calls.__setitem__("limits", calls["limits"] + 1))
    return calls


def _retire(world: _World, messages: list[str]) -> int:
    return builder._retire_standing_bootstrap_cells(
        _Client(), world, "nauvis", "player", "iron-plate", "iron-ore", (0.0, 0.0),
        messages.append,
    )


def test_partial_replacement_output_retires_machinery_and_retains_full_provider(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    world = _World(tmp_path)
    calls = _live(monkeypatch, world)

    assert _retire(world, []) == 1

    state = ledger.load("iron-plate")
    assert world.ordered and all(CHEST not in order for order in world.ordered)
    assert world.entities.get(_starter_chest()) == CHEST
    assert state.lifecycle_state == "released"
    assert state.retained_providers == (_starter_chest(),)
    assert calls["limits"] == 1  # startup caps lift with the machinery, not the chest
    assert json.loads(state.to_json())["version"] == 2


def test_bot_timeout_resumes_persisted_teardown_instead_of_failing(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    world = _World(tmp_path, slow=frozenset({"electric-furnace"}))
    calls = _live(monkeypatch, world)
    messages: list[str] = []

    assert _retire(world, messages) == 0
    pending = ledger.load("iron-plate")
    assert pending.lifecycle_state == "retiring" and pending.starter_geometry is not None
    assert pending.retained_providers == (_starter_chest(),)
    assert any("BOOTSTRAP RETIRE PENDING" in m for m in messages)
    # A timeout is resumable work, never a release or a false success.
    assert calls["limits"] == 0

    # The live survey can no longer recognize the partly removed stack.
    assert _retire(world, messages) == 1
    assert world.ordered[1] <= world.ordered[0]
    assert ledger.load("iron-plate").lifecycle_state == "released"
    assert calls["limits"] == 1


def test_starter_machinery_waits_for_storage_room_then_resumes(tmp_path, monkeypatch):
    # Furnace output and inserter hands are cargo too: nothing is ordered until storage can take it.
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    world = _World(tmp_path)
    calls = _live(monkeypatch, world)
    room = iter(((6, 0), (6, 9)))
    monkeypatch.setattr(builder.live_base, "teardown_storage_room", lambda *_a: next(room))
    messages: list[str] = []

    with pytest.raises(retirement.RecoverableRetirementStorageWait) as raised:
        _retire(world, messages)

    assert raised.value.code == "teardown_storage_wait"
    assert raised.value.state == "supply_wait"
    assert raised.value.details["stacks_needed"] > raised.value.details["empty_storage_stacks"]
    assert world.ordered == []
    durable = ledger.load("iron-plate")
    assert durable.lifecycle_state == "retiring" and durable.starter_geometry is not None
    assert any("waits for logistic storage" in m for m in messages)
    assert calls["limits"] == 0

    assert _retire(world, messages) == 1
    assert ledger.load("iron-plate").lifecycle_state == "released" and calls["limits"] == 1


def _released_with_provider(tmp_path, monkeypatch) -> BootstrapDistrictLedger:
    ledger = _ledger(tmp_path)
    ledger.mark_validating("iron-plate", 1)
    ledger.mark_retiring(
        "iron-plate",
        starter_geometry={"drill_position": [1.5, 2.5], "output_direction": "north", "pole_side": 1},
        retained_providers=[_starter_chest()],
    )
    ledger.mark_released("iron-plate")
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(builder, "_STARTER_PROVIDER_RECLAIM_ORDERED", {})
    monkeypatch.setattr(builder, "_STARTER_PROVIDER_LAST_NOTE", {})
    return ledger


def _reclaim_world(monkeypatch, *, contents: dict, room=(1, 5), present=True) -> list[dict]:
    state = {"present": present}
    monkeypatch.setattr(builder.live_base, "entity_signatures_at",
                        lambda _c, _s, _f, ps: {p: {"name": CHEST if state["present"] else "NONE"} for p in ps})
    monkeypatch.setattr(builder.live_base, "chest_contents", lambda *_a: dict(contents))
    monkeypatch.setattr(builder.live_base, "teardown_storage_room", lambda *_a: room)
    orders: list[dict] = []

    def order(_c, _b, _s, _f, plan, _label, _e, **kwargs):
        orders.append({"plan": plan, **kwargs})
        state["present"] = False
        raise retirement.RecoverableRetirementPending("bots still working")

    monkeypatch.setattr(builder, "retire_entities_via_bots", order)
    return orders


def _reclaim(ledger) -> None:
    builder._reclaim_retained_starter_providers(
        _Client(), object(), "nauvis", "player", ledger.load("iron-plate"), lambda _m: None,
    )


def test_nonempty_provider_stays_while_it_drains(tmp_path, monkeypatch):
    ledger = _released_with_provider(tmp_path, monkeypatch)
    orders = _reclaim_world(monkeypatch, contents={"iron-plate": 1200})

    _reclaim(ledger)

    assert orders == []
    assert ledger.load("iron-plate").retained_providers == (_starter_chest(),)


def test_empty_provider_is_reclaimed_without_blocking(tmp_path, monkeypatch):
    ledger = _released_with_provider(tmp_path, monkeypatch)
    orders = _reclaim_world(monkeypatch, contents={})

    _reclaim(ledger)
    assert len(orders) == 1 and orders[0]["timeout"] == 0.0
    assert ledger.load("iron-plate").retained_providers == (_starter_chest(),)

    _reclaim(ledger)  # the next pass observes the chest gone
    state = ledger.load("iron-plate")
    assert state.retained_providers == ()
    assert state.history[-1]["event"] == "retained_provider_reclaimed_drained"


def test_reclaim_waits_for_proven_storage_room(tmp_path, monkeypatch):
    ledger = _released_with_provider(tmp_path, monkeypatch)
    orders = _reclaim_world(monkeypatch, contents={}, room=(1, 0))

    _reclaim(ledger)

    assert orders == []
    assert ledger.load("iron-plate").retained_providers == (_starter_chest(),)


def test_reclaim_without_a_covering_network_stays_retained(tmp_path, monkeypatch):
    ledger = _released_with_provider(tmp_path, monkeypatch)
    orders = _reclaim_world(monkeypatch, contents={}, room=None)

    _reclaim(ledger)

    assert orders == []


def test_storage_wait_from_the_helper_does_not_mark_the_reclaim_ordered(tmp_path, monkeypatch):
    ledger = _released_with_provider(tmp_path, monkeypatch)
    _reclaim_world(monkeypatch, contents={})

    def full(*_a, **_k):
        raise retirement.RecoverableRetirementStorageWait("full", needed=1, free=0)

    monkeypatch.setattr(builder, "retire_entities_via_bots", full)
    monkeypatch.setattr(builder, "_STARTER_PROVIDER_RECLAIM_ORDERED", {})

    _reclaim(ledger)

    assert builder._STARTER_PROVIDER_RECLAIM_ORDERED == {}


def test_operation_maintenance_reclaims_retained_providers(monkeypatch):
    from orchestrator.operation_session import DeterministicSession

    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.emit = object(), object(), lambda _m: None
    calls = []
    monkeypatch.setattr(builder, "_refresh_logistic_chest_budget", lambda *_a: None)
    monkeypatch.setattr(builder, "_refresh_owned_standing_gates", lambda *_a: None)
    monkeypatch.setattr(builder, "_maintain_retained_starter_providers", lambda *a: calls.append(a))
    monkeypatch.setattr(builder, "_MATERIAL_RESERVATION_LEDGER", None)

    session._prepare_operation()

    assert len(calls) == 1


def test_runtime_initialization_clears_provider_caches(monkeypatch):
    from types import SimpleNamespace

    builder._STARTER_PROVIDER_RECLAIM_ORDERED[(1.5, 2.5)] = "drained"
    builder._STARTER_PROVIDER_LAST_NOTE[(1.5, 2.5)] = "note"
    monkeypatch.setattr(builder, "load_json", lambda _path: {"recipes": []})
    monkeypatch.setattr(builder, "install_catalog_line_recipes", lambda _catalog: [])
    monkeypatch.setattr(builder, "install_catalog_machines", lambda _catalog: {})
    monkeypatch.setattr(builder, "install_catalog_stack_sizes", lambda _catalog: None)
    monkeypatch.setattr(builder, "validate_builder_target", lambda *_a: None)

    builder.initialize_operation_runtime(
        SimpleNamespace(export_recipe_catalog=lambda **_k: "catalog.json"),
        "nauvis", "player", "automation-science-pack", lambda _m: None,
    )

    assert builder._STARTER_PROVIDER_RECLAIM_ORDERED == {}
    assert builder._STARTER_PROVIDER_LAST_NOTE == {}


def test_version_one_district_reads_as_version_two_and_schema_accepts_retained(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.mark_validating("iron-plate", 1)
    state = ledger.mark_retiring(
        "iron-plate",
        starter_geometry={"drill_position": [1.5, 2.5], "output_direction": "north", "pole_side": 1},
        retained_providers=[_starter_chest()],
    )
    payload = json.loads(state.to_json())
    assert payload["version"] == 2
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))

    legacy = dict(payload, version=1)
    del legacy["starter_geometry"], legacy["retained_providers"]
    migrated = BootstrapDistrictState.from_json(json.dumps(legacy))
    assert migrated.version == 2 and migrated.retained_providers == ()

    with pytest.raises(BootstrapLifecycleError):
        BootstrapDistrictState.from_json(json.dumps(dict(payload, lifecycle_state="provisioning")))
