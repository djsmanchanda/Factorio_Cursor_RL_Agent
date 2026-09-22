# Path: training/laya_guidance.py
# Purpose: Apply bounded human guidance to Laya state without granting execution authority.

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping


class LayaGuidanceError(ValueError):
    """Raised when a guidance source cannot be read safely."""


_FOCUSES = {"throughput", "reliability", "efficiency", "exploration", "general"}
_RUNTIMES = {"disposable_training", "real_nauvis", "legacy_retired"}


def _bounded(value: Any, maximum: int, default: str = "") -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return (text or default)[:maximum]


class LayaGuidanceStore:
    """Read the newest guidance per focus from a JSON/JSONL file or directory."""

    def __init__(self, path: str | Path, *, max_items: int = 5):
        self.path = Path(path)
        if max_items < 1 or max_items > 16:
            raise ValueError("max_items must be between 1 and 16")
        self.max_items = max_items

    def _paths(self) -> list[Path]:
        if self.path.is_file():
            return [self.path]
        if self.path.is_dir():
            return sorted(self.path.glob("*.json"), key=lambda item: item.stat().st_mtime_ns)
        return []

    @staticmethod
    def _records(path: Path) -> list[dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, Mapping):
            records.append(dict(payload))
            return records
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, Mapping):
                records.append(dict(row))
        return records

    def active(self, *, surface: str | None = None, runtime: str | None = None) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for path in self._paths():
            for record in self._records(path):
                guidance = record.get("guidance") if isinstance(record.get("guidance"), Mapping) else {}
                instruction = _bounded(guidance.get("instruction"), 1000)
                if not instruction:
                    continue
                focus = _bounded(guidance.get("focus"), 32, "general")
                if focus not in _FOCUSES:
                    continue
                source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
                record_runtime = _bounded(source.get("runtime"), 32)
                record_surface = _bounded(source.get("surface"), 128)
                if surface and record_surface and record_surface != surface:
                    continue
                if runtime and record_runtime and record_runtime != runtime:
                    continue
                outcome = _bounded(guidance.get("outcome"), 16, "unknown")
                if outcome == "blocked":
                    continue
                latest[focus] = {
                    "record_id": _bounded(record.get("record_id"), 128, path.stem),
                    "focus": focus,
                    "instruction": instruction,
                    "goal": _bounded(guidance.get("goal"), 512),
                    "outcome": outcome,
                    "source": {"surface": record_surface, "runtime": record_runtime},
                }
        return list(latest.values())[-self.max_items:]

    def apply(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Return a state with guidance as soft priorities, preserving hard rules."""
        result = copy.deepcopy(dict(state))
        run = result.get("run") if isinstance(result.get("run"), Mapping) else {}
        guidance = self.active(surface=run.get("surface"), runtime=run.get("runtime"))
        if not guidance:
            return result
        guidelines = list(result.get("guidelines") or [])
        existing_rules = {str(item.get("rule_id")): item for item in guidelines if isinstance(item, Mapping)}
        goals = list(result.get("goal_stack") or [])
        existing_goals = {str(item.get("goal_id")): item for item in goals if isinstance(item, Mapping)}
        for item in guidance:
            focus = item["focus"]
            rule_id = f"rule.human.{focus}"
            existing_rules[rule_id] = {
                "rule_id": rule_id,
                "kind": "soft",
                "statement": f"Human guidance ({focus}): {item['instruction']}",
                "rationale": "Operator intervention; prefer this priority while it remains current.",
            }
            if item["goal"]:
                goal_id = f"goal.human.{focus}"
                existing_goals[goal_id] = {
                    "goal_id": goal_id,
                    "kind": "human_guidance",
                    "objective": item["goal"],
                    "status": "active",
                    "gap": None,
                    "age_ticks": 0,
                    "retry_count": 0,
                    "last_failure_kind": "none",
                }
        result["guidelines"] = list(existing_rules.values())[-16:]
        # Human goals are inserted ahead of stale objectives but remain soft;
        # validators and the model still decide which legal action serves them.
        human_goals = [goal for goal in existing_goals.values() if str(goal.get("kind")) == "human_guidance"]
        other_goals = [goal for goal in existing_goals.values() if str(goal.get("kind")) != "human_guidance"]
        result["goal_stack"] = (human_goals + other_goals)[:16]
        return result


__all__ = ["LayaGuidanceError", "LayaGuidanceStore"]
