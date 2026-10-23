# Path: tests/test_laya_ops_checkpoint.py | Purpose: Protect coherent checkpoint publication, exact RNG/controller recovery and cumulative budgets.
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import training.laya_ops_checkpoint as checkpoint
from training.laya_guided_policy import GuidedPolicy
from training.laya_clean_runner import Settings
from training.laya_ops_runner import run


def test_checkpoint_encoding_preserves_tuple_keys_sets_and_evaluator_types():
    from orchestrator.checkpoint_milestones import FactorySnapshot, CounterEvidence
    value = {("intent", 2): {1, 3}, "history": [FactorySnapshot(tick=20, production={"iron": CounterEvidence(produced=4)})],
             "gap": float("inf")}
    assert checkpoint.decode(json.loads(json.dumps(checkpoint.encode(value), allow_nan=False))) == value
    with pytest.raises(ValueError, match="unsupported"):
        checkpoint.decode({"type": "executable", "value": "anything"})


def _capture_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "ROOT", tmp_path)
    monkeypatch.setattr(checkpoint, "source_hashes", lambda: {"runtime": "pinned"})
    monkeypatch.setattr(checkpoint, "verify_instance", lambda root: {})
    monkeypatch.setattr(checkpoint, "_engine", lambda root: "engine")
    root = tmp_path / "data/world"
    (root / "server/logs/deterministic-material-reservations").mkdir(parents=True)
    (root / "server/logs/deterministic-material-reservations/logical.json").write_text('{"episode_id":"logical"}')
    save = root / "save.zip"; save.write_bytes(b"unique-completed-save")
    facts = {"tick": 123, "production": {"iron": 2}}
    adapter = SimpleNamespace(root=root, manifest={"mod_hashes": {"mod": "hash"}},
        bridge=SimpleNamespace(command=lambda _: "paused"), query=lambda: copy.deepcopy(facts),
        checkpoint=lambda: {"save": str(save), "facts": copy.deepcopy(facts), "ancestor_source_sha256": "ancestor"},
        go_live=lambda: None)
    session = SimpleNamespace(episode_id="logical", reference_point=(0, 0))
    state = {"run_id": "logical-run", "elapsed_wall": 90, "results": [], "settings": {}, "c6": {"run_id": "logical-run"}}
    return adapter, session, state


def test_atomic_generation_binds_save_sidecars_rng_and_source(tmp_path, monkeypatch):
    adapter, session, state = _capture_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "data/bundle"
    policy = GuidedPolicy(7)
    meta = checkpoint.capture(adapter, policy, session, destination, state)
    loaded, controller = checkpoint.load_bundle(destination)
    assert loaded == meta and controller["elapsed_wall"] == 90
    assert controller["rng"] == policy.rng.getstate()
    assert controller["session"]["episode_id"] == "logical"
    assert "context/server/logs/deterministic-material-reservations/logical.json" in meta["files"]
    (destination / "source.zip").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        checkpoint.load_bundle(destination)


def test_failed_save_generation_never_publishes_a_checkpoint(tmp_path, monkeypatch):
    adapter, session, state = _capture_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "data/bundle"
    adapter.query = lambda: {"tick": 124}
    with pytest.raises(ValueError, match="changed"):
        checkpoint.capture(adapter, GuidedPolicy(7), session, destination, state)
    assert not destination.exists()
    assert list(destination.parent.glob(".bundle.pending-*"))  # retained crash evidence


class World:
    root = None
    extra_recipes = ()
    def __init__(self, tick=1000): self.tick = tick
    def query(self): return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [], "networks": {},
                             "ports": [], "resources": [], "recipes": {}}
    def advance(self, ticks): self.tick += ticks


class Session:
    def __init__(self): self.calls = []
    def run(self, operation, **params):
        self.calls.append((operation, params))
        return {"ok": True, "operation": operation, "params": params}
    def close(self): pass


