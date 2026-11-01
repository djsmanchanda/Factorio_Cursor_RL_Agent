# Path: planners/ghost_line_artifact.py
# Purpose: Offline, fail-closed derivation of a ghost-only construction
# artifact from already explicit science-line geometry.
#
# This module never contacts a server, a runtime or a live surface. It accepts
# one explicit BuildPlan, converts ONLY ordinary directly placed terminal
# collectors/inserters into equivalent build ghosts, preserves existing ghost
# placements byte-for-byte, and rejects every other action. It does not observe
# terrain, survey a site, reserve inventory, count bots, measure throughput or
# authorize a world mutation: the result is a non-executable artifact pending
# those checks.
#
# A placed prototype name is not necessarily an item name (for example the
# ``straight-rail`` entity is built from the ``rail`` item and
# ``hazard-concrete-left`` consumes the ``hazard-concrete`` item). This module
# therefore never falls back to the prototype name: the caller must supply an
# explicit prototype-to-item mapping with provenance, and the reported item
# counts are caller-declared accounting rather than a runtime-verified bill.

from __future__ import annotations

import copy
from collections import Counter
from typing import Mapping

from core.runtime_placement_mapping import mapping_from_runtime_catalog
from planners.plan_validation import (
    INFINITY_ENTITIES,
    assert_no_production_infinity,
    validate_build_plan,
)

ARTIFACT_VERSION = "ghost-line-artifact-1"
ARTIFACT_STATUS = "offline_non_executable_pending_checks"

_GHOST_ACTION_TYPES = frozenset({"place_ghost", "place_tile_ghost"})

_MAPPING_KEYS = frozenset({"provenance", "entities", "tiles"})

_CAPTURE_KEYS = frozenset({"catalog_bytes", "expected_sha256", "surface", "force", "active_mods"})

# Only these ordinary 1x1 terminal collectors/inserters may be rewritten. They
# are bot-buildable prototypes whose placement carries no recipe or runtime
# setting, so their position/direction survives the rewrite exactly.
_CONVERTIBLE_ENTITIES = frozenset({
    "wooden-chest",
    "iron-chest",
    "steel-chest",
    "burner-inserter",
    "inserter",
    "long-handed-inserter",
    "fast-inserter",
    "bulk-inserter",
    "stack-inserter",
})

