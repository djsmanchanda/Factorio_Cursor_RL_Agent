# Path: tests/test_laya_checkpoint_regression.py | Purpose: Test disposable checkpoint, queue and observational export contracts.
import hashlib
import json
from zipfile import ZipFile

import pytest

from training.laya_checkpoint_bundle import ROLES, capture, restore, validate
from training.laya_outcome_export import export
from training.laya_segment_queue import Queue


IDENTITY = {"source_sha256": "a" * 64, "creator_code_sha256": "b" * 64, "creator_commit": "f40b875",
            "mod_sha256": "c" * 64, "factorio_version": "2.0", "predicate_version": "c6-v1",
            "predicate_sha256": "9" * 64, "state_schema": "test-v1", "ancestor_sha256": "d" * 64}


class FakeHook:
    def __init__(self, save):
        self.save = save
        self.restored = None

    def capture_quiescent(self):
        return {"save": self.save, "paused": True, "tick": 100, "before_fingerprint": "same",
                "after_fingerprint": "same", "strict_earned": True, "strict_predicate_version": "c1-v1",
                "checkpoint": "C1", "sidecars": {role: {"value": role} for role in ROLES}}

    def restore_quiescent(self, save, state, manifest, attempt):
        self.restored = (save, state, manifest, attempt)


@pytest.fixture
def bundle(tmp_path):
    save = tmp_path / "save.zip"
    with ZipFile(save, "w") as archive:
        archive.writestr("level.dat", "fake")
    hook = FakeHook(save)
    catalog = tmp_path / "catalog"
    capture(catalog, "gen1", hook, checkpoint="C1", identity=IDENTITY, lineage="lineage1")
    return catalog, hook


def spec():
    arm = {"commit": "f40b875", "code_sha256": "e" * 64, "freeze_sha256": "f" * 64, "dirty": False}
    return {"suite_id": "suite1", "arms": {"candidate": arm, "reference": arm}, "seeds": [1, 2],
            "generations": ["gen1"], "budget_ticks": 1000, **{key: IDENTITY[key] for key in
            ("factorio_version", "mod_sha256", "predicate_version", "predicate_sha256", "state_schema", "source_sha256")}}


