# Path: tests/test_optimizer_artifact_binding.py
# Purpose: Prove the offline artifact-to-construction-plan adapter binds exact
# ghost geometry, configuration, materials and settings; rejects malformed,
# inconsistent and caller-hash-mismatched inputs; and grants no authority.
#
# Every scope, project, transaction and item mapping below is SYNTHETIC test
# data. Nothing here contacts Factorio, RCON, a save, a stock, a surface or a
# live force.

from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json

import pytest

from core.optimizer_artifact_binding import (
    BINDING_DOMAIN,
    BINDING_VERSION,
    ArtifactBindingError,
    ArtifactBindingReason,
    bind_artifact_to_construction_plan,
)
from core.optimizer_construction_state import (
    ConstructionScope,
    EntityRef,
    initial_construction_state,
)
from planners.ghost_line_artifact import derive_ghost_artifact
from planners.science_block_geometry import build_science_block_geometry

pytestmark = pytest.mark.contracts

_DIGEST_A = "a" * 64
_SPEC = {
    "recipe": "iron-plate",
    "machine_count": 3,
    "origin_x": 10,
    "origin_y": 20,
    "belt_type": "transport-belt",
    "inserter_type": "fast-inserter",
    "mining_feed": True,
    "feed_style": "chest",
    "direct_bus_ingredients": [],
    "chained_ingredients": [],
    "terminal_collector": True,
}
_ENTITY_ITEMS = {
    "electric-furnace": "electric-furnace",
    "electric-mining-drill": "electric-mining-drill",
    "fast-inserter": "fast-inserter",
    "medium-electric-pole": "medium-electric-pole",
    "steel-chest": "steel-chest",
    "transport-belt": "transport-belt",
}


def _artifact() -> dict:
    source = build_science_block_geometry(_SPEC)["plan"]
    return derive_ghost_artifact(
        source,
        item_mapping={
            "provenance": "synthetic test prototype-to-item table",
            "entities": dict(_ENTITY_ITEMS),
            "tiles": {},
        },
    )


def _scope() -> ConstructionScope:
    return ConstructionScope(
        instance_id="synthetic-binding-instance",
        manifest_digest=_DIGEST_A,
        surface="training/synthetic-binding",
        force="training-synthetic-binding-force",
    )


def _bind(artifact: dict, **overrides):
    settings = {
        "scope": _scope(),
        "project_id": "synthetic-project",
        "transaction_id": "synthetic-transaction",
        "throughput_output": "iron-plate",
        "throughput_target": Fraction(1, 2),
        "throughput_unit": "items/second",
    }
    settings.update(overrides)
    return bind_artifact_to_construction_plan(artifact, **settings)


def _raises(reason: ArtifactBindingReason, action) -> None:
    with pytest.raises(ArtifactBindingError) as caught:
        action()
    assert caught.value.reason is reason
    assert caught.value.code == reason.value


def _actions(artifact: dict) -> list[dict]:
    return [
        action
        for phase in artifact["plan"]["phases"]
        for action in phase["actions"]
    ]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def test_binds_exact_artifact_materials_geometry_and_settings() -> None:
    artifact = _artifact()
    bound = _bind(artifact)

    assert bound.execution_authorized is False
    assert bound.runtime_provenance_claimed is False
    assert bound.artifact_sha256 == hashlib.sha256(_canonical(artifact)).hexdigest()
    assert bound.binding_sha256 == hashlib.sha256(bound.canonical_binding).hexdigest()
    assert bound.canonical_artifact == _canonical(artifact)
    assert bound.plan.binding.plan_hash == bound.binding_sha256
    assert bound.plan.binding.scope == _scope()
    assert bound.plan.binding.project_id == "synthetic-project"
    assert bound.plan.binding.transaction_id == "synthetic-transaction"

    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]
    assert len(bound.plan.required_entities) == len(_actions(artifact))
    assert Fraction(bound.plan.throughput_target) == Fraction(1, 2)
    assert bound.plan.throughput_unit == "items/second"

    identities = {
        (entity.entity, entity.x, entity.y, entity.direction)
        for entity in bound.plan.required_entities
    }
    assert ("steel-chest", Fraction(41, 2), Fraction(53, 2), 0) in identities
    assert ("fast-inserter", Fraction(39, 2), Fraction(53, 2), 12) in identities
    assert ("electric-mining-drill", Fraction(23, 2), Fraction(37, 2), 8) in identities

    payload = json.loads(bound.canonical_binding)
    assert payload["binding_domain"] == BINDING_DOMAIN
    assert payload["binding_version"] == BINDING_VERSION
    assert payload["artifact_sha256"] == bound.artifact_sha256
    assert payload["artifact"] == artifact
    assert payload["plan_settings"] == {
        "throughput_output": "iron-plate",
        "throughput_target": "1/2",
        "throughput_unit": "items/second",
    }
    assert payload["materials"] == artifact["declared_item_bill"]

    state = initial_construction_state(bound.plan)
    assert state.phase.value == "wait"
    assert state.construction_observed is False


