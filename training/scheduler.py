# Path: training/scheduler.py
# Purpose: Validate isolated workers and coordinate exclusive hardware phases.

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    instance_id: str
    host: str
    game_port: int
    rcon_port: int
    script_output: Path
    surface_prefix: str
    force_prefix: str

    def __post_init__(self) -> None:
        if not self.worker_id or not self.instance_id:
            raise ValueError("training worker identity cannot be empty")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("training workers must use a loopback host")
        if not 1 <= self.game_port <= 65535 or not 1 <= self.rcon_port <= 65535:
            raise ValueError("worker ports must be explicit valid ports")
        if self.game_port == self.rcon_port:
            raise ValueError("game and RCON ports must differ")
        if not self.surface_prefix.startswith("training/"):
            raise ValueError("training surface prefix must start with training/")
        if not self.force_prefix.startswith("training-"):
            raise ValueError("training force prefix must start with training-")


def validate_worker_specs(workers: Sequence[WorkerSpec]) -> None:
    if not workers:
        raise ValueError("at least one explicit training worker is required")
    identities = [worker.worker_id for worker in workers]
    ports = [port for worker in workers for port in (worker.game_port, worker.rcon_port)]
    outputs = [worker.script_output.resolve() for worker in workers]
    if len(identities) != len(set(identities)):
        raise ValueError("worker ids must be unique")
    if len(ports) != len(set(ports)):
        raise ValueError("all worker ports must be unique")
    if len(outputs) != len(set(outputs)):
        raise ValueError("worker script-output directories must be unique")


def load_worker_specs(path: Path) -> list[WorkerSpec]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read training workers: {path}") from exc
    raw_workers = payload.get("workers") if isinstance(payload, dict) else None
    if not isinstance(raw_workers, list):
        raise ValueError("training workers must contain a workers list")
    try:
        workers = [WorkerSpec(
            worker_id=str(item["worker_id"]), instance_id=str(item["instance_id"]),
            host=str(item["host"]), game_port=int(item["game_port"]),
            rcon_port=int(item["rcon_port"]), script_output=Path(str(item["script_output"])),
            surface_prefix=str(item.get("surface_prefix", "training/")),
            force_prefix=str(item.get("force_prefix", "training-")),
        ) for item in raw_workers if isinstance(item, dict)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("training worker configuration is invalid") from exc
    if len(workers) != len(raw_workers):
        raise ValueError("each training worker must be an object")
    validate_worker_specs(workers)
    return workers
class ResourcePhaseLock:
    """Allow concurrency within one phase while excluding competing phases."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._phase: str | None = None
        self._holders = 0

    @contextmanager
    def acquire(self, phase: str) -> Iterator[None]:
        if phase not in {"collection", "policy_training", "llm_research", "evaluation"}:
            raise ValueError(f"unknown resource phase: {phase}")
        with self._condition:
            while self._phase not in {None, phase}:
                self._condition.wait()
            self._phase = phase
            self._holders += 1
        try:
            yield
        finally:
            with self._condition:
                self._holders -= 1
                if self._holders == 0:
                    self._phase = None
                    self._condition.notify_all()


@dataclass(frozen=True)
class BatchMeasurement:
    worker_count: int
    completed_episodes: int
    elapsed_seconds: float
    mean_ups: float
    failures: int = 0

    @property
    def throughput(self) -> float:
        return self.completed_episodes / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


def next_worker_count(
    history: Sequence[BatchMeasurement], *, minimum: int = 1, maximum: int = 20,
    step: int = 2, minimum_ups: float = 50.0, improvement_ratio: float = 0.05,
) -> int:
    """Increase only while the latest safe benchmark materially improves throughput."""
    if not history:
        return minimum
    latest = history[-1]
    if latest.failures or latest.mean_ups < minimum_ups:
        return max(minimum, latest.worker_count - step)
    if len(history) == 1:
        return min(maximum, latest.worker_count + step)
    previous = history[-2]
    if latest.throughput >= previous.throughput * (1.0 + improvement_ratio):
        return min(maximum, latest.worker_count + step)
    return latest.worker_count
