# Path: core/optimizer_artifact_binding.py | Purpose: Offline binding of a ghost construction artifact to an immutable construction plan.
"""Deterministic, offline artifact-to-construction-plan binding adapter.

This module is deliberately narrow. It accepts one already-derived
``ghost-line-artifact-1`` mapping, revalidates its exact structure and internal
consistency, derives the immutable ``ConstructionPlan`` content from that
artifact, and binds the artifact bytes plus the relevant plan settings under one
canonical SHA-256 digest. It never contacts Factorio, RCON, a save, the
filesystem or a live force, chooses no strategy, authorizes no execution and
claims no runtime provenance.

The binding digest is the construction plan's ``plan_hash``. It covers the
complete normalized artifact (geometry, configuration, caller-declared item
mapping, prototype counts, declared material bill and safety claims), the
derived entity/material content, and the throughput settings. Callers may pass
an independently known artifact digest and/or expected plan hash; either
mismatch is rejected before a plan is returned.

Only ``place_ghost`` entity actions are bound. ``place_tile_ghost`` cannot be
represented by ``ConstructionPlan.required_entities`` without inventing a
fake entity identity, so tile ghosts fail closed instead of being silently
dropped or mislabeled.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
import hashlib
import json
import math
import re
from typing import Mapping

from core.optimizer_construction_state import (
    THROUGHPUT_UNITS,
    ConstructionBinding,
    ConstructionPlan,
    ConstructionScope,
    ConstructionStateError,
    EntityRef,
)
from planners.ghost_line_artifact import ARTIFACT_STATUS, ARTIFACT_VERSION
from planners.plan_validation import assert_no_production_infinity, validate_build_plan

BINDING_VERSION = "optimizer-artifact-binding-2"
ARTIFACT_HASH_ALGORITHM = "sha256"
BINDING_DOMAIN = "factorio.optimizer.artifact_binding"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_PREFIXED_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")

_ARTIFACT_KEYS = frozenset({
    "artifact_version",
    "status",
    "executable",
    "player_force_build_claim",
    "plan",
    "declared_item_bill",
    "item_bill_basis",
    "prototype_action_counts",
    "item_mapping",
    "counts",
    "converted_direct_actions",
    "claims",
    "pending_checks",
    "unknowns",
    "validation",
})
_OPTIONAL_ARTIFACT_KEYS = frozenset({"planning_context"})

_CLAIM_KEYS = frozenset({
    "materials_available",
    "construction_bots_available",
    "sustained_rate_verified",
    "world_safety_verified",
})

_ITEM_BILL_BASIS = "caller_declared_prototype_item_mapping"

_MAPPING_KEYS = frozenset({"provenance", "entities", "tiles"})

_PLAN_KEYS = frozenset({"phases"})
_PHASE_KEYS = frozenset({"name", "actions"})
_COUNT_KEYS = frozenset({
    "ghost_placements",
    "direct_placements",
    "removals",
    "converted_direct_placements",
})
_PROTOTYPE_COUNT_KEYS = frozenset({"entity", "tile"})
_CONVERTED_KEYS = frozenset({
    "phase", "phase_index", "action_index", "entity", "position", "direction",
})

# Every action setting the BuildPlan schema defines. An artifact carrying any
# other key is not a recognised configuration and is rejected instead of hashed
# as if it were meaningful.
_ACTION_KEYS = frozenset({
    "action_type",
    "entity",
    "tile",
    "position",
    "direction",
    "input_priority",
    "output_priority",
    "underground_type",
    "recipe",
    "logistic_request",
    "logistic_requests",
    "logistic_group",
    "logistic_sections",
    "clear_logistic_groups",
    "inventory_limit",
    "clear_inventory_limit",
    "infinity_filter",
    "fill_percentage",
    "clear_logistic_condition",
    "clear_chest_gate",
    "chest_gate",
    "logistic_condition",
})

_CARDINAL_DIRECTIONS = {
    "north": 0,
    "east": 4,
    "south": 8,
    "west": 12,
}

NON_CLAIMS = (
    "offline adapter; no network, RCON, save, filesystem, surface, stock or runtime contact",
    "returning a bound plan does not authorize execution and does not release or reserve material",
    "plan_hash identifies the exact artifact content and settings; it does not prove the artifact "
    "was produced from surveyed terrain or a verified runtime observation",
    "scope, project, transaction and any caller-supplied digests are caller assertions",
    "only place_ghost entity geometry is bound; tile ghosts fail closed",
    "optional planning context is hashed as caller-asserted data, not verified or authoritative",
)


class ArtifactBindingReason(str, Enum):
    """Typed fail-closed reasons for artifact binding."""

    INVALID_ARTIFACT = "invalid_artifact"
    INVALID_PLAN = "invalid_plan"
    INVALID_SETTINGS = "invalid_settings"
    INVALID_SCOPE = "invalid_scope"
    INVALID_BINDING = "invalid_binding"
    INVALID_EXPECTED_HASH = "invalid_expected_hash"
    UNSAFE_CLAIM = "unsafe_claim"
    UNSUPPORTED_TILE = "unsupported_tile"
    INCONSISTENT_BILL = "inconsistent_bill"
    INCONSISTENT_COUNTS = "inconsistent_counts"
    INCONSISTENT_CONVERSION = "inconsistent_conversion"
    ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"
    PLAN_HASH_MISMATCH = "plan_hash_mismatch"


class ArtifactBindingError(ValueError):
    """The artifact, its settings, or a caller-supplied digest is unacceptable."""

    def __init__(self, reason: ArtifactBindingReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = reason.value


@dataclass(frozen=True)
class BoundArtifactPlan:
    """Immutable plan whose hash binds one exact artifact and its settings.

    ``binding_sha256`` is also ``plan.binding.plan_hash``. The canonical byte
    strings are retained so callers can independently rehash exactly what was
    bound. The artifact is not stored as a mutable object: every read of
    ``artifact`` returns a fresh mapping decoded from ``canonical_artifact``, so
    mutating a returned mapping (or the original caller input) cannot alter the
    bound content, its digests, or the plan. This object grants no execution
    authority and asserts no runtime provenance.
    """

    plan: ConstructionPlan
    artifact_sha256: str
    binding_sha256: str
    canonical_artifact: bytes
    canonical_binding: bytes
    non_claims: tuple[str, ...] = NON_CLAIMS

    @property
    def artifact(self) -> dict[str, object]:
        """Return a detached artifact mapping decoded from the canonical bytes.

        Each access builds an independent object, so callers may inspect or
        mutate it without desynchronizing ``canonical_artifact``,
        ``artifact_sha256``, ``binding_sha256`` or ``plan``.
        """
        return json.loads(self.canonical_artifact.decode("utf-8"))

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def runtime_provenance_claimed(self) -> bool:
        return False


def bind_artifact_to_construction_plan(
    artifact: Mapping[str, object],
    *,
    scope: ConstructionScope,
    project_id: str,
    transaction_id: str,
    throughput_output: str,
    throughput_target: object,
    throughput_unit: str,
    expected_artifact_sha256: str | None = None,
    expected_plan_hash: str | None = None,
) -> BoundArtifactPlan:
    """Bind one ghost artifact to an immutable construction plan, offline.

    The artifact must be an exact ``ghost-line-artifact-1`` mapping. Its plan
    must contain only ``place_ghost`` entity actions, and its declared bill,
    prototype counts, conversion records and item mapping must agree with that
    geometry. Tile ghosts are rejected because ``ConstructionPlan`` cannot
    represent them as entity identities.

    ``throughput_output``, ``throughput_target`` and ``throughput_unit`` are
    immutable plan settings included in the binding digest. ``expected_*``
    values, when supplied, must be lowercase SHA-256 hex digests (an optional
    ``sha256:`` prefix is accepted); any mismatch raises before a plan exists.
    """

    normalized, canonical_artifact = _normalize_artifact(artifact)
    entities = _derive_entities(normalized["plan"])
    materials = _validate_materials(normalized, entities)
    target = _positive_fraction(throughput_target, "throughput_target")
    _validate_settings(throughput_output, throughput_unit)
    _validate_scope(scope)
    _validate_binding_fields(project_id, transaction_id)

    artifact_sha256 = hashlib.sha256(canonical_artifact).hexdigest()
    binding_payload = {
        "binding_domain": BINDING_DOMAIN,
        "binding_version": BINDING_VERSION,
        "artifact_sha256": artifact_sha256,
        "artifact": normalized,
        "geometry": [
            entity.to_dict() for entity in sorted(entities, key=_entity_key)
        ],
        "materials": dict(materials),
        "plan_settings": {
            "throughput_output": throughput_output,
            "throughput_target": str(target),
            "throughput_unit": throughput_unit,
        },
    }
    canonical_binding = _canonical_json_bytes(binding_payload)
    binding_sha256 = hashlib.sha256(canonical_binding).hexdigest()

    _check_expected(
        expected_artifact_sha256,
        artifact_sha256,
        "artifact",
        ArtifactBindingReason.ARTIFACT_HASH_MISMATCH,
    )
    _check_expected(
        expected_plan_hash,
        binding_sha256,
        "plan",
        ArtifactBindingReason.PLAN_HASH_MISMATCH,
    )

    try:
        binding = ConstructionBinding(
            scope=scope,
            project_id=project_id,
            transaction_id=transaction_id,
            plan_hash=binding_sha256,
        )
        plan = ConstructionPlan(
            binding=binding,
            required_entities=entities,
            required_materials=materials,
            throughput_output=throughput_output,
            throughput_target=target,
            throughput_unit=throughput_unit,
        )
    except ConstructionStateError as error:
        raise ArtifactBindingError(
            ArtifactBindingReason.INVALID_PLAN,
            f"artifact cannot produce a valid construction plan: {error}",
        ) from error

    return BoundArtifactPlan(
        plan=plan,
        artifact_sha256=artifact_sha256,
        binding_sha256=binding_sha256,
        canonical_artifact=canonical_artifact,
        canonical_binding=canonical_binding,
    )


def _fail(reason: ArtifactBindingReason, message: str) -> None:
    raise ArtifactBindingError(reason, message)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ArtifactBindingError(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"artifact is not canonical JSON: {error}",
        ) from error
    return text.encode("utf-8")


def _normalize_artifact(value: object) -> tuple[dict, bytes]:
    if not isinstance(value, Mapping):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, "artifact must be a mapping")
    canonical = _canonical_json_bytes(value)
    try:
        normalized = json.loads(canonical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:  # pragma: no cover
        raise ArtifactBindingError(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"artifact is not canonical JSON: {error}",
        ) from error
    if not isinstance(normalized, dict):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, "artifact must be a JSON object")

    missing = sorted(_ARTIFACT_KEYS - set(normalized))
    extra = sorted(set(normalized) - _ARTIFACT_KEYS - _OPTIONAL_ARTIFACT_KEYS)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unknown {extra}")
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "artifact fields are not exact: " + ", ".join(detail),
        )

    context = normalized.get("planning_context")
    if "planning_context" in normalized and (
        not isinstance(context, dict)
        or set(context) != {"version", "trust", "data"}
        or context.get("version") != "planner-context-v1"
        or context.get("trust") != "unverified"
        or not isinstance(context.get("data"), dict)
    ):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT,
              "planning_context must be a versioned, unverified data object")

    if normalized["artifact_version"] != ARTIFACT_VERSION:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"artifact_version must be {ARTIFACT_VERSION!r}",
        )
    if normalized["status"] != ARTIFACT_STATUS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"artifact status must be {ARTIFACT_STATUS!r}",
        )
    if normalized["executable"] is not False:
        _fail(
            ArtifactBindingReason.UNSAFE_CLAIM,
            "artifact must declare executable as false",
        )
    if normalized["player_force_build_claim"] is not False:
        _fail(
            ArtifactBindingReason.UNSAFE_CLAIM,
            "artifact must not claim player-force build authority",
        )

    claims = normalized["claims"]
    if not isinstance(claims, dict) or set(claims) != _CLAIM_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "artifact claims must contain exactly the four known claim flags",
        )
    unsafe = sorted(key for key, claim in claims.items() if claim is not False)
    if unsafe:
        _fail(
            ArtifactBindingReason.UNSAFE_CLAIM,
            f"artifact makes unsupported safety claim(s): {unsafe}",
        )

    if normalized["item_bill_basis"] != _ITEM_BILL_BASIS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"item_bill_basis must be {_ITEM_BILL_BASIS!r}",
        )
    if not isinstance(normalized["validation"], str) or not normalized["validation"]:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "artifact validation description must be a non-empty string",
        )
    _nonempty_strings(normalized["pending_checks"], "pending_checks")
    unknowns = normalized["unknowns"]
    if not isinstance(unknowns, dict) or not all(
        isinstance(key, str) and isinstance(text, str)
        for key, text in unknowns.items()
    ):
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "artifact unknowns must map strings to strings",
        )

    _validate_plan_shape(normalized["plan"])
    return normalized, canonical


def _validate_plan_shape(plan: object) -> None:
    if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            "artifact plan must contain exactly 'phases'",
        )
    phases = plan["phases"]
    if not isinstance(phases, list) or not phases:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            "artifact plan must contain at least one phase",
        )
    for phase_index, phase in enumerate(phases):
        if not isinstance(phase, dict) or set(phase) != _PHASE_KEYS:
            _fail(
                ArtifactBindingReason.INVALID_PLAN,
                f"phase {phase_index} must contain exactly 'name' and 'actions'",
            )
        if not isinstance(phase["name"], str) or not phase["name"]:
            _fail(
                ArtifactBindingReason.INVALID_PLAN,
                f"phase {phase_index} name must be a non-empty string",
            )
        actions = phase["actions"]
        if not isinstance(actions, list):
            _fail(
                ArtifactBindingReason.INVALID_PLAN,
                f"phase {phase_index} actions must be a list",
            )
        for action_index, action in enumerate(actions):
            _validate_ghost_action(action, phase_index, action_index)

    try:
        validate_build_plan(plan)
        assert_no_production_infinity([("artifact-binding", plan)])
    except ValueError as error:
        raise ArtifactBindingError(
            ArtifactBindingReason.INVALID_PLAN,
            f"artifact plan is not a valid ghost BuildPlan: {error}",
        ) from error


def _validate_ghost_action(action: object, phase_index: int, action_index: int) -> None:
    where = f"phase {phase_index} action {action_index}"
    if not isinstance(action, dict):
        _fail(ArtifactBindingReason.INVALID_PLAN, f"{where} must be a mapping")
    unknown = sorted(set(action) - _ACTION_KEYS)
    if unknown:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{where} has unknown setting(s): {unknown}",
        )
    action_type = action.get("action_type")
    if action_type == "place_tile_ghost":
        _fail(
            ArtifactBindingReason.UNSUPPORTED_TILE,
            f"{where} is a tile ghost; ConstructionPlan requires entity identity",
        )
    if action_type != "place_ghost":
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{where} action_type must be 'place_ghost', got {action_type!r}",
        )
    if "tile" in action:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{where} place_ghost cannot carry a tile",
        )
    if not isinstance(action.get("entity"), str) or not action["entity"]:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{where} requires a non-empty entity",
        )
    _position(action, where)
    if "direction" in action and action["direction"] not in _CARDINAL_DIRECTIONS:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{where} direction must be one of {sorted(_CARDINAL_DIRECTIONS)}",
        )


def _position(action: Mapping[str, object], label: str) -> tuple[object, object]:
    position = action.get("position")
    if not isinstance(position, dict) or set(position) != {"x", "y"}:
        _fail(
            ArtifactBindingReason.INVALID_PLAN,
            f"{label} position must contain exactly 'x' and 'y'",
        )
    for axis in ("x", "y"):
        value = position[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(
                ArtifactBindingReason.INVALID_PLAN,
                f"{label} position {axis} must be a finite number",
            )
        if isinstance(value, float) and not math.isfinite(value):
            _fail(
                ArtifactBindingReason.INVALID_PLAN,
                f"{label} position {axis} must be a finite number",
            )
    return position["x"], position["y"]


def _derive_entities(plan: dict) -> tuple[EntityRef, ...]:
    entities: list[EntityRef] = []
    for phase in plan["phases"]:
        for action in phase["actions"]:
            x, y = _position(action, "ghost action")
            try:
                entities.append(
                    EntityRef(
                        entity=action["entity"],
                        x=x,
                        y=y,
                        direction=_CARDINAL_DIRECTIONS.get(action.get("direction"), 0),
                        recipe=action.get("recipe"),
                    )
                )
            except ConstructionStateError as error:
                raise ArtifactBindingError(
                    ArtifactBindingReason.INVALID_PLAN,
                    f"ghost action cannot be represented as an entity identity: {error}",
                ) from error
    return tuple(entities)


def _entity_key(entity: EntityRef) -> tuple[object, ...]:
    return (entity.entity, entity.x, entity.y, entity.direction, entity.quality)


def _validate_materials(
    artifact: dict, entities: tuple[EntityRef, ...],
) -> dict[str, int]:
    artifact_counts = artifact["prototype_action_counts"]
    if not isinstance(artifact_counts, dict) or set(artifact_counts) != _PROTOTYPE_COUNT_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "prototype_action_counts must contain exactly 'entity' and 'tile'",
        )
    entity_counts = _count_map(artifact_counts["entity"], "prototype entity counts")
    tile_counts = _count_map(artifact_counts["tile"], "prototype tile counts")
    if tile_counts:
        _fail(
            ArtifactBindingReason.UNSUPPORTED_TILE,
            "artifact prototype counts include tile ghosts, which cannot be bound",
        )

    actual_entities: Counter[str] = Counter(entity.entity for entity in entities)
    actual = dict(sorted(actual_entities.items()))
    if actual != entity_counts:
        _fail(
            ArtifactBindingReason.INCONSISTENT_COUNTS,
            f"prototype entity counts {entity_counts} do not match plan {actual}",
        )

    item_mapping = artifact["item_mapping"]
    if not isinstance(item_mapping, dict) or set(item_mapping) != _MAPPING_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "item_mapping must contain exactly 'provenance', 'entities' and 'tiles'",
        )
    provenance = item_mapping["provenance"]
    if not isinstance(provenance, str) or not provenance.strip():
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "item_mapping provenance must be a non-empty string",
        )
    entity_items = _prototype_map(item_mapping["entities"], "item_mapping entities")
    tile_items = _prototype_map(item_mapping["tiles"], "item_mapping tiles")
    overlap = sorted(set(entity_items) & set(tile_items))
    if overlap:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"prototype(s) {overlap} are mapped as both entity and tile",
        )

    recomputed: Counter[str] = Counter()
    for prototype, count in entity_counts.items():
        item = entity_items.get(prototype)
        if item is None:
            _fail(
                ArtifactBindingReason.INCONSISTENT_BILL,
                f"no item mapping for ghost entity prototype {prototype!r}",
            )
        recomputed[item] += count
    declared = _positive_counts(artifact["declared_item_bill"], "declared_item_bill")
    expected = dict(sorted(recomputed.items()))
    if declared != expected:
        _fail(
            ArtifactBindingReason.INCONSISTENT_BILL,
            f"declared_item_bill {declared} does not match mapped actions {expected}",
        )

    _validate_counts(artifact, len(entities))
    return expected


def _validate_counts(artifact: dict, action_count: int) -> None:
    counts = artifact["counts"]
    if not isinstance(counts, dict) or set(counts) != _COUNT_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "artifact counts must contain exactly the four known counters",
        )
    values = {
        key: _non_negative_int(value, f"counts.{key}")
        for key, value in counts.items()
    }
    converted = artifact["converted_direct_actions"]
    if not isinstance(converted, list):
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "converted_direct_actions must be a list",
        )
    if values["ghost_placements"] != action_count:
        _fail(
            ArtifactBindingReason.INCONSISTENT_COUNTS,
            "counts.ghost_placements does not match the plan action count",
        )
    if values["direct_placements"] != 0 or values["removals"] != 0:
        _fail(
            ArtifactBindingReason.INCONSISTENT_COUNTS,
            "a bound ghost artifact must report zero direct placements and removals",
        )
    if values["converted_direct_placements"] != len(converted):
        _fail(
            ArtifactBindingReason.INCONSISTENT_COUNTS,
            "counts.converted_direct_placements does not match conversion records",
        )

    phases = artifact["plan"]["phases"]
    seen: set[tuple[int, int]] = set()
    for record in converted:
        _validate_conversion(record, phases, seen)


def _validate_conversion(
    record: object, phases: list, seen: set[tuple[int, int]],
) -> None:
    if not isinstance(record, dict) or set(record) != _CONVERTED_KEYS:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "each converted direct action must contain exactly its six fields",
        )
    phase_index = _non_negative_int(record["phase_index"], "phase_index")
    action_index = _non_negative_int(record["action_index"], "action_index")
    phase_name = record["phase"]
    if not isinstance(phase_name, str) or not phase_name:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "converted action phase must be a non-empty string",
        )
    if not isinstance(record["entity"], str) or not record["entity"]:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "converted action entity must be a non-empty string",
        )
    if phase_index >= len(phases) or action_index >= len(phases[phase_index]["actions"]):
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action index is outside the plan",
        )
    key = (phase_index, action_index)
    if key in seen:
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action index is recorded more than once",
        )
    seen.add(key)

    phase = phases[phase_index]
    action = phase["actions"][action_index]
    if record["phase"] != phase["name"]:
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action phase name does not match the plan",
        )
    if action.get("entity") != record["entity"]:
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action entity does not match the plan",
        )
    position = record["position"]
    if not isinstance(position, dict) or set(position) != {"x", "y"}:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            "converted action position must contain exactly 'x' and 'y'",
        )
    if position != action.get("position"):
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action position does not match the plan",
        )
    if record["direction"] != action.get("direction"):
        _fail(
            ArtifactBindingReason.INCONSISTENT_CONVERSION,
            "converted action direction does not match the plan",
        )


def _count_map(value: object, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, f"{label} must be a mapping")
    normalized: dict[str, int] = {}
    for prototype, count in value.items():
        if not isinstance(prototype, str) or not prototype:
            _fail(
                ArtifactBindingReason.INVALID_ARTIFACT,
                f"{label} prototype names must be non-empty strings",
            )
        normalized[prototype] = _positive_int(count, f"{label}[{prototype!r}]")
    return dict(sorted(normalized.items()))


def _prototype_map(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, f"{label} must be a mapping")
    normalized: dict[str, str] = {}
    for prototype, item in value.items():
        if not isinstance(prototype, str) or not prototype:
            _fail(
                ArtifactBindingReason.INVALID_ARTIFACT,
                f"{label} prototype names must be non-empty strings",
            )
        if not isinstance(item, str) or not item.strip():
            _fail(
                ArtifactBindingReason.INVALID_ARTIFACT,
                f"{label}[{prototype!r}] must be a non-empty item name",
            )
        normalized[prototype] = item
    return dict(sorted(normalized.items()))


def _positive_counts(value: object, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, f"{label} must be a mapping")
    return {
        _nonempty_string(item, f"{label} item"): _positive_int(
            count, f"{label}[{item!r}]"
        )
        for item, count in value.items()
    }


def _nonempty_strings(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"{label} must be a list of non-empty strings",
        )


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _non_negative_int(value, label)
    if result <= 0:
        _fail(ArtifactBindingReason.INVALID_ARTIFACT, f"{label} must be positive")
    return result


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(
            ArtifactBindingReason.INVALID_ARTIFACT,
            f"{label} must be a non-negative integer",
        )
    return value


def _positive_fraction(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        _fail(ArtifactBindingReason.INVALID_SETTINGS, f"{label} must be a finite number")
    try:
        result = Fraction(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
        raise ArtifactBindingError(
            ArtifactBindingReason.INVALID_SETTINGS,
            f"{label} must be a finite number",
        ) from error
    if result <= 0:
        _fail(ArtifactBindingReason.INVALID_SETTINGS, f"{label} must be positive")
    return result


def _validate_settings(output: object, unit: object) -> None:
    if not isinstance(output, str) or not output:
        _fail(
            ArtifactBindingReason.INVALID_SETTINGS,
            "throughput_output must be a non-empty string",
        )
    if not isinstance(unit, str) or unit not in THROUGHPUT_UNITS:
        _fail(
            ArtifactBindingReason.INVALID_SETTINGS,
            f"throughput_unit must be one of {', '.join(THROUGHPUT_UNITS)}",
        )


def _validate_scope(scope: object) -> None:
    if not isinstance(scope, ConstructionScope):
        _fail(
            ArtifactBindingReason.INVALID_SCOPE,
            "scope must be a ConstructionScope",
        )
    for value in (scope.instance_id, scope.surface, scope.force):
        if not isinstance(value, str) or not value:
            _fail(
                ArtifactBindingReason.INVALID_SCOPE,
                "scope identity fields must be non-empty strings",
            )
    if not isinstance(scope.manifest_digest, str) or not _DIGEST.fullmatch(
        scope.manifest_digest
    ):
        _fail(
            ArtifactBindingReason.INVALID_SCOPE,
            "scope manifest_digest must be lowercase SHA-256 hex",
        )


def _validate_binding_fields(project_id: object, transaction_id: object) -> None:
    for label, value in (
        ("project_id", project_id),
        ("transaction_id", transaction_id),
    ):
        if not isinstance(value, str) or not value:
            _fail(
                ArtifactBindingReason.INVALID_BINDING,
                f"{label} must be a non-empty string",
            )


def _normalize_digest(value: object, label: str) -> str:
    if isinstance(value, str):
        prefixed = _PREFIXED_DIGEST.fullmatch(value)
        if prefixed:
            return prefixed.group(1)
        if _DIGEST.fullmatch(value):
            return value
    _fail(
        ArtifactBindingReason.INVALID_EXPECTED_HASH,
        f"{label} must be a lowercase SHA-256 hex digest",
    )
    raise AssertionError("unreachable")


def _check_expected(
    expected: object,
    actual: str,
    label: str,
    mismatch: ArtifactBindingReason,
) -> None:
    if expected is None:
        return
    supplied = _normalize_digest(expected, f"expected_{label}_sha256")
    if supplied != actual:
        _fail(
            mismatch,
            f"caller-supplied {label} hash {supplied} does not match {actual}",
        )
