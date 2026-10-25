# Path: tests/test_laya_run_retention.py | Purpose: Protect run retention: recent, exemplar and active-cohort runs stay whole; condensed runs keep verified decision data.

import json
import subprocess
from pathlib import Path

import pytest

from tools.laya_run_retention import ACTIVE, MARKER, apply, condense, exemplars, plan, run_world_ok, score


def _job(job_id, cohort, *, finished, final=(), phases=(), c_seconds=None, status="completed"):
    top = max(phases, key=lambda p: int(p[1:]), default=None)
    return {"id": job_id, "status": status, "started": 1000 + finished - 1, "runner_exited": 1000 + finished,
            "world": str(cohort / "arms/a/code/data/laya-saved-world" / cohort.name / job_id),
            "accepted_final": list(final), "phases": list(phases),
            "phase_times": {top: {"game_seconds": c_seconds}} if top else {}}


def _run_world(data: Path) -> Path:
    world = data / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/r0"
    (world / "evidence").mkdir(parents=True)
    (world / "evidence/a.jsonl").write_text("a\n")
    (world / "evidence/decision-states.jsonl").write_text("b\n")
    return world


def _cohort(registry, name, jobs_spec):
    cohort = registry / name
    jobs = [_job(job_id, cohort, **spec) for job_id, spec in jobs_spec]
    for job in jobs:
        world = cohort.parent / job["world"]
        (world / "evidence").mkdir(parents=True)
        (world / "evidence/decision-states.jsonl").write_text('{"d": 1}\n')
    (cohort / "job-status.json").write_text(json.dumps({"jobs": jobs}))
    return cohort


def test_best_is_strict_milestones_then_speed_and_median_is_the_middle_run(tmp_path):
    cohort = tmp_path / "c"
    jobs = [_job("slow-c4", cohort, finished=1, final=["C0", "C4"], phases=["C4"], c_seconds=900),
            _job("fast-c4", cohort, finished=2, final=["C0", "C4"], phases=["C4"], c_seconds=300),
            _job("proxy-c6", cohort, finished=3, final=["C0"], phases=["C6"], c_seconds=100),
            _job("c2", cohort, finished=4, final=["C0", "C2"], phases=["C2"], c_seconds=200),
            _job("never-started", cohort, finished=5, status="queued")]
    jobs[-1].pop("started")
    # Strict final acceptance outranks a proxy-only C6; speed breaks the C4 tie.
    assert exemplars(jobs) == {"best": "fast-c4", "median": "c2"}


def test_plan_spares_recent_runs_exemplars_and_active_cohorts(tmp_path):
    registry = tmp_path / "experiments"
    old = _cohort(registry, "old", [(f"r{i}", {"finished": 10 + i, "final": ["C0"] * (i % 3 + 1),
                                               "phases": [f"C{i}"], "c_seconds": 50}) for i in range(5)])
    _cohort(registry, "live", [("done", {"finished": 1}), ("running", {"finished": 2, "status": "running"})])
    _cohort(registry, "resumable", [("done", {"finished": 3}), ("cut", {"finished": 4, "status": "interrupted"})])
    _cohort(registry, "new", [("n1", {"finished": 100}), ("n2", {"finished": 101})])

    selected = {r["job"]["id"] for r in plan(registry, keep_recent_runs=2)}

    best_and_median = set(exemplars(json.loads((old / "job-status.json").read_text())["jobs"]).values())
    assert len(best_and_median) == 2
    assert selected == {f"r{i}" for i in range(5)} - best_and_median


