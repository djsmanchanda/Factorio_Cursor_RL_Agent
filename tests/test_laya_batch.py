# Path: tests/test_laya_batch.py | Purpose: Protect the experiment batch: matched seed-major job order, runner-log outcome parsing, per-arm report.

import json

import pytest
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tools.run_laya_batch import Batch, summarize

LOG = """RUN START: ts=x
+1s DECISION 0 phase=C0 tick=240000 need=x p=1: starter iron-plate
+83s PHASE REACHED: C0 iron_copper_starter at tick 249000 (rate proxy)
+158s PHASE REACHED: C1 starter_mall at tick 258000 (rate proxy)
+900s PHASE REACHED: C2 iron_copper_rollout at tick 348000 (rate proxy)
+901s   OUTCOME: untyped_stuck -- power gap cannot be routed safely
+1041s STALLED: 18000 ticks without a built operation
+1042s SUMMARY: stop=stalled decisions=224 failures=0 phases=C0,C1,C2 accepted=C0
"""


def _batch(tmp_path, seeds=(1, 2)):
    cfg = {"name": "t", "until": "2099-01-01T00:00:00+05:30", "concurrency": 2, "game_port_base": 1,
           "rcon_port_base": 2, "trial_wall_seconds": 60, "max_game_ticks": 600, "source_save": "s", "python": "p",
           "seeds": list(seeds), "arms": [{"id": "control", "code": "c"}, {"id": "fix", "code": "f"}]}
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(cfg))
    return Batch(path)


def test_jobs_are_seed_major_so_a_deadline_cut_keeps_arms_matched(tmp_path):
    assert [j["id"] for j in _batch(tmp_path).jobs] == ["control-s1", "fix-s1", "fix-s2", "control-s2"]


def test_outcome_and_phase_times_come_from_the_runner_log(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(LOG)
    out = summarize(tmp_path)
    assert out["runner_stop"] == "stalled" and out["phases"] == ["C0", "C1", "C2"] and out["accepted"] == ["C0"]
    assert out["phase_times"]["C2"]["game_seconds"] == (348000 - 240000) / 60
    assert out["untyped_stuck"] == 1
    (tmp_path / "logs/laya-runner.log").write_text("+9s SUMMARY: stop=window_timeout decisions=7 failures=0 phases=none accepted=C0\n")
    assert summarize(tmp_path)["phases"] == []


def test_batch_can_bound_each_worlds_update_threads_affinity_and_python_priority(tmp_path):
    batch = _batch(tmp_path)
    batch.cfg["runtime"] = {"update_threads": 1, "cpu_affinity": "slot", "python_nice": 5}

    args = batch.runtime_args(3)

    assert args[:2] == ["--update-threads", "1"]
    assert args[2] == "--cpu-affinity" and args[3].isdigit()
    assert "--nice" not in args
    assert batch._cmd(batch.arms["control"], "run")[:3] == ["nice", "-n", "5"]
    assert batch._cmd(batch.arms["control"], "prepare")[0] == "p"

    override = {"id": "override", "code": "o", "runtime": {"update_threads": 4}}
    assert batch.runtime_args(3, override) == [
        "--update-threads", "4", "--cpu-affinity", args[3],
    ]


def test_report_counts_phases_per_arm_and_lists_unstarted_jobs(tmp_path):
    batch = _batch(tmp_path, seeds=(1,))
    batch.jobs[0].update(status="completed", runner_stop="stalled", phases=["C0", "C1", "C2"], accepted=["C0"],
                         phase_times={"C2": {"game_seconds": 1800}}, untyped_stuck=2)
    batch.jobs[1].update(status="not_started_deadline")
    batch.report()
    report = (tmp_path / "report.md").read_text()
    assert "| control | 1/1 | {'stalled': 1} | 1/1/0/0 | 30.0 (n=1) |" in report
    assert "| fix | 0/1 |" in report and "not_started_deadline" in report


def _host_batch(registry, name):
    root = registry / name
    root.mkdir(parents=True)
    batch = _batch(root, seeds=(1,))
    batch.cfg.update(adaptive=False, host_budget={"registry": str(registry),
                      "admit_interval_seconds": 0, "admit_min_available_gb": 0})
    batch.path.write_text(json.dumps(batch.cfg))
    return Batch(batch.path)


class _PendingPool:
    def submit(self, *args):
        return object()


def test_host_gate_pauses_and_resumes_real_batch_admission(tmp_path, monkeypatch):
    from tools.laya_host_scheduler import DEFAULTS, cycle

    registry = tmp_path / "experiments"
    batch = _host_batch(registry, "cohort")
    host = tmp_path / "host"
    config = dict(DEFAULTS, manual_target=0, manual_target_until=1001,
                  manual_previous_target=24, mode="performance")
    metrics = {"active_slots": 0, "queued": 2, "ups_samples": 0, "ups_p95": None,
               "ups_p98": None, "total_ups": None, "unstable": [],
               "temp_c": 70, "available_gb": 20, "power_profile": "performance"}
    monkeypatch.setattr("tools.laya_host_scheduler.measure", lambda *a, **kw: metrics)
    monkeypatch.setattr("tools.run_laya_batch.time.time", lambda: 1000)
    cycle(config, {"target": 24}, registry=registry, root=host, now=1000)
    job = batch.jobs[0]
    assert batch.submit(_PendingPool(), job, 0) is None
    assert job["status"] == "queued"

    monkeypatch.setattr("tools.run_laya_batch.time.time", lambda: 1002)
    report = cycle(config, {"target": 0}, registry=registry, root=host, now=1002)
    assert report["target_slots"] == 24
    assert batch.submit(_PendingPool(), job, 0) is not None
    saved = json.loads((batch.root / "job-status.json").read_text())
    assert saved["jobs"][0]["admission_reserved"] is True


def test_missing_stale_and_malformed_gates_never_admit(tmp_path):
    from tools.laya_host_scheduler import write_gate

    batch = _host_batch(tmp_path, "cohort")
    for gate in (None, "stale", "corrupt"):
        if gate == "stale":
            write_gate(tmp_path, 30, 1)
        elif gate == "corrupt":
            (tmp_path / "_host-scheduler-gate/job-status.json").write_text("{")
        assert batch.submit(_PendingPool(), batch.jobs[0], 0) is None
        assert batch.jobs[0]["status"] == "queued"


def test_cohorts_reserve_the_last_slot_atomically(tmp_path):
    from tools.laya_host_scheduler import write_gate

    first, second = (_host_batch(tmp_path, name) for name in ("first", "second"))
    write_gate(tmp_path, 1, time.time())
    barrier = Barrier(2)

    def admit(batch):
        barrier.wait(timeout=5)
        return batch.submit(_PendingPool(), batch.jobs[0], 0) is not None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(admit, (first, second)))
    assert sorted(results) == [False, True]


