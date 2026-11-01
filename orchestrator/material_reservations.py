# Path: orchestrator/material_reservations.py
# Purpose: Persist and allocate exact construction stock to named deterministic projects.

from __future__ import annotations

import json
import os
import hashlib
import tempfile
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Collection, Mapping

from core.optimizer_construction_state import ConstructionBinding, ConstructionScope, ConstructionStateError

_ACTIVE_STATES = frozenset({"planned", "supply_wait", "ready"})
_VALID_STATES = _ACTIVE_STATES | {"constructing", "completed", "failed"}


class MaterialReservationError(RuntimeError):
    """Persisted project identity or material accounting is contradictory."""


def _exclusive_update(method):
    """Serialize cooperating writers and refuse to save a stale loaded state."""
    @wraps(method)
    def update(self, *args, **kwargs):
        with self._writer_lock():
            self._require_fresh()
            if self._current_digest() != self._disk_digest:
                self._stale = True
                raise MaterialReservationError("material ledger changed since it was loaded; reload before writing")
            before = (self.revision, self.version, dict(self.projects), dict(self.bootstrap_supply), self._disk_digest)
            try:
                return method(self, *args, **kwargs)
            except Exception:
                self.revision, self.version, self.projects, self.bootstrap_supply, self._disk_digest = before
                raise
    return update


def _counts(values: Mapping[str, object], label: str, *, allow_zero: bool = False) -> dict[str, int]:
    if not isinstance(values, Mapping):
        raise MaterialReservationError(f"{label} must be an item-count mapping")
    if any(not isinstance(item, str) or not item or type(count) is not int
           or count < (0 if allow_zero else 1) for item, count in values.items()):
        raise MaterialReservationError(f"{label} must contain non-empty item names and "
                                       f"{'non-negative' if allow_zero else 'positive'} integer counts")
    return dict(sorted(values.items()))


