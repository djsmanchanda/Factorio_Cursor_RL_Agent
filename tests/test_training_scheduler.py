# Path: tests/test_training_scheduler.py
# Purpose: Verify isolated worker contracts, phase exclusion, and tuning.

from __future__ import annotations

import threading
from queue import Queue

import pytest

import tools.run_adaptive_training_batch as adaptive
from tools.run_adaptive_training_batch import _assign, _parse, _retry_jobs
from tools.run_training_batch import EpisodeQueue, _jobs

from training.features import MINING_DELIVERY_FEATURES_V2
from training.policies import DiagonalLinUCB, policy_snapshot
from training.scenarios.mining_delivery import generate_mining_delivery_scenario
from training.store import TrainingStore

from training.scheduler import (
    AdaptiveScaleState, BatchMeasurement, ResourcePhaseLock, UpsWindow, WorkerSpec,
    adjust_adaptive_slots, next_worker_count, percentile, validate_worker_specs,
)


def worker(tmp_path, worker_id: str, game_port: int, rcon_port: int) -> WorkerSpec:
    return WorkerSpec(
        worker_id, f"instance-{worker_id}", "127.0.0.1", game_port, rcon_port,
        tmp_path / worker_id, "training/", "training-",
    )


def test_workers_need_unique_explicit_training_boundaries(tmp_path) -> None:
    first = worker(tmp_path, "one", 35001, 28001)
    second = worker(tmp_path, "two", 35002, 28002)
    validate_worker_specs([first, second])
    with pytest.raises(ValueError, match="ports"):
        validate_worker_specs([first, worker(tmp_path, "three", 35003, 28001)])
    with pytest.raises(ValueError, match="loopback"):
        WorkerSpec("bad", "bad", "example.com", 1, 2, tmp_path / "bad", "training/", "training-")


def test_slots_can_share_one_explicit_factorio_runtime(tmp_path) -> None:
    first = worker(tmp_path, "slot-one", 35001, 28001)
    second = WorkerSpec(
        "slot-two", first.instance_id, first.host, first.game_port, first.rcon_port,
        first.script_output, first.surface_prefix, first.force_prefix,
    )
    validate_worker_specs([first, second])

    different_runtime = WorkerSpec(
        "slot-three", "instance-three", first.host, first.game_port, first.rcon_port,
        first.script_output, first.surface_prefix, first.force_prefix,
    )
    with pytest.raises(ValueError, match="shared instance_id"):
        validate_worker_specs([first, different_runtime])


def test_repeated_attempts_keep_one_scenario_on_one_shared_runtime_slot(tmp_path) -> None:
    workers = [worker(tmp_path, f"slot-{index}", 35001 + index, 28001 + index) for index in range(3)]
    scenarios = [{"scenario_id": "mining-delivery-0001"}, {"scenario_id": "mining-delivery-0002"}]
    jobs = [(f"episode-{index}", scenarios[index % 2], index) for index in range(6)]

    assigned = _assign(jobs, workers)
    owners = {job[1]["scenario_id"]: worker_id for worker_id, batch in assigned.items() for job in batch}
    assert len(owners) == 2
    for worker_id, batch in assigned.items():
        assert {job[1]["scenario_id"] for job in batch} <= {
            scenario_id for scenario_id, owner in owners.items() if owner == worker_id
        }

    regular = _jobs(scenarios, attempts=3, workers=workers)
    regular_owners = {job[1]["scenario_id"]: worker_id for worker_id, batch in regular.items() for job in batch}
    assert regular_owners == owners

def test_shared_episode_queue_reuses_idle_slots_without_overlapping_a_scenario() -> None:
    scenario_a, scenario_b = {"scenario_id": "a"}, {"scenario_id": "b"}
    queue = EpisodeQueue([
        ("a-1", scenario_a, 1), ("a-2", scenario_a, 2),
        ("b-1", scenario_b, 3), ("b-2", scenario_b, 4),
    ])

    first_a = queue.claim()
    first_b = queue.claim()
    assert (first_a[0], first_b[0]) == ("a-1", "b-1")

    queue.release("b")
    assert queue.claim()[0] == "b-2"
    queue.release("b")
    queue.release("a")
    assert queue.claim()[0] == "a-2"
    queue.release("a")
    assert queue.claim() is None


