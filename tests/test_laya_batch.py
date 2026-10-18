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


def test_jobs_are_seed_major_so_a_deadline_cut_keeps_arms_matched(tmp_path):
    assert [j["id"] for j in _batch(tmp_path).jobs] == ["control-s1", "fix-s1", "control-s2", "fix-s2"]


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
