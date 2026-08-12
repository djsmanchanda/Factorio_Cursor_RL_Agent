# Path: training/scheduler.py
# Purpose: Validate isolated training slots and coordinate exclusive hardware phases.

from __future__ import annotations

import json
import math
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
    if len(identities) != len(set(identities)):
        raise ValueError("worker ids must be unique")
    runtimes: dict[str, tuple[str, int, int, Path]] = {}
    endpoints: dict[tuple[str, int, int, Path], str] = {}
    game_ports: dict[tuple[str, int], str] = {}
    rcon_ports: dict[tuple[str, int], str] = {}
    outputs: dict[Path, str] = {}
    for worker in workers:
        output = worker.script_output.resolve()
        endpoint = (worker.host, worker.game_port, worker.rcon_port, output)
        prior_endpoint = runtimes.setdefault(worker.instance_id, endpoint)
        if prior_endpoint != endpoint:
            raise ValueError("slots sharing an instance_id must use one Factorio runtime")
        prior_instance = endpoints.setdefault(endpoint, worker.instance_id)
        if prior_instance != worker.instance_id:
            raise ValueError("a Factorio runtime endpoint must use one shared instance_id")
        for claimed, key in (
            (game_ports, (worker.host, worker.game_port)),
            (rcon_ports, (worker.host, worker.rcon_port)),
            (outputs, output),
        ):
            owner = claimed.setdefault(key, worker.instance_id)
            if owner != worker.instance_id:
                raise ValueError("Factorio runtime ports and script-output must stay isolated")


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


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample set."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= quantile <= 1.0 or not all(math.isfinite(float(value)) for value in values):
        raise ValueError("percentile quantile and samples must be finite and bounded")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True)
class UpsWindow:
    """A measured server window used for conservative parallelism decisions."""

    samples: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.samples:
            raise ValueError("an UPS window needs at least one sample")
        if not all(math.isfinite(value) and value > 0 for value in self.samples):
            raise ValueError("UPS samples must be finite and positive")

    @property
    def mean_ups(self) -> float:
        return sum(self.samples) / len(self.samples)

    @property
    def safe_ups_p95(self) -> float:
        """UPS maintained for at least 95% of the sample window."""
        return percentile(self.samples, 0.05)

    @property
    def safe_ups_p98(self) -> float:
        """UPS maintained for at least 98% of the sample window."""
        return percentile(self.samples, 0.02)

    @property
    def tick_time_p98_ms(self) -> float:
        """The corresponding high-tail tick time, in milliseconds."""
        return 1000.0 / self.safe_ups_p98


@dataclass(frozen=True)
class AdaptiveScaleState:
    slots: int
    healthy_windows: int = 0
    unhealthy_windows: int = 0


def adjust_adaptive_slots(
    state: AdaptiveScaleState,
    window: UpsWindow | None,
    *,
    minimum: int = 4,
    maximum: int = 32,
    step: int = 4,
    minimum_safe_ups: float | None = None,
    minimum_safe_ups_p95: float = 57.0,
    minimum_safe_ups_p98: float = 55.0,
    healthy_windows_to_grow: int = 1,
    unhealthy_windows_to_shrink: int = 1,
) -> tuple[AdaptiveScaleState, str]:
    """Apply four-slot hysteresis to one measured UPS window."""
    if minimum < 1 or maximum < minimum or step < 1:
        raise ValueError("adaptive slot bounds are invalid")
    if not minimum <= state.slots <= maximum:
        raise ValueError("adaptive state slots are outside its bounds")
    if healthy_windows_to_grow < 1 or unhealthy_windows_to_shrink < 1:
        raise ValueError("adaptive hysteresis windows must be positive")
    if minimum_safe_ups is not None:
        minimum_safe_ups_p98 = minimum_safe_ups
    if minimum_safe_ups_p95 <= 0 or minimum_safe_ups_p98 <= 0:
        raise ValueError("adaptive UPS thresholds must be positive")
    if window is None:
        return state, "no_ups_window"

    healthy = state.healthy_windows
    unhealthy = state.unhealthy_windows
    if (
        window.safe_ups_p95 >= minimum_safe_ups_p95
        and window.safe_ups_p98 >= minimum_safe_ups_p98
    ):
        healthy += 1
        unhealthy = 0
    else:
        unhealthy += 1
        healthy = 0

    if healthy >= healthy_windows_to_grow and state.slots < maximum:
        slots = min(maximum, state.slots + step)
        return AdaptiveScaleState(slots), "increase_safe_ups"
    if unhealthy >= unhealthy_windows_to_shrink and state.slots > minimum:
        slots = max(minimum, state.slots - step)
        return AdaptiveScaleState(slots), "decrease_safe_ups"
    return AdaptiveScaleState(state.slots, healthy, unhealthy), "hold_safe_ups"


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
