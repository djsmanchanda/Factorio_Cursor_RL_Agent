# Path: tests/test_optimizer_build_slice_replay.py
# Purpose: Replay one synthetic offline build slice -- explicit line geometry,
# a derived ghost-only artifact, a disposable training identity, and a
# tmp_path material ledger -- without observing or mutating a Factorio runtime.
#
# No optimizer module is invoked or invented here; the file name records the
# requested integration slice. Every line coordinate, prototype-to-item
# mapping, stock count, progress fact and topology/observation input in this
# module is SYNTHETIC test data. Nothing here grants runtime authorization,
# reserves real stock, measures bots/placement/construction/throughput, or
# contacts a server, save, sandbox surface or live force.

from __future__ import annotations

from pathlib import Path

import pytest

from core.execution_readiness import propose_execution
from orchestrator.material_reservations import (
    MaterialReservationError,
    MaterialReservationLedger,
    plan_material_bill,
)
from planners.ghost_line_artifact import (
    ARTIFACT_STATUS,
    ARTIFACT_VERSION,
    derive_ghost_artifact,
)
from planners.science_block_geometry import build_science_block_geometry
from training.isolation import assert_training_identity

_SCENARIO_ID = "optimizer-slice-replay-001"
_SURFACE = f"training/{_SCENARIO_ID}"
_FORCE = f"training-{_SCENARIO_ID}"
_EPISODE_ID = f"synthetic-episode-{_SCENARIO_ID}"
_OTHER_EPISODE_ID = f"synthetic-episode-{_SCENARIO_ID}-other"

# SYNTHETIC provenance: this is a hand-authored test mapping, not data read
# from a Factorio prototype export and not verified against any runtime.
_ITEM_MAPPING_PROVENANCE = (
    "SYNTHETIC TEST FIXTURE ONLY: hand-authored prototype-to-item mapping; "
    "never exported from, or verified against, a Factorio runtime"
)

# SYNTHETIC build telemetry: plain test data, not a game observation.
_BUILD_INTENT = {
    "intents": [
        {
            "kind": "block_placeholder",
            "block_type": "iron-gear-wheel",
            "count": 2,
        },
    ],
}


def _explicit_gear_line_spec() -> dict:
    """Return one explicit synthetic line request; its origin is unsurveyed."""

    return {
        "recipe": "iron-gear-wheel",
        "machine_count": 2,
        "origin_x": -40,
        "origin_y": 24,
        "belt_type": "transport-belt",
        "inserter_type": "fast-inserter",
        "mining_feed": False,
        "feed_style": "chained",
        "direct_bus_ingredients": [],
        "chained_ingredients": [0],
        "terminal_collector": True,
    }


def _synthetic_item_mapping(geometry: dict) -> dict:
    prototypes = set(geometry["ghost_entities"]) | set(geometry["direct_entities"])
    return {
        "provenance": _ITEM_MAPPING_PROVENANCE,
        "entities": {name: name for name in sorted(prototypes)},
        "tiles": {},
    }


def _build_synthetic_artifact() -> tuple[dict, dict]:
    geometry = build_science_block_geometry(_explicit_gear_line_spec())
    artifact = derive_ghost_artifact(
        geometry["plan"],
        item_mapping=_synthetic_item_mapping(geometry),
    )
    return geometry, artifact


def _ledger(
    tmp_path: Path,
    *,
    episode_id: str = _EPISODE_ID,
    surface: str = _SURFACE,
    force: str = _FORCE,
) -> MaterialReservationLedger:
    return MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id=episode_id,
        surface=surface,
        force=force,
    )


def _actions(plan: dict) -> list[dict]:
    return [action for phase in plan["phases"] for action in phase["actions"]]


def _synthetic_progress() -> dict:
    return {
        "ultimate_capacity": 100,
        "current_capacity": 10,
        "committed_capacity": 10,
        "active_phase_capacity": 50,
        "completed_phases": [],
        "rationale": "SYNTHETIC readiness input; not a game observation",
    }


def _synthetic_phasing() -> dict:
    return {
        "desired_active_capacity": 50,
        "previous_active_capacity": 50,
        "reason": "SYNTHETIC phasing input; not a controller decision",
        "next_allowed_actions": ["ghost_expand"],
    }


