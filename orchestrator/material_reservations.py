# Path: orchestrator/material_reservations.py
# Purpose: Persist and allocate exact construction stock to named deterministic projects.

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

_ACTIVE_STATES = frozenset({"planned", "supply_wait", "ready"})
_VALID_STATES = _ACTIVE_STATES | {"constructing", "completed", "failed"}


class MaterialReservationError(RuntimeError):
    """Persisted project identity or material accounting is contradictory."""


def _counts(values: Mapping[str, object], label: str) -> dict[str, int]:
    try:
        result = {str(item): int(count) for item, count in values.items()}
    except (AttributeError, TypeError, ValueError) as error:
        raise MaterialReservationError(f"{label} must be an item-count mapping") from error
    if any(not item or count <= 0 for item, count in result.items()):
        raise MaterialReservationError(f"{label} counts must be positive")
    return dict(sorted(result.items()))


def plan_material_bill(plan: Mapping[str, object]) -> dict[str, int]:
    """Count every entity/tile placement needed by a self-contained project."""
    bill: Counter[str] = Counter()
    for phase in plan.get("phases", []):  # type: ignore[union-attr]
        for action in phase.get("actions", []):
            action_type = action.get("action_type")
            if action_type in {"place_entity", "place_ghost"}:
                bill[str(action["entity"])] += 1
            elif action_type == "place_tile_ghost":
                bill[str(action["tile"])] += 1
    return dict(sorted(bill.items()))


@dataclass(frozen=True)
class MaterialProject:
    project_id: str
    target_item: str | None
    state: str
    required: Mapping[str, int]
    reserved: Mapping[str, int]
    source_producers: Mapping[str, str | None]
    expected_rates: Mapping[str, float | None]
    eta_seconds: Mapping[str, float | None]
    priority: int
    sequence: int
    hold_until_producing: bool = False
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.project_id:
            raise MaterialReservationError("Material project ID must be non-empty")
        if self.target_item is not None and not self.target_item:
            raise MaterialReservationError("Material target item must be non-empty")
        if self.state not in _VALID_STATES:
            raise MaterialReservationError(f"Unknown material project state {self.state!r}")
        if self.priority < 0 or self.sequence < 1 or self.revision < 1:
            raise MaterialReservationError("Material project ordering must be positive")
        required = _counts(self.required, "required")
        if not required:
            raise MaterialReservationError("Material project requires at least one item")
        reserved = {
            str(item): int(count) for item, count in self.reserved.items()
        }
        if any(
            item not in required or count < 0 or count > required[item]
            for item, count in reserved.items()
        ):
            raise MaterialReservationError("Material reservation exceeds its project bill")
        if set(self.source_producers) - set(required):
            raise MaterialReservationError("Material source exists outside the project bill")
        if set(self.expected_rates) - set(required) or set(self.eta_seconds) - set(required):
            raise MaterialReservationError("Material rate/ETA exists outside the project bill")
        for value in self.expected_rates.values():
            if value is not None and value < 0:
                raise MaterialReservationError("Expected material rate cannot be negative")
        for value in self.eta_seconds.values():
            if value is not None and value < 0:
                raise MaterialReservationError("Material ETA cannot be negative")

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "target_item": self.target_item,
            "state": self.state,
            "required": dict(sorted(self.required.items())),
            "reserved": dict(sorted(self.reserved.items())),
            "source_producers": dict(sorted(self.source_producers.items())),
            "expected_rates": dict(sorted(self.expected_rates.items())),
            "eta_seconds": dict(sorted(self.eta_seconds.items())),
            "priority": self.priority,
            "sequence": self.sequence,
            "hold_until_producing": self.hold_until_producing,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> MaterialProject:
        try:
            return cls(
                project_id=str(payload["project_id"]),
                target_item=(
                    str(payload["target_item"])
                    if payload.get("target_item") is not None else None
                ),
                state=str(payload["state"]),
                required=_counts(payload["required"], "required"),  # type: ignore[arg-type]
                reserved={
                    str(item): int(count)
                    for item, count in payload["reserved"].items()  # type: ignore[union-attr]
                },
                source_producers={
                    str(item): (str(value) if value is not None else None)
                    for item, value in payload["source_producers"].items()  # type: ignore[union-attr]
                },
                expected_rates={
                    str(item): (float(value) if value is not None else None)
                    for item, value in payload["expected_rates"].items()  # type: ignore[union-attr]
                },
                eta_seconds={
                    str(item): (float(value) if value is not None else None)
                    for item, value in payload["eta_seconds"].items()  # type: ignore[union-attr]
                },
                priority=int(payload["priority"]),
                sequence=int(payload["sequence"]),
                hold_until_producing=bool(payload.get("hold_until_producing", False)),
                revision=int(payload["revision"]),
            )
        except MaterialReservationError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise MaterialReservationError("Malformed material project") from error