def test_legacy_occupancy_counts_even_when_status_is_stale(tmp_path):
    from tools.laya_host_scheduler import write_gate

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "job-status.json").write_text(json.dumps({
        "updated": 0, "controller": {"slots": 14},
        "jobs": [{"status": "running", "host_occupied_at_start": 0}],
    }))
    batch = _host_batch(tmp_path, "new")
    write_gate(tmp_path, 1, time.time())
    assert batch.submit(_PendingPool(), batch.jobs[0], 0) is None
    write_gate(tmp_path, 2, time.time())
    assert batch.submit(_PendingPool(), batch.jobs[0], 0) is not None


def test_failed_submission_releases_its_reservation(tmp_path):
    import pytest
    from tools.laya_host_scheduler import write_gate

    class BrokenPool:
        def submit(self, *args):
            raise RuntimeError("pool stopped")

    batch = _host_batch(tmp_path, "cohort")
    write_gate(tmp_path, 1, time.time())
    with pytest.raises(RuntimeError, match="pool stopped"):
        batch.submit(BrokenPool(), batch.jobs[0], 0)
    saved = json.loads((batch.root / "job-status.json").read_text())
    assert saved["jobs"][0]["status"] == "queued"
    assert saved["jobs"][0]["admission_reserved"] is False


def test_batch_loop_waits_for_the_gate_before_starting_jobs(tmp_path, monkeypatch):
    from tools.laya_host_scheduler import write_gate

    batch = _host_batch(tmp_path, "cohort")
    write_gate(tmp_path, 0, time.time())
    started = []
    real_sleep = time.sleep

    def fake_job(job, slot):
        saved = json.loads((batch.root / "job-status.json").read_text())
        assert next(j for j in saved["jobs"] if j["id"] == job["id"])["admission_reserved"]
        started.append(job["id"])
        job.update(status="completed", admission_reserved=False)

    polls = 0

    def poll(seconds):
        nonlocal polls
        polls += 1
        if polls == 1:
            assert started == []
            write_gate(tmp_path, 2, time.time())
        assert polls < 100
        real_sleep(0.005)

    monkeypatch.setattr(batch, "run_job", fake_job)
    monkeypatch.setattr("tools.run_laya_batch.time.sleep", poll)
    monkeypatch.setattr("tools.run_laya_batch.signal.signal", lambda *a: None)
    batch.run()
    assert sorted(started) == ["control-s1", "fix-s1"]
    assert all(job["status"] == "completed" for job in batch.jobs)


