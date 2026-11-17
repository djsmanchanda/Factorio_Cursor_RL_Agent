# Path: tests/test_laya_runtime_components.py | Purpose: Verify startup provenance, immutable resume and legacy behavior.
import json
from types import SimpleNamespace

import pytest

from orchestrator.runtime_component_manifest import ManifestMismatch, build_manifest, install_manifest
from tools.laya_strict_sidecar import run as sidecar_run

pytestmark = pytest.mark.fast


def test_deterministic_manifest_and_source_is_not_loaded_attestation(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"world_id": "w", "surface": "nauvis", "engine_version": "declared"}))
    first = build_manifest(entrypoint="fixture", world_root=tmp_path)
    assert first == build_manifest(entrypoint="fixture", world_root=tmp_path)
    assert first["source"]["files_sha256"]["factorio_mod/control.lua"]
    assert first["runtime"]["loaded_mods"]["state"] == "unknown"
    assert first["runtime"]["loaded_engine"]["state"] == "unknown"
    assert first["runtime"]["declared_world"]["identity"]["engine_version"] == "declared"
    import jsonschema
    from orchestrator.runtime_component_manifest import ROOT
    jsonschema.validate(first, json.loads((ROOT / "schemas/laya_runtime_components.schema.json").read_text()))


def test_immutable_mismatch_and_legacy_missing(tmp_path):
    path = tmp_path / "components.json"
    first = build_manifest(entrypoint="fixture")
    ref = install_manifest(path, first)
    original = path.read_bytes()
    assert install_manifest(path, first) == ref
    changed = {**first, "execution": {**first["execution"], "entrypoint": "changed"}}
    with pytest.raises(ManifestMismatch):
        install_manifest(path, changed)
    assert path.read_bytes() == original
    missing = tmp_path / "legacy.json"
    assert install_manifest(missing, first, legacy=True)["state"] == "unknown"
    assert not missing.exists()


def test_sidecar_artifact_reference_and_legacy(tmp_path):
    out = tmp_path / "strict.json"
    result = sidecar_run(lambda: None, out, interval=1, connect_timeout=1, max_failures=1,
                         stopped=lambda: True)
    ref = result["runtime_components"]
    assert ref["state"] == "known"
    assert json.loads(out.read_text())["runtime_components"] == ref
    assert (tmp_path / "strict.json.components.json").is_file()
    old = tmp_path / "old.json"
    old.write_text("{}")
    result = sidecar_run(lambda: None, old, interval=1, connect_timeout=1, max_failures=1,
                         stopped=lambda: True)
    assert result["runtime_components"]["state"] == "unknown"


@pytest.mark.parametrize("different", [False, True])
def test_competing_manifest_publish_never_overwrites_evidence(tmp_path, monkeypatch, different):
    import orchestrator.runtime_component_manifest as manifests
    path = tmp_path / "components.json"
    first = build_manifest(entrypoint="fixture")
    contender = {**first, "version": "other"} if different else first
    original_link = manifests.os.link

    def concurrent_publish(temporary, target):
        # No reader can see the first writer's partial bytes.
        assert not path.exists()
        assert manifests.manifest_bytes(first) == type(path)(temporary).read_bytes()
        target.write_bytes(manifests.manifest_bytes(contender))
        return original_link(temporary, target)

    monkeypatch.setattr(manifests.os, "link", concurrent_publish)
    if different:
        with pytest.raises(ManifestMismatch):
            install_manifest(path, first)
    else:
        assert install_manifest(path, first)["state"] == "known"
    assert path.read_bytes() == manifests.manifest_bytes(contender)
    assert list(tmp_path.iterdir()) == [path]


def test_failed_flush_does_not_publish_partial_manifest(tmp_path, monkeypatch):
    import orchestrator.runtime_component_manifest as manifests
    monkeypatch.setattr(manifests.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("flush failed")))
    with pytest.raises(OSError, match="flush failed"):
        install_manifest(tmp_path / "components.json", build_manifest(entrypoint="fixture"))
    assert not list(tmp_path.iterdir())


def test_nonfinite_manifest_rejected_before_publication(tmp_path):
    with pytest.raises(ValueError):
        install_manifest(tmp_path / "components.json", {"value": float("nan")})
    assert not list(tmp_path.iterdir())


def test_runner_mismatch_prevents_session_construction_and_journal_write(tmp_path, monkeypatch):
    from training.laya_ops_runner import run
    import training.laya_ops
    journal = tmp_path / "journal.jsonl"
    original = b"original manifest\n"
    journal.with_name(journal.name + ".components.json").write_bytes(original)
    monkeypatch.setattr(training.laya_ops, "DeterministicSession",
                        lambda *a, **k: pytest.fail("session constructed before identity check"))
    with pytest.raises(ManifestMismatch):
        run(SimpleNamespace(root=None), None, journal=journal, decisions=0, measure_ticks=600,
            seed=1, run_id="identity-test")
    assert not journal.exists()
    assert journal.with_name(journal.name + ".components.json").read_bytes() == original


def test_runner_contract_versions_are_supplied_by_their_owner():
    from training.laya_ops_checkpoint import VERSION as restart_version
    from training.laya_operation_outcome import VERSION as outcome_version
    first = build_manifest(entrypoint="fixture", runner_contracts={
        "ops_restart": restart_version, "typed_operation_outcome": outcome_version})
    assert first["contracts"]["ops_restart"] == restart_version
    assert first["contracts"]["typed_operation_outcome"] == outcome_version
    sidecar = build_manifest(entrypoint="sidecar")
    assert sidecar["contracts"]["ops_restart"] is None
    assert sidecar["contracts"]["typed_operation_outcome"] is None


def test_frozen_source_does_not_claim_its_parent_repository_identity(tmp_path, monkeypatch):
    import subprocess
    import orchestrator.runtime_component_manifest as manifests
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    frozen = tmp_path / "frozen"
    (frozen / "orchestrator").mkdir(parents=True)
    (frozen / "orchestrator" / "planner.py").write_text("# frozen source\n")
    monkeypatch.setattr(manifests, "ROOT", frozen)
    source = build_manifest(entrypoint="frozen")["source"]
    assert source["files_sha256"]["orchestrator/planner.py"]
    for field in ("branch", "commit", "tracked_dirty", "tracked_diff_sha256"):
        assert source[field] is None