def test_resource_phase_lock_allows_same_phase_and_excludes_other_phase() -> None:
    lock = ResourcePhaseLock()
    entered: list[str] = []
    release = threading.Event()

    def research() -> None:
        with lock.acquire("llm_research"):
            entered.append("research")
            release.wait(1)

    with lock.acquire("collection"):
        thread = threading.Thread(target=research)
        thread.start()
        assert entered == []
    release.set()
    thread.join(1)
    assert entered == ["research"]


def test_concurrency_only_grows_after_safe_throughput_gain() -> None:
    first = BatchMeasurement(4, 100, 100, 60)
    faster = BatchMeasurement(6, 170, 100, 58)
    assert next_worker_count([first], minimum=4, maximum=20) == 6
    assert next_worker_count([first, faster], minimum=4, maximum=20) == 8
    unhealthy = BatchMeasurement(8, 200, 100, 40)
    assert next_worker_count([faster, unhealthy], minimum=4, maximum=20) == 6


def test_ups_window_uses_conservative_lower_tail_for_p98_safety() -> None:
    window = UpsWindow((60.0, 59.0, 58.0, 40.0, 60.0))
    assert percentile(window.samples, 0.5) == 59.0
    assert window.safe_ups_p98 < 55.0
    assert window.tick_time_p98_ms > 1000.0 / 55.0


def test_adaptive_slots_grow_in_four_slot_steps_after_two_healthy_windows() -> None:
    state = AdaptiveScaleState(4)
    healthy = UpsWindow((60.0, 59.0, 58.0))
    state, reason = adjust_adaptive_slots(state, healthy)
    assert (state.slots, reason) == (4, "hold_safe_ups")
    state, reason = adjust_adaptive_slots(state, healthy)
    assert (state.slots, reason) == (8, "increase_safe_ups")


def test_adaptive_slots_require_both_requested_p95_and_p98_thresholds() -> None:
    p95_failure = UpsWindow(tuple([56.0] * 6 + [60.0] * 94))
    assert p95_failure.safe_ups_p98 >= 55.0
    assert p95_failure.safe_ups_p95 < 57.0
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(20), p95_failure)
    assert (state.slots, reason) == (16, "decrease_safe_ups")

    passing = UpsWindow(tuple([55.0] * 2 + [57.0] * 3 + [60.0] * 95))
    assert passing.safe_ups_p98 >= 55.0
    assert passing.safe_ups_p95 >= 57.0
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(20), passing)
    assert (state.slots, reason) == (20, "hold_safe_ups")

def test_adaptive_slots_shrink_by_four_on_an_unhealthy_window() -> None:
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(12), UpsWindow((54.0, 53.0, 52.0)))
    assert (state.slots, reason) == (8, "decrease_safe_ups")


def test_adaptive_slots_never_breach_bounds_or_scale_without_measurement() -> None:
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(4), None, maximum=8)
    assert (state.slots, reason) == (4, "no_ups_window")
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(8), UpsWindow((60.0, 60.0, 60.0)), maximum=8)
    assert (state.slots, reason) == (8, "hold_safe_ups")


def test_capacity_requeue_keeps_scenario_seed_and_refreshes_episode_identity() -> None:
    scenario = {"scenario_id": "mining-delivery-0001"}
    jobs = [
        ("episode-old-a", scenario, 3),
        ("episode-finished", scenario, 4),
    ]
    retried = _retry_jobs(jobs, {"episode-old-a"})
    assert len(retried) == 1
    episode_id, returned_scenario, seed = retried[0]
    assert episode_id.startswith("episode-mining-delivery-0001-retry-")
    assert episode_id != "episode-old-a"
    assert returned_scenario is scenario
    assert seed == 3

