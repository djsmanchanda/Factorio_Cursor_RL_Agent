# Path: tests/test_laya_host_scheduler.py | Purpose: Protect the host-wide slot budget: measured growth, sweet-spot backoff, cool/thermal caps, graceful drain and fair allocation.

from __future__ import annotations

import json
from pathlib import Path

from tools.laya_host_scheduler import (
    Cohort,
    DEFAULTS,
    allocate,
    choose,
    effective_max,
    main,
    world_ups,
)


def _config(**changes):
    config = dict(DEFAULTS)
    config.update(changes)
    return config


def _metrics(**changes):
    metrics = {
        "active_slots": 8,
        "queued": 10,
        "ups_samples": 8,
        "ups_p95": 115.0,
        "ups_p98": 110.0,
        "ups_mean": 116.0,
        "total_ups": 1000.0,
        "unstable": [],
        "temp_c": 70.0,
        "available_gb": 10.0,
        "power_profile": "performance",
    }
    metrics.update(changes)
    if "ups_samples" not in changes:
        metrics["ups_samples"] = metrics["active_slots"]
    return metrics


def test_target_starts_at_x_and_reverts_when_total_ups_plateaus():
    config = _config(start_slots=8, max_slots=16, step=2, settle_windows=2, window_seconds=60)
    state = {"target": 8, "mode": "performance"}

    target, reason, state = choose(config, state, _metrics(), now=100)
    assert (target, reason) == (10, "trial_increase")
    assert state["trial_base"] == 8 and state["trial_total_ups"] == 1000.0

    target, reason, state = choose(config, state, _metrics(active_slots=10, total_ups=1005.0), now=160)
    assert (target, reason) == (10, "trial_window")
    target, reason, state = choose(config, state, _metrics(active_slots=10, total_ups=1005.0), now=220)
    assert (target, reason) == (8, "sweet_spot")
    assert state["sweet_spot"] == 8 and state["settle_until"] == 220 + 2 * 60

    target, reason, _ = choose(config, state, _metrics(active_slots=8), now=260)
    assert (target, reason) == (8, "settling")


def test_target_keeps_climbing_while_total_ups_improves():
    config = _config(start_slots=8, max_slots=16, step=2)
    state = {"target": 8, "mode": "performance"}
    _, _, state = choose(config, state, _metrics(), now=100)

    target, reason, state = choose(config, state, _metrics(active_slots=10, total_ups=1300.0), now=160)
    assert (target, reason) == (10, "trial_window")
    target, reason, state = choose(config, state, _metrics(active_slots=10, total_ups=1300.0), now=340)
    assert (target, reason) == (12, "trial_increase")
    assert state["sweet_spot"] == 10


def test_balanced_mode_uses_its_configured_cap():
    config = _config(max_slots=30, balanced_max_slots=26, min_slots=4)
    cap, reason = effective_max(config, _metrics(active_slots=20, temp_c=70.0), "balanced")
    assert (cap, reason) == (26, "balanced")


def test_cool_mode_caps_then_recovers_to_performance():
    config = _config(start_slots=12, max_slots=20, cool_max_slots=8, mode="cool")
    state = {"target": 12, "mode": "cool"}

    target, reason, _ = choose(config, state, _metrics(active_slots=12, temp_c=75.0), now=100)
    assert (target, reason) == (8, "mode_cap")

    config = _config(start_slots=12, max_slots=20, mode="performance")
    state = {"target": 8, "mode": "cool"}
    target, reason, state = choose(config, state, _metrics(active_slots=8, temp_c=75.0), now=160)
    assert state["mode"] == "performance"
    assert (target, reason) == (10, "trial_increase")


def test_manual_target_drains_without_starting_more_worlds():
    config = _config(min_slots=4, manual_target=5)
    state = {"target": 16, "mode": "performance"}
    cohorts = [
        Cohort("old", Path("old"), 0, running=6, queued=10, oldest=1),
        Cohort("new", Path("new"), 0, running=4, queued=10, oldest=2),
    ]

    allotment = allocate(5, cohorts)
    assert allotment == {"old": 6, "new": 4}

    target, reason, _ = choose(config, state, _metrics(active_slots=10), now=100)
    assert (target, reason) == (5, "manual_target")

    config["manual_target"] = 0
    target, reason, _ = choose(config, state, _metrics(active_slots=10), now=160)
    assert (target, reason) == (0, "manual_target")


