# Path: orchestrator/bootstrap_work.py
# Purpose: Persist a small, bounded dependency graph for bootstrap recovery work.

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorkCycle:
    """A concrete dependency loop, suitable for a typed deferred decision."""

    members: tuple[str, ...]
    reason: str


class BootstrapWorkLedger:
    """Episode-scoped work evidence, deliberately smaller than a scheduler.

    The controller remains responsible for choosing a task each pass.  This
    ledger only makes a recovery dependency durable across a runner restart and
    catches cycles before a stock-wait callback can repeat them indefinitely.
    """

    VERSION = 1
    MAX_EDGES = 96

    def __init__(self, script_output: Path | str, *, episode_id: str) -> None:
        self.path = (
            Path(script_output).parent / "logs" / "deterministic-bootstrap-work"
            / f"{episode_id}.json"
        )
        self.episode_id = episode_id
        self.edges: dict[str, dict[str, str]] = {}
        self.waits: dict[str, dict[str, float | int]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != self.VERSION or payload.get("episode_id") != self.episode_id:
            raise ValueError("Bootstrap work ledger scope does not match this run")
        raw_edges = payload.get("edges", {})
        if not isinstance(raw_edges, dict):
            raise ValueError("Bootstrap work ledger edges must be an object")
        self.edges = {
            str(parent): {str(child): str(reason) for child, reason in children.items()}
            for parent, children in raw_edges.items()
            if isinstance(children, dict)
        }
        raw_waits = payload.get("waits", {})
        if isinstance(raw_waits, dict):
            self.waits = {
                str(item): {
                    "target": int(record["target"]),
                    "best": int(record["best"]),
                    "last_progress": float(record["last_progress"]),
                }
                for item, record in raw_waits.items()
                if isinstance(record, dict)
                and {"target", "best", "last_progress"} <= set(record)
            }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "episode_id": self.episode_id,
            "edges": {parent: dict(sorted(children.items())) for parent, children in sorted(self.edges.items())},
            "waits": dict(sorted(self.waits.items())),
        }
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _path(self, start: str, target: str) -> tuple[str, ...] | None:
        todo: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
        seen: set[str] = set()
        while todo:
            node, path = todo.pop(0)
            if node == target:
                return path
            if node in seen:
                continue
            seen.add(node)
            todo.extend((child, path + (child,)) for child in self.edges.get(node, {}) if child not in seen)
        return None

    def depend(self, parent: str, child: str, *, reason: str) -> WorkCycle | None:
        """Record an edge or return the cycle it would create.

        Duplicate evidence is idempotent.  Oldest edges are retained first so
        a pathological run cannot make its restart state unbounded.
        """
        if not parent or not child or parent == child:
            return WorkCycle((parent, child), reason)
        loop = self._path(child, parent)
        if loop is not None:
            return WorkCycle((parent,) + loop, reason)
        if sum(len(children) for children in self.edges.values()) >= self.MAX_EDGES:
            return WorkCycle((parent, child), "dependency_graph_limit")
        children = self.edges.setdefault(parent, {})
        if child not in children:
            children[child] = reason
            self._save()
        return None

    def reconcile(self, completed: Iterable[str]) -> None:
        """Release resolved work edges without touching unrelated evidence."""
        completed_set = set(completed)
        if not completed_set:
            return
        before = json.dumps(self.edges, sort_keys=True)
        self.edges = {
            parent: {child: reason for child, reason in children.items() if child not in completed_set}
            for parent, children in self.edges.items()
            if parent not in completed_set
        }
        if json.dumps(self.edges, sort_keys=True) != before:
            self._save()

    def observe_stock(
        self, item: str, target: int, have: int, *, settle_seconds: float = 60.0,
        now: float | None = None,
    ) -> str:
        """Record one non-blocking stock observation.

        ``stalled`` is emitted at most once per settle window.  No sleeps live
        here: callers yield back to task selection after each observation and
        restart resumes the same wall-clock horizon.
        """
        if target < 1 or have < 0 or settle_seconds <= 0:
            raise ValueError("Invalid bootstrap stock observation")
        moment = time.time() if now is None else float(now)
        if have >= target:
            if item in self.waits:
                del self.waits[item]
                self._save()
            return "ready"
        record = self.waits.get(item)
        if record is None or int(record["target"]) != target:
            self.waits[item] = {
                "target": target, "best": have, "last_progress": moment,
            }
            self._save()
            return "growing"
        if have > int(record["best"]):
            record["best"] = have
            record["last_progress"] = moment
            self._save()
            return "growing"
        if moment - float(record["last_progress"]) >= settle_seconds:
            record["last_progress"] = moment
            self._save()
            return "stalled"
        return "waiting"