def test_binding_is_order_independent_and_deterministic() -> None:
    artifact = _artifact()
    reordered = {key: artifact[key] for key in reversed(list(artifact))}

    first = _bind(artifact)
    second = _bind(reordered)

    assert first.artifact_sha256 == second.artifact_sha256
    assert first.binding_sha256 == second.binding_sha256
    assert first.plan == second.plan


def test_settings_change_the_binding_hash_but_not_the_artifact_hash() -> None:
    artifact = _artifact()
    base = _bind(artifact)
    slower = _bind(artifact, throughput_target=Fraction(1, 3))
    tick_unit = _bind(artifact, throughput_unit="items/tick")
    fluid_unit = _bind(artifact, throughput_unit="fluid/second")

    assert slower.artifact_sha256 == base.artifact_sha256
    assert tick_unit.artifact_sha256 == base.artifact_sha256
    assert slower.binding_sha256 != base.binding_sha256
    assert tick_unit.binding_sha256 != base.binding_sha256
    assert fluid_unit.binding_sha256 != base.binding_sha256
    assert fluid_unit.plan.throughput_unit == "fluid/second"
    assert slower.plan.binding.plan_hash == slower.binding_sha256


def test_all_configuration_and_mapping_bytes_are_bound() -> None:
    artifact = _artifact()
    base = _bind(artifact)

    configured = copy.deepcopy(artifact)
    _actions(configured)[0]["recipe"] = "iron-plate"
    changed_config = _bind(configured)

    remapped = copy.deepcopy(artifact)
    remapped["item_mapping"]["provenance"] = "another synthetic table"
    changed_mapping = _bind(remapped)

    assert changed_config.artifact_sha256 != base.artifact_sha256
    assert changed_config.binding_sha256 != base.binding_sha256
    assert changed_mapping.artifact_sha256 != base.artifact_sha256
    assert changed_mapping.binding_sha256 != base.binding_sha256


def test_bound_machine_recipe_is_part_of_required_observation() -> None:
    artifact = _artifact()
    configured = next(action for action in _actions(artifact)
                      if action["entity"] == "electric-furnace")
    configured["recipe"] = "iron-plate"
    bound = _bind(artifact)
    machine = next(entity for entity in bound.plan.required_entities
                   if entity.x == Fraction(configured["position"]["x"])
                   and entity.y == Fraction(configured["position"]["y"]))
    assert machine.recipe == configured["recipe"]
    assert machine.to_dict()["recipe"] == configured["recipe"]


def test_old_binding_version_hash_fails_closed() -> None:
    artifact = _artifact()
    bound = _bind(artifact)
    old_payload = json.loads(bound.canonical_binding)
    old_payload["binding_version"] = "optimizer-artifact-binding-1"
    old_hash = hashlib.sha256(_canonical(old_payload)).hexdigest()
    _raises(ArtifactBindingReason.PLAN_HASH_MISMATCH,
            lambda: _bind(artifact, expected_plan_hash=old_hash))


