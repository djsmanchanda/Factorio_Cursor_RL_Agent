# Path: orchestrator/material_reservations.py
# Purpose: Persist and allocate exact construction stock to named deterministic projects.

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Collection, Mapping

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
        # Prospective hold of finite kit stock for required startup dependency
        # bills.  It is not an owner: it only keeps discretionary projects from
        # reserving the held items before the named dependencies need them.
        self.dependency_budget: dict[str, int] = {}
        self.dependency_claimants: tuple[str, ...] = ()
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
            raw_budget = payload.get("dependency_budget")
            if raw_budget is not None:
                if not isinstance(raw_budget, dict):
                    raise MaterialReservationError("Dependency budget must be an object")
                self.dependency_budget = _counts(
                    raw_budget.get("targets", {}), "dependency budget targets",
                )
                self.dependency_claimants = tuple(
                    str(claimant) for claimant in raw_budget.get("claimants", ())
                )
                if any(not claimant for claimant in self.dependency_claimants):
                    raise MaterialReservationError(
                        "Dependency budget claimants must be non-empty",
                    )
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
            "dependency_budget": {
                "targets": dict(sorted(self.dependency_budget.items())),
                "claimants": sorted(self.dependency_claimants),
            },
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

    def set_dependency_budget(
        self, targets: Mapping[str, int], *,
        claimants: Collection[str] = (),
    ) -> None:
        """Hold finite stock for required startup dependency bills.

        The hold is not a project: it never appears in ``stock_targets`` and
        never overwrites another project's reservation.  It only removes the
        held quantities from the pool that discretionary partial reservations
        can claim, until the named claimants (the required dependency projects)
        take them or the budget is cleared.  Items no longer needed must be
        dropped from ``targets``; passing an empty mapping clears the hold.
        """
        normalized = {
            str(item): int(count) for item, count in targets.items() if int(count) > 0
        }
        new_claimants = tuple(dict.fromkeys(str(c) for c in claimants if str(c)))
        if set(normalized) and not new_claimants:
            raise MaterialReservationError(
                "Dependency budget with targets needs at least one claimant",
            )
        new_budget = dict(sorted(normalized.items()))
        if (
            new_budget == self.dependency_budget
            and new_claimants == self.dependency_claimants
        ):
            return
        self.dependency_budget = new_budget
        self.dependency_claimants = new_claimants
        self.revision += 1
        self._save()

    def clear_dependency_budget(self) -> None:
        self.set_dependency_budget({}, claimants=())

    @staticmethod
    def _active(project: MaterialProject) -> bool:
        return (
            project.state in _ACTIVE_STATES
            or (project.state == "constructing" and project.hold_until_producing)
        )

    def project_is_active(self, project_id: str) -> bool:
        project = self.projects.get(project_id)
        return project is not None and self._active(project)

    @staticmethod
    def _take(project: MaterialProject, pool: dict[str, int]) -> dict[str, int]:
        """Reserve as much of the project's bill as the pool holds."""
        reserved: dict[str, int] = {}
        for item, required in project.required.items():
            allocated = min(required, pool.get(item, 0))
            if allocated:
                reserved[item] = allocated
                pool[item] = pool.get(item, 0) - allocated
        return reserved

    def _rebalance(self, stock: Mapping[str, int]) -> None:
        pool = {str(item): max(0, int(count)) for item, count in stock.items()}
        ordered = sorted(
            self.projects.values(), key=lambda project: (-project.priority, project.sequence),
        )
        active = [project for project in ordered if self._active(project)]
        # Fund committed construction and whole bills first; waiting projects
        # then keep partial reservations from what is left.  Otherwise a waiting
        # project can hold the last unit a producer of its own missing item
        # needs (live 2026-10-08 and 2026-10-09: quad mall cells waiting on
        # long-handed inserters held the passive-provider chest that the
        # long-handed-inserter cell needed).
        allocations: dict[str, dict[str, int]] = {}
        for project in active:
            if project.state == "constructing":
                allocations[project.project_id] = self._take(project, pool)
        # Fund required claimants and complete producers that can replenish a
        # held item. Other complete bills draw only the unheld finite pool.
        for project in active:
            required_dependency = project.project_id in self.dependency_claimants
            replenishes = project.target_item in self.dependency_budget
            if (project.project_id not in allocations and (required_dependency or replenishes)
                    and all(pool.get(item, 0) >= count for item, count in project.required.items())):
                allocations[project.project_id] = self._take(project, pool)
        # Partial dependencies may claim their finite chest, but leave other
        # components for fully fundable producers before taking partial holds.
        for project in active:
            if project.project_id in self.dependency_claimants and project.project_id not in allocations:
                held = {}
                for item in self.dependency_budget:
                    count = min(project.required.get(item, 0), pool.get(item, 0))
                    if count:
                        held[item] = count
                        pool[item] -= count
                allocations[project.project_id] = held
        claimed: Counter[str] = Counter()
        for project in active:
            if project.project_id in self.dependency_claimants:
                claimed.update(allocations.get(project.project_id, {}))
        for item, count in self.dependency_budget.items():
            pool[item] = max(0, pool.get(item, 0) - max(0, count - claimed.get(item, 0)))
        for project in active:
            if project.project_id not in allocations and all(
                pool.get(item, 0) >= count for item, count in project.required.items()
            ):
                allocations[project.project_id] = self._take(project, pool)
        for project in active:
            allocation = allocations.setdefault(project.project_id, {})
            for item, required in project.required.items():
                count = min(max(0, required - allocation.get(item, 0)), pool.get(item, 0))
                if count:
                    allocation[item] = allocation.get(item, 0) + count
                    pool[item] -= count
        updated: dict[str, MaterialProject] = {}
        for project in ordered:
            if not self._active(project):
                updated[project.project_id] = replace(project, reserved={}, eta_seconds={})
                continue
            reserved = allocations[project.project_id]
            eta: dict[str, float | None] = {}
            complete = True
            for item, required in project.required.items():
                missing = required - reserved.get(item, 0)
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
        claimants: Collection[str] = (),
    ) -> dict[str, int]:
        """Return stock not reserved outside the caller's transaction.

        A coherent build may preflight under one parent name and submit its
        mine/refinery packets under child names.  Those packets still belong
        to one transaction: excluding all of them prevents a retry from
        treating its own reservations as foreign stock and demanding a second
        copy of the same bill.
        """
        self._rebalance(stock)
        allowed = set(claimants)
        if claimant is not None:
            allowed.add(claimant)
        reserved: Counter[str] = Counter()
        for project in self.projects.values():
            if self._active(project) and project.project_id not in allowed:
                reserved.update(project.reserved)
        # A caller that is itself a budget claimant may spend the hold.  The
        # unclaimed remainder stays hidden from every other transaction.
        replenishing_caller = any(
            pid in allowed and project.target_item in self.dependency_budget
            and project.state in {"ready", "constructing"}
            for pid, project in self.projects.items()
        )
        if not allowed.intersection(self.dependency_claimants) and not replenishing_caller:
            claimed: Counter[str] = Counter()
            for project in self.projects.values():
                if (
                    self._active(project)
                    and project.project_id in self.dependency_claimants
                ):
                    claimed.update(project.reserved)
            for item, count in self.dependency_budget.items():
                reserved[item] += max(0, count - claimed.get(item, 0))
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
