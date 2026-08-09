# Path: tests/test_training_scheduler.py
# Purpose: Verify isolated worker contracts, phase exclusion, and tuning.

from __future__ import annotations

import threading

import pytest

from training.scheduler import (
    BatchMeasurement, ResourcePhaseLock, WorkerSpec, next_worker_count,
    validate_worker_specs,
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
