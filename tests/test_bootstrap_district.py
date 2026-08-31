# Path: tests/test_bootstrap_district.py
# Purpose: Verify persistent bootstrap-district identity, reservations, and retirement transitions.

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from orchestrator.bootstrap_district import (
    BootstrapDistrictLedger, BootstrapLifecycleError,
    REQUIRED_RESERVATION_ROLES,
)
from orchestrator import autonomous_builder as builder


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "bootstrap_district_state.schema.json").read_text(
        encoding="utf-8",
    )
)


def _action(entity: str, x: float, y: float) -> dict:
    return {
        "action_type": "place_ghost",
        "entity": entity,
        "position": {"x": x, "y": y},
    }


def _ledger(tmp_path: Path) -> BootstrapDistrictLedger:
    return BootstrapDistrictLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id="episode-1", surface="nauvis", force="player",
        bootstrap_profile="reduced-v1",
    )


def _reservations() -> dict[str, frozenset[tuple[int, int]]]:
    return {
        role: frozenset({(index, 10)})
        for index, role in enumerate(sorted(REQUIRED_RESERVATION_ROLES))
    }


def _provisioned(tmp_path: Path):
    ledger = _ledger(tmp_path)
    ledger.record_pioneer(
        "iron-plate", "iron-ore", [_action("electric-furnace", 1.5, 2.5)],
    )
    state = ledger.provision(
        "iron-plate", reservations=_reservations(),
        replacement_origin=(20.0, 30.0), replacement_provider=(34.5, 42.5),
        replacement_furnaces=6,
        replacement_actions=[
            _action("electric-furnace", 20.5, 30.5),
            _action("passive-provider-chest", 34.5, 42.5),
        ],
        transport_source=(10.5, 10.5),
        transport_actions=[_action("transport-belt", 10.5, 10.5)],
    )
    return ledger, state


def test_lifecycle_persists_full_footprint_and_releases_after_output(
    tmp_path: Path,
) -> None:
    ledger, state = _provisioned(tmp_path)

    state = ledger.mark_validating("iron-plate", measured_output_count=3)
    state = ledger.mark_retiring("iron-plate")
    state = ledger.mark_released("iron-plate")
    payload = json.loads(state.to_json())

    assert state.lifecycle_state == "released"
    assert state.measured_output_count == 3
    assert set(state.reservations) == REQUIRED_RESERVATION_ROLES
    assert state.replacement_actions[-1]["entity"] == "passive-provider-chest"
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))
    assert ledger.load("iron-plate") == state