def test_optional_planning_context_is_hashed_but_never_trusted() -> None:
    artifact = _artifact()
    artifact["planning_context"] = {
        "version": "planner-context-v1", "trust": "unverified", "data": {"demand": "195"},
    }
    bound = _bind(artifact)
    assert bound.artifact["planning_context"] == artifact["planning_context"]
    assert bound.execution_authorized is False
    artifact["planning_context"]["data"]["demand"] = "196"
    _raises(ArtifactBindingReason.ARTIFACT_HASH_MISMATCH,
            lambda: _bind(artifact, expected_artifact_sha256=bound.artifact_sha256))
    del artifact["planning_context"]
    _raises(ArtifactBindingReason.ARTIFACT_HASH_MISMATCH,
            lambda: _bind(artifact, expected_artifact_sha256=bound.artifact_sha256))


@pytest.mark.parametrize("context", [None, {}, {"version": "planner-context-v1", "trust": "verified", "data": {}},
                                     {"version": "planner-context-v1", "trust": "unverified", "data": []}])
def test_planning_context_must_explicitly_remain_unverified(context) -> None:
    artifact = _artifact()
    artifact["planning_context"] = context
    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(artifact))


def test_caller_supplied_matching_hashes_are_accepted() -> None:
    artifact = _artifact()
    base = _bind(artifact)

    same = _bind(
        artifact,
        expected_artifact_sha256=base.artifact_sha256,
        expected_plan_hash=base.binding_sha256,
    )
    prefixed = _bind(
        artifact,
        expected_artifact_sha256=f"sha256:{base.artifact_sha256}",
        expected_plan_hash=f"sha256:{base.binding_sha256}",
    )

    assert same.binding_sha256 == base.binding_sha256
    assert prefixed.binding_sha256 == base.binding_sha256


def test_caller_supplied_hash_mismatch_is_rejected() -> None:
    artifact = _artifact()
    base = _bind(artifact)
    wrong = "0" * 64

    _raises(
        ArtifactBindingReason.ARTIFACT_HASH_MISMATCH,
        lambda: _bind(artifact, expected_artifact_sha256=wrong),
    )
    _raises(
        ArtifactBindingReason.PLAN_HASH_MISMATCH,
        lambda: _bind(artifact, expected_plan_hash=wrong),
    )
    _raises(
        ArtifactBindingReason.PLAN_HASH_MISMATCH,
        lambda: _bind(artifact, expected_plan_hash=base.artifact_sha256),
    )
    _raises(
        ArtifactBindingReason.INVALID_EXPECTED_HASH,
        lambda: _bind(artifact, expected_plan_hash="not-a-digest"),
    )
    _raises(
        ArtifactBindingReason.INVALID_EXPECTED_HASH,
        lambda: _bind(artifact, expected_artifact_sha256="A" * 64),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("artifact_version", "ghost-line-artifact-0"),
        ("status", "executable"),
        ("item_bill_basis", "prototype_name_guess"),
        ("validation", ""),
        ("pending_checks", "survey"),
        ("unknowns", []),
    ),
)
def test_malformed_artifact_fields_fail_closed(field: str, value: object) -> None:
    artifact = _artifact()
    artifact[field] = value
    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(artifact))


def test_missing_or_extra_artifact_fields_fail_closed() -> None:
    artifact = _artifact()
    missing = copy.deepcopy(artifact)
    del missing["declared_item_bill"]
    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(missing))

    extra = copy.deepcopy(artifact)
    extra["runtime_verified"] = True
    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(extra))

    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(["not", "a", "mapping"]))


@pytest.mark.parametrize(
    ("field",),
    (("executable",), ("player_force_build_claim",)),
)
def test_artifact_cannot_claim_execution_or_build_authority(field: str) -> None:
    artifact = _artifact()
    artifact[field] = True
    _raises(ArtifactBindingReason.UNSAFE_CLAIM, lambda: _bind(artifact))