def test_allocate_gives_new_slots_round_robin_oldest_first():
    cohorts = [
        Cohort("old", Path("old"), 0, running=6, queued=10, oldest=1),
        Cohort("new", Path("new"), 0, running=4, queued=10, oldest=2),
    ]

    assert allocate(12, cohorts) == {"old": 6, "new": 6}
    assert allocate(16, cohorts) == {"old": 8, "new": 8}


def test_thermal_derating_lowers_the_effective_maximum():
    config = _config(min_slots=4, max_slots=20, temp_hard_c=90.0)
    cap, reason = effective_max(config, _metrics(active_slots=12, temp_c=91.0), "performance")
    assert (cap, reason) == (10, "thermal_hard")


def test_no_ups_measurement_holds_the_starting_target():
    config = _config(start_slots=12)
    state = {"target": 12, "mode": "performance"}

    target, reason, _ = choose(config, state, _metrics(ups_p95=None, ups_p98=None), now=100)
    assert (target, reason) == (12, "no_measurement")


def test_world_ups_reads_the_decision_log(tmp_path):
    world = tmp_path / "world"
    (world / "logs").mkdir(parents=True)
    (world / "logs/laya-runner.log").write_text(
        "+0s DECISION 0 phase=C0 tick=1000 need=x p=1: starter\n"
        "+60s DECISION 1 phase=C0 tick=8200 need=x p=1: starter\n"
    )
    (world / "laya-runner.heartbeat.json").write_text(json.dumps({"elapsed_s": 60}))

    assert world_ups(world, 120) == 120.0


def test_cli_configures_start_max_and_manual_target(tmp_path):
    root = tmp_path / "host"

    main(["--root", str(root), "configure", "--start", "10", "--max", "18", "--cool-max", "8"])
    main(["--root", str(root), "target", "12"])

    config = json.loads((root / "config.json").read_text())
    assert (config["start_slots"], config["max_slots"], config["cool_max_slots"]) == (10, 18, 8)
    assert config["manual_target"] == 12

    main(["--root", str(root), "clear-target"])
    assert json.loads((root / "config.json").read_text())["manual_target"] is None


def test_cycle_publishes_the_gate_that_frozen_cohort_budgets_already_read(tmp_path):
    import time
    from tools.laya_host_scheduler import cycle

    registry = tmp_path / "experiments"
    cohort = registry / "cohort"
    cohort.mkdir(parents=True)
    (cohort / "job-status.json").write_text(json.dumps({
        "name": "cohort",
        "updated": time.time(),
        "controller": {"slots": 20},
        "jobs": [{"id": "job", "status": "running", "started": time.time() - 100}],
    }))
    root = tmp_path / "host"
    config = _config(start_slots=12, max_slots=16, mode="performance")

    report = cycle(config, {"target": 12, "mode": "performance"}, registry=registry, root=root)

    gate = json.loads((registry / "_host-scheduler-gate" / "job-status.json").read_text())
    assert gate["controller"]["slots"] == report["target_slots"]
    assert gate["jobs"][0]["host_occupied_at_start"] is True
    assert all(item["name"] != "_host-scheduler-gate" for item in report["cohorts"])


def test_hot_windows_hold_the_cap_instead_of_collapsing_every_cycle():
    config = _config(start_slots=12, max_slots=16, min_slots=4, temp_limit_c=88.0, mode="performance")
    state = {"target": 12, "mode": "performance"}

    first, reason, state = choose(config, state, _metrics(active_slots=13, temp_c=89.9), now=100)
    second, reason2, _ = choose(config, state, _metrics(active_slots=13, temp_c=89.9), now=280)

    assert (first, reason) == (12, "thermal")
    assert (second, reason2) == (12, "thermal")


