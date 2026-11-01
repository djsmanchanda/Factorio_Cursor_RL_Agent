# Path: tests/test_runtime_placement_mapping.py | Purpose: Test pinned prototype placement mapping and ghost bills offline.
import copy
import hashlib
import json

import pytest

from core.runtime_placement_mapping import PlacementMappingError, mapping_from_runtime_catalog
from tests.test_optimizer_artifact_binding import _artifact, _bind
from planners.ghost_line_artifact import derive_ghost_artifact, derive_ghost_artifact_from_runtime_catalog


pytestmark = pytest.mark.contracts
MODS = {"base": "2.1.21", "test-mod": "1.0"}


def _catalog():
    return {"schema_version": "1.1.0", "kind": "runtime_catalog", "ok": True, "tick": 42,
            "provenance": {"export_command": "export_runtime_catalog", "export_tick": 42,
                           "active_mods": MODS, "active_mods_count": 2,
                           "scope": {"force": "player", "surface": "nauvis",
                                     "surface_requested": True, "entity_scope": "selected_prototype_types",
                                     "entity_types": ["fixture"]}},
            "entities": [{"name": name, "type": "fixture", "placement": {"items": [{"name": name, "count": 1}]}}
                         for name in _artifact()["item_mapping"]["entities"]]}


def _mapping(catalog, **overrides):
    raw = json.dumps(catalog).encode()
    arguments = {"expected_sha256": hashlib.sha256(raw).hexdigest(),
                 "entity_names": _artifact()["item_mapping"]["entities"],
                 "surface": "nauvis", "force": "player", "active_mods": MODS}
    arguments.update(overrides)
    return mapping_from_runtime_catalog(raw, **arguments)


def test_mapping_derives_matching_bound_ghost_bill():
    existing = _artifact()
    mapping = _mapping(_catalog())
    generated = derive_ghost_artifact(existing["plan"], item_mapping=mapping)
    bound = _bind(generated)
    assert generated["declared_item_bill"] == existing["declared_item_bill"]
    assert bound.plan.required_materials == _bind(existing).plan.required_materials
    assert "sha256:" in mapping["provenance"]
    assert bound.execution_authorized is False


def test_engine_first_item_wins_even_if_second_matches_prototype():
    catalog = _catalog()
    entity = catalog["entities"][0]
    entity["placement"]["items"] = [{"name": "actual-item", "count": 1},
                                    {"name": entity["name"], "count": 1}]
    assert _mapping(catalog)["entities"][entity["name"]] == "actual-item"


@pytest.mark.parametrize("mutate", [
    lambda catalog: catalog.update(schema_version="1.0.0"),
    lambda catalog: catalog.update(ok=False),
    lambda catalog: catalog["provenance"].update(export_tick=41),
    lambda catalog: catalog["provenance"].update(active_mods_count=True),
    lambda catalog: catalog["provenance"]["scope"].update(surface="other"),
    lambda catalog: catalog["provenance"]["scope"].update(surface_requested=False),
    lambda catalog: catalog["provenance"].update(active_mods={"base": "other"}),
    lambda catalog: catalog["entities"].append(copy.deepcopy(catalog["entities"][0])),
    lambda catalog: catalog["entities"][0].update(type="not-exported"),
    lambda catalog: catalog["entities"][0]["placement"].update(items=[]),
    lambda catalog: catalog["entities"][0]["placement"]["items"][0].update(count=2),
    lambda catalog: catalog["entities"][0]["placement"]["items"][0].update(count=True),
])
def test_malformed_or_unrepresentable_capture_fails_closed(mutate):
    catalog = _catalog()
    mutate(catalog)
    with pytest.raises(PlacementMappingError):
        _mapping(catalog)


def test_missing_entity_and_wrong_digest_fail_closed():
    with pytest.raises(PlacementMappingError, match="not exported"):
        _mapping(_catalog(), entity_names=["missing"])
    with pytest.raises(PlacementMappingError, match="SHA-256 mismatch"):
        _mapping(_catalog(), expected_sha256="0" * 64)


def test_duplicate_json_keys_are_rejected_even_when_digest_matches():
    raw = b'{"kind":"runtime_catalog","kind":"forged"}'
    with pytest.raises(PlacementMappingError, match="duplicate JSON"):
        mapping_from_runtime_catalog(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                     entity_names=["pipe"], force="player", surface="nauvis", active_mods=MODS)


def test_science_artifact_uses_capture_first_item_and_binds_bill():
    existing = _artifact()
    catalog = _catalog()
    belt = next(entity for entity in catalog["entities"] if entity["name"] == "transport-belt")
    belt["placement"]["items"] = [{"name": "captured-belt-item", "count": 1},
                                  {"name": "transport-belt", "count": 1}]
    raw = json.dumps(catalog).encode()
    capture = {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
               "surface": "nauvis", "force": "player", "active_mods": MODS}
    artifact = derive_ghost_artifact_from_runtime_catalog(existing["plan"], placement_capture=capture)
    assert artifact["declared_item_bill"]["captured-belt-item"] == existing["declared_item_bill"]["transport-belt"]
    assert "transport-belt" not in artifact["declared_item_bill"]
    assert dict(_bind(artifact).plan.required_materials) == artifact["declared_item_bill"]


def test_capture_rejects_tile_ghosts_before_billing():
    raw = json.dumps(_catalog()).encode()
    capture = {"catalog_bytes": raw, "expected_sha256": hashlib.sha256(raw).hexdigest(),
               "surface": "nauvis", "force": "player", "active_mods": MODS}
    plan = {"phases": [{"name": "landfill", "actions": [
        {"action_type": "place_tile_ghost", "tile": "landfill", "position": {"x": 0, "y": 0}}
    ]}]}
    with pytest.raises(ValueError, match="tile ghosts"):
        derive_ghost_artifact_from_runtime_catalog(plan, placement_capture=capture)