def test_artifact_cannot_claim_unverified_safety_facts() -> None:
    artifact = _artifact()
    artifact["claims"]["materials_available"] = True
    _raises(ArtifactBindingReason.UNSAFE_CLAIM, lambda: _bind(artifact))

    incomplete = _artifact()
    del incomplete["claims"]["world_safety_verified"]
    _raises(ArtifactBindingReason.INVALID_ARTIFACT, lambda: _bind(incomplete))


def test_tile_ghosts_fail_closed_instead_of_becoming_fake_entities() -> None:
    artifact = _artifact()
    artifact["plan"]["phases"][0]["actions"].append({
        "action_type": "place_tile_ghost",
        "tile": "stone-path",
        "position": {"x": 0, "y": 0},
    })
    _raises(ArtifactBindingReason.UNSUPPORTED_TILE, lambda: _bind(artifact))


@pytest.mark.parametrize(
    ("mutate",),
    (
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"entity": ""}),),
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"direction": "up"}),),
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"position": {"x": True, "y": 1}}),),
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"unknown_setting": 1}),),
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"tile": "stone-path"}),),
        (lambda a: a["plan"]["phases"][0]["actions"][0].update({"action_type": "place_entity"}),),
    ),
)
def test_malformed_geometry_fails_closed(mutate) -> None:
    artifact = _artifact()
    mutate(artifact)
    _raises(ArtifactBindingReason.INVALID_PLAN, lambda: _bind(artifact))


def test_inconsistent_material_bill_is_rejected() -> None:
    artifact = _artifact()
    artifact["declared_item_bill"]["transport-belt"] += 1
    _raises(ArtifactBindingReason.INCONSISTENT_BILL, lambda: _bind(artifact))


def test_inconsistent_prototype_counts_are_rejected() -> None:
    artifact = _artifact()
    artifact["prototype_action_counts"]["entity"]["transport-belt"] += 1
    _raises(ArtifactBindingReason.INCONSISTENT_COUNTS, lambda: _bind(artifact))


def test_missing_item_mapping_for_used_prototype_is_rejected() -> None:
    artifact = _artifact()
    del artifact["item_mapping"]["entities"]["transport-belt"]
    _raises(ArtifactBindingReason.INCONSISTENT_BILL, lambda: _bind(artifact))


def test_rolled_up_counts_must_match_the_geometry() -> None:
    artifact = _artifact()
    artifact["counts"]["ghost_placements"] += 1
    _raises(ArtifactBindingReason.INCONSISTENT_COUNTS, lambda: _bind(artifact))

    direct = _artifact()
    direct["counts"]["direct_placements"] = 1
    _raises(ArtifactBindingReason.INCONSISTENT_COUNTS, lambda: _bind(direct))

    removed = _artifact()
    removed["counts"]["removals"] = 1
    _raises(ArtifactBindingReason.INCONSISTENT_COUNTS, lambda: _bind(removed))

    converted = _artifact()
    converted["counts"]["converted_direct_placements"] = 0
    _raises(ArtifactBindingReason.INCONSISTENT_COUNTS, lambda: _bind(converted))


@pytest.mark.parametrize(
    ("mutate",),
    (
        (lambda a: a["converted_direct_actions"][0].update({"entity": "fast-inserter"}),),
        (lambda a: a["converted_direct_actions"][0].update({"phase": "other"}),),
        (lambda a: a["converted_direct_actions"][0].update({"position": {"x": 0, "y": 0}}),),
        (lambda a: a["converted_direct_actions"][0].update({"direction": "east"}),),
        (lambda a: a["converted_direct_actions"][0].update({"phase_index": 99}),),
    ),
)
def test_conversion_records_must_match_the_bound_geometry(mutate) -> None:
    artifact = _artifact()
    mutate(artifact)
    _raises(ArtifactBindingReason.INCONSISTENT_CONVERSION, lambda: _bind(artifact))