class MaterialReservationLedger:
    """Episode-scoped allocation of finite construction stock to named work."""

    def __init__(
        self, script_output: Path | str, *, episode_id: str,
        surface: str, force: str,
    ) -> None:
        self.path = (
            Path(script_output).parent / "logs" / "deterministic-material-reservations"
            / f"{episode_id}.json"
        )
        self.episode_id = episode_id
        self.surface = surface
        self.force = force
        self.revision = 0
        self.projects: dict[str, MaterialProject] = {}
        self.bootstrap_supply: dict[str, dict[str, dict[str, int]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != 1:
                raise MaterialReservationError("Unsupported material ledger version")
            if (
                payload.get("episode_id") != self.episode_id
                or payload.get("surface") != self.surface
                or payload.get("force") != self.force
            ):
                raise MaterialReservationError("Material ledger scope does not match this run")
            self.revision = int(payload["revision"])
            raw_supply = payload.get("bootstrap_supply", {})
            if not isinstance(raw_supply, dict):
                raise MaterialReservationError("Bootstrap supply must be an object")
            self.bootstrap_supply = {
                str(profile): {
                    "targets": _counts(record["targets"], "bootstrap targets"),
                    "inserted": {
                        str(item): int(count)
                        for item, count in record.get("inserted", {}).items()
                        if int(count) > 0
                    },
                }
                for profile, record in raw_supply.items()
            }
            projects = tuple(
                MaterialProject.from_dict(item) for item in payload.get("projects", ())
            )
        except MaterialReservationError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MaterialReservationError("Malformed persisted material ledger") from error
        if len({project.project_id for project in projects}) != len(projects):
            raise MaterialReservationError("Material project IDs must be unique")
        self.projects = {project.project_id: project for project in projects}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "force": self.force,
            "revision": self.revision,
            "bootstrap_supply": self.bootstrap_supply,
            "projects": [
                project.to_dict()
                for project in sorted(
                    self.projects.values(), key=lambda value: value.sequence,
                )
            ],
        }
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        temporary.replace(self.path)

    def bootstrap_supply_applied(self, profile: str) -> bool:
        return profile in self.bootstrap_supply

    def record_bootstrap_supply(
        self, profile: str, targets: Mapping[str, int], inserted: Mapping[str, int],
    ) -> None:
        if not profile:
            raise MaterialReservationError("Bootstrap profile must be non-empty")
        normalized_targets = _counts(targets, "bootstrap targets")
        normalized_inserted = {
            str(item): int(count)
            for item, count in inserted.items()
            if int(count) > 0
        }
        if set(normalized_inserted) - set(normalized_targets):
            raise MaterialReservationError("Inserted bootstrap item is outside its targets")
        self.bootstrap_supply[profile] = {
            "targets": normalized_targets,
            "inserted": dict(sorted(normalized_inserted.items())),
        }
        self.revision += 1
        self._save()

    @staticmethod
    def _active(project: MaterialProject) -> bool:
        return (
            project.state in _ACTIVE_STATES
            or (project.state == "constructing" and project.hold_until_producing)
        )

    def project_is_active(self, project_id: str) -> bool:
        project = self.projects.get(project_id)
        return project is not None and self._active(project)

    def _rebalance(self, stock: Mapping[str, int]) -> None:
        pool = {str(item): max(0, int(count)) for item, count in stock.items()}
        ordered = sorted(
            self.projects.values(), key=lambda project: (-project.priority, project.sequence),
        )
        updated: dict[str, MaterialProject] = {}
        for project in ordered:
            if not self._active(project):
                updated[project.project_id] = replace(project, reserved={}, eta_seconds={})
                continue
            reserved: dict[str, int] = {}
            eta: dict[str, float | None] = {}
            complete = True
            for item, required in project.required.items():
                allocated = min(required, pool.get(item, 0))
                if allocated:
                    reserved[item] = allocated
                    pool[item] = pool.get(item, 0) - allocated
                missing = required - allocated
                complete = complete and missing == 0
                rate = project.expected_rates.get(item)
                eta[item] = 0.0 if missing == 0 else (
                    missing / rate if rate is not None and rate > 0 else None
                )
            state = project.state
            if state != "constructing":
                state = "ready" if complete else "supply_wait"
            updated[project.project_id] = replace(
                project, state=state, reserved=reserved, eta_seconds=eta,
            )
        self.projects.update(updated)

    def declare(
        self, project_id: str, required: Mapping[str, int], stock: Mapping[str, int], *,
        target_item: str | None = None,
        source_producers: Mapping[str, str | None] | None = None,
        expected_rates: Mapping[str, float | None] | None = None,
        priority: int = 50,
        hold_until_producing: bool = False,
    ) -> MaterialProject:
        bill = _counts(required, "required")
        sources = source_producers or {}
        rates = expected_rates or {}
        existing = self.projects.get(project_id)
        sequence = existing.sequence if existing is not None else (
            max((project.sequence for project in self.projects.values()), default=0) + 1
        )
        revision = (existing.revision + 1) if existing is not None else 1
        keep_constructing = bool(
            existing is not None
            and existing.state == "constructing"
            and existing.required == bill
        )
        project = MaterialProject(
            project_id=project_id,
            target_item=target_item if target_item is not None else (
                existing.target_item if existing is not None else None
            ),
            state="constructing" if keep_constructing else "planned",
            required=bill,
            reserved=existing.reserved if keep_constructing else {},
            source_producers={
                item: sources.get(item)
                for item in bill
            },
            expected_rates={
                item: rates.get(item)
                for item in bill
            },
            eta_seconds={},
            priority=max(priority, existing.priority if existing is not None else 0),
            sequence=sequence,
            hold_until_producing=(
                hold_until_producing
                or (existing.hold_until_producing if existing is not None else False)
            ),
            revision=revision,
        )
        self.projects[project_id] = project
        self._rebalance(stock)
        self.revision += 1
        self._save()
        return self.projects[project_id]

    def stock_targets(self) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for project in self.projects.values():
            if self._active(project):
                totals.update(project.required)
        return dict(sorted(totals.items()))

    def shortage_targets(
        self, project_id: str, stock: Mapping[str, int],
    ) -> dict[str, int]:
        if project_id not in self.projects:
            raise MaterialReservationError(f"Unknown material project {project_id!r}")
        self._rebalance(stock)
        project = self.projects[project_id]
        if not self._active(project):
            self._save()
            return {}
        targets = self.stock_targets()
        self._save()
        return {
            item: targets[item]
            for item, required in project.required.items()
            if project.reserved.get(item, 0) < required
        }

    def allocatable_stock(
        self, stock: Mapping[str, int], *, claimant: str | None = None,
    ) -> dict[str, int]:
        self._rebalance(stock)
        reserved: Counter[str] = Counter()
        for project in self.projects.values():
            if self._active(project) and project.project_id != claimant:
                reserved.update(project.reserved)
        return {
            item: max(0, int(count) - reserved.get(item, 0))
            for item, count in stock.items()
        }

    def required_stock(self, item: str) -> int:
        return self.stock_targets().get(item, 0)

    def mark_constructing(self, project_id: str, stock: Mapping[str, int]) -> None:
        project = self.projects.get(project_id)
        if project is None:
            return
        self.projects[project_id] = replace(
            project, state="constructing", revision=project.revision + 1,
        )
        self._rebalance(stock)
        self.revision += 1
        self._save()

    def complete(self, project_id: str) -> None:
        project = self.projects.get(project_id)
        if project is None or project.state == "completed":
            return
        self.projects[project_id] = replace(
            project, state="completed", reserved={}, eta_seconds={},
            revision=project.revision + 1,
        )
        self.revision += 1
        self._save()

    def fail(self, project_id: str) -> None:
        project = self.projects.get(project_id)
        if project is None:
            return
        self.projects[project_id] = replace(
            project, state="failed", reserved={}, eta_seconds={},
            revision=project.revision + 1,
        )
        self.revision += 1
        self._save()


_ACTIVE_LEDGER: MaterialReservationLedger | None = None


def set_active_material_ledger(ledger: MaterialReservationLedger | None) -> None:
    global _ACTIVE_LEDGER
    _ACTIVE_LEDGER = ledger


def active_material_ledger() -> MaterialReservationLedger | None:
    return _ACTIVE_LEDGER