# Every key the BuildPlan schema defines for an action. Anything else is an
# unknown setting and cannot be project into a validated ghost artifact.
_SCHEMA_ACTION_KEYS = frozenset({
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

# A converted collector/inserter may only keep fields that describe the same
# entity at the same place facing the same way. Recipe/request/limit settings
# belong to a later configuration action, not to a bot-buildable ghost.
_CONVERTIBLE_ACTION_KEYS = frozenset({
    "action_type", "entity", "position", "direction",
})

_PENDING_CHECKS = (
    "survey",
    "coverage",
    "power",
    "inventory_reservation",
    "runtime",
)

_CLAIMS = {
    "materials_available": False,
    "construction_bots_available": False,
    "sustained_rate_verified": False,
    "world_safety_verified": False,
}

_UNKNOWNS = {
    "survey": (
        "Terrain, ore patch, existing entities and obstacles are not observed; "
        "the source coordinates are an unverified request."
    ),
    "coverage": (
        "Roboport/logistic coverage, bot availability and bot travel are not "
        "measured, so no construction reach is established."
    ),
    "power": (
        "No power source, electric network, pole reach, supply or consumption "
        "is established by this artifact."
    ),
    "inventory_reservation": (
        "The gross item bill comes from a caller-declared prototype-to-item "
        "mapping; it is not verified against runtime prototype data, and no "
        "stock is reserved, held, delivered or consumed."
    ),
    "runtime": (
        "Sustained belt/inserter throughput, feed adequacy, fluid purity and "
        "construction completion are not measured offline."
    ),
    "world_safety": (
        "No server or surface was contacted. Placement authorization, "
        "ownership checks and rollback remain unverified."
    ),
}


def derive_ghost_artifact(
    source_plan: Mapping[str, object],
    *,
    item_mapping: Mapping[str, object],
) -> dict:
    """Derive a validated, ghost-only construction artifact, offline.

    The source plan must be already explicit, schema-valid geometry. Existing
    ``place_ghost``/``place_tile_ghost`` actions are preserved exactly; only
    ordinary directly placed terminal collectors/inserters become equivalent
    ``place_ghost`` actions. Removals, scripted sources, power/direct-only
    actions, configuration actions, unknown settings and collisions raise
    ``ValueError`` instead of being guessed or silently dropped.

    ``item_mapping`` is required and must contain exactly ``provenance``
    (a non-empty string), ``entities`` and ``tiles``. The latter two map each
    ghosted prototype to the item it consumes. Every placed prototype must be
    mapped; multiple prototypes may map to the same item. The resulting counts
    are gross caller-declared accounting, not an independently verified
    runtime bill.
    """
    source = _normalise_source(source_plan)
    provenance, entity_items, tile_items = _normalise_item_mapping(item_mapping)
    validate_build_plan(source)

    phases, converted = _convert(source)
    plan = {"phases": phases}
    _assert_ghost_only(plan)
    assert_no_production_infinity([("ghost-line-artifact", plan)])
    validate_build_plan(plan)

    entity_counts, tile_counts = _prototype_action_counts(plan)
    declared_bill = _declared_item_bill(
        entity_counts, tile_counts, entity_items, tile_items,
    )

    ghost_placements = sum(entity_counts.values()) + sum(tile_counts.values())
    return {
        "artifact_version": ARTIFACT_VERSION,
        "status": ARTIFACT_STATUS,
        "executable": False,
        "player_force_build_claim": False,
        "plan": plan,
        "declared_item_bill": declared_bill,
        "item_bill_basis": "caller_declared_prototype_item_mapping",
        "prototype_action_counts": {
            "entity": dict(sorted(entity_counts.items())),
            "tile": dict(sorted(tile_counts.items())),
        },
        "item_mapping": {
            "provenance": provenance,
            "entities": dict(entity_items),
            "tiles": dict(tile_items),
        },
        "counts": {
            "ghost_placements": ghost_placements,
            "direct_placements": 0,
            "removals": 0,
            "converted_direct_placements": len(converted),
        },
        "converted_direct_actions": converted,
        "claims": dict(_CLAIMS),
        "pending_checks": list(_PENDING_CHECKS),
        "unknowns": dict(_UNKNOWNS),
        "validation": (
            "validate_build_plan(source) -> assert_ghost_only -> "
            "assert_no_production_infinity -> validate_build_plan(artifact) -> "
            "declared_item_bill(entity_items, tile_items)"
        ),
    }


def derive_ghost_artifact_from_runtime_catalog(
    source_plan: Mapping[str, object], *, placement_capture: Mapping[str, object],
) -> dict:
    """Derive a ghost artifact using the capture's first item per prototype.

    Capture bytes, digest and scope are caller assertions, not authenticated
    runtime provenance. Only entity ghosts with one-item requirements are
    supported; a tile ghost or unexported prototype fails closed.
    """
    if not isinstance(placement_capture, Mapping) or set(placement_capture) != _CAPTURE_KEYS:
        raise ValueError("placement_capture must contain catalog bytes, digest, scope and active mods")
    source = _normalise_source(source_plan)
    validate_build_plan(source)
    phases, _ = _convert(source)
    converted = {"phases": phases}
    _assert_ghost_only(converted)
    entities, tiles = _prototype_action_counts(converted)
    if tiles:
        raise ValueError("runtime placement capture does not support tile ghosts")
    item_mapping = mapping_from_runtime_catalog(
        placement_capture["catalog_bytes"], expected_sha256=placement_capture["expected_sha256"],
        entity_names=entities, surface=placement_capture["surface"], force=placement_capture["force"],
        active_mods=placement_capture["active_mods"],
    )
    return derive_ghost_artifact(source, item_mapping=item_mapping)


def _normalise_source(source_plan: Mapping[str, object]) -> dict:
    if not isinstance(source_plan, Mapping):
        raise ValueError("Source plan must be a mapping")
    unknown_top_level = sorted(set(source_plan) - {"phases"})
    if unknown_top_level:
        raise ValueError(
            "Source plan has unknown top-level field(s): "
            f"{unknown_top_level}"
        )
    phases = source_plan.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("Source plan must contain at least one phase")

    normalised_phases = []
    for phase in phases:
        if not isinstance(phase, Mapping):
            raise ValueError("Source plan phases must be mappings")
        if set(phase) != {"name", "actions"}:
            raise ValueError(
                "Source plan phase must contain exactly 'name' and 'actions'"
            )
        name = phase["name"]
        phase_actions = phase["actions"]
        if not isinstance(name, str) or not name:
            raise ValueError("Source plan phase names must be non-empty strings")
        if not isinstance(phase_actions, list):
            raise ValueError("Source plan phase actions must be a list")
        normalised_phases.append({
            "name": name,
            "actions": [copy.deepcopy(action) for action in phase_actions],
        })
    return {"phases": normalised_phases}


def _convert(source: dict) -> tuple[list[dict], list[dict]]:
    phases = []
    converted: list[dict] = []
    for phase_index, phase in enumerate(source["phases"]):
        actions = []
        for action_index, action in enumerate(phase["actions"]):
            if not isinstance(action, Mapping):
                raise ValueError("Source plan actions must be mappings")
            action_type = action.get("action_type")
            if action_type == "remove_entity":
                raise ValueError(
                    "Cannot derive a ghost artifact from a removal action: "
                    "removals are not bot-buildable ghosts"
                )
            if action.get("entity") in INFINITY_ENTITIES:
                raise ValueError(
                    f"Cannot derive a ghost artifact from scripted source "
                    f"{action['entity']!r}"
                )
            _assert_known_settings(action)
            _assert_exact_position(action)

            if action_type in _GHOST_ACTION_TYPES:
                actions.append(copy.deepcopy(dict(action)))
                continue
            if action_type == "configure_entity":
                raise ValueError(
                    "configure_entity cannot be represented identically as a "
                    "bot-buildable ghost; configuration requires a separate "
                    "existing-entity contract"
                )
            if action_type != "place_entity":
                raise ValueError(
                    f"Unsupported action_type {action_type!r} in source geometry"
                )

            entity = action.get("entity")
            if not isinstance(entity, str) or entity not in _CONVERTIBLE_ENTITIES:
                raise ValueError(
                    f"Unsupported direct action entity {entity!r}: only "
                    "ordinary terminal collector/inserter placements may be "
                    "rewritten as ghosts"
                )
            unsupported = sorted(set(action) - _CONVERTIBLE_ACTION_KEYS)
            if unsupported:
                raise ValueError(
                    f"Direct {entity} carries setting(s) {unsupported} that "
                    "cannot be represented identically as a bot-buildable ghost"
                )

            ghost = {
                "action_type": "place_ghost",
                "entity": entity,
                "position": copy.deepcopy(action["position"]),
            }
            if "direction" in action:
                ghost["direction"] = action["direction"]
            actions.append(ghost)
            converted.append({
                "phase": phase["name"],
                "phase_index": phase_index,
                "action_index": action_index,
                "entity": entity,
                "position": copy.deepcopy(action["position"]),
                "direction": action.get("direction"),
            })
        phases.append({"name": phase["name"], "actions": actions})
    return phases, converted


def _assert_known_settings(action: Mapping[str, object]) -> None:
    unknown = sorted(set(action) - _SCHEMA_ACTION_KEYS)
    if unknown:
        raise ValueError(f"Action has unknown setting(s): {unknown}")
    if action.get("action_type") == "place_tile_ghost":
        if "entity" in action:
            raise ValueError("place_tile_ghost cannot carry an entity")
        if not isinstance(action.get("tile"), str) or not action.get("tile"):
            raise ValueError("place_tile_ghost requires a non-empty tile")
    elif action.get("action_type") in _GHOST_ACTION_TYPES:
        if "tile" in action:
            raise ValueError("place_ghost cannot carry a tile")
        if not isinstance(action.get("entity"), str) or not action.get("entity"):
            raise ValueError("place_ghost requires a non-empty entity")


def _assert_exact_position(action: Mapping[str, object]) -> None:
    position = action.get("position")
    if not isinstance(position, Mapping) or set(position) != {"x", "y"}:
        raise ValueError(
            "Every action position must contain exactly 'x' and 'y'"
        )


def _assert_ghost_only(plan: dict) -> None:
    for phase in plan["phases"]:
        for action in phase["actions"]:
            action_type = action.get("action_type")
            if action_type not in _GHOST_ACTION_TYPES:
                raise ValueError(
                    f"Derived artifact still contains non-ghost action_type "
                    f"{action_type!r}"
                )
            if action.get("entity") in INFINITY_ENTITIES:
                raise ValueError(
                    f"Derived artifact still contains scripted source "
                    f"{action['entity']!r}"
                )


def _normalise_item_mapping(
    item_mapping: object,
) -> tuple[str, dict[str, str], dict[str, str]]:
    if not isinstance(item_mapping, Mapping):
        raise ValueError("item_mapping must be a mapping")
    if set(item_mapping) != _MAPPING_KEYS:
        raise ValueError(
            "item_mapping must contain exactly 'provenance', 'entities' and "
            "'tiles'"
        )
    provenance = item_mapping["provenance"]
    if not isinstance(provenance, str) or not provenance.strip():
        raise ValueError(
            "item_mapping provenance must be a non-empty string naming where "
            "the prototype-to-item mapping came from"
        )
    entities = _normalise_prototype_map(item_mapping["entities"], "entities")
    tiles = _normalise_prototype_map(item_mapping["tiles"], "tiles")
    overlap = sorted(set(entities) & set(tiles))
    if overlap:
        raise ValueError(
            f"item_mapping prototype(s) {overlap} appear under both entities "
            "and tiles: ambiguous mapping"
        )
    return provenance, entities, tiles


def _normalise_prototype_map(values: object, label: str) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise ValueError(f"item_mapping {label} must be a mapping")
    normalised: dict[str, str] = {}
    for prototype, item in values.items():
        if not isinstance(prototype, str) or not prototype:
            raise ValueError(
                f"item_mapping {label} prototype names must be non-empty strings"
            )
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"item_mapping {label}[{prototype!r}] must name exactly one "
                "non-empty item"
            )
        normalised[prototype] = item
    return dict(sorted(normalised.items()))


def _prototype_action_counts(plan: dict) -> tuple[Counter[str], Counter[str]]:
    entities: Counter[str] = Counter()
    tiles: Counter[str] = Counter()
    for phase in plan["phases"]:
        for action in phase["actions"]:
            if action.get("action_type") == "place_tile_ghost":
                tiles[str(action["tile"])] += 1
            else:
                entities[str(action["entity"])] += 1
    return entities, tiles


def _declared_item_bill(
    entity_counts: Counter[str],
    tile_counts: Counter[str],
    entity_items: Mapping[str, str],
    tile_items: Mapping[str, str],
) -> dict[str, int]:
    bill: Counter[str] = Counter()
    for prototype, count in entity_counts.items():
        item = entity_items.get(prototype)
        if item is None:
            raise ValueError(
                f"No item mapping for ghost entity prototype {prototype!r}"
            )
        bill[item] += count
    for prototype, count in tile_counts.items():
        item = tile_items.get(prototype)
        if item is None:
            raise ValueError(
                f"No item mapping for tile ghost prototype {prototype!r}"
            )
        bill[item] += count
    return dict(sorted(bill.items()))
