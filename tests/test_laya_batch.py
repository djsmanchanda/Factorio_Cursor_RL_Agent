# Path: tests/test_laya_batch.py | Purpose: Protect the experiment batch: matched seed-major job order, runner-log outcome parsing, per-arm report.

import json

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


def test_jobs_are_seed_major_with_the_arm_order_rotated_per_seed(tmp_path):
    assert [j["id"] for j in _batch(tmp_path).jobs] == ["control-s1", "fix-s1", "fix-s2", "control-s2"]


def test_the_deadline_admits_whole_seed_blocks(tmp_path, monkeypatch):
    # Review 2026-10-20: a per-job deadline check could start a seed's first arm and censor the rest.
    import tools.run_laya_batch as harness
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    batch = _batch(tmp_path, seeds=(1, 2, 3))
    clock = [batch.until - 100]
    monkeypatch.setattr(harness.time, "time", lambda: clock[0])

    class Controller:
        maximum, slots, last = 4, 1, {}

        def update(self, running, queued, now=None):
            return None

    def job(item, slot):
        clock[0] += 50  # past the last start time while seed 1's first arm runs (a per-job check would censor its second)
        item["status"] = "completed"

    batch.controller = Controller()
    monkeypatch.setattr(batch, "run_job", job)
    monkeypatch.setattr(batch, "report", lambda: None)
    batch.run()
    status = {j["id"]: j["status"] for j in batch.jobs}
    assert status["control-s1"] == status["fix-s1"] == "completed"  # seed 1 was admitted: its block completes
    assert {status[f"{a}-s{s}"] for a in ("control", "fix") for s in (2, 3)} == {"not_started_deadline"}


