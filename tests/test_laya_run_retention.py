# Path: tests/test_laya_run_retention.py | Purpose: Protect run retention: recent, exemplar and active-cohort runs stay whole; condensed runs keep verified decision data.

import json
import subprocess
from pathlib import Path

import pytest

from tools.laya_run_retention import ACTIVE, MARKER, apply, condense, exemplars, plan


def _job(job_id, cohort, *, finished, final=(), phases=(), c_seconds=None, status="completed"):
    top = max(phases, key=lambda p: int(p[1:]), default=None)
    return {"id": job_id, "status": status, "started": 1000 + finished - 1, "runner_exited": 1000 + finished,
            "world": str(cohort / "arms/a/code/data/laya-saved-world" / cohort.name / job_id),
            "accepted_final": list(final), "phases": list(phases),
            "phase_times": {top: {"game_seconds": c_seconds}} if top else {}}


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
