# Path: core/runtime_placement_mapping.py | Purpose: Derive ghost item mappings from pinned prototype observations.
"""Offline placement mapping from a caller-pinned runtime catalog capture.

The digest binds the supplied bytes, not their origin, freshness or truth. This
module does not verify a lane, inventory, terrain, bot availability or any
execution authority. Only one-item entity ghosts are currently representable
by the artifact contract; tile ghosts and multi-item placements fail closed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re


class PlacementMappingError(ValueError):
    """The capture cannot safely supply the requested placement mapping."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlacementMappingError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise PlacementMappingError(f"invalid JSON number: {value}")


def mapping_from_runtime_catalog(
    catalog_bytes: bytes, *, expected_sha256: str, entity_names: Iterable[str],
    surface: str, force: str, active_mods: Mapping[str, str],
) -> dict:
    """Return an artifact-compatible mapping for explicitly requested entities.

    Version 1.1.0 preserves the engine's first placement item or reports the
    entire list unavailable, rather than silently skipping malformed entries.
    Surface, force and mod expectations are caller assertions, not independent
    authentication. The report's tick is metadata, not a freshness guarantee.
    """
    if not isinstance(catalog_bytes, bytes):
        raise PlacementMappingError("catalog must be raw bytes")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise PlacementMappingError("expected catalog SHA-256 must be pinned")
    digest = hashlib.sha256(catalog_bytes).hexdigest()
    if digest != expected_sha256.lower():
        raise PlacementMappingError("catalog SHA-256 mismatch")
    if not isinstance(surface, str) or not surface.strip() or not isinstance(force, str) or not force.strip():
        raise PlacementMappingError("explicit surface and force are required")
    if (not isinstance(active_mods, Mapping) or not active_mods
            or any(not isinstance(key, str) or not key.strip()
                   or not isinstance(value, str) or not value.strip()
                   for key, value in active_mods.items())):
        raise PlacementMappingError("explicit active mods are required")
    if isinstance(entity_names, (str, bytes)):
        raise PlacementMappingError("entity names must be a collection")
    try:
        names = list(entity_names)
    except TypeError as error:
        raise PlacementMappingError("entity names must be a collection") from error
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise PlacementMappingError("entity names must be nonempty strings")
    try:
        report = json.loads(catalog_bytes, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlacementMappingError("invalid catalog JSON") from error
    if not isinstance(report, dict) or report.get("kind") != "runtime_catalog" or report.get("schema_version") != "1.1.0" or report.get("ok") is not True:
        raise PlacementMappingError("unsupported or unsuccessful runtime catalog")
    tick = report.get("tick")
    provenance = report.get("provenance")
    if type(tick) is not int or tick < 0 or not isinstance(provenance, dict):
        raise PlacementMappingError("invalid catalog provenance")
    mods = provenance.get("active_mods")
    scope = provenance.get("scope")
    if (provenance.get("export_command") != "export_runtime_catalog"
            or type(provenance.get("export_tick")) is not int
            or provenance["export_tick"] != tick
            or not isinstance(mods, dict) or mods != dict(active_mods)
            or type(provenance.get("active_mods_count")) is not int
            or provenance["active_mods_count"] != len(mods)
            or not isinstance(scope, dict) or scope.get("surface_requested") is not True
            or scope.get("surface") != surface or scope.get("force") != force
            or scope.get("entity_scope") != "selected_prototype_types"):
        raise PlacementMappingError("catalog provenance does not match expectations")
    entity_types = scope.get("entity_types")
    entities = report.get("entities")
    if (not isinstance(entity_types, list) or not entity_types
            or any(not isinstance(item, str) or not item for item in entity_types)
            or len(set(entity_types)) != len(entity_types) or not isinstance(entities, list)):
        raise PlacementMappingError("invalid catalog entity scope")
    indexed = {}
    for entity in entities:
        if not isinstance(entity, dict):
            raise PlacementMappingError("invalid catalog entity")
        name, kind = entity.get("name"), entity.get("type")
        if not isinstance(name, str) or not name or name in indexed or kind not in entity_types:
            raise PlacementMappingError("duplicate or out-of-scope catalog entity")
        indexed[name] = entity
    mapping = {}
    for name in sorted(set(names)):
        entity = indexed.get(name)
        if entity is None:
            raise PlacementMappingError(f"entity not exported: {name}")
        placement = entity.get("placement")
        items = placement.get("items") if isinstance(placement, dict) else None
        if not isinstance(items, list) or not items:
            raise PlacementMappingError(f"placement items unavailable: {name}")
        first = items[0]
        if (not isinstance(first, dict) or not isinstance(first.get("name"), str)
                or not first["name"].strip() or type(first.get("count")) is not int
                or first["count"] != 1):
            raise PlacementMappingError(f"unsupported first placement item: {name}")
        mapping[name] = first["name"]
    return {"provenance": f"runtime_catalog 1.1.0 sha256:{digest} tick:{tick}",
            "entities": mapping, "tiles": {}}