def plan_material_bill(plan: Mapping[str, object]) -> dict[str, int]:
    """Count every entity/tile placement needed by a self-contained project."""
    bill: Counter[str] = Counter()
    for phase in plan.get("phases", []):  # type: ignore[union-attr]
        for action in phase.get("actions", []):
            action_type = action.get("action_type")
            if action_type in {"place_entity", "place_ghost", "place_ghost_no_reconcile"}:
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
    construction_binding: ConstructionBinding | None = None

    def __post_init__(self) -> None:
        if not self.project_id:
            raise MaterialReservationError("Material project ID must be non-empty")
        if self.target_item is not None and not self.target_item:
            raise MaterialReservationError("Material target item must be non-empty")
        if self.state not in _VALID_STATES:
            raise MaterialReservationError(f"Unknown material project state {self.state!r}")
        if type(self.hold_until_producing) is not bool:
            raise MaterialReservationError("hold_until_producing must be a boolean")
        if self.construction_binding is not None and (
                not isinstance(self.construction_binding, ConstructionBinding) or not self.hold_until_producing):
            raise MaterialReservationError("bound construction requires a valid binding and held reservation")
        if self.priority < 0 or self.sequence < 1 or self.revision < 1:
            raise MaterialReservationError("Material project ordering must be positive")
        required = _counts(self.required, "required")
        if not required:
            raise MaterialReservationError("Material project requires at least one item")
        reserved = _counts(self.reserved, "reserved", allow_zero=True)
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
        result = {
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
        if self.construction_binding is not None:
            result["construction_binding"] = self.construction_binding.to_dict()
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> MaterialProject:
        try:
            raw_binding = payload.get("construction_binding")
            if "construction_binding" in payload and raw_binding is None:
                raise MaterialReservationError("Malformed construction binding")
            binding = None
            if raw_binding is not None:
                if (not isinstance(raw_binding, Mapping)
                        or set(raw_binding) != {"scope", "project_id", "transaction_id", "plan_hash"}
                        or not isinstance(raw_binding["scope"], Mapping)
                        or set(raw_binding["scope"]) != {"instance_id", "manifest_digest", "surface", "force"}):
                    raise MaterialReservationError("Malformed construction binding")
                binding = ConstructionBinding(scope=ConstructionScope(**raw_binding["scope"]),
                                              project_id=raw_binding["project_id"],
                                              transaction_id=raw_binding["transaction_id"],
                                              plan_hash=raw_binding["plan_hash"])
            return cls(
                project_id=str(payload["project_id"]),
                target_item=(
                    str(payload["target_item"])
                    if payload.get("target_item") is not None else None
                ),
                state=str(payload["state"]),
                required=_counts(payload["required"], "required"),  # type: ignore[arg-type]
                reserved=_counts(payload["reserved"], "reserved", allow_zero=True),  # type: ignore[arg-type]
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
                hold_until_producing=payload.get("hold_until_producing", False),
                revision=int(payload["revision"]),
                construction_binding=binding,
            )
        except MaterialReservationError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, ConstructionStateError) as error:
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
        self.version = 1
        self.projects: dict[str, MaterialProject] = {}
        self.bootstrap_supply: dict[str, dict[str, dict[str, int]]] = {}
        self._disk_digest: str | None = None
        self._stale = False
        self._mutex = threading.RLock()
        self._load()

    def _require_fresh(self) -> None:
        if self._stale:
            raise MaterialReservationError("material ledger is stale; reload before use")

    def _current_digest(self) -> str | None:
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise MaterialReservationError("cannot read material ledger for update") from error

    @contextmanager
    def _writer_lock(self):
        """Lock a stable sibling file; older non-cooperating writers are not covered."""
        with self._mutex:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(f".{self.path.name}.lock")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(lock_path, flags, 0o600)
            except OSError as error:
                raise MaterialReservationError("cannot open material ledger lock") from error
            with os.fdopen(fd, "r+b") as lock:
                try:
                    if os.name == "nt":
                        import msvcrt
                        lock.seek(0, os.SEEK_END)
                        if lock.tell() == 0:
                            lock.write(b"\0")
                            lock.flush()
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                except (OSError, ImportError) as error:
                    raise MaterialReservationError("cannot lock material ledger") from error
                try:
                    yield
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def revision_fence(self, expected_revision: int):
        """Hold the cooperative writer lock while this loaded state is current.

        Yields the persisted ledger SHA-256. Cooperating writers in other
        processes block until exit; writers that ignore the lock are not
        excluded. Do not call this ledger's mutators inside the fence.
        """
        if type(expected_revision) is not int or expected_revision < 0:
            raise MaterialReservationError("expected ledger revision must be a non-negative integer")
        with self._writer_lock():
            self._require_fresh()
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                raw = None
            except OSError as error:
                raise MaterialReservationError("cannot read material ledger for fence") from error
            digest = None if raw is None else hashlib.sha256(raw).hexdigest()
            if digest is None or digest != self._disk_digest:
                self._stale = True
                raise MaterialReservationError("material ledger changed since it was loaded; reload before use")
            # Unchanged bytes do not prove the loaded objects are unchanged: project
            # mappings are mutable. Callers rely on in-memory funding, so it must match disk
            # (normalized through the loader so legacy default-omitting records compare equal).
            persisted = json.loads(raw)
            persisted_projects = sorted((MaterialProject.from_dict(item) for item in persisted.get("projects", ())),
                                        key=lambda value: value.sequence)
            if (persisted.get("revision") != self.revision
                    or [project.to_dict() for project in persisted_projects] != self._project_payload()):
                self._stale = True
                raise MaterialReservationError("loaded material ledger differs from its persisted state; reload")
            if self.revision != expected_revision:
                raise MaterialReservationError("material ledger revision differs from the expected revision")
            yield digest

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = self.path.read_bytes()
            payload = json.loads(raw)
            version = payload.get("version")
            if type(version) is not int or version not in {1, 2}:
                raise MaterialReservationError("Unsupported material ledger version")
            if (
                payload.get("episode_id") != self.episode_id
                or payload.get("surface") != self.surface
                or payload.get("force") != self.force
            ):
                raise MaterialReservationError("Material ledger scope does not match this run")
            if type(payload.get("revision")) is not int or payload["revision"] < 0:
                raise MaterialReservationError("Material ledger revision must be a non-negative integer")
            self.revision = payload["revision"]
            raw_supply = payload.get("bootstrap_supply", {})
            if not isinstance(raw_supply, dict):
                raise MaterialReservationError("Bootstrap supply must be an object")
            self.bootstrap_supply = {
                str(profile): {
                    "targets": _counts(record["targets"], "bootstrap targets"),
                    "inserted": {item: count for item, count in
                                 _counts(record.get("inserted", {}), "bootstrap inserted", allow_zero=True).items()
                                 if count > 0},
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
        bound_count = sum(project.construction_binding is not None for project in projects)
        if (version == 1 and bound_count) or (version == 2 and not bound_count):
            raise MaterialReservationError("Material ledger version does not match bound projects")
        if version == 2 and any(project.project_id.startswith("optimizer:")
                                and project.construction_binding is None for project in projects):
            raise MaterialReservationError("Optimizer project is missing its construction binding")
        if any(project.construction_binding is not None and (
                project.construction_binding.scope.surface != self.surface
                or project.construction_binding.scope.force != self.force) for project in projects):
            raise MaterialReservationError("Bound project scope does not match ledger")
        self.projects = {project.project_id: project for project in projects}
        self.version = version
        self._disk_digest = hashlib.sha256(raw).hexdigest()

    def _project_payload(self) -> list[dict]:
        return [project.to_dict() for project in sorted(self.projects.values(), key=lambda value: value.sequence)]

    def _save(self) -> None:
        bound_count = sum(project.construction_binding is not None for project in self.projects.values())
        if self.version == 2 and not bound_count:
            raise MaterialReservationError("Cannot downgrade a bound material ledger")
        version = 2 if bound_count else 1
        if version == 2 and any(project.project_id.startswith("optimizer:")
                                and project.construction_binding is None for project in self.projects.values()):
            raise MaterialReservationError("Optimizer project is missing its construction binding")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version,
            "episode_id": self.episode_id,
            "surface": self.surface,
            "force": self.force,
            "revision": self.revision,
            "bootstrap_supply": self.bootstrap_supply,
            "projects": self._project_payload(),
        }
        content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{self.path.name}.", suffix=".tmp",
                                             dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.version = version
        self._disk_digest = hashlib.sha256(content).hexdigest()

    def bootstrap_supply_applied(self, profile: str) -> bool:
        with self._mutex:
            self._require_fresh()
            return profile in self.bootstrap_supply

    @_exclusive_update
    def record_bootstrap_supply(
        self, profile: str, targets: Mapping[str, int], inserted: Mapping[str, int],
    ) -> None:
        if not profile:
            raise MaterialReservationError("Bootstrap profile must be non-empty")
        normalized_targets = _counts(targets, "bootstrap targets")
        normalized_inserted = {item: count for item, count in
                               _counts(inserted, "bootstrap inserted", allow_zero=True).items()
                               if count > 0}
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
        with self._mutex:
            self._require_fresh()
            project = self.projects.get(project_id)
            return project is not None and self._active(project)

    def _allocation_view(self, stock: Mapping[str, int]) -> dict[str, MaterialProject]:
        """Compute allocations without changing or persisting this ledger."""
        pool = _counts(stock, "stock", allow_zero=True)
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
        return updated

    def _rebalance(self, stock: Mapping[str, int]) -> None:
        self.projects.update(self._allocation_view(stock))

    @_exclusive_update
    def declare(
        self, project_id: str, required: Mapping[str, int], stock: Mapping[str, int], *,
        target_item: str | None = None,
        source_producers: Mapping[str, str | None] | None = None,
        expected_rates: Mapping[str, float | None] | None = None,
        priority: int = 50,
        hold_until_producing: bool = False,
        construction_binding: ConstructionBinding | None = None,
    ) -> MaterialProject:
        if type(hold_until_producing) is not bool:
            raise MaterialReservationError("hold_until_producing must be a boolean")
        bill = _counts(required, "required")
        stock = _counts(stock, "stock", allow_zero=True)
        sources = source_producers or {}
        rates = expected_rates or {}
        existing = self.projects.get(project_id)
        if construction_binding is None and self.version == 2 and project_id.startswith("optimizer:"):
            raise MaterialReservationError("Optimizer project requires a construction binding")
        if construction_binding is not None:
            if not isinstance(construction_binding, ConstructionBinding) or not hold_until_producing:
                raise MaterialReservationError("bound construction requires a valid binding and held reservation")
            scope = construction_binding.scope
            if scope.surface != self.surface or scope.force != self.force:
                raise MaterialReservationError("construction binding scope does not match ledger")
            if any(project.project_id.startswith("optimizer:") and project.construction_binding is None
                   for project in self.projects.values()):
                raise MaterialReservationError("Existing optimizer project is missing its construction binding")
        if existing is not None and (existing.construction_binding is not None or construction_binding is not None):
            if (existing.construction_binding != construction_binding or existing.required != bill
                    or existing.state in {"completed", "failed"}):
                raise MaterialReservationError("bound construction identity, bill, or state conflicts with existing project")
        sequence = existing.sequence if existing is not None else (
            max((project.sequence for project in self.projects.values()), default=0) + 1
        )
        revision = (existing.revision + 1) if existing is not None else 1
        keep_constructing = bool(
            existing is not None
            and existing.state == "constructing"
            # Transient submission names (for example power_bridge) are
            # reused for later builds. Their old consumed bill cannot fund
            # another build; only an explicitly held producer stays active.
            and self._active(existing)
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
            construction_binding=construction_binding,
        )
        self.projects[project_id] = project
        self._rebalance(stock)
        self.revision += 1
        self._save()
        return self.projects[project_id]

    def stock_targets(self) -> dict[str, int]:
        with self._mutex:
            self._require_fresh()
            totals: Counter[str] = Counter()
            for project in self.projects.values():
                if self._active(project):
                    totals.update(project.required)
            return dict(sorted(totals.items()))

    @_exclusive_update
    def shortage_targets(
        self, project_id: str, stock: Mapping[str, int],
    ) -> dict[str, int]:
        if project_id not in self.projects:
            raise MaterialReservationError(f"Unknown material project {project_id!r}")
        before = dict(self.projects)
        self._rebalance(stock)
        if self.projects != before:
            self.revision += 1
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

    @_exclusive_update
    def refresh_isolated_bound_project(
        self, project_id: str, stock: Mapping[str, int], *, binding: ConstructionBinding,
        required: Mapping[str, int], target_item: str, expected_revision: int,
    ) -> MaterialProject:
        """Rebalance one isolated held project from a caller-asserted stock snapshot.

        The snapshot is not authenticated or made fresh by this method. It
        refuses other active/constructing projects because ledger entries have
        no network attribution, and it never authorizes dispatch or releases a
        construction hold. Cooperating writers are serialized by the ledger.
        """
        if type(expected_revision) is not int or expected_revision < 0 or self.revision != expected_revision:
            raise MaterialReservationError("material ledger revision changed before isolated refresh")
        if not isinstance(binding, ConstructionBinding) or not isinstance(target_item, str) or not target_item:
            raise MaterialReservationError("isolated refresh requires an explicit binding and target")
        bill = _counts(required, "required")
        available = _counts(stock, "stock", allow_zero=True)
        if not bill or set(available) != set(bill):
            raise MaterialReservationError("isolated refresh requires a complete stock count for the exact bill")
        project = self.projects.get(project_id)
        if (project is None or project.construction_binding != binding or project.required != bill
                or project.target_item != target_item or not project.hold_until_producing
                or project.state not in {"ready", "supply_wait"}):
            raise MaterialReservationError("isolated refresh requires the matching active held project")
        if any(other.project_id != project_id and (self._active(other) or other.state == "constructing")
               for other in self.projects.values()):
            raise MaterialReservationError("isolated refresh cannot rebalance other active or constructing projects")
        refreshed = self._allocation_view(available)[project_id]
        if refreshed != project:
            self.projects[project_id] = refreshed
            self.revision += 1
            self._save()
        return self.projects[project_id]

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
        with self._mutex:
            self._require_fresh()
            allocations = self._allocation_view(stock)
            allowed = set(claimants)
            if claimant is not None:
                allowed.add(claimant)
            reserved: Counter[str] = Counter()
            for project in allocations.values():
                if self._active(project) and project.project_id not in allowed:
                    reserved.update(project.reserved)
            return {
                item: max(0, int(count) - reserved.get(item, 0))
                for item, count in stock.items()
            }

    def required_stock(self, item: str) -> int:
        return self.stock_targets().get(item, 0)

    @_exclusive_update
    def mark_constructing(self, project_id: str, stock: Mapping[str, int]) -> None:
        project = self.projects.get(project_id)
        if project is None:
            return
        stock = _counts(stock, "stock", allow_zero=True)
        self.projects[project_id] = replace(
            project, state="constructing", revision=project.revision + 1,
        )
        self._rebalance(stock)
        self.revision += 1
        self._save()

    @_exclusive_update
    def complete(self, project_id: str) -> None:
        project = self.projects.get(project_id)
        if project is not None and project.construction_binding is not None:
            raise MaterialReservationError("bound construction needs a verified release path")
        if project is None or project.state == "completed":
            return
        self.projects[project_id] = replace(
            project, state="completed", reserved={}, eta_seconds={},
            revision=project.revision + 1,
        )
        self.revision += 1
        self._save()

    @_exclusive_update
    def fail(self, project_id: str) -> None:
        project = self.projects.get(project_id)
        if project is not None and project.construction_binding is not None:
            raise MaterialReservationError("bound construction needs a verified release path")
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
