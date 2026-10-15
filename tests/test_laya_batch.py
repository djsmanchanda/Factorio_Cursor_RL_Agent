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