def test_explicit_synthetic_gear_line_replays_as_a_non_executable_ghost_artifact() -> None:
    geometry, artifact = _build_synthetic_artifact()

    replay = derive_ghost_artifact(
        geometry["plan"],
        item_mapping=_synthetic_item_mapping(geometry),
    )

    assert replay == artifact
    assert geometry["spec"]["recipe"] == "iron-gear-wheel"
    assert geometry["spec"]["machine_count"] == 2
    assert geometry["player_force_build_claim"] is False
    assert geometry["has_direct_actions"] is True
    assert geometry["counts"] == {
        "ghost_placements": 25,
        "direct_placements": 2,
        "removals": 0,
    }

    assert artifact["artifact_version"] == ARTIFACT_VERSION
    assert artifact["status"] == ARTIFACT_STATUS
    assert artifact["executable"] is False
    assert artifact["player_force_build_claim"] is False
    assert artifact["counts"] == {
        "ghost_placements": 27,
        "direct_placements": 0,
        "removals": 0,
        "converted_direct_placements": 2,
    }
    assert {action["action_type"] for action in _actions(artifact["plan"])} == {
        "place_ghost",
    }
    assert artifact["declared_item_bill"] == {
        "assembling-machine-2": 2,
        "fast-inserter": 5,
        "medium-electric-pole": 4,
        "steel-chest": 1,
        "transport-belt": 15,
    }
    assert plan_material_bill(artifact["plan"]) == artifact["declared_item_bill"]
    assert artifact["item_mapping"]["provenance"] == _ITEM_MAPPING_PROVENANCE
    assert set(artifact["claims"].values()) == {False}
    assert "inventory_reservation" in artifact["pending_checks"]
    assert "runtime" in artifact["pending_checks"]


def test_synthetic_training_identity_and_persisted_ledger_scope_fail_closed(
    tmp_path: Path,
) -> None:
    assert_training_identity(_SURFACE, _FORCE)

    for surface, force in (
        ("nauvis", _FORCE),
        (_SURFACE, "player"),
        ("training/optimizer-slice-replay-other", _FORCE),
    ):
        with pytest.raises(ValueError):
            assert_training_identity(surface, force)

    # This stock is SYNTHETIC in-memory fixture data, not an inventory read.
    ledger = _ledger(tmp_path)
    ledger.declare(
        "synthetic_scope_probe",
        {"transport-belt": 1},
        {"transport-belt": 1},
    )
    assert ledger.path.is_relative_to(tmp_path)
    assert ledger.path.is_file()

    # The ledger path is episode-scoped, but its persisted payload still
    # re-checks surface and force when the same episode is reopened.
    with pytest.raises(MaterialReservationError, match="scope does not match"):
        _ledger(tmp_path, force="player")
    with pytest.raises(MaterialReservationError, match="scope does not match"):
        _ledger(tmp_path, surface="nauvis")

    other_path = ledger.path.with_name(f"{_OTHER_EPISODE_ID}.json")
    other_path.write_text(ledger.path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MaterialReservationError, match="scope does not match"):
        _ledger(tmp_path, episode_id=_OTHER_EPISODE_ID)