def test_restored_next_distribution_rng_draw_and_total_decisions_match_uninterrupted(tmp_path, monkeypatch):
    captures = []
    def capture(adapter, policy, session, destination, controller):
        stored = {**controller, "rng": policy.rng.getstate(), "session": {"episode_id": "logical"},
                  "policy": {"class": type(policy).__name__, "epsilon": policy.epsilon, "preferences": policy.preferences}}
        captures.append(checkpoint.decode(checkpoint.encode(stored)))
        return {"tick": adapter.tick, "capture_seconds": 0}
    monkeypatch.setattr(checkpoint, "capture", capture)
    monkeypatch.setattr(checkpoint, "restore_session", lambda *a: None)
    settings = Settings(max_game_ticks=540000, max_wall_seconds=6000)
    full = run(World(), GuidedPolicy(3), session=Session(), journal=tmp_path / "full.jsonl", decisions=8,
               measure_ticks=600, seed=3, run_id="logical-run", settings=settings,
               checkpoint_dir=tmp_path / "bundles", checkpoint_ticks=300)
    state = next(s for s in captures if s["summary"]["decisions"] >= 2)
    recovery = copy.deepcopy(state)
    resumed = run(World(state["checkpoint_tick"]), GuidedPolicy(999), session=Session(), journal=tmp_path / "resumed.jsonl",
                  decisions=8, measure_ticks=600, seed=3, run_id="logical-run", settings=settings, recovery=recovery)
    rows = lambda path: [json.loads(l) for l in path.read_text().splitlines() if json.loads(l).get("event") == "decision"]
    original = rows(tmp_path / "full.jsonl")[state["summary"]["decisions"]:]
    recovered = rows(tmp_path / "resumed.jsonl")
    assert [r["choice"] for r in recovered] == [r["choice"] for r in original]
    assert resumed["decisions"] == full["decisions"] == 8
    assert resumed["phases_reached"] == full["phases_reached"]
    assert resumed["laya_acceptance"] == full["laya_acceptance"]
    assert recovery == state  # The supplied verified generation remains immutable.
    with pytest.raises(ValueError, match="mismatch"):
        run(World(), GuidedPolicy(3), session=Session(), journal=tmp_path / "bad.jsonl", decisions=9,
            measure_ticks=600, seed=3, run_id="logical-run", settings=settings, recovery=state)


def test_recovery_does_not_reset_active_wall_budget(tmp_path, monkeypatch):
    # Exercise the same serialized controller shape with a fully exhausted allowance.
    captures = []
    def capture(adapter, policy, session, destination, controller):
        captures.append(checkpoint.decode(checkpoint.encode({**controller, "rng": policy.rng.getstate(),
            "session": {"episode_id": "logical"}, "policy": {"class": type(policy).__name__,
                "epsilon": policy.epsilon, "preferences": policy.preferences}})))
        return {"tick": adapter.tick, "capture_seconds": 0}
    monkeypatch.setattr(checkpoint, "capture", capture)
    monkeypatch.setattr(checkpoint, "restore_session", lambda *a: None)
    settings=Settings(max_game_ticks=540000,max_wall_seconds=60)
    run(World(), GuidedPolicy(3),session=Session(),journal=tmp_path/'prefix.jsonl',decisions=8,measure_ticks=600,
        seed=3,run_id='logical-run',settings=settings,checkpoint_dir=tmp_path/'bundles')
    state=captures[0];state['elapsed_wall']=60
    session=Session()
    result=run(World(state['checkpoint_tick']),GuidedPolicy(3),session=session,journal=tmp_path/'resumed.jsonl',decisions=8,
        measure_ticks=600,seed=3,run_id='logical-run',settings=settings,recovery=state)
    assert result['stop']=='wall_budget' and session.calls==[]


def test_pending_wait_uses_elapsed_age_across_host_monotonic_epoch_change(monkeypatch):
    from orchestrator import construction_coverage
    monkeypatch.setattr(construction_coverage,'_PENDING_POWER_BRIDGES',{('nauvis','player'):990.0})
    monkeypatch.setattr(checkpoint.time,'monotonic',lambda:1000.0)
    state=checkpoint.decode(checkpoint.encode(checkpoint.runtime_state()))
    assert state['orchestrator.construction_coverage']['_PENDING_POWER_BRIDGES'][('nauvis','player')]==10
    monkeypatch.setattr(checkpoint.time,'monotonic',lambda:20.0)
    checkpoint.restore_runtime(state)
    assert construction_coverage._PENDING_POWER_BRIDGES[('nauvis','player')]==10


def test_restore_preserves_shared_coverage_progress_identity():
    from orchestrator import construction_coverage, stage_services
    original=construction_coverage._COVERAGE_PROGRESS
    state=checkpoint.decode(checkpoint.encode(checkpoint.runtime_state()))
    saved=state['orchestrator.construction_coverage']['_COVERAGE_PROGRESS']
    saved.revision=17
    checkpoint.restore_runtime(state)
    assert construction_coverage._COVERAGE_PROGRESS is original
    assert stage_services._COVERAGE_PROGRESS is original
    assert original.revision==17
