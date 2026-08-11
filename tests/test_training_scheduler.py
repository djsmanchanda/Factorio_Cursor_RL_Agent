# Path: tests/test_training_scheduler.py
# Purpose: Verify isolated worker contracts, phase exclusion, and tuning.

from __future__ import annotations

import threading

import pytest

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


def test_adaptive_slots_shrink_by_four_on_an_unhealthy_window() -> None:
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(12), UpsWindow((54.0, 53.0, 52.0)))
    assert (state.slots, reason) == (8, "decrease_safe_ups")


def test_adaptive_slots_never_breach_bounds_or_scale_without_measurement() -> None:
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(4), None, maximum=8)
    assert (state.slots, reason) == (4, "no_ups_window")
    state, reason = adjust_adaptive_slots(AdaptiveScaleState(8), UpsWindow((60.0, 60.0, 60.0)), maximum=8)
    assert (state.slots, reason) == (8, "hold_safe_ups")