def test_batch_refuses_to_overwrite_existing_cohort_accounting(tmp_path, monkeypatch):
    import pytest

    batch = _host_batch(tmp_path, "cohort")
    status = batch.root / "job-status.json"
    status.write_text('{"jobs": [{"id": "earned", "status": "completed"}]}')
    before = status.read_bytes()
    monkeypatch.setattr("tools.run_laya_batch.signal.signal", lambda *a: None)
    with pytest.raises(ValueError, match="existing cohort status"):
        batch.run()
    assert status.read_bytes() == before


def test_pre_registry_status_shapes_do_not_block_admission(tmp_path):
    # Live Miniship 2026-10-21: data/laya-experiments/ten-hour-20261015/job-status.json is a bare list; reading it
    # as {"jobs": ...} made every host-budget admission "unavailable_host_budget".
    from tools.laya_host_scheduler import write_gate

    for name, content in (("old-list", "[]"), ("no-jobs", "{}")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "job-status.json").write_text(content)
    batch = _host_batch(tmp_path, "new")
    write_gate(tmp_path, 1, time.time())
    assert batch.submit(_PendingPool(), batch.jobs[0], 0) is not None


def test_summary_keeps_final_acceptance_separate_from_the_latch(tmp_path):
    # Live C14c/C15d: this harness recorded only the latched list, so every final count read as zero.
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(
        "+9s SUMMARY: stop=stalled decisions=3 failures=0 phases=C0 accepted=C0,C2,C5 accepted_final=C0,C2\n")
    parsed = summarize(tmp_path)
    assert parsed["accepted_ever"] == ["C0", "C2", "C5"] and parsed["accepted_final"] == ["C0", "C2"]
    (tmp_path / "logs/laya-runner.log").write_text("+9s SUMMARY: stop=stalled decisions=3 failures=0 phases=C0 accepted=C0 accepted_final=unknown\n")
    assert summarize(tmp_path)["accepted_final"] is None


def test_resume_after_a_host_crash_keeps_finished_jobs_and_reruns_the_dead_ones(tmp_path, monkeypatch):
    # Live Miniship 2026-10-22 02:39: a hard stop left C14c/C15d jobs "running" with dead worlds and stale
    # reservations that the host budget kept counting.
    batch = _batch(tmp_path, seeds=(1, 2))
    status = {"control-s1": {"status": "completed", "started": 5.0},
              "fix-s1": {"status": "running", "started": 6.0, "world": "w/fix-s1", "admission_reserved": True},
              "fix-s2": {"status": "runner_error", "started": 7.0}, "control-s2": {"status": "queued"}}
    (tmp_path / "job-status.json").write_text(json.dumps({"jobs": [dict(j, **status[j["id"]]) for j in batch.jobs]}))
    batch.resume()
    jobs = {j["id"]: j for j in batch.jobs}
    assert jobs["control-s1"]["status"] == "completed" and jobs["fix-s2"]["status"] == "runner_error"
    rerun = jobs["fix-s1"]
    assert rerun["status"] == "queued" and rerun["attempt"] == 1 and "admission_reserved" not in rerun
    assert rerun["previous_attempts"] == [{"status": "running", "world": "w/fix-s1", "started": 6.0}]
    assert jobs["control-s2"] == {"id": "control-s2", "arm": "control", "seed": 2, "status": "queued"}
    ran = []
    monkeypatch.setattr(batch, "run_job", lambda job, slot: (ran.append(job["id"]), job.update(status="completed")))
    monkeypatch.setattr(batch, "report", lambda: None)
    batch.run(resume=True)
    assert sorted(ran) == ["control-s2", "fix-s1"]
    with pytest.raises(ValueError):
        _batch(tmp_path, seeds=(1, 2)).run()  # a fresh launch still refuses to overwrite a cohort's status