def test_outcome_and_phase_times_come_from_the_runner_log(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(LOG)
    out = summarize(tmp_path)
    assert out["runner_stop"] == "stalled" and out["phases"] == ["C0", "C1", "C2"] and out["accepted"] == ["C0"]
    assert out["phase_times"]["C2"]["game_seconds"] == (348000 - 240000) / 60
    assert out["untyped_stuck"] == 1
    (tmp_path / "logs/laya-runner.log").write_text("+9s SUMMARY: stop=window_timeout decisions=7 failures=0 phases=none accepted=C0\n")
    assert summarize(tmp_path)["phases"] == []


def test_report_counts_phases_per_arm_and_lists_unstarted_jobs(tmp_path):
    batch = _batch(tmp_path, seeds=(1,))
    batch.jobs[0].update(status="completed", runner_stop="stalled", phases=["C0", "C1", "C2"], accepted=["C0"],
                         phase_times={"C2": {"game_seconds": 1800}}, untyped_stuck=2)
    batch.jobs[1].update(status="not_started_deadline")
    batch.report()
    report = (tmp_path / "report.md").read_text()
    assert "| control | 1/1 | {'stalled': 1} | 1/1/0/0 | 30.0 (n=1) |" in report
    assert "| fix | 0/1 |" in report and "not_started_deadline" in report


def _world(root, name, ups=120.0, extra="", gap=5):
    world = root / name
    (world / "logs").mkdir(parents=True)
    lines = [f"+{e}s DECISION {i} phase=C1 tick={240000 + int(e * ups)} need=x" for i, e in enumerate(range(0, 301, 30))]
    (world / "logs/laya-runner.log").write_text("\n".join(lines) + "\n" + extra)
    (world / "laya-runner.heartbeat.json").write_text(json.dumps({"elapsed_s": 300 + gap}))
    return {"id": name, "status": "running", "started": 0.0, "world": str(world)}


def _controller(tmp_path, monkeypatch, gb=20.0, **adaptive):
    import tools.run_laya_batch as harness
    monkeypatch.setattr(harness, "_available_gb", lambda: gb)
    cfg = {"concurrency": 4, "max_concurrency": 6, "adaptive": {"window_seconds": 300, **adaptive}}
    return harness.SlotController(cfg, tmp_path / "controller.jsonl")


def test_slots_grow_after_steady_ups_while_work_waits(tmp_path, monkeypatch):
    control = _controller(tmp_path, monkeypatch)
    running = [_world(tmp_path, f"w{i}") for i in range(4)]
    assert control.update(running, queued=10, now=301) == "hold_safe_ups"
    assert control.update(running, queued=10, now=602) == "increase_safe_ups" and control.slots == 5
    assert json.loads((tmp_path / "controller.jsonl").read_text().splitlines()[-1])["ups_min"] == 120.0


def test_slots_shrink_on_low_ups_but_never_below_the_floor(tmp_path, monkeypatch):
    control = _controller(tmp_path, monkeypatch, min=3)
    running = [_world(tmp_path, "fast"), _world(tmp_path, "slow", ups=70.0)]
    assert control.update(running, queued=10, now=301) == "decrease_safe_ups" and control.slots == 3
    control.update(running, queued=10, now=602)
    assert control.slots == 3


def test_laya_loop_trouble_and_low_memory_shrink_without_waiting_for_ups(tmp_path, monkeypatch):
    control = _controller(tmp_path, monkeypatch)
    timed_out = _world(tmp_path, "rcon", extra="+301s   OUTCOME: tool_error -- TimeoutError: rcon timed out\n")
    assert control.update([timed_out], queued=5, now=301) == "decrease_laya_unstable" and control.slots == 3
    stuck = _world(tmp_path, "stuck", gap=900)
    assert control.update([stuck], queued=5, now=602) == "decrease_laya_unstable"
    assert _controller(tmp_path, monkeypatch, gb=1.0).update([_world(tmp_path, "m")], 5, now=301) == "decrease_memory"


def test_an_up_down_up_flip_flop_holds_slots_steady_before_growing_again(tmp_path, monkeypatch):
    import tools.run_laya_batch as harness
    control = _controller(tmp_path, monkeypatch, grow_after=1, settle_windows=3)
    control.maximum = 10
    running = [_world(tmp_path, f"w{i}") for i in range(10)]
    memory = iter([20.0, 1.0, 20.0, 20.0, 20.0, 20.0, 20.0])
    monkeypatch.setattr(harness, "_available_gb", lambda: next(memory))
    decisions = [control.update(running, queued=10, now=301 * (i + 1)) for i in range(5)]
    assert decisions == ["increase_safe_ups", "decrease_memory", "increase_safe_ups+settle",
                         "hold_settling", "hold_settling"] and control.slots == 5
    assert control.update(running, queued=10, now=301 * 6) == "increase_safe_ups" and control.slots == 6


def test_a_plain_tool_error_or_no_waiting_work_does_not_move_slots(tmp_path, monkeypatch):
    control = _controller(tmp_path, monkeypatch, grow_after=1)
    bug = _world(tmp_path, "bug", extra="+301s   OUTCOME: tool_error -- KeyError: 'splitter'\n")
    assert control.update([bug] * 4, queued=0, now=301) == "hold_no_demand" and control.slots == 4
    assert control.update([bug] * 4, queued=3, now=302) is None  # one decision per window


def test_adaptive_false_keeps_the_configured_slots(tmp_path, monkeypatch):
    import tools.run_laya_batch as harness
    control = harness.SlotController({"concurrency": 4, "adaptive": False}, tmp_path / "c.jsonl")
    assert control.update([_world(tmp_path, "w", ups=10.0)], queued=9, now=10**9) is None and control.slots == 4


def test_the_batch_starts_jobs_only_up_to_the_controllers_current_slots(tmp_path, monkeypatch):
    import threading
    import tools.run_laya_batch as harness
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    batch = _batch(tmp_path, seeds=(1, 2, 3, 4))
    live, peak, lock = [0], [0], threading.Lock()

    class Controller:
        maximum, slots, last = 4, 1, {}

        def update(self, running, queued, now=None):
            self.slots = 1 if queued > 4 else 3  # grows mid-run once two jobs have finished
            return None

    def job(item, slot):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            assert live[0] <= batch.controller.slots
        with lock:
            live[0] -= 1
        item["status"] = "completed"

    batch.controller = Controller()
    monkeypatch.setattr(batch, "run_job", job)
    monkeypatch.setattr(batch, "report", lambda: None)
    batch.run()
    assert all(j["status"] == "completed" for j in batch.jobs)
    assert [j["slots_at_start"] for j in batch.jobs][:2] == [1, 1] and batch.jobs[-1]["slots_at_start"] == 3


def _cohort(registry, name, jobs, slots, updated):
    root = registry / name
    root.mkdir(parents=True)
    (root / "job-status.json").write_text(json.dumps({"name": name, "updated": updated, "jobs": jobs,
                                                      "controller": {"slots": slots}}))


def test_a_host_budget_counts_other_cohorts_and_reserves_older_ones_free_room(tmp_path):
    from tools.run_laya_batch import HostBudget
    registry, now = tmp_path / "cohorts", 10_000.0
    running = {"status": "running", "started": 100.0, "world": "w"}
    # Older and waiting: 3 running of its 5 slots reserves 2. Newer: its running jobs count, its room does not.
    _cohort(registry, "older", [running] * 3 + [{"status": "queued"}], 5, now - 10)
    _cohort(registry, "newer", [dict(running, started=now + 1)] * 2 + [{"status": "queued"}], 9, now - 10)
    _cohort(registry, "dead", [running] * 7, 7, now - 5000)  # a stale supervisor no longer occupies the host
    _cohort(registry, "done", [{"status": "completed", "started": 1.0}], 4, now - 10)
    (registry / "legacy").mkdir()
    (registry / "legacy/job-status.json").write_text("[]")  # older harnesses wrote a bare list
    own = registry / "own"
    own.mkdir()
    host = HostBudget(own, {"registry": str(registry)}).snapshot([{"status": "queued"}], now)
    assert len(host["running"]) == 5 and host["reserved"] == 2 and host["cap"] is None  # no shared older cohort
    # Live C15 attempt 1: an older shared cohort at a host total of 10 must bound a newer one starting at 20.
    _cohort(registry, "shared", [dict(running, host_occupied_at_start=8)] + [{"status": "queued"}], 10, now - 10)
    assert HostBudget(own, {"registry": str(registry)}).snapshot([{"status": "queued"}], now)["cap"] == 10


def test_a_shared_batch_admits_only_into_the_host_total(tmp_path, monkeypatch):
    import tools.run_laya_batch as harness
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    batch = _batch(tmp_path, seeds=(1, 2, 3))
    snapshots = iter([{"running": [{}] * 3, "reserved": 1, "cap": None}] * 2 + [{"running": [], "reserved": 0, "cap": 9}] * 100)
    batch.host = type("Host", (), {"snapshot": lambda self, jobs, now: next(snapshots), "next_admit": 0.0,
                                   "admit_interval_s": 0.0, "may_admit": lambda self, now, decision: True})()
    starts = []

    class Controller:
        maximum, slots, last = 6, 5, {}

        def update(self, running, queued, now=None):
            return None

    def job(item, slot):
        item["status"] = "completed"

    batch.controller = Controller()
    monkeypatch.setattr(batch, "run_job", lambda item, slot: (starts.append(item["host_occupied_at_start"]), job(item, slot)))
    monkeypatch.setattr(batch, "report", lambda: None)
    batch.run()
    assert starts[0] == 4 and {j["status"] for j in batch.jobs} == {"completed"}  # 4 occupied of 5: one starts first


def test_a_shared_batch_admits_one_trial_per_interval_and_only_with_memory_headroom(tmp_path, monkeypatch):
    import tools.run_laya_batch as harness
    from tools.run_laya_batch import HostBudget
    host = HostBudget(tmp_path, {"registry": str(tmp_path), "admit_interval_seconds": 60, "admit_min_available_gb": 4.5})
    monkeypatch.setattr(harness, "_available_gb", lambda: 6.0)
    assert host.may_admit(100.0, "hold_safe_ups") and host.may_admit(100.0, "no_ups_window")
    # Live C14 2026-10-21: it kept admitting while the host total shrank and older cohorts shed slots.
    assert not host.may_admit(100.0, "start") and not host.may_admit(100.0, "decrease_safe_ups")
    assert not host.may_admit(100.0, "decrease_safe_ups+settle")
    host.next_admit = 160.0
    assert not host.may_admit(120.0, "hold_safe_ups") and host.may_admit(160.0, "hold_safe_ups")
    monkeypatch.setattr(harness, "_available_gb", lambda: 4.0)  # below the older cohorts' floor plus one world
    assert not host.may_admit(200.0, "increase_safe_ups")


def test_resume_keeps_finished_jobs_and_reruns_interrupted_ones_in_fresh_worlds(tmp_path, monkeypatch):
    import tools.run_laya_batch as harness
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    batch = _batch(tmp_path, seeds=(1, 2, 3))
    status = {"control-s1": {"status": "completed", "started": 5.0, "world": "w/control-s1"},
              "fix-s1": {"status": "interrupted", "started": 6.0, "world": "w/fix-s1", "elapsed_s": 40},
              "fix-s2": {"status": "runner_error", "started": 7.0}, "control-s2": {"status": "not_started_interrupted"},
              "control-s3": {"status": "not_started_interrupted"}, "fix-s3": {"status": "not_started_interrupted"}}
    (tmp_path / "job-status.json").write_text(json.dumps({"jobs": [dict(j, **status[j["id"]]) for j in batch.jobs]}))
    batch.resume()
    jobs = {j["id"]: j for j in batch.jobs}
    assert jobs["control-s1"]["status"] == "completed" and jobs["fix-s2"]["status"] == "runner_error"  # evidence kept
    assert jobs["fix-s1"]["status"] == "queued" and jobs["fix-s1"]["attempt"] == 1
    assert jobs["fix-s1"]["previous_attempts"] == [{"status": "interrupted", "world": "w/fix-s1", "started": 6.0, "elapsed_s": 40}]
    assert jobs["control-s3"] == {"id": "control-s3", "arm": "control", "seed": 3, "status": "queued"}
    worlds, ran = {}, []

    class Controller:
        maximum, slots, last = 4, 4, {}

        def update(self, running, queued, now=None):
            return None

    def job(item, slot):
        ran.append(item["id"])
        item["status"] = "completed"
    batch.controller = Controller()
    monkeypatch.setattr(batch, "run_job", job)
    monkeypatch.setattr(batch, "report", lambda: None)
    batch.until = 0  # past the deadline: only blocks already started are admitted again
    batch.run()
    assert sorted(ran) == ["control-s2", "fix-s1"]  # seed 3 never started, so it stays censored
    assert {jobs[i]["status"] for i in ("control-s3", "fix-s3")} == {"not_started_deadline"}
    import tools.run_laya_batch as h
    code = tmp_path / "code"
    arm = {"id": "fix", "code": str(code), "mods": "m"}
    batch.arms["fix"] = arm
    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    rerun = dict(jobs["fix-s1"], status="queued")
    try:
        h.Batch.run_job(batch, rerun, 0)  # the real method; the instance's was stubbed above
    except RuntimeError:
        pass
    assert rerun["world"].endswith("/fix-s1-r1")


def test_growth_step_is_configurable(tmp_path, monkeypatch):
    control = _controller(tmp_path, monkeypatch, grow_after=1, grow_step=2)
    running = [_world(tmp_path, f"w{i}") for i in range(4)]
    assert control.update(running, queued=10, now=301) == "increase_safe_ups" and control.slots == 6
