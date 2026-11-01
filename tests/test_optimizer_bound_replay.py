# Path: tests/test_optimizer_bound_replay.py
# Purpose: One offline, synthetic end-to-end replay from the local historical
# recipe export through demand, explicit science-line geometry, a ghost
# artifact, its hash-bound construction plan, the receipt reducer and a
# disposable held-reservation ledger.
#
# The catalog bytes are the only historical input. Every scope, surface, force,
# episode, project, transaction, prototype-to-item mapping, stock count and
# counter window below is SYNTHETIC test data. Nothing here contacts Factorio,
# RCON, a save, a stock, a sandbox surface or a live force, and no result is a
# live provenance, placement or production claim.

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.optimizer_artifact_binding import (
    ArtifactBindingError,
    ArtifactBindingReason,
    bind_artifact_to_construction_plan,
)
from core.optimizer_construction_state import (
    ConstructionBinding,
    ConstructionPhase,
    ConstructionReason,
    ConstructionReceipt,
    ConstructionScope,
    ConstructionStateError,
    FundingAction,
    ITEMS_PER_SECOND,
    ObservationAction,
    OrderAction,
    ReleaseAction,
    ThroughputEvidence,
    apply_construction_receipt,
    initial_construction_state,
    reduce_construction,
    resume_construction,
)
from core.science_line_candidate import build_science_line_candidate
from orchestrator.material_reservations import MaterialReservationLedger
from planners.ghost_line_artifact import derive_ghost_artifact
from training.isolation import assert_training_identity

pytestmark = pytest.mark.contracts

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output"
    / "factorio_mod/recipe_catalogs/recipe_catalog_243688.json"
)
_FIXTURE_SHA256 = "9792354548cef192aac4c24dd76637d0c3a27cc787f87f537eca7acf915da71c"

_SCENARIO_ID = "optimizer-bound-replay-001"
_SURFACE = f"training/{_SCENARIO_ID}"
_FORCE = f"training-{_SCENARIO_ID}"
_EPISODE_ID = f"synthetic-episode-{_SCENARIO_ID}"
_PROJECT_ID = "synthetic_bound_replay_project"
_TRANSACTION_ID = "synthetic-bound-replay-transaction"
_OUTPUT = "automation-science-pack"
_TARGET = Fraction(1, 2)
_CONSUMER = "synthetic-automation-pack-consumer"

# SYNTHETIC provenance: only the catalog hash, force and tick are observed from
# the stored export. The version, surface, command and mod set are test values,
# never claims about the export's real runtime or mod state.
_SYNTHETIC_SURFACE = "synthetic-offline-fixture-surface"
_SYNTHETIC_MODS = [{"name": "synthetic-offline-test-mod", "version": "0.0.0"}]
_ITEM_MAPPING_PROVENANCE = (
    "SYNTHETIC TEST FIXTURE ONLY: hand-authored prototype-to-item mapping; "
    "never exported from, or verified against, a Factorio runtime"
)

_PREFERENCES = {
    "automation-science-pack": "automation-science-pack",
    "copper-plate": "copper-plate",
    "iron-gear-wheel": "iron-gear-wheel",
    "iron-plate": "iron-plate",
}
_BOUNDARIES = [{"name": "copper-ore", "cap": 5}, {"name": "iron-ore", "cap": 5}]
_MACHINES = {
    "automation-science-pack": "assembling-machine-2",
    "copper-plate": "electric-furnace",
    "iron-gear-wheel": "assembling-machine-2",
    "iron-plate": "electric-furnace",
}
_LINE_SPEC = {
    "recipe": "automation-science-pack",
    "machine_count": 7,
    "origin_x": -40,
    "origin_y": 24,
    "belt_type": "transport-belt",
    "inserter_type": "fast-inserter",
    "mining_feed": False,
    "feed_style": "chained",
    "direct_bus_ingredients": [],
    "chained_ingredients": [0, 1],
    "terminal_collector": True,
}
_EXPECTED_BILL = {
    "assembling-machine-2": 7,
    "fast-inserter": 15,
    "medium-electric-pole": 8,
    "steel-chest": 1,
    "transport-belt": 45,
}
_EXPECTED_GHOST_ACTIONS = 76

