# Path: orchestrator/priority_list.py
# Purpose: Persist and rank autonomous construction tasks using Factorio ticks.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

_AGE_STEP_TICKS = 18_000  # one rating point per five in-game minutes
_MAX_AGE_BONUS = 20
# Persisted retry ticks can belong to an earlier save/run. Never make a
# fresh runner wait several real minutes for an old absolute game tick.
_MAX_RETRY_AHEAD_TICKS = 600
_DEFAULT_RATINGS = {
    "transport-belt": 100,
    "inserter": 95,
    "assembling-machine-1": 90,
    "electric-furnace": 85,
    "assembling-machine-2": 80,
    "electric-mining-drill": 75,
    "chemical-plant": 70,
    "oil-refinery": 65,
    "pumpjack": 60,
    "offshore-pump": 55,
    "lab": 50,
}


@dataclass
class PriorityItem:
    item: str
    target: int
    base_rating: int
    created_tick: int
    progress_percent: int = 0
    status: str = "ready"
    reason: str = ""
    retry_tick: int = 0

    def rating(self, tick: int) -> int:
        age = max(0, tick - self.created_tick)
        return min(100, self.base_rating + min(_MAX_AGE_BONUS, age // _AGE_STEP_TICKS))

    def elapsed_ticks(self, tick: int) -> int:
        return max(0, tick - self.created_tick)


class PriorityList:
    """Small persistent queue; physical construction remains planner-owned."""

    def __init__(self, path: Path, tick: int) -> None:
        self.path = path
        self.tick = tick
        self.items: dict[str, PriorityItem] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != "1.0.0":
                raise ValueError(f"Unsupported priority-list version in {path}")
            for entry in payload.get("items", []):
                task = PriorityItem(**{
                    name: entry[name]
                    for name in PriorityItem.__dataclass_fields__
                    if name in entry
                })
                if task.created_tick > tick:
                    task.created_tick = tick
                if task.retry_tick > tick + _MAX_RETRY_AHEAD_TICKS:
                    task.retry_tick = tick + _MAX_RETRY_AHEAD_TICKS
                self.items[task.item] = task

    def sync(self, targets: Mapping[str, int], stock: Mapping[str, int], tick: int) -> None:
        self.tick = tick
        for item, target in targets.items():
            task = self.items.get(item)
            if task is None:
                task = PriorityItem(
                    item=item, target=target,
                    base_rating=_DEFAULT_RATINGS.get(item, 45), created_tick=tick,
                )
                self.items[item] = task
            # The CALLER's figure wins. Ratcheting with max() meant a target
            # could only ever rise, and it is persisted -- so one run that
            # raised transport-belt to 4800 left every later run waiting for
            # 4800, with no line in the log saying where the number came from.
            # add_demands still raises it within a run by raising the mapping
            # this is given.
            task.target = target
            task.progress_percent = min(100, stock.get(item, 0) * 100 // task.target)
            if task.progress_percent < 100 and task.status == "complete":
                task.status = "ready"
        self._save()

    def next(self, targets: Mapping[str, int], tick: int) -> PriorityItem | None:
        ready = [
            task for item, task in self.items.items()
            if item in targets and task.status != "complete" and task.retry_tick <= tick
        ]
        if not ready:
            return None
        return min(
            ready,
            key=lambda task: (
                -task.rating(tick), task.progress_percent,
                task.created_tick, task.item,
            ),
        )

    def promote(self, item: str, target: int, tick: int) -> None:
        """Make one newly discovered blocking prerequisite ready immediately."""
        self.tick = tick
        task = self.items.get(item)
        if task is None:
            task = PriorityItem(
                item=item, target=target,
                base_rating=_DEFAULT_RATINGS.get(item, 45), created_tick=tick,
            )
            self.items[item] = task
        task.target = max(task.target, target)
        task.base_rating = 100
        task.status = "ready"
        task.reason = "blocking prerequisite"
        task.retry_tick = tick
        self._save()

    def defer(self, item: str, tick: int, reason: str, *, retry_ticks: int = 3_600) -> None:
        self.tick = tick
        task = self.items[item]
        task.status = "deferred"
        task.reason = reason
        task.retry_tick = tick + retry_ticks
        self._save()

    def complete(self, item: str, tick: int) -> None:
        self.tick = tick
        task = self.items[item]
        task.status = "complete"
        task.progress_percent = 100
        task.retry_tick = tick
        task.reason = ""
        self._save()

    def wait_ticks(self, targets: Mapping[str, int], tick: int) -> int | None:
        retries = [
            task.retry_tick for item, task in self.items.items()
            if item in targets and task.status != "complete" and task.retry_tick > tick
        ]
        return min(retries) - tick if retries else None

    def describe(self, task: PriorityItem, tick: int) -> str:
        return (
            f"PRIORITY: {task.item} rating={task.rating(tick)}/100 "
            f"completion={task.progress_percent}% age={task.elapsed_ticks(tick)} ticks"
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0.0",
            "tick": self.tick,
            "items": [
                {
                    **asdict(self.items[item]),
                    "rating": self.items[item].rating(self.tick),
                    "elapsed_ticks": self.items[item].elapsed_ticks(self.tick),
                }
                for item in sorted(self.items)
            ],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
