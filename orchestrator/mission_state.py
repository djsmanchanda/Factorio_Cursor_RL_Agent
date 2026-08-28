# Path: orchestrator/mission_state.py
# Purpose: Persist one deterministic mission across controller calls and emit typed blocker events.

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Mapping


BOOTSTRAP_PROFILES = ("reduced-v1", "supplied-v1")
MISSION_STATUSES = {"running", "completed", "stuck", "error"}
_CODE_TOKEN = re.compile(r"[^a-z0-9_]+")


class MissionStateError(RuntimeError):
    """The durable mission ledger is missing or malformed."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def _blocker_code(value: object) -> str:
    normalized = _CODE_TOKEN.sub("_", str(value).strip().lower()).strip("_")
    return normalized or "untyped_stuck"


class MissionStateLedger:
    """Append transitions to one atomic snapshot plus a blocker JSONL stream."""

    def __init__(
        self, path: Path, blocker_events_path: Path, *, episode_id: str | None,
        bootstrap_profile: str, command: str, target: str, surface: str,
        force: str, repository_revision: str | None = None,
        save_provenance: Mapping[str, object] | None = None,
    ) -> None:
        if bootstrap_profile not in BOOTSTRAP_PROFILES:
            raise MissionStateError(
                f"Unknown bootstrap profile {bootstrap_profile!r}; expected one of "
                + ", ".join(BOOTSTRAP_PROFILES)
            )
        self.path = path
        self.blocker_events_path = blocker_events_path
        identity = {
            "episode_id": episode_id,
            "bootstrap_profile": bootstrap_profile,
            "command": command,
            "target": target,
            "surface": surface,
            "force": force,
        }
        previous = self._load_matching(identity)
        timestamp = _now()
        if previous is None:
            mission_id = episode_id or f"mission-{timestamp}"
            self.payload: dict[str, object] = {
                "version": 1,
                "mission_id": mission_id,
                **identity,
                "repository_revision": repository_revision,
                "save_provenance": dict(save_provenance or {}),
                "status": "running",
                "stage": "starting",
                "current_target": None,
                "attempt": 1,
                "started_at": timestamp,
                "updated_at": timestamp,
                "ended_at": None,
                "controllers": [],
                "blockers": [],
                "events": [],
            }
        else:
            self.payload = previous
            self.payload.update({
                "repository_revision": repository_revision,
                "save_provenance": dict(save_provenance or {}),
                "status": "running",
                "stage": "starting",
                "current_target": None,
                "attempt": int(previous.get("attempt", 0)) + 1,
                "updated_at": timestamp,
                "ended_at": None,
            })
        self._event("mission_started", stage="starting")
        self._write()

    def _load_matching(self, identity: Mapping[str, object]) -> dict[str, object] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MissionStateError(
                f"Mission ledger is unreadable: {self.path}: {error}"
            ) from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise MissionStateError(f"Mission ledger has an unsupported shape: {self.path}")
        if all(payload.get(key) == value for key, value in identity.items()):
            return payload
        return None

    def _event(self, event: str, **fields: object) -> None:
        events = self.payload.setdefault("events", [])
        assert isinstance(events, list)
        events.append({
            "event": event,
            "at": _now(),
            "attempt": self.payload["attempt"],
            **fields,
        })

    def _write(self) -> None:
        self.payload["updated_at"] = _now()
        _atomic_write(self.path, self.payload)

    def transition(self, stage: str, *, current_target: str | None = None) -> None:
        self.payload["stage"] = stage
        self.payload["current_target"] = current_target
        self._event("stage_changed", stage=stage, target=current_target)
        self._write()

    def controller_started(self, target: str) -> None:
        self.transition("controller", current_target=target)
        controllers = self.payload.setdefault("controllers", [])
        assert isinstance(controllers, list)
        controllers.append({
            "target": target,
            "attempt": self.payload["attempt"],
            "status": "running",
            "started_at": _now(),
            "ended_at": None,
        })
        self._event("controller_started", stage="controller", target=target)
        self._write()

    def controller_finished(self, target: str, status: str) -> None:
        if status not in {"completed", "failed"}:
            raise MissionStateError(f"Unknown controller status {status!r}")
        controllers = self.payload.setdefault("controllers", [])
        assert isinstance(controllers, list)
        for controller in reversed(controllers):
            if (
                isinstance(controller, dict)
                and controller.get("target") == target
                and controller.get("status") == "running"
            ):
                controller["status"] = status
                controller["ended_at"] = _now()
                break
        self._event(
            "controller_finished", stage="controller", target=target, status=status,
        )
        self._write()

    def record_blocker(
        self, error: BaseException, *, code: str | None = None,
        classification: str | None = None, state: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        classification = classification or str(
            getattr(error, "classification", "bug")
        )
        state = state or str(getattr(error, "state", "failed"))
        if classification not in {"bug", "intended_difficulty"}:
            classification = "bug"
        if state not in {
            "planned", "constructing", "coverage_wait", "power_wait",
            "supply_wait", "producing", "retiring", "retired", "failed",
        }:
            state = "failed"
        merged_details = dict(getattr(error, "details", {}) or {})
        merged_details.update(details or {})
        blockers = self.payload.setdefault("blockers", [])
        assert isinstance(blockers, list)
        blocker = {
            "blocker_id": f"blocker-{len(blockers) + 1:04d}",
            "mission_id": self.payload["mission_id"],
            "attempt": self.payload["attempt"],
            "observed_at": _now(),
            "code": _blocker_code(code or getattr(error, "code", "untyped_stuck")),
            "classification": classification,
            "state": state,
            "stage": self.payload["stage"],
            "target": self.payload["current_target"] or self.payload["target"],
            "message": str(error),
            "details": merged_details,
        }
        blockers.append(blocker)
        self.blocker_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.blocker_events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(blocker, sort_keys=True, separators=(",", ":")) + "\n")
        self._event(
            "blocker_recorded", stage=self.payload["stage"],
            target=blocker["target"], blocker_id=blocker["blocker_id"],
        )
        self._write()
        return blocker

    def finish(self, status: str) -> None:
        if status not in MISSION_STATUSES - {"running"}:
            raise MissionStateError(f"Unknown terminal mission status {status!r}")
        self.payload["status"] = status
        self.payload["stage"] = status
        self.payload["current_target"] = None
        self.payload["ended_at"] = _now()
        self._event("mission_finished", stage=status, status=status)
        self._write()