_CARDINAL_DIRECTIONS = {"north": 0, "east": 4, "south": 8, "west": 12}


def _scope() -> ConstructionScope:
    return ConstructionScope(
        instance_id="synthetic-bound-replay-instance",
        manifest_digest=hashlib.sha256(
            b"synthetic offline bound replay manifest"
        ).hexdigest(),
        surface=_SURFACE,
        force=_FORCE,
    )


def _provenance(fixture_bytes: bytes, catalog: dict) -> dict:
    return {
        "catalog_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "game_version": "0.0.0",
        "force": catalog["force"],
        "surface": _SYNTHETIC_SURFACE,
        "export_tick": catalog["tick"],
        "export_command": "/synthetic-offline-test-export",
        "mods": [dict(mod) for mod in _SYNTHETIC_MODS],
        "mod_set_sha256": mod_set_sha256(_SYNTHETIC_MODS),
    }


def _bind(artifact: dict, **overrides) -> object:
    settings = {
        "scope": _scope(),
        "project_id": _PROJECT_ID,
        "transaction_id": _TRANSACTION_ID,
        "throughput_output": _OUTPUT,
        "throughput_target": _TARGET,
        "throughput_unit": ITEMS_PER_SECOND,
    }
    settings.update(overrides)
    return bind_artifact_to_construction_plan(artifact, **settings)


def _receipt(
    receipt_id: str,
    sequence: int,
    action: object,
    binding: ConstructionBinding,
) -> ConstructionReceipt:
    return ConstructionReceipt(receipt_id, sequence, binding, action)


def _ledger(tmp_path: Path) -> MaterialReservationLedger:
    return MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id=_EPISODE_ID,
        surface=_SURFACE,
        force=_FORCE,
    )


def _actions(artifact: dict) -> list[dict]:
    return [
        action
        for phase in artifact["plan"]["phases"]
        for action in phase["actions"]
    ]


def _identity(action: dict) -> tuple[str, Fraction, Fraction, int]:
    position = action["position"]
    return (
        action["entity"],
        Fraction(position["x"]),
        Fraction(position["y"]),
        _CARDINAL_DIRECTIONS.get(action.get("direction"), 0),
    )


def _assert_binding_error(reason: ArtifactBindingReason, action) -> None:
    with pytest.raises(ArtifactBindingError) as caught:
        action()
    assert caught.value.reason is reason
    assert caught.value.code == reason.value


def _assert_construction_error(reason: ConstructionReason, action) -> None:
    with pytest.raises(ConstructionStateError) as caught:
        action()
    assert caught.value.reason is reason
    assert caught.value.code == reason.value