def test_duplicate_conversion_record_is_rejected() -> None:
    artifact = _artifact()
    artifact["converted_direct_actions"].append(
        copy.deepcopy(artifact["converted_direct_actions"][0])
    )
    artifact["counts"]["converted_direct_placements"] += 1
    _raises(ArtifactBindingReason.INCONSISTENT_CONVERSION, lambda: _bind(artifact))


@pytest.mark.parametrize(
    ("overrides",),
    (
        ({"throughput_unit": "items/minute"},),
        ({"throughput_output": ""},),
        ({"throughput_target": 0},),
        ({"throughput_target": -1},),
        ({"throughput_target": float("nan")},),
        ({"throughput_target": float("inf")},),
    ),
)
def test_invalid_plan_settings_are_rejected(overrides: dict) -> None:
    _raises(
        ArtifactBindingReason.INVALID_SETTINGS,
        lambda: _bind(_artifact(), **overrides),
    )


def test_invalid_scope_and_binding_fields_are_rejected() -> None:
    _raises(
        ArtifactBindingReason.INVALID_SCOPE,
        lambda: _bind(_artifact(), scope="not-a-scope"),
    )
    _raises(
        ArtifactBindingReason.INVALID_BINDING,
        lambda: _bind(_artifact(), project_id=""),
    )
    _raises(
        ArtifactBindingReason.INVALID_BINDING,
        lambda: _bind(_artifact(), transaction_id=None),
    )


def test_returned_plan_is_not_an_execution_authorization() -> None:
    bound = _bind(_artifact())

    assert bound.execution_authorized is False
    assert bound.runtime_provenance_claimed is False
    assert bound.artifact["executable"] is False
    assert bound.artifact["player_force_build_claim"] is False
    assert set(bound.artifact["claims"].values()) == {False}
    assert bound.plan.binding.plan_hash == bound.binding_sha256
    assert any("does not authorize execution" in note for note in bound.non_claims)
    assert any("does not prove" in note for note in bound.non_claims)


def test_mutating_the_original_input_cannot_change_the_binding() -> None:
    artifact = _artifact()
    bound = _bind(artifact)
    expected = json.loads(bound.canonical_artifact)

    artifact["declared_item_bill"].clear()
    artifact["item_mapping"]["entities"].clear()
    artifact["prototype_action_counts"]["entity"]["transport-belt"] = 1
    artifact["counts"]["ghost_placements"] = 0
    artifact["plan"]["phases"][0]["actions"].clear()

    assert bound.artifact == expected
    assert _canonical(bound.artifact) == bound.canonical_artifact
    assert hashlib.sha256(bound.canonical_artifact).hexdigest() == bound.artifact_sha256
    assert hashlib.sha256(bound.canonical_binding).hexdigest() == bound.binding_sha256
    assert bound.plan.binding.plan_hash == bound.binding_sha256
    assert dict(bound.plan.required_materials) == bound.artifact["declared_item_bill"]
    assert len(bound.plan.required_entities) == bound.artifact["counts"]["ghost_placements"]


def test_returned_artifact_is_detached_and_each_read_is_independent() -> None:
    bound = _bind(_artifact())
    expected = json.loads(bound.canonical_artifact)

    first = bound.artifact
    first["declared_item_bill"].clear()
    first["item_mapping"]["entities"]["transport-belt"] = "iron-plate"
    first["plan"]["phases"][0]["actions"].clear()
    first["counts"]["ghost_placements"] = 0

    second = bound.artifact
    assert first is not second
    assert first["plan"] is not second["plan"]
    assert second == expected
    assert second["declared_item_bill"]
    assert second["plan"]["phases"][0]["actions"]

    third = bound.artifact
    assert third == second
    assert third is not second
    assert _canonical(third) == bound.canonical_artifact
    assert hashlib.sha256(bound.canonical_artifact).hexdigest() == bound.artifact_sha256
    assert hashlib.sha256(bound.canonical_binding).hexdigest() == bound.binding_sha256
    assert bound.plan.binding.plan_hash == bound.binding_sha256
    assert dict(bound.plan.required_materials) == third["declared_item_bill"]
    assert len(bound.plan.required_entities) == len(_actions(third))
