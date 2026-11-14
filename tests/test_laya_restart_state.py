# Path: tests/test_laya_restart_state.py | Purpose: Verify bounded restart ownership and strict milestone continuity.
import copy
import json

import pytest

from orchestrator.bootstrap_work import BootstrapWorkLedger
from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot, RecipeEvidence
from orchestrator.material_reservations import MaterialReservationLedger
from orchestrator.operation_session import DeterministicSession
from orchestrator.checkpoint_restart_state import RestartState, VERSION
from training.laya_restart_state import RestartState as TrainingRestartState
from training.laya_restart_state import export_restart_state, import_restart_state, sidecar_registry

pytestmark = pytest.mark.fast


def snapshot(tick, count):
    return FactorySnapshot.from_mapping({"tick": tick, "base_valid": True,
        "production": {"plastic-bar": {"produced": count, "craft_count": count}},
        "providers": {"plastic-bar": {"delivered": count}}})


def make_bundle(tmp_path):
    source = tmp_path / "source" / "server" / "script-output"
    source.mkdir(parents=True)
    identity = "laya-original"
    ledger = MaterialReservationLedger(source, episode_id=identity, surface="nauvis", force="player")
    ledger.declare("foundation", {"transport-belt": 4}, {"transport-belt": 4})
    registry = sidecar_registry(source, identity)
    BootstrapWorkLedger(source, episode_id=identity).depend("foundation", "belt", reason="shortage")
    for role in ("steel_seed", "steel_district", "oil_transaction"):
        registry[role].parent.mkdir(parents=True, exist_ok=True)
        registry[role].write_text(json.dumps({"role": role}))
    registry["bootstrap_district"].mkdir(parents=True)
    (registry["bootstrap_district"] / "0123456789abcdef.json").write_text('{"district": true}')
    baseline = snapshot(0, 1)
    first = snapshot(100, 2)
    evaluator = CheckpointEvaluator()
    assert evaluator.evaluate("C0", baseline).passed
    return source, export_restart_state(source, identity, "factorio-2/test-mod-hash", baseline, [baseline, first], evaluator)


def test_real_evaluator_baseline_history_and_ownership_roundtrip(tmp_path):
    source, bundle = make_bundle(tmp_path)
    target = tmp_path / "copy" / "server" / "script-output"
    target.mkdir(parents=True)
    state = import_restart_state(json.loads(json.dumps(bundle)), target, episode_id="laya-original",
                                 mod_scope="factorio-2/test-mod-hash")
    assert state.baseline == snapshot(0, 1)
    assert state.history == (snapshot(0, 1), snapshot(100, 2))
    assert state.evaluator.evaluate("C0", FactorySnapshot()).passed
    assert state.evaluator.evaluate("C4", snapshot(200, 2), baseline=state.baseline).passed
    assert not CheckpointEvaluator().evaluate("C4", snapshot(200, 2), baseline=snapshot(100, 2)).passed
    assert state.evaluator.evaluate("C5", snapshot(7200, 3), history=state.history).passed
    assert not CheckpointEvaluator().evaluate("C5", snapshot(7200, 3), history=state.history[-1:]).passed
    for role, path in sidecar_registry(source, "laya-original").items():
        destination = sidecar_registry(target, "laya-original")[role]
        if path.is_dir():
            assert {p.name: p.read_bytes() for p in path.iterdir()} == {p.name: p.read_bytes() for p in destination.iterdir()}
        else:
            assert path.read_bytes() == destination.read_bytes()
    ledger = MaterialReservationLedger(target, episode_id="laya-original", surface="nauvis", force="player")
    assert ledger.projects["foundation"].reserved == {"transport-belt": 4}
    with pytest.raises(ValueError, match="not empty"):
        import_restart_state(bundle, target, episode_id="laya-original", mod_scope="factorio-2/test-mod-hash")


def test_snapshot_fields_roundtrip_without_from_mapping_coercions(tmp_path):
    source = tmp_path / "server" / "script-output"
    baseline = FactorySnapshot(recipes={"recipe": RecipeEvidence(name="recipe", output_count=4, produced=None)})
    bundle = export_restart_state(source, "episode", "mods", baseline, [baseline], CheckpointEvaluator())
    state = import_restart_state(bundle, tmp_path / "target" / "script-output", episode_id="episode", mod_scope="mods")
    assert state.baseline == baseline