def test_adaptive_stage_aborts_and_requeues_when_live_ups_is_unsafe(monkeypatch, tmp_path) -> None:
    scenario = generate_mining_delivery_scenario(7)
    worker_spec = worker(tmp_path, "slot-one", 35001, 28001)

    class UnsafeSampler:
        def __init__(self, *_args, **_kwargs):
            self.error = None

        @property
        def sample_count(self):
            return 3

        def start(self):
            return None

        def stop(self):
            return None

        def window(self):
            return UpsWindow((10.0, 10.0, 10.0))

    def stopped_worker(worker_spec, _jobs, _password, _policy, _directory, _events, stop_event):
        assert stop_event.wait(1)

    monkeypatch.setattr(adaptive, "UpsSampler", UnsafeSampler)
    monkeypatch.setattr(adaptive, "_run_worker", stopped_worker)
    policy = DiagonalLinUCB("policy-test", MINING_DELIVERY_FEATURES_V2)
    stage_jobs = [("episode-capacity", scenario, 0)]
    with TrainingStore(tmp_path / "experience.db") as store:
        store.save_scenario(scenario, "train", scenario["scenario_hash"])
        store.save_policy(policy.policy_id, "diagonal_linucb", 0, {}, policy_snapshot(policy))
        result = adaptive._run_stage(
            [worker_spec], stage_jobs, "secret", policy, tmp_path / "live", store,
            AdaptiveScaleState(8), minimum_slots=4, maximum_slots=8, step=4,
            minimum_ups_p95=57.0, minimum_ups_p98=55.0,
            healthy_windows_to_grow=2, unhealthy_windows_to_shrink=1,
        )
        _results, completed, failed, _window, _error, interrupted, state, reason = result
        assert completed == failed == 0
        assert interrupted == {"episode-capacity"}
        assert (state.slots, reason) == (4, "decrease_safe_ups")
        assert store.rows("episodes")[0]["status"] == "aborted"
        assert store.rows("transitions") == []
    retried = _retry_jobs(stage_jobs, interrupted)
    assert retried[0][0] != "episode-capacity"

def test_adaptive_controller_defaults_to_requested_twenty_slot_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "sys.argv", ["adaptive", "--workers", str(tmp_path / "workers.json")],
    )
    args = _parse()
    assert args.initial_slots == 20
    assert args.episodes_per_policy == 100
    assert (args.minimum_ups_p95, args.minimum_ups_p98) == (57.0, 55.0)

def test_adaptive_controller_learns_only_after_a_complete_policy_cohort(monkeypatch, tmp_path) -> None:
    worker_spec = worker(tmp_path, "slot-one", 35001, 28001)
    cohort_sizes = []

    def staged_result(_workers, stage_jobs, *_args, **_kwargs):
        episode_id = stage_jobs[0][0]
        return (
            [(episode_id, {"episode_id": episode_id}, None)], 1, 0,
            UpsWindow((60.0, 60.0, 60.0)), None, set(), None, None,
        )

    def learn(policy, results):
        cohort_sizes.append(len(results))
        return policy

    monkeypatch.setattr(adaptive, "load_worker_specs", lambda _path: [worker_spec])
    monkeypatch.setattr(adaptive, "_rcon_password", lambda _args: "secret")
    monkeypatch.setattr(adaptive, "_run_stage", staged_result)
    monkeypatch.setattr(adaptive, "_learn_policy", learn)
    assert adaptive.main([
        "--workers", str(tmp_path / "workers.json"), "--count", "3",
        "--attempts-per-scenario", "1", "--initial-slots", "1",
        "--minimum-slots", "1", "--maximum-slots", "1", "--step", "1",
        "--episodes-per-slot", "1", "--episodes-per-policy", "3",
        "--database", str(tmp_path / "experience.db"),
        "--checkpoint", str(tmp_path / "policy.json"),
        "--live-directory", str(tmp_path / "live"),
    ]) == 0
    assert cohort_sizes == [3]