def test_condense_keeps_verified_decision_data_and_leaves_a_pointer(tmp_path):
    data = tmp_path / "data"
    world = data / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/a-s1"
    decisions = "".join(json.dumps({"index": i, "state": "x" * 50}) + "\n" for i in range(600))
    for relative, text in {"evidence/decision-states.jsonl": decisions, "manifest.json": "{}",
                           "server/script-output/laya_saved_world/1.json": "{}", "saves/baseline.zip": "zip",
                           "logs/laya-runner.log": "log"}.items():
        (world / relative).parent.mkdir(parents=True, exist_ok=True)
        (world / relative).write_text(text)
    corpus = tmp_path / "corpus"

    pointer = condense(world, data, corpus)

    stored = corpus / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/a-s1/evidence/decision-states.jsonl.zst"
    assert subprocess.run(["zstd", "-dc", str(stored)], stdout=subprocess.PIPE, check=True).stdout.decode() == decisions
    assert pointer["files"] == 2 and pointer["decision_records"] == 600
    assert not list(corpus.rglob("*.zip")) and not list(corpus.rglob("1.json"))
    assert [p.name for p in world.iterdir()] == [MARKER]


def test_apply_condenses_once_and_skips_pointers(tmp_path):
    registry = tmp_path / "experiments"
    _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"], "phases": ["C1"]}) for i in range(4)])
    corpus = tmp_path / "corpus"

    assert apply(registry, keep_recent_runs=0, corpus=corpus) == {"condensed": 2, "corpus": str(corpus)}
    assert apply(registry, keep_recent_runs=0, corpus=corpus)["condensed"] == 0