def test_synthetic_shortage_then_funding_and_recovery_reload(
    tmp_path: Path,
) -> None:
    _, artifact = _build_synthetic_artifact()
    bill = dict(artifact["declared_item_bill"])

    # SYNTHETIC stock only: plain dictionaries, never a live/sandbox inventory.
    short_stock = {**bill, "assembling-machine-2": 1, "fast-inserter": 2}
    funded_stock = dict(bill)

    project_id = "synthetic_gear_line_commissioning"
    ledger = _ledger(tmp_path)
    short = ledger.declare(
        project_id,
        bill,
        short_stock,
        target_item="iron-gear-wheel",
        priority=100,
        hold_until_producing=True,
    )

    expected_reserved = {
        item: min(required, short_stock.get(item, 0))
        for item, required in bill.items()
    }
    expected_shortage = {
        item: required
        for item, required in bill.items()
        if short_stock.get(item, 0) < required
    }
    assert short.state == "supply_wait"
    assert short.reserved == expected_reserved
    assert ledger.shortage_targets(project_id, short_stock) == expected_shortage

    short_reload = _ledger(tmp_path)
    persisted = short_reload.projects[project_id]
    assert persisted.state == "supply_wait"
    assert persisted.required == bill
    assert persisted.reserved == expected_reserved
    assert persisted.hold_until_producing is True
    assert short_reload.shortage_targets(project_id, short_stock) == expected_shortage

    funded = short_reload.declare(
        project_id,
        bill,
        funded_stock,
        target_item="iron-gear-wheel",
        priority=100,
        hold_until_producing=True,
    )
    assert funded.state == "ready"
    assert funded.reserved == bill
    assert funded.eta_seconds == {item: 0.0 for item in bill}
    assert short_reload.shortage_targets(project_id, funded_stock) == {}

    funded_reload = _ledger(tmp_path)
    assert funded_reload.projects[project_id].state == "ready"
    assert funded_reload.projects[project_id].reserved == bill
    assert funded_reload.required_stock("transport-belt") == bill["transport-belt"]

    # SYNTHETIC lifecycle bookkeeping for a held optimizer project. This ledger
    # has no build-progress input, so `mark_constructing` alone must not release
    # stock: the project holds its reservation until an explicitly synthetic
    # completion signal arrives. No entity, ghost or bot is placed.
    funded_reload.mark_constructing(project_id, funded_stock)
    held = _ledger(tmp_path)
    assert held.projects[project_id].state == "constructing"
    assert held.projects[project_id].hold_until_producing is True
    assert held.projects[project_id].reserved == bill
    assert held.project_is_active(project_id) is True
    assert held.stock_targets() == bill

    # SYNTHETIC build telemetry only: zero (and, by the same rule, partial)
    # progress is a local fixture fact, never a game observation. Without a
    # completion signal, a reload still retains the whole reservation.
    synthetic_build_progress = {
        "built": 0,
        "total": sum(bill.values()),
        "observed": False,
    }
    assert synthetic_build_progress["observed"] is False
    no_build_reload = _ledger(tmp_path)
    assert no_build_reload.projects[project_id].state == "constructing"
    assert no_build_reload.projects[project_id].reserved == bill

    # A competing project cannot reuse stock already held by the optimizer,
    # whether it asks for the allocatable remainder or re-declares full stock.
    competitor_id = "synthetic_competing_project"
    no_free_stock = {item: 0 for item in bill}
    assert held.allocatable_stock(funded_stock, claimant=competitor_id) == no_free_stock
    competing = held.declare(
        competitor_id,
        {"transport-belt": 1},
        funded_stock,
        priority=10,
    )
    assert competing.state == "supply_wait"
    assert competing.reserved == {}
    competing_reload = _ledger(tmp_path)
    assert competing_reload.projects[project_id].reserved == bill
    assert competing_reload.projects[competitor_id].reserved == {}
    assert competing_reload.allocatable_stock(
        funded_stock, claimant=competitor_id,
    ) == no_free_stock
    competing_reload.fail(competitor_id)

    # SYNTHETIC completion signal: a local test flag stands in for a runtime
    # observation. `complete` is called only after this signal, and it proves a
    # persisted terminal state -- not commissioning, placement or throughput.
    synthetic_completion_signal = {
        "source": "SYNTHETIC TEST SIGNAL",
        "observed": False,
        "built": sum(bill.values()),
    }
    assert synthetic_completion_signal["observed"] is False
    competing_reload.complete(project_id)
    completed = _ledger(tmp_path)
    assert completed.projects[project_id].state == "completed"
    assert completed.projects[project_id].reserved == {}
    assert completed.stock_targets() == {}
    assert completed.allocatable_stock(funded_stock) == funded_stock

    # SYNTHETIC recovery bookkeeping through the existing fail/re-declare API.
    recovery_id = "synthetic_recovery_probe"
    recovering = completed.declare(
        recovery_id,
        {"transport-belt": 2},
        {},
        priority=10,
    )
    assert recovering.state == "supply_wait"
    completed.fail(recovery_id)
    failed = _ledger(tmp_path)
    assert failed.projects[recovery_id].state == "failed"
    recovered = failed.declare(
        recovery_id,
        {"transport-belt": 2},
        {"transport-belt": 2},
        priority=10,
    )
    assert recovered.state == "ready"
    assert recovered.reserved == {"transport-belt": 2}
    assert _ledger(tmp_path).projects[recovery_id].state == "ready"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("PARTIAL", "reconciliation_partial"),
        ("BLOCKED", "reconciliation_blocked"),
    ],
)
def test_synthetic_partial_and_blocked_recovery_propose_no_projection(
    status: str,
    reason: str,
) -> None:
    progress = _synthetic_progress()
    phasing = _synthetic_phasing()

    proposal = propose_execution(progress, phasing, _BUILD_INTENT, status).to_dict()

    assert proposal["allowed_actions"] == ["hold_position"]
    assert proposal["next_recommended_step"] == "hold_position"
    assert proposal["requires_human_approval"] is True
    assert proposal["blocked_actions"] == [
        {"action": "project_more_ghosts", "reason": reason},
        {"action": "request_phase_advance", "reason": reason},
        {"action": "request_module_upgrade", "reason": reason},
    ]
    assert "project_more_ghosts" not in proposal["allowed_actions"]
