# Path: tests/test_checkpoint_catalog.py
# Purpose: Verify immutable checkpoint ordering, provenance, retention, and safe persistence.
from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema
import pytest

from tools.checkpoint_catalog import (
    CheckpointCatalog,
    DEFAULT_CHECKPOINT_DEFINITIONS,
    atomic_json,
    classify_commit_age,
    retention_capacity,
    save_name,
)


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_preserves_explicit_subcheckpoint_order_and_atomic_round_trip(tmp_path):
    path = tmp_path / "state" / "registry.json"
    catalog = CheckpointCatalog(path)
    catalog.add_checkpoint("C0", "base", "base-v1", order=0)
    catalog.add_checkpoint("C1", "starter_mall", "mall-v1", order=10)
    catalog.add_checkpoint("C1a", "mall_power", "mall-power-v1", order=15)
    catalog.add_checkpoint("C1b", "mall_output", "mall-output-v1", order=16)
    catalog.add_checkpoint("C2", "metals", "metals-v1", order=20)
    catalog.save()

    loaded = CheckpointCatalog.load(path)
    assert [item["id"] for item in loaded.checkpoints] == ["C0", "C1", "C1a", "C1b", "C2"]
    assert not list(path.parent.glob("*.tmp"))


def test_default_and_provisional_generations_require_explicit_promotion(tmp_path):
    catalog = CheckpointCatalog(tmp_path / "registry.json")
    catalog.add_checkpoint("C3", "plastic", "plastic-v1")
    provisional = {
        "generation_id": "g-star",
        "origin_checkpoint_id": "C3",
        "path": "bundles/g-star",
        "creator_commit": "abcdef12",
        "created_at": "2026-09-15T10:00:00Z",
        "provisional": True,
        "pinned": False,
        "active_input": False,
    }
    catalog.add_generation("C3", provisional)
    catalog.set_provisional_default("C3", "g-star")
    assert catalog.default_generation("C3")["provisional"] is True
    with pytest.raises(ValueError, match="C0 verification"):
        catalog.promote_default("C3", "g-star")
    catalog.promote_default("C3", "g-star", c0_verified=True)
    assert catalog.default_generation("C3")["provisional"] is False
    assert catalog.default_generation("C3")["active_input"] is True


def test_retention_keeps_twenty_per_origin_and_all_protected_generations(tmp_path):
    catalog = CheckpointCatalog(tmp_path / "registry.json")
    catalog.add_checkpoint("C0", "base", "base-v1")
    for index in range(24):
        catalog.add_generation("C0", {
            "generation_id": f"g-{index}", "origin_checkpoint_id": "C0", "path": f"bundles/{index}",
            "creator_commit": "abcdef12", "created_at": f"2026-01-{index + 1:02d}T00:00:00Z",
            "provisional": index == 0, "pinned": index == 1, "active_input": index == 2,
        })
    removed = catalog.prune()
    remaining = catalog.checkpoint("C0")["generations"]
    assert len(remaining) == 22  # 20 rolling plus the pinned and active inputs
    assert {item["generation_id"] for item in removed} == {"g-0", "g-3"}
    assert retention_capacity(5) == 100


def test_save_names_are_readable_and_collision_safe():
    started = datetime(2026, 9, 15, 14, 7, tzinfo=timezone.utc)
    first = save_name("C1a", "abcdef123456", started, sequence=2)
    assert first == "checkpoint1a_abcdef12_09_15_02pm_s02"
    second = save_name("C1a", "abcdef123456", started, sequence=2, existing=[first])
    assert second == first + "_02"
    assert "*" not in second
    with pytest.raises(ValueError):
        save_name("C../", "abcdef12", started)


def test_commit_age_classification_supports_stale_divergent_and_unverifiable():
    assert classify_commit_age("new", "new") == "current"
    resolver = lambda creator, newest: {("old", "new"): 11, ("near", "new"): 3, ("fork", "new"): None}.get((creator, newest))
    assert classify_commit_age("old", "new", resolver=resolver) == "stale"
    assert classify_commit_age("near", "new", resolver=resolver) == "current"
    assert classify_commit_age("fork", "new", resolver=resolver) == "divergent"
    assert classify_commit_age("unknown", "new") == "unverifiable"


def test_safe_atomic_json_rejects_escape_and_symlink(tmp_path):
    root = tmp_path / "catalog"
    atomic_json(root / "registry.json", {"ok": True}, root=root)
    assert json.loads((root / "registry.json").read_text()) == {"ok": True}
    with pytest.raises(ValueError, match="escapes"):
        atomic_json(tmp_path / "outside.json", {}, root=root)
    link = root / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        atomic_json(link / "bad.json", {}, root=root)


def test_new_payloads_validate_against_their_contracts():
    catalog = CheckpointCatalog(Path("registry.json"))
    for order, (checkpoint_id, label, predicate_version) in enumerate(DEFAULT_CHECKPOINT_DEFINITIONS):
        catalog.add_checkpoint(checkpoint_id, label, predicate_version, order=order)
    registry_schema = json.loads((ROOT / "schemas/deterministic_checkpoint_registry.schema.json").read_text())
    jsonschema.validate(catalog.payload, registry_schema)
    bundle_schema = json.loads((ROOT / "schemas/deterministic_checkpoint_bundle.schema.json").read_text())
    bundle = {
        "version": 1, "kind": "milestone-regression", "acceptance_eligible": False,
        "segment_regression_eligible": True, "bundle_id": "bundle-1",
        "checkpoint_id": "C0", "generation_id": "g0", "run_id": "run-1",
        "lineage_id": "lineage-1", "capture_tick": 1, "capture_reason": "milestone",
        "save_name": "checkpoint0_abcdef12_09_15_02pm_s01",
        "created_at": "2026-09-15T00:00:00Z",
        "world": {"path": "world.zip", "sha256": "a" * 64, "size": 1},
        "sidecars": [], "files": {"world.zip": "a" * 64},
        "file_manifest": {"world.zip": {"sha256": "a" * 64, "size": 1}},
        "predicate": {"version": "base-v1", "evidence": {}},
        "provenance": {"creator_commit": "abcdef12", "mod_hashes": {"mod": "a"}, "surface": "nauvis", "force": "player", "predicate_version": "base-v1"},
    }
    jsonschema.validate(bundle, bundle_schema)