def test_cohort_controller_runs_retention_only_with_a_host_budget_and_never_fails(tmp_path, monkeypatch):
    from tools.run_laya_batch import Batch

    registry = tmp_path / "experiments"
    root = registry / "c"
    root.mkdir(parents=True)
    cfg = {"name": "c", "until": "2099-01-01T00:00:00+05:30", "concurrency": 1, "game_port_base": 1,
           "rcon_port_base": 2, "max_game_ticks": 600, "source_save": "s", "python": "p", "seeds": [1],
           "arms": [{"id": "a", "code": "c"}]}
    (root / "batch.json").write_text(json.dumps(cfg))
    calls = []
    monkeypatch.setattr("tools.run_laya_batch.apply_retention", lambda *a, **kw: calls.append(a) or {})
    Batch(root / "batch.json").retain()
    assert calls == []

    (root / "batch.json").write_text(json.dumps({**cfg, "host_budget": {"registry": str(registry)}}))

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("tools.run_laya_batch.apply_retention", fail)
    Batch(root / "batch.json").retain()
    events = [json.loads(line) for line in (root / "batch-events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "retention_error" and "disk full" in events[-1]["error"]


def test_an_interrupted_deletion_is_never_condensed_again(tmp_path, monkeypatch):
    registry = tmp_path / "experiments"
    _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"], "phases": ["C1"]}) for i in range(3)])
    target = plan(registry, keep_recent_runs=0)[0]
    world = Path(target["job"]["world"])
    (world / "saves").mkdir()

    def crash(_path):
        raise OSError("power loss")

    monkeypatch.setattr("tools.laya_run_retention._rmtree", crash)
    with pytest.raises(OSError):
        condense(world, registry.parent, tmp_path / "corpus")
    assert (world / MARKER).exists()
    assert world not in {Path(r["job"]["world"]) for r in plan(registry, keep_recent_runs=0)}


def test_resumable_statuses_cover_the_controller_rerun_set():
    from tools.run_laya_batch import Batch

    assert set(Batch.RERUN) <= ACTIVE


def test_host_resume_holds_retention_lock_through_publishing_state(tmp_path, monkeypatch):
    import fcntl
    from tools.run_laya_batch import Batch

    registry = tmp_path / "experiments"
    cohort = registry / "c"
    cohort.mkdir(parents=True)
    cfg = {"name": "c", "until": "2099-01-01T00:00:00+05:30", "concurrency": 1,
           "game_port_base": 1, "rcon_port_base": 2, "max_game_ticks": 600, "source_save": "s",
           "python": "p", "seeds": [1], "arms": [{"id": "a", "code": "c"}],
           "host_budget": {"registry": str(registry)}}
    config = cohort / "batch.json"
    config.write_text(json.dumps(cfg))
    batch = Batch(config)
    calls = []

    def probe(stage):
        with (registry / ".retention.lock").open("a") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(stage)

    monkeypatch.setattr(batch, "_resume", lambda: probe("resume"))
    monkeypatch.setattr(batch, "save", lambda: probe("publish"))
    batch.resume()

    assert calls == ["resume", "publish"]
    with (registry / ".retention.lock").open("a") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_plan_reads_a_legacy_bare_list_and_skips_malformed_entries(tmp_path):
    registry = tmp_path / "experiments"
    cohort = _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"]}) for i in range(4)])
    jobs = json.loads((cohort / "job-status.json").read_text())["jobs"]
    (cohort / "job-status.json").write_text(json.dumps(jobs + ["bad", None, 7, {"id": ""}, {"id": "w"}]))
    assert {r["job"]["id"] for r in plan(registry, keep_recent_runs=0)} == {
        f"r{i}" for i in range(4)} - set(exemplars(jobs).values())
    (cohort / "job-status.json").write_text(json.dumps({"status": "legacy"}))
    assert plan(registry, keep_recent_runs=0) == []


def test_only_unknown_null_accepted_final_falls_back_to_accepted():
    job = _job("x", Path("/tmp/c"), finished=1, final=["C0", "C2"], phases=["C2"])
    job["accepted_final"], job["accepted"] = None, ["C0", "C2"]
    assert score(job)[:2] == (2, 2)

    regressed = _job("y", Path("/tmp/c"), finished=2, final=[], phases=["C2"])
    known = _job("z", Path("/tmp/c"), finished=3, final=["C0"], phases=["C2"])
    regressed["accepted"] = ["C6"]
    assert score(regressed)[:2] == (-1, 0)
    assert score(regressed) < score(known)
    assert exemplars([regressed, known])["best"] == "z"


def test_plan_and_apply_refuse_cohort_arm_and_symlinked_worlds(tmp_path):
    registry = tmp_path / "experiments"
    cohort = registry / "c"
    code = cohort / "arms/a/code"
    outside = tmp_path / "outside"
    (outside / "evidence").mkdir(parents=True)
    (outside / "evidence/keep.jsonl").write_text("{}\n")
    (code / "data/laya-saved-world/c").mkdir(parents=True)
    link = code / "data/laya-saved-world/c/link"
    link.symlink_to(outside)
    jobs = [{"id": name, "status": "completed", "started": 1, "runner_exited": 2, "world": str(world)}
            for name, world in {"root": cohort, "arm": code, "escape": outside, "link": link}.items()]
    (cohort / "job-status.json").write_text(json.dumps({"jobs": jobs}))

    assert plan(registry, keep_recent_runs=0) == []
    result = apply(registry, keep_recent_runs=0, corpus=tmp_path / "corpus")

    assert result["condensed"] == 0
    assert (cohort / "job-status.json").exists() and (code / "data").is_dir()
    assert (outside / "evidence/keep.jsonl").exists() and link.is_symlink()


def test_resume_rerun_world_suffix_is_accepted_but_other_names_are_not(tmp_path):
    cohort = tmp_path / "experiments/c"
    world = cohort / "arms/a/code/data/laya-saved-world/c/r0-r2"
    world.mkdir(parents=True)
    job = {"id": "r0", "attempt": 2, "world": str(world)}

    assert run_world_ok(cohort, job)
    job["attempt"] = 1
    assert not run_world_ok(cohort, job)


def test_partial_deletion_never_shrinks_a_complete_index(tmp_path):
    data = tmp_path / "data"
    world = data / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/r0"
    (world / "evidence").mkdir(parents=True)
    (world / "evidence/a.jsonl").write_text("a\n")
    (world / "evidence/decision-states.jsonl").write_text("b\n")
    corpus = tmp_path / "corpus"

    first = condense(world, data, corpus)
    index = Path(first["index"])
    assert len(index.read_text().splitlines()) == 2
    (world / MARKER).unlink()

    retry = condense(world, data, corpus)

    assert retry["files"] == 2
    assert len(index.read_text().splitlines()) == 2
    assert len((corpus / "sources.jsonl").read_text().splitlines()) == 1


def test_crash_before_pointer_then_retry_is_idempotent(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = data / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/r0"
    (world / "evidence").mkdir(parents=True)
    (world / "evidence/a.jsonl").write_text("a\n")
    (world / "evidence/decision-states.jsonl").write_text("b\n")
    corpus = tmp_path / "corpus"
    real, failed = retention._publish, []

    def crash(path, payload):
        if path.name == MARKER and not failed:
            failed.append(path)
            raise OSError("power loss")
        return real(path, payload)

    monkeypatch.setattr(retention, "_publish", crash)
    with pytest.raises(OSError):
        retention.condense(world, data, corpus)
    assert not (world / MARKER).exists()
    assert (world / "evidence/decision-states.jsonl").exists()

    monkeypatch.setattr(retention, "_publish", real)
    pointer = retention.condense(world, data, corpus)

    assert pointer["files"] == 2
    assert len(Path(pointer["index"]).read_text().splitlines()) == 2
    assert len((corpus / "sources.jsonl").read_text().splitlines()) == 1


def test_negative_keep_recent_runs_and_missing_zstd_fail_before_deletion(tmp_path, monkeypatch):
    registry = tmp_path / "experiments"
    _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"]}) for i in range(3)])
    with pytest.raises(ValueError, match=">= 0"):
        apply(registry, keep_recent_runs=-1)

    monkeypatch.setattr("tools.laya_run_retention.shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="zstd"):
        apply(registry, keep_recent_runs=0, corpus=tmp_path / "corpus")
    worlds = list((registry / "old/arms/a/code/data/laya-saved-world/old").iterdir())
    assert worlds and all((world / "evidence/decision-states.jsonl").exists() for world in worlds)


def test_changed_status_between_plan_and_apply_prevents_deletion(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    registry = tmp_path / "experiments"
    _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"], "phases": ["C1"]}) for i in range(4)])
    selected = retention.plan(registry, keep_recent_runs=0)
    assert selected
    jobs = json.loads((registry / "old/job-status.json").read_text())["jobs"]
    jobs[0]["status"] = "running"
    (registry / "old/job-status.json").write_text(json.dumps({"jobs": jobs}))
    monkeypatch.setattr(retention, "plan", lambda *_args, **_kwargs: selected)

    result = retention.apply(registry, keep_recent_runs=0, corpus=tmp_path / "corpus")

    assert result["condensed"] == 0
    assert all((Path(run["job"]["world"]) / "evidence/decision-states.jsonl").exists() for run in selected)


def test_changed_exemplar_between_plan_and_apply_prevents_deletion(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    registry = tmp_path / "experiments"
    _cohort(registry, "old", [(f"r{i}", {"finished": i, "final": ["C0"], "phases": ["C1"]}) for i in range(4)])
    selected = retention.plan(registry, keep_recent_runs=0)
    target = selected[0]
    jobs = json.loads((registry / "old/job-status.json").read_text())["jobs"]
    next(job for job in jobs if job["id"] == target["job"]["id"]).update(accepted_final=["C0", "C6"])
    (registry / "old/job-status.json").write_text(json.dumps({"jobs": jobs}))
    monkeypatch.setattr(retention, "plan", lambda *_args, **_kwargs: [target])

    assert retention.apply(registry, keep_recent_runs=0, corpus=tmp_path / "corpus")["condensed"] == 0
    assert (Path(target["job"]["world"]) / "evidence/decision-states.jsonl").exists()


def test_directory_fsync_failure_never_deletes_the_source_world(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = _run_world(data)

    def fail(_path):
        raise OSError("directory fsync failed")

    monkeypatch.setattr(retention, "_fsync_dir", fail)
    with pytest.raises(OSError, match="fsync"):
        retention.condense(world, data, tmp_path / "corpus")

    assert (world / "evidence/decision-states.jsonl").read_text() == "b\n"
    assert not (world / MARKER).exists()


def test_missing_indexed_corpus_file_preserves_world_and_index(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = _run_world(data)
    corpus = tmp_path / "corpus"

    def crash(_path):
        raise OSError("power loss")

    monkeypatch.setattr(retention, "_rmtree", crash)
    with pytest.raises(OSError):
        retention.condense(world, data, corpus)
    (world / MARKER).unlink()
    index = corpus / "index" / "laya-experiments__c__arms__a__code__data__laya-saved-world__c__r0.jsonl"
    before = index.read_bytes()
    (corpus / "laya-experiments/c/arms/a/code/data/laya-saved-world/c/r0/evidence/a.jsonl").unlink()

    with pytest.raises(RuntimeError, match="missing"):
        retention.condense(world, data, corpus)

    assert (world / "evidence/decision-states.jsonl").exists()
    assert index.read_bytes() == before


def test_changed_surviving_source_preserves_world_and_index(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = _run_world(data)
    corpus = tmp_path / "corpus"

    def crash(_path):
        raise OSError("power loss")

    monkeypatch.setattr(retention, "_rmtree", crash)
    with pytest.raises(OSError):
        retention.condense(world, data, corpus)
    (world / MARKER).unlink()
    index = corpus / "index" / "laya-experiments__c__arms__a__code__data__laya-saved-world__c__r0.jsonl"
    before = index.read_bytes()
    (world / "evidence/a.jsonl").write_text("changed\n")

    with pytest.raises(RuntimeError, match="changed"):
        retention.condense(world, data, corpus)

    assert (world / "evidence/a.jsonl").read_text() == "changed\n"
    assert index.read_bytes() == before


def test_corrupt_index_is_not_replaced_and_preserves_the_world(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = _run_world(data)
    corpus = tmp_path / "corpus"

    def crash(_path):
        raise OSError("power loss")

    monkeypatch.setattr(retention, "_rmtree", crash)
    with pytest.raises(OSError):
        retention.condense(world, data, corpus)
    (world / MARKER).unlink()
    index = corpus / "index" / "laya-experiments__c__arms__a__code__data__laya-saved-world__c__r0.jsonl"
    index.write_text(index.read_text() + "not json\n")
    before = index.read_bytes()

    with pytest.raises(ValueError, match="corrupt retention index"):
        retention.condense(world, data, corpus)

    assert (world / "evidence/decision-states.jsonl").exists()
    assert index.read_bytes() == before


def test_complete_index_with_survivors_is_reused_without_recopying(tmp_path, monkeypatch):
    import tools.laya_run_retention as retention

    data = tmp_path / "data"
    world = _run_world(data)
    corpus = tmp_path / "corpus"
    writes = []
    real_publish, real_rmtree = retention._publish, retention._rmtree

    def crash(_path):
        raise OSError("power loss")

    monkeypatch.setattr(retention, "_rmtree", crash)
    with pytest.raises(OSError):
        retention.condense(world, data, corpus)
    (world / MARKER).unlink()
    index = corpus / "index" / "laya-experiments__c__arms__a__code__data__laya-saved-world__c__r0.jsonl"
    before = index.read_bytes()

    monkeypatch.setattr(retention, "_rmtree", real_rmtree)
    monkeypatch.setattr(retention, "_publish", lambda path, payload: writes.append(path) or real_publish(path, payload))
    pointer = retention.condense(world, data, corpus)

    assert [path for path in writes if path != world / MARKER] == []
    assert pointer["files"] == 2 and index.read_bytes() == before
    assert [path.name for path in world.iterdir()] == [MARKER]


def test_nested_cohort_state_blocks_selection_and_symlinked_descendants_are_not_followed(tmp_path):
    registry = tmp_path / "experiments"
    cohort = registry / "c"
    world = cohort / "arms/a/code/data/laya-saved-world/c/r0"
    (world / "checkpoints/gen").mkdir(parents=True)
    (world / "checkpoints/gen/job-status.json").write_text("{}")
    job = {"id": "r0", "status": "completed", "started": 1, "runner_exited": 2, "world": str(world)}
    (cohort / "job-status.json").write_text(json.dumps({"jobs": [job]}))

    assert not run_world_ok(cohort, job)
    (world / "checkpoints/gen/job-status.json").unlink()
    (world / "checkpoints/gen").rmdir()
    assert run_world_ok(cohort, job)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job-status.json").write_text("{}")
    (world / "link").symlink_to(outside, target_is_directory=True)

    assert run_world_ok(cohort, job)
    assert (outside / "job-status.json").exists()