def test_capture_restore_and_tampering(bundle):
    catalog, hook = bundle
    manifest = validate(catalog, "gen1", expected={"predicate_version": "c6-v1"})
    restore(catalog, "gen1", hook, expected={"state_schema": "test-v1"}, attempt_id="new-attempt")
    assert manifest["tick"] == 100 and set(hook.restored[1]) == set(ROLES)
    assert hook.restored[3] != manifest["lineage"]
    with pytest.raises(ValueError, match="identity"):
        validate(catalog, "gen1", expected={"predicate_version": "proxy-v2"})
    (catalog / "gen1" / "policy_rng.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate(catalog, "gen1")


def test_missing_state_and_unsafe_generation_rejected(tmp_path):
    save = tmp_path / "save.zip"
    with ZipFile(save, "w") as archive:
        archive.writestr("x", "x")
    hook = FakeHook(save)
    original = hook.capture_quiescent
    hook.capture_quiescent = lambda: {**original(), "sidecars": {}}
    with pytest.raises(ValueError, match="incomplete"):
        capture(tmp_path / "catalog", "gen", hook, checkpoint="C1", identity=IDENTITY, lineage="lineage")
    with pytest.raises(ValueError):
        validate(tmp_path / "catalog", "../escape")


class FakeManager:
    global_admission_contract = "laya-global-admission-v1"

    def __init__(self):
        self.jobs = {}
        self.fail_after_submit = True

    def submit_or_observe(self, job):
        if job["attempt_id"] not in self.jobs:
            index = len(self.jobs)
            self.jobs[job["attempt_id"]] = {"game_port": 34000 + index, "rcon_port": 27000 + index,
                                            "root": f"disposable/{index}"}
        if self.fail_after_submit:
            self.fail_after_submit = False
            raise RuntimeError("coordinator interrupted after admission")
        return {"status": "running"}


def test_queue_recovery_matching_and_strict_baseline(bundle, tmp_path):
    catalog, _ = bundle
    path = tmp_path / "queue.db"
    queue = Queue(path, catalog)
    assert len(queue.enqueue(spec())) == 8
    manager = FakeManager()
    with pytest.raises(RuntimeError):
        queue.dispatch("suite1", manager)
    queue.close()
    queue = Queue(path, catalog)
    queue.dispatch("suite1", manager)
    assert len(manager.jobs) == 8
    assert len({item["game_port"] for item in manager.jobs.values()}) == 8
    assert len({item["rcon_port"] for item in manager.jobs.values()}) == 8
    assert len({item["root"] for item in manager.jobs.values()}) == 8
    jobs = queue.jobs("suite1")
    segment = next(job for job in jobs if job["checkpoint"] == "C1")
    queue.finish(segment["attempt_id"], "completed", {"predicate_version": "c2-v1", "predicate_sha256": "9" * 64, "baseline_verified": True,
                 "strict_telemetry_complete": True, "strict_reached": {"C2": 100}, "proxy": {"version": "v2"}})
    report = queue.report("suite1")
    row = next(row for row in report["rows"] if row["attempt_id"] == segment["attempt_id"])
    assert row["successor_passed"] is False
    assert report["planned"] == 8 and report["terminal"] == 1 and report["unknown"] == 7
    assert report["full_run_gate"] == "not_evaluated"
    assert {(job["checkpoint"], job["seed"]) for job in jobs if job["arm"] == "candidate"} == {(job["checkpoint"], job["seed"]) for job in jobs if job["arm"] == "reference"}
    later = next(job for job in jobs if job["checkpoint"] == "C1" and job["attempt_id"] != segment["attempt_id"])
    queue.finish(later["attempt_id"], "functional_failure", {"predicate_version": "c2-v1", "predicate_sha256": "9" * 64, "baseline_verified": True,
                 "strict_telemetry_complete": True, "strict_reached": {"C2": 101}})
    assert next(row for row in queue.report("suite1")["rows"] if row["attempt_id"] == later["attempt_id"])["later_failure"]
    queue.close()


def test_tampered_generation_is_incompatible_before_admission(bundle, tmp_path):
    catalog, _ = bundle
    queue = Queue(tmp_path / "queue.db", catalog)
    queue.enqueue(spec())
    (catalog / "gen1" / "phase_baselines.json").write_text("{}")
    manager = FakeManager()
    manager.fail_after_submit = False
    queue.dispatch("suite1", manager)
    assert sum(job["status"] == "incompatible" for job in queue.jobs("suite1")) == 4
    assert len(manager.jobs) == 4


def test_outcomes_are_observational_and_joined():
    menu = [{"option_id": "wait", "probability": 0.3, "legal": True}, {"option_id": "build", "probability": 0.7, "legal": True}]
    menu_hash = hashlib.sha256(json.dumps(menu, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    choice = {"choice_id": "choice", "attempt_id": "attempt", "ancestor_sha256": "a" * 64,
              "lineage_id": "lineage", "state": {}, "tick": 10, "menu": menu, "menu_sha256": menu_hash, "selected_option_id": "wait"}
    receipt = {"choice_id": "choice", "attempt_id": "attempt", "selected_option_id": "wait", "status": "waited"}
    outcome = {"attempt_id": "attempt", "ancestor_sha256": "a" * 64, "lineage_id": "lineage", "status": "timeout",
               "censored": True, "predicate_version": "c6-v1", "predicate_sha256": "9" * 64,
               "strict_measurements": [{"checkpoint": "C6", "predicate_version": "c6-v1", "tick": 20, "value": None}], "end_tick": 20}
    rows = export([choice], [receipt], [outcome])
    assert rows[0]["label_kind"] == "observational" and rows[0]["selected_propensity"] == 0.3
    with pytest.raises(ValueError, match="join"):
        export([choice], [], [outcome])
    with pytest.raises(ValueError, match="menu hash"):
        export([{**choice, "menu_sha256": "bad"}], [receipt], [outcome])