def test_released_district_never_recreates_its_pioneer(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, _state = _provisioned(tmp_path)
    ledger.mark_validating("iron-plate", measured_output_count=3)
    ledger.mark_retiring("iron-plate")
    ledger.mark_released("iron-plate")
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(
        builder, "_direct_plate_foundation_ready", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "_bootstrap_direct_plate_line",
        lambda *_a: pytest.fail("released district must not recreate a pioneer"),
    )

    with pytest.raises(builder.StuckError) as failure:
        builder._prep_plate_foundation(
            object(), object(), "nauvis", "player", set(), {}, {},
            (0.0, 0.0), lambda _message: None, {}, {},
        )

    assert failure.value.code == "bootstrap_lifecycle_conflict"
    assert "refusing to recreate its pioneer" in str(failure.value)


def test_released_foundation_stays_ready_during_a_partial_later_expansion(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, _state = _provisioned(tmp_path)
    opening = [
        _action("electric-furnace", x, y)
        for y in (30.5, 33.5, 36.5)
        for x in (20.5, 26.5)
    ]
    future = [
        _action("electric-furnace", x, y)
        for y in (30.5, 33.5, 36.5)
        for x in (32.5, 38.5)
    ]
    ledger.update_replacement(
        "iron-plate", replacement_provider=(52.5, 42.5),
        replacement_furnaces=12,
        replacement_actions=opening + future,
    )
    ledger.mark_validating("iron-plate", measured_output_count=3)
    ledger.mark_retiring("iron-plate")
    ledger.mark_released("iron-plate")
    live_positions = {
        (action["position"]["x"], action["position"]["y"])
        for action in opening
    }
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(
        builder.live_base, "live_entity_positions",
        lambda _client, _surface, _force, _name, _positions: frozenset(
            live_positions
        ),
    )

    assert builder._direct_plate_foundation_ready(
        object(), "nauvis", "player", "iron-plate",
    )


def test_live_entity_positions_filters_for_the_expected_real_entity() -> None:
    class Client:
        query = ""

        def command(self, query: str) -> str:
            self.query = query
            return "1,3"

    client = Client()
    positions = ((20.5, 30.5), (20.5, 33.5), (20.5, 36.5))

    found = builder.live_base.live_entity_positions(
        client, "nauvis", "player", "electric-furnace", positions,
    )

    assert found == frozenset((positions[0], positions[2]))
    assert "name='electric-furnace'" in client.query
    assert "force=f" in client.query
    assert "e.type~='entity-ghost'" in client.query


def test_retirement_cannot_skip_measured_replacement_output(tmp_path: Path) -> None:
    ledger, _state = _provisioned(tmp_path)

    with pytest.raises(BootstrapLifecycleError, match="Illegal bootstrap transition"):
        ledger.mark_retiring("iron-plate")
    with pytest.raises(BootstrapLifecycleError, match="measured output"):
        ledger.mark_validating("iron-plate", measured_output_count=0)


def test_pioneer_and_provisioning_are_restart_idempotent(tmp_path: Path) -> None:
    ledger, first = _provisioned(tmp_path)
    resumed = _ledger(tmp_path)

    same = resumed.provision(
        "iron-plate", reservations=_reservations(),
        replacement_origin=(20.0, 30.0), replacement_provider=(34.5, 42.5),
        replacement_furnaces=6,
        replacement_actions=[
            _action("electric-furnace", 20.5, 30.5),
            _action("passive-provider-chest", 34.5, 42.5),
        ],
        transport_source=(10.5, 10.5),
        transport_actions=[_action("transport-belt", 10.5, 10.5)],
    )

    assert same.revision == first.revision
    assert resumed.states() == (same,)


def test_submitted_replacement_is_restart_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    ledger, provisioned = _provisioned(tmp_path)

    submitted = ledger.mark_replacement_submitted("iron-plate")
    repeated = _ledger(tmp_path).mark_replacement_submitted("iron-plate")

    assert submitted.replacement_submitted
    assert submitted.revision == provisioned.revision + 1
    assert repeated == submitted
    assert submitted.history[-1]["event"] == "replacement_submitted"


def test_persisted_replacement_site_cannot_move(tmp_path: Path) -> None:
    ledger, _state = _provisioned(tmp_path)

    with pytest.raises(BootstrapLifecycleError, match="site changed"):
        ledger.provision(
            "iron-plate", reservations=_reservations(),
            replacement_origin=(50.0, 60.0), replacement_provider=(64.5, 72.5),
            replacement_furnaces=6,
            replacement_actions=[_action("electric-furnace", 50.5, 60.5)],
        )


def test_released_district_cannot_regain_a_starter(tmp_path: Path) -> None:
    ledger, _state = _provisioned(tmp_path)
    ledger.mark_validating("iron-plate", 1)
    ledger.mark_retiring("iron-plate")
    ledger.mark_released("iron-plate")

    with pytest.raises(BootstrapLifecycleError, match="cannot regain"):
        ledger.record_pioneer(
            "iron-plate", "iron-ore", [_action("electric-furnace", 1.5, 2.5)],
        )


def test_controller_reserves_future_district_before_initial_build(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    ledger.record_pioneer(
        "iron-plate", "iron-ore", [_action("electric-furnace", 1.5, 2.5)],
    )
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    extraction = SimpleNamespace(
        build_plan={
            "reserved_tiles": [[0, 0], [1, 0]],
            "phases": [{"name": "mine", "actions": [
                _action("electric-mining-drill", 0.5, 0.5),
            ]}],
        },
        smelter_reserved_area=((20.0, 30.0), (40.0, 50.0)),
        smelter_origin=(20.0, 30.0), mine_origin=(0.5, 0.5), furnace_count=6,
        ore_output=(10.5, 10.5),
    )
    replacement = {"phases": [{"name": "refinery", "actions": [
        _action("electric-furnace", 20.5, 30.5),
        _action("passive-provider-chest", 34.5, 42.5),
    ]}]}
    route = [_action("transport-belt", 10.5, 10.5)]
    system = {"phases": [*replacement["phases"], {
        "name": "route", "actions": route,
    }]}

    builder._record_bootstrap_provisioning(
        "iron-plate", extraction, replacement, system, route, (34.5, 42.5),
    )
    state = ledger.load("iron-plate")

    assert state is not None
    assert state.lifecycle_state == "provisioning"
    assert state.reservations["mine_growth"] == frozenset({(0, 0), (1, 0)})
    assert (20, 30) in state.reservations["refinery_growth"]
    assert (10, 10) in state.reservations["transport_service"]
    assert state.transport_source == (10.5, 10.5)
    assert state.transport_actions[0]["entity"] == "transport-belt"
    assert any(
        action["entity"] == "passive-provider-chest"
        and action["position"] == {"x": 34.5, "y": 42.5}
        for action in state.replacement_actions
    )


def test_provisioning_retry_keeps_the_persisted_district_without_a_mine_plan(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, state = _provisioned(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    extraction = SimpleNamespace(
        build_plan=None, mine_origin=None, smelter_origin=state.replacement_origin,
        smelter_reserved_area=((20.0, 30.0), (40.0, 50.0)), furnace_count=6,
    )

    builder._record_bootstrap_provisioning(
        "iron-plate", extraction, {"phases": []}, {"phases": []}, [],
        state.replacement_provider,
    )

    assert ledger.load("iron-plate") == state


def test_provisioning_retry_reuses_exact_route_and_rejects_a_shifted_head(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, state = _provisioned(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)

    route = builder._bootstrap_owned_transport_route(
        "iron-plate", state.replacement_origin, state.transport_source,
    )

    assert route == state.transport_actions
    with pytest.raises(builder.StuckError) as failure:
        builder._bootstrap_owned_transport_route(
            "iron-plate", state.replacement_origin, (11.5, 10.5),
        )
    assert failure.value.code == "bootstrap_lifecycle_conflict"


def test_science_transition_health_requires_owned_reservations_and_output(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    states = {}
    for recipe, ore, offset in (
        ("iron-plate", "iron-ore", 0.0),
        ("copper-plate", "copper-ore", 30.0),
    ):
        ledger.record_pioneer(
            recipe, ore, [_action("electric-furnace", 1.5 + offset, 2.5)],
        )
        states[recipe] = ledger.provision(
            recipe, reservations=_reservations(),
            replacement_origin=(20.0 + offset, 30.0),
            replacement_provider=(34.5 + offset, 42.5),
            replacement_furnaces=6,
            replacement_actions=[
                _action("electric-furnace", 20.5 + offset, 30.5),
                _action("passive-provider-chest", 34.5 + offset, 42.5),
            ],
        )
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(builder, "_bootstrap_state", states.get)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_direct_plate_foundation_ready", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: pytest.fail("managed transition must use exact counters"),
    )
    output = {"iron-plate": 3, "copper-plate": 2}
    monkeypatch.setattr(
        builder, "_measured_bootstrap_replacement_output",
        lambda _c, _s, recipe, _state: output[recipe],
    )

    healthy, status = builder._metal_science_transition_status(
        object(), "nauvis", "player",
    )

    assert healthy
    assert all(
        district["reservation_sufficient"]
        and district["measured_output_count"] > 0
        for district in status["districts"].values()
    )


def test_science_transition_names_power_or_transport_remedy_before_output(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, state = _provisioned(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(builder, "_bootstrap_state", lambda _recipe: state)
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: False,
    )
    monkeypatch.setattr(
        builder, "_direct_plate_foundation_ready", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: pytest.fail("unrelated line output is not transition proof"),
    )
    monkeypatch.setattr(
        builder, "_measured_bootstrap_replacement_output", lambda *_a: 0,
    )

    healthy, status = builder._metal_science_transition_status(
        object(), "nauvis", "player",
    )

    assert not healthy
    assert status["districts"]["iron-plate"]["measured_output_count"] == 0
    assert status["districts"]["iron-plate"]["remedy"] == (
        "repair_power_or_transport"
    )


def test_controller_releases_pioneer_only_after_exact_replacement_output(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, _state = _provisioned(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    starter = builder.live_base.DirectPlateStarter((1.5, 2.5), "north", 1)
    surveys = iter((starter, None))
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: next(surveys),
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(20.5, 30.5): 3000.0},
    )
    monkeypatch.setattr(
        builder.live_base, "bootstrap_cell_origins", lambda *_a, **_k: [],
    )
    retirements: list[str] = []
    monkeypatch.setattr(
        builder, "retire_entities_via_bots",
        lambda _c, _b, _s, _f, _p, label, _e, **_k: retirements.append(label),
    )
    monkeypatch.setattr(
        builder, "_release_metal_starter_limits_if_complete", lambda *_a: None,
    )

    removed = builder._retire_standing_bootstrap_cells(
        object(), object(), "nauvis", "player", "iron-plate", "iron-ore",
        (0.0, 0.0), lambda _message: None,
    )

    state = ledger.load("iron-plate")
    assert removed == 1
    assert retirements == ["direct_iron-plate_starter"]
    assert state is not None and state.lifecycle_state == "released"
    assert state.measured_output_count == 3


def test_controller_keeps_pioneer_when_replacement_has_not_produced(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger, _state = _provisioned(tmp_path)
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    starter = builder.live_base.DirectPlateStarter((1.5, 2.5), "north", 1)
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: starter,
    )
    monkeypatch.setattr(
        builder.live_base, "progress_counters",
        lambda *_a, **_k: {(20.5, 30.5): 0.5},
    )
    monkeypatch.setattr(builder, "consume_wait", lambda *_a: None)
    monkeypatch.setattr(
        builder, "retire_entities_via_bots",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("unvalidated pioneer must not be removed")
        ),
    )

    removed = builder._retire_standing_bootstrap_cells(
        object(), object(), "nauvis", "player", "iron-plate", "iron-ore",
        (0.0, 0.0), lambda _message: None,
    )

    state = ledger.load("iron-plate")
    assert removed == 0
    assert state is not None and state.lifecycle_state == "provisioning"


def test_pioneer_cannot_adopt_nearby_refinery_or_enter_retirement(
    tmp_path: Path, monkeypatch,
) -> None:
    ledger = _ledger(tmp_path)
    ledger.record_pioneer(
        "stone-brick", "stone", [_action("electric-furnace", 54.5, -67.5)],
    )
    monkeypatch.setattr(builder, "_BOOTSTRAP_DISTRICT_LEDGER", ledger)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: pytest.fail("a pioneer district has no replacement line"),
    )

    assert not builder._direct_plate_foundation_ready(
        object(), "nauvis", "player", "stone-brick",
    )

    starter = builder.live_base.DirectPlateStarter((54.5, -67.5), "north", 1)
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: starter,
    )
    monkeypatch.setattr(
        builder, "_measured_bootstrap_replacement_output",
        lambda *_a: pytest.fail("pioneer state has no replacement to measure"),
    )

    with pytest.raises(builder.StuckError) as failure:
        builder._retire_standing_bootstrap_cells(
            object(), object(), "nauvis", "player", "stone-brick", "stone",
            (0.0, 0.0), lambda _message: None,
        )

    assert failure.value.code == "bootstrap_lifecycle_conflict"
    assert "before replacement provisioning" in str(failure.value)