def test_an_incomplete_trial_waits_for_its_worlds_instead_of_false_reverting():
    config = _config(start_slots=8, max_slots=16, min_ups_p95=105, min_ups_p98=95, window_seconds=60)
    state = {"target": 10, "mode": "performance", "trial_base": 8, "trial_total_ups": 1000.0,
             "trial_started": 100.0}

    target, reason, state = choose(config, state, _metrics(active_slots=9, ups_samples=9, total_ups=1005.0), now=160)
    assert (target, reason) == (10, "trial_wait")

    target, reason, state = choose(config, state, _metrics(active_slots=9, ups_samples=9, total_ups=1005.0), now=400)
    assert (target, reason) == (8, "trial_incomplete")
    assert state.get("sweet_spot") is None


def test_trial_cannot_delay_thermal_memory_or_liveness_caps():
    config = _config(start_slots=24, max_slots=30, temp_limit_c=93, temp_hard_c=95)
    for changes, expected in [
        ({"temp_c": 96}, (22, "thermal_hard")),
        ({"available_gb": 1}, (23, "memory")),
        ({"unstable": ["world"]}, (23, "runner_unstable")),
    ]:
        state = {"target": 26, "mode": "performance", "trial_base": 24,
                 "trial_total_ups": 2800, "trial_started": 100}
        target, reason, state = choose(config, state, _metrics(active_slots=24, **changes), now=160)
        assert (target, reason) == expected
        assert state["trial_base"] is None


def test_trial_waits_a_full_window_after_all_worlds_are_measured():
    config = _config(start_slots=8, max_slots=16, window_seconds=180)
    state = {"target": 10, "mode": "performance", "trial_base": 8,
             "trial_total_ups": 1000, "trial_started": 100}
    metrics = _metrics(active_slots=10, total_ups=1300)
    assert choose(config, state, metrics, now=160)[:2] == (10, "trial_window")
    assert choose(config, state, metrics, now=280)[:2] == (10, "trial_window")
    assert choose(config, state, metrics, now=340)[:2] == (12, "trial_increase")


def test_a_timed_target_restores_the_previous_target_when_it_expires(tmp_path):
    import time
    from tools.laya_host_scheduler import cycle

    root = tmp_path / "host"
    registry = tmp_path / "experiments"
    registry.mkdir()
    config = _config(start_slots=12, manual_target=8, temp_limit_c=200.0, temp_hard_c=200.0,
                     manual_target_until=time.time() + 0.1, manual_previous_target=18)
    report = cycle(config, {"target": 8, "mode": "performance"}, registry=registry, root=root,
                   now=time.time() + 2)
    assert report["target_slots"] == 18
    saved = json.loads((root / "config.json").read_text())
    assert saved["manual_target"] is None and saved["manual_target_until"] is None


def test_pause_and_resume_cli_control_the_manual_target(tmp_path):
    root = tmp_path / "host"
    (root).mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({"target": 18, "mode": "performance"}))
    main(["--root", str(root), "pause", "--for", "2h"])
    config = json.loads((root / "config.json").read_text())
    assert config["manual_target"] == 0
    assert config["manual_target_until"] > 0

    main(["--root", str(root), "resume"])
    config = json.loads((root / "config.json").read_text())
    assert config["manual_target"] is None
    assert json.loads((root / "state.json").read_text())["target"] == 18


def test_bump_restarts_the_search_from_a_new_target_instead_of_pinning_it(tmp_path):
    root = tmp_path / "host"
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({"target": 18, "mode": "performance", "sweet_spot": 18}))
    main(["--root", str(root), "target", "20"])
    main(["--root", str(root), "bump", "+2"])
    config = json.loads((root / "config.json").read_text())
    state = json.loads((root / "state.json").read_text())
    assert config["manual_target"] is None and config["manual_previous_target"] is None
    assert state["target"] == 20 and state["sweet_spot"] is None

    # Automatic control resumes from the bumped target: an unhealthy window steps it down.
    target, reason, _ = choose(_config(step=1), state, _metrics(active_slots=20, ups_p95=90.0))
    assert (target, reason) == (19, "unhealthy")