def test_bound_replay_end_to_end_is_offline_synthetic_and_fail_closed(
    tmp_path: Path,
) -> None:
    if not _FIXTURE.is_file():
        pytest.skip("local historical recipe export is unavailable")
    fixture_bytes = _FIXTURE.read_bytes()
    catalog = json.loads(fixture_bytes)
    assert hashlib.sha256(fixture_bytes).hexdigest() == _FIXTURE_SHA256
    assert catalog["machines"], "exact fixture bytes must carry machine records"

    # 1. Real historical catalog bytes -> exact fixed-recipe demand.
    demand = plan_catalog_demand(
        fixture_bytes,
        provenance=_provenance(fixture_bytes, catalog),
        demand={_OUTPUT: 1},
        boundaries=_BOUNDARIES,
        preferences=_PREFERENCES,
    )
    assert demand.balance.status == "unique"
    assert demand.balance.feasible is True
    assert demand.selected_recipes == (
        "automation-science-pack",
        "copper-plate",
        "iron-gear-wheel",
        "iron-plate",
    )

    # 2. Demand + explicit machine facts -> explicit science-line geometry.
    candidate = build_science_line_candidate(
        demand,
        catalog,
        recipe=_OUTPUT,
        line_spec=_LINE_SPEC,
        machine_by_recipe=_MACHINES,
        catalog_bytes=fixture_bytes,
    )
    assert candidate.count_status == "adequate"
    assert candidate.geometry["ghost_entities"]["assembling-machine-2"] == 7
    assert candidate.deployable is False
    assert candidate.player_force_build_claim is False

    # 3. Geometry -> ghost-only artifact with caller-declared item accounting.
    prototypes = set(candidate.ghost_entities) | set(candidate.direct_entities)
    artifact = derive_ghost_artifact(
        candidate.geometry["plan"],
        item_mapping={
            "provenance": _ITEM_MAPPING_PROVENANCE,
            "entities": {name: name for name in sorted(prototypes)},
            "tiles": {},
        },
    )
    assert artifact["executable"] is False
    assert artifact["player_force_build_claim"] is False
    assert set(artifact["claims"].values()) == {False}
    assert artifact["declared_item_bill"] == _EXPECTED_BILL
    assert artifact["counts"]["ghost_placements"] == _EXPECTED_GHOST_ACTIONS

    # 4. Artifact -> hash-bound immutable construction plan.
    bound = _bind(artifact)
    plan = bound.plan
    actions = _actions(artifact)
    assert bound.execution_authorized is False
    assert bound.runtime_provenance_claimed is False
    assert bound.artifact["executable"] is False
    assert bound.plan.binding.plan_hash == bound.binding_sha256
    assert_training_identity(_SURFACE, _FORCE)

    # Exact bill and exact entity identity set are the same at every stage.
    assert dict(plan.required_materials) == artifact["declared_item_bill"]
    assert dict(plan.required_materials) == _EXPECTED_BILL
    assert len(plan.required_entities) == len(actions) == _EXPECTED_GHOST_ACTIONS
    assert {
        (entity.entity, entity.x, entity.y, entity.direction)
        for entity in plan.required_entities
    } == {_identity(action) for action in actions}

    # Deterministic re-binding accepts independently known hashes.
    rebound = _bind(
        artifact,
        expected_artifact_sha256=bound.artifact_sha256,
        expected_plan_hash=bound.binding_sha256,
    )
    assert rebound.plan == plan
    assert rebound.artifact_sha256 == bound.artifact_sha256
    assert rebound.binding_sha256 == bound.binding_sha256

    # 5. Disposable held-reservation ledger for the same synthetic scope.
    bill = dict(plan.required_materials)
    ledger = _ledger(tmp_path)
    ledger.declare(
        _PROJECT_ID,
        bill,
        bill,
        target_item=_OUTPUT,
        priority=100,
        hold_until_producing=True,
    )
    ledger.mark_constructing(_PROJECT_ID, bill)
    constructing = _ledger(tmp_path).projects[_PROJECT_ID]
    assert constructing.state == "constructing"
    assert constructing.reserved == bill
    assert constructing.hold_until_producing is True
    assert _ledger(tmp_path).stock_targets() == bill

    # 6. Ordered receipts: order, explicit funding from the held ledger, one
    # partial observation, then the complete plan entity set.
    binding = plan.binding
    entities = plan.required_entities
    partial_entities = entities[: len(entities) // 2]
    assert partial_entities and len(partial_entities) < len(entities)

    order = _receipt(
        "r-order", 1, OrderAction(entities), binding,
    )
    fund = _receipt(
        "r-fund", 2, FundingAction(dict(constructing.reserved)), binding,
    )
    partial_observe = _receipt(
        "r-partial", 3,
        ObservationAction(partial_entities, complete=False, transaction_owned=True),
        binding,
    )
    full_observe = _receipt(
        "r-complete", 4,
        ObservationAction(entities, complete=True, transaction_owned=True),
        binding,
    )

    initial = initial_construction_state(plan)
    partial = reduce_construction(plan, (order, fund, partial_observe))
    assert partial.phase is ConstructionPhase.PARTIAL
    assert set(partial.observed_entities) == set(partial_entities)
    assert partial.observation_complete is False
    assert dict(partial.held_reservations) == constructing.reserved == bill

    # Replay of the same prefix is deterministic, and an order alone is not
    # construction evidence.
    assert partial == resume_construction(
        plan, initial, (order, fund, partial_observe),
    )
    assert partial.order_acknowledged is True
    assert partial.construction_observed is False

    built = resume_construction(plan, partial, (full_observe,))
    assert built == reduce_construction(
        plan, (order, fund, partial_observe, full_observe),
    )
    assert built.phase is ConstructionPhase.BUILT
    assert built.observed_entities == entities
    assert dict(built.held_reservations) == constructing.reserved == bill

    # 7. Explicitly scoped counter windows: an output/unit or binding mismatch
    # fails closed, a below-target window records production without
    # commissioning, and a qualifying scoped window commissions exactly.
    below_window = ThroughputEvidence(
        evidence_id="synthetic-window-below-target",
        binding=binding,
        output=_OUTPUT,
        unit=ITEMS_PER_SECOND,
        consumer=_CONSUMER,
        start_tick=6000,
        end_tick=6600,
        start_count=1000,
        end_count=1001,
    )
    below = apply_construction_receipt(
        plan, built, _receipt("r-window-below", 5, below_window, binding),
    )
    assert below.phase is ConstructionPhase.BUILT
    assert below.throughput_fact is not None
    assert below.throughput_fact.rate == Fraction(1, 10)
    assert below.throughput_fact.consumer == _CONSUMER
    assert below.facts()["measured_production"] is True
    assert below.facts()["commission_met"] is False
    assert dict(below.held_reservations) == bill

    wrong_output = ThroughputEvidence(
        evidence_id="synthetic-window-wrong-output",
        binding=binding,
        output="iron-gear-wheel",
        unit=ITEMS_PER_SECOND,
        consumer=_CONSUMER,
        start_tick=6600,
        end_tick=6660,
        start_count=1001,
        end_count=1002,
    )
    _assert_construction_error(
        ConstructionReason.MEASUREMENT_MISMATCH,
        lambda: apply_construction_receipt(
            plan, below, _receipt("r-window-wrong-output", 50, wrong_output, binding),
        ),
    )
    wrong_binding = ConstructionBinding(
        scope=binding.scope,
        project_id=binding.project_id,
        transaction_id="synthetic-other-transaction",
        plan_hash=binding.plan_hash,
    )
    wrong_scope = replace(wrong_output, binding=wrong_binding)
    _assert_construction_error(
        ConstructionReason.TRANSACTION_MISMATCH,
        lambda: apply_construction_receipt(
            plan, below, _receipt("r-window-wrong-scope", 51, wrong_scope, binding),
        ),
    )

    commission_window = ThroughputEvidence(
        evidence_id="synthetic-window-commissioning",
        binding=binding,
        output=_OUTPUT,
        unit=ITEMS_PER_SECOND,
        consumer=_CONSUMER,
        start_tick=6660,
        end_tick=6720,
        start_count=1002,
        end_count=1003,
    )
    commissioned = apply_construction_receipt(
        plan,
        below,
        _receipt("r-window-commission", 6, commission_window, binding),
    )
    assert commissioned.phase is ConstructionPhase.COMMISSIONED
    assert commissioned.throughput_fact is not None
    assert commissioned.throughput_fact.rate == Fraction(1)
    assert commissioned.throughput_fact.consumer == _CONSUMER
    assert commissioned.throughput_fact.unit == ITEMS_PER_SECOND
    assert commissioned.throughput_fact.start_tick == 6660
    assert commissioned.throughput_fact.end_tick == 6720
    assert commissioned.throughput_fact.counter_advance == 1
    assert commissioned.facts()["measured_production"] is True
    assert commissioned.facts()["commission_met"] is True
    assert dict(commissioned.held_reservations) == bill

    # 8. Reservations stay held until an explicit terminal release; the ledger
    # then mirrors the completed synthetic project.
    release = _receipt("r-release", 7, ReleaseAction(bill), binding)
    released = apply_construction_receipt(plan, commissioned, release)
    assert released.phase is ConstructionPhase.COMMISSIONED
    assert released.held_reservations == ()
    assert apply_construction_receipt(plan, released, release) == released

    all_receipts = (
        order, fund, partial_observe, full_observe,
        _receipt("r-window-below", 5, below_window, binding),
        _receipt("r-window-commission", 6, commission_window, binding),
        release,
    )
    assert reduce_construction(plan, all_receipts) == released

    ledger.complete(_PROJECT_ID)
    completed = _ledger(tmp_path)
    assert completed.projects[_PROJECT_ID].state == "completed"
    assert completed.projects[_PROJECT_ID].reserved == {}
    assert completed.stock_targets() == {}

    # 9. Forged prior states and wrong hashes fail closed instead of replaying.
    forged_built = replace(
        partial,
        phase=ConstructionPhase.BUILT,
        observed_entities=entities,
        observation_complete=True,
    )
    _assert_construction_error(
        ConstructionReason.INVALID_STATE,
        lambda: apply_construction_receipt(plan, forged_built, full_observe),
    )
    forged_funding = replace(
        initial, funded=tuple(sorted(bill.items())),
    )
    _assert_construction_error(
        ConstructionReason.INVALID_STATE,
        lambda: apply_construction_receipt(plan, forged_funding, fund),
    )
    wrong_plan_hash = ConstructionBinding(
        scope=binding.scope,
        project_id=binding.project_id,
        transaction_id=binding.transaction_id,
        plan_hash="0" * 64,
    )
    _assert_construction_error(
        ConstructionReason.PLAN_HASH_MISMATCH,
        lambda: apply_construction_receipt(
            plan,
            built,
            _receipt(
                "r-wrong-plan", 52, OrderAction((entities[0],)), wrong_plan_hash,
            ),
        ),
    )

    # Mutated artifact bytes no longer match the expected artifact hash; an
    # internally inconsistent mutation is rejected even without an expectation.
    tampered = bound.artifact
    next(
        action
        for action in _actions(tampered)
        if action["entity"] == "transport-belt"
    )["position"] = {"x": 1000.5, "y": 1000.5}
    _assert_binding_error(
        ArtifactBindingReason.ARTIFACT_HASH_MISMATCH,
        lambda: _bind(
            tampered, expected_artifact_sha256=bound.artifact_sha256,
        ),
    )
    _assert_binding_error(
        ArtifactBindingReason.PLAN_HASH_MISMATCH,
        lambda: _bind(artifact, expected_plan_hash="0" * 64),
    )
    recounted = bound.artifact
    recounted["declared_item_bill"]["transport-belt"] += 1
    _assert_binding_error(
        ArtifactBindingReason.INCONSISTENT_BILL,
        lambda: _bind(recounted),
    )

    # A detached artifact read cannot mutate the binding it was decoded from.
    detached = bound.artifact
    detached["declared_item_bill"].clear()
    detached["plan"]["phases"][0]["actions"].clear()
    assert bound.artifact["declared_item_bill"] == _EXPECTED_BILL
    assert len(_actions(bound.artifact)) == _EXPECTED_GHOST_ACTIONS
    assert hashlib.sha256(bound.canonical_artifact).hexdigest() == bound.artifact_sha256
    assert bound.plan.binding.plan_hash == bound.binding_sha256