def test_missing_sidecars_are_explicit_and_symlinks_are_rejected(tmp_path):
    source = tmp_path / "source" / "script-output"
    baseline = snapshot(0, 1)
    bundle = export_restart_state(source, "episode", "mods", baseline, [baseline], CheckpointEvaluator())
    assert all(not entries for entries in bundle["sidecars"].values())
    target = tmp_path / "target" / "script-output"
    path = sidecar_registry(target, "episode")["material_reservations"]
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="Symlink"):
        import_restart_state(bundle, target, episode_id="episode", mod_scope="mods")


@pytest.mark.parametrize("mutation", ["version", "scope", "milestones", "reached", "history", "snapshot", "digest", "registry", "path"])
def test_invalid_bundle_fails_before_sidecars_are_written(tmp_path, mutation):
    _, bundle = make_bundle(tmp_path)
    bundle = copy.deepcopy(bundle)
    if mutation == "version": bundle["version"] = 2
    if mutation == "scope": bundle["scope"]["force"] = "other"
    if mutation == "milestones": bundle["milestones"][0]["predicate_version"] = "other"
    if mutation == "reached": bundle["reached"] = ["C99"]
    if mutation == "history": bundle["history"] = []
    if mutation == "snapshot": bundle["baseline"]["tick"] = True
    if mutation == "digest": next(iter(bundle["sidecars"]["material_reservations"].values()))["sha256"] = "0" * 64
    if mutation == "registry": del bundle["sidecars"]["oil_transaction"]
    if mutation == "path": bundle["sidecars"]["bootstrap_district"]["../bad.json"] = {}
    target = tmp_path / "target" / "server" / "script-output"
    with pytest.raises(ValueError):
        import_restart_state(bundle, target, episode_id="laya-original", mod_scope="factorio-2/test-mod-hash")
    assert not (target.parent / "logs").exists()


def test_training_export_import_restores_real_session_boundary(tmp_path):
    source, bundle = make_bundle(tmp_path)
    target = tmp_path / "copy" / "server" / "script-output"
    foreign = sidecar_registry(target, "other-episode")["material_reservations"]
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"untouched": true}')
    state = import_restart_state(bundle, target, episode_id="laya-original",
                                 mod_scope="factorio-2/test-mod-hash")
    assert TrainingRestartState is RestartState
    assert type(state) is RestartState
    assert bundle["version"] == VERSION == 1
    session = DeterministicSession.__new__(DeterministicSession)
    session.episode_id = "laya-original"
    session.script_output = target
    session.restore_restart_acceptance(state)
    assert session._baseline == snapshot(0, 1)
    assert session._history == [snapshot(0, 1), snapshot(100, 2)]
    assert "C0" in session._evaluator.reached
    assert session.export_restart_state(mod_scope="factorio-2/test-mod-hash") == bundle
    assert foreign.read_text() == '{"untouched": true}'
    assert MaterialReservationLedger(target, episode_id="laya-original", surface="nauvis", force="player").projects["foundation"].reserved == {"transport-belt": 4}
    assert sidecar_registry(target, "laya-original")["oil_transaction"].read_bytes() == sidecar_registry(source, "laya-original")["oil_transaction"].read_bytes()
    with pytest.raises(ValueError, match="initialized"):
        session.restore_restart_acceptance(state)
    other = DeterministicSession.__new__(DeterministicSession)
    other.episode_id = "foreign"
    other.script_output = target
    with pytest.raises(ValueError, match="identity"):
        other.restore_restart_acceptance(state)
    other.episode_id = "laya-original"
    other.script_output = tmp_path / "elsewhere"
    with pytest.raises(ValueError, match="identity"):
        other.restore_restart_acceptance(state)


@pytest.mark.parametrize("field,value", [("episode_id", "foreign"), ("surface", "other"),
                                         ("force", "other"), ("mod_scope", "other")])
def test_foreign_scope_rejected_without_sidecar_mutation(tmp_path, field, value):
    _, bundle = make_bundle(tmp_path)
    bundle["scope"][field] = value
    target = tmp_path / "target" / "server" / "script-output"
    foreign = sidecar_registry(target, "foreign")["material_reservations"]
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text('{"keep": true}')
    with pytest.raises(ValueError, match="scope"):
        import_restart_state(bundle, target, episode_id="laya-original", mod_scope="factorio-2/test-mod-hash")
    assert foreign.read_text() == '{"keep": true}'
    assert not sidecar_registry(target, "laya-original")["material_reservations"].exists()
