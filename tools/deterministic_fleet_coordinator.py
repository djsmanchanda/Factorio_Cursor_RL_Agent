# Path: tools/deterministic_fleet_coordinator.py
# Purpose: Persist and dispatch isolated deterministic checkpoint-fleet lanes.
"""File-backed coordinator for the deterministic checkpoint fleet.

The dashboard writes intent to ``checkpoint-fleet.json``.  This module is the
process-safe consumer of that intent: it pins checkpoint generations, prepares
isolated lanes, allocates ports, starts/stops the two lane services when
explicitly asked, and records structured runner results.  Importing the
module, constructing a coordinator, or asking for status never starts a live
process.

The coordinator deliberately does not infer success from a missing PID or a
log phrase.  A runner must publish a structured fleet result in
``episode/fleet-result.json`` (or the equivalent ``fleet_result`` manifest
field).  A runner which disappears without that evidence is recorded as an
infrastructure error with its log tail.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.checkpoint_catalog import CheckpointCatalog, classify_commit_age
from tools.milestone_checkpoint import MilestoneCheckpointError, verify as verify_checkpoint_bundle
from tools.deterministic_fleet_runtime import (
    DEFAULT_GAME_PORT,
    DEFAULT_RCON_PORT,
    DeterministicFleetRuntime,
    FleetRuntimeError,
    LanePlan,
    MAX_ACTIVE_SERVERS,
    resolve_clean_commit,
)
from tools.runner_process import running_runner_pid


STATE_SCHEMA_VERSION = "1.0.0"
MAX_LOG_TAIL = 100
MAX_QUEUE = 4096
MAX_RUNS = 4096
DEFAULT_POLL_INTERVAL = 5.0
HEARTBEAT_SCHEMA_VERSION = "1.0.0"
TIMING_SAMPLE_LIMIT = 20
SLOWDOWN_FACTOR = 2.5


class FleetCoordinatorError(RuntimeError):
    """A fleet intent cannot be consumed safely."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe(value: object, field: str, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FleetCoordinatorError(f"{field} must be a non-empty string")
    return value.strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FleetCoordinatorError(f"unreadable JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise FleetCoordinatorError(f"JSON object required: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _empty_state() -> dict[str, Any]:
    checkpoints = (
        ("C0", "Base"), ("C1", "Starter mall"),
        ("C2", "Iron + copper rollout"), ("C3", "Stone rollout"),
        ("C4", "First plastic"), ("C5", "Stable plastic"),
        ("C6", "Advanced circuit consumer"),
    )
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "registry_version": "1",
        "settings": {
            "paused": False, "auto_run_commits": True,
            "active_cap": MAX_ACTIVE_SERVERS, "active_servers": 0,
        },
        "checkpoints": [
            {"id": ident, "name": label, "generation": None,
             "starred": ident == "C6", "creator_commit": None, "stale": False}
            for ident, label in checkpoints
        ],
        "commits": [], "queue": [], "runs": [], "frontier_runs": [],
        "timing_history": {}, "ranked_commits": [],
        "updated_at": None,
        "api": {
            "version": "1.0", "facade": False,
            "coordinator_connected": False, "mutations": "coordinator",
        },
        "helper": {
            "available": False, "default_checkpoint": "Cn", "selected_runs": 0,
        },
        "promotion_candidates": [], "promotion_intents": [],
        "coordinator": {
            "dispatch_count": 0, "ordinary_since_middle": 0,
            "next_suite_order": 0, "auto_seen_commits": [],
        },
    }


class _DefaultExecutor:
    def __call__(self, command: Sequence[str]) -> Any:
        raise FleetCoordinatorError(
            "lifecycle execution is disabled; use an injected executor or explicit CLI --execute"
        )


def _subprocess_executor(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), text=True, capture_output=True, check=True)


class CheckpointFleetCoordinator:
    """Consume dashboard intents and manage persisted fleet lanes.

    ``command_executor`` is intentionally injectable.  It receives one
    command list for each lifecycle action and may return a
    ``CompletedProcess``, an integer status, or ``None``.  The default never
    executes a command.
    """

    def __init__(
        self,
        server_data: Path,
        *,
        repo_root: Path | None = None,
        catalog_path: Path | None = None,
        runtime: DeterministicFleetRuntime | None = None,
        command_executor: Callable[[Sequence[str]], Any] | None = None,
        game_port_base: int = DEFAULT_GAME_PORT,
        rcon_port_base: int = DEFAULT_RCON_PORT,
        max_active: int = MAX_ACTIVE_SERVERS,
        mission_id: str = "default",
        factorio_version: str = "unknown",
        helper_api_url: str | None = None,
    ) -> None:
        if max_active != MAX_ACTIVE_SERVERS:
            raise FleetCoordinatorError("checkpoint fleet active cap is fixed at eight servers")
        self.server_data = Path(server_data).resolve()
        self.root = self.server_data / "checkpoint-fleet"
        self.state_path = self.root / "checkpoint-fleet.json"
        self.lock_path = self.root / ".coordinator.lock"
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.catalog_path = Path(catalog_path).resolve() if catalog_path else self.root / "registry.json"
        self.runtime = runtime or DeterministicFleetRuntime(
            repo_root=self.repo_root, fleet_root=self.root / "runtime",
            game_port_base=game_port_base, rcon_port_base=rcon_port_base,
            max_servers=MAX_ACTIVE_SERVERS,
        )
        self.command_executor = command_executor or _DefaultExecutor()
        self.max_active = MAX_ACTIVE_SERVERS
        self.mission_id = _safe(mission_id, "mission_id")
        self.factorio_version = _safe(factorio_version, "factorio_version")
        self.helper_api_url = helper_api_url
        self.heartbeat_path = self.root / "coordinator-heartbeat.json"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._load_unlocked()
            try:
                yield state
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return _empty_state()
        state = _read_json(self.state_path)
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise FleetCoordinatorError("checkpoint fleet state has an unsupported schema")
        for key, expected in (("settings", dict), ("checkpoints", list), ("commits", list),
                              ("queue", list), ("runs", list), ("frontier_runs", list)):
            if not isinstance(state.get(key), expected):
                raise FleetCoordinatorError(f"checkpoint fleet state has invalid {key}")
        if len(state["queue"]) > MAX_QUEUE or len(state["runs"]) > MAX_RUNS:
            raise FleetCoordinatorError("checkpoint fleet state exceeds its bounded history")
        state.setdefault("coordinator", _empty_state()["coordinator"])
        state.setdefault("timing_history", {})
        state.setdefault("ranked_commits", [])
        state.setdefault("promotion_candidates", [])
        state.setdefault("promotion_intents", [])
        return state

    def _save_unlocked(self, state: dict[str, Any]) -> None:
        state["settings"]["active_servers"] = sum(
            item.get("status") == "running" for item in state["runs"]
        )
        state["updated_at"] = _now()
        _atomic_json(self.state_path, state)

    def status(self) -> dict[str, Any]:
        with self._locked() as state:
            return json.loads(json.dumps(self._project_state(state)))

    def _project_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return the one state shape shared by the coordinator and dashboard.

        Registry metadata is projected into the state response without writing
        it back.  This keeps a dashboard refresh read-only while allowing the
        registry to be initialized or extended independently of active runs.
        """
        result = json.loads(json.dumps(state))
        api = result.setdefault("api", {})
        heartbeat = self._read_heartbeat()
        api.update({
            "version": "1.0", "facade": False,
            "coordinator_connected": bool(heartbeat and heartbeat.get("status") == "running"),
            "mutations": "coordinator",
            "heartbeat": heartbeat,
        })
        helper = result.setdefault("helper", {
            "available": False, "default_checkpoint": "Cn", "selected_runs": 0,
        })
        helper["available"] = bool(self.helper_api_url) or bool(helper.get("available"))
        helper["selected_runs"] = sum(
            bool(item.get("helper")) for item in [*result.get("queue", []), *result.get("runs", [])]
            if isinstance(item, dict)
        )
        try:
            catalog = self._catalog()
        except FleetCoordinatorError:
            catalog = None
        if catalog is not None:
            selected_commits = [
                str(item.get("commit")) for item in result.get("commits", [])
                if isinstance(item, Mapping) and item.get("commit")
            ]
            newest_commit = selected_commits[-1] if selected_commits else None
            current = {item.get("id"): item for item in result.get("checkpoints", []) if isinstance(item, dict)}
            projected = []
            for item in catalog.checkpoints:
                generation = catalog.default_generation(item["id"])
                existing = current.get(item["id"], {})
                age_status = (
                    classify_commit_age(
                        str(generation.get("creator_commit", "")), newest_commit,
                        resolver=self._ancestor_distance,
                    )
                    if generation and newest_commit else "unverifiable"
                )
                projected.append({
                    "id": item["id"], "name": item.get("label", item["id"]),
                    "generation": generation.get("path") if generation else None,
                    "generation_id": generation.get("generation_id") if generation else None,
                    "starred": bool(generation and generation.get("provisional")),
                    "creator_commit": generation.get("creator_commit") if generation else existing.get("creator_commit"),
                    "stale": age_status == "stale",
                    "age_status": age_status,
                    "predicate_version": item.get("predicate_version"),
                })
            result["checkpoints"] = projected
            result["registry_version"] = str(catalog.payload.get("registry_revision", 1))
        return result

    def _ancestor_distance(self, creator_commit: str, newest_commit: str) -> int | None:
        """Return commit distance, or ``None`` for a known divergent input."""
        try:
            ancestor = subprocess.run(
                ["git", "-C", str(self.repo_root), "merge-base", "--is-ancestor",
                 creator_commit, newest_commit],
                check=False, capture_output=True,
            )
            if ancestor.returncode == 1:
                return None
            if ancestor.returncode != 0:
                raise FleetCoordinatorError("unable to compare checkpoint commit ancestry")
            return int(self._git("rev-list", "--count", f"{creator_commit}..{newest_commit}"))
        except (OSError, ValueError, FleetCoordinatorError):
            raise

    def _read_heartbeat(self) -> dict[str, Any] | None:
        if not self.heartbeat_path.is_file():
            return None
        try:
            heartbeat = _read_json(self.heartbeat_path)
        except FleetCoordinatorError:
            return {"status": "invalid"}
        if heartbeat.get("schema_version") != HEARTBEAT_SCHEMA_VERSION:
            return {"status": "incompatible"}
        return heartbeat

    def _write_heartbeat(self, *, status: str, execute: bool, error: str | None = None) -> None:
        payload: dict[str, Any] = {
            "schema_version": HEARTBEAT_SCHEMA_VERSION,
            "status": status, "pid": os.getpid(), "execute": bool(execute),
            "updated_at": _now(),
        }
        if error:
            payload["error"] = error[:500]
        _atomic_json(self.heartbeat_path, payload)

    def _catalog(self) -> CheckpointCatalog:
        if not self.catalog_path.is_file():
            raise FleetCoordinatorError(f"checkpoint catalog is missing: {self.catalog_path}")
        try:
            return CheckpointCatalog.load(self.catalog_path)
        except (OSError, ValueError) as error:
            raise FleetCoordinatorError(str(error)) from error

    def _timeline(self, state: Mapping[str, Any], catalog: CheckpointCatalog | None = None) -> list[str]:
        if catalog is not None:
            return [str(item["id"]) for item in catalog.checkpoints]
        return [str(item["id"]) for item in state.get("checkpoints", []) if isinstance(item, dict) and item.get("id")]

    def _runnable_timeline(self, state: Mapping[str, Any], catalog: CheckpointCatalog) -> list[str]:
        """Return only checkpoints with a promoted generation.

        A freshly initialized catalog intentionally has definitions for future
        milestones but only C0 is runnable.  Default suites must not enqueue
        lanes which cannot yet have an input save.
        """
        return [
            str(item["id"])
            for item in catalog.checkpoints
            if catalog.default_generation(str(item["id"])) is not None
        ]

    @staticmethod
    def _queue_kind(item: Mapping[str, Any], timeline: Sequence[str]) -> str:
        kind = str(item.get("kind") or "default")
        if kind in {"verify", "verification"}:
            return "verification"
        checkpoint = item.get("checkpoint")
        return "endpoint" if checkpoint in {timeline[0], timeline[-1]} else "middle"

    def _resolve_generation(
        self, catalog: CheckpointCatalog, checkpoint_id: str, generation_id: str | None = None,
    ) -> tuple[dict[str, Any], Path]:
        try:
            generation = (
                next(item for item in catalog.checkpoint(checkpoint_id)["generations"]
                     if item.get("generation_id") == generation_id)
                if generation_id else catalog.default_generation(checkpoint_id)
            )
        except (KeyError, StopIteration) as error:
            raise FleetCoordinatorError(f"checkpoint generation is missing: {checkpoint_id}") from error
        if not generation:
            raise FleetCoordinatorError(f"checkpoint has no default generation: {checkpoint_id}")
        raw = Path(str(generation.get("path", "")))
        bundle = raw if raw.is_absolute() else (catalog.path.parent / raw)
        bundle = bundle.resolve()
        if not bundle.is_dir() or not bundle.is_relative_to(catalog.path.parent.resolve()):
            raise FleetCoordinatorError(f"checkpoint bundle is missing or escapes catalog root: {bundle}")
        return generation, bundle

    def _candidate_generation(
        self, catalog: CheckpointCatalog, candidate: Mapping[str, Any], checkpoint_id: str,
    ) -> tuple[dict[str, Any], Path]:
        """Resolve a promotion candidate only through the catalog registry."""
        generation_id = candidate.get("generation_id")
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise FleetCoordinatorError("promotion candidate is not backed by a catalog generation")
        generation, bundle = self._resolve_generation(catalog, checkpoint_id, generation_id)
        if candidate.get("path") and str(candidate["path"]) != str(bundle):
            # Relative paths are the canonical state representation; accepting
            # a stale absolute alias would make the promotion record ambiguous.
            candidate_path = Path(str(candidate["path"]))
            candidate_path = (catalog.path.parent / candidate_path).resolve() if not candidate_path.is_absolute() else candidate_path.resolve()
            if candidate_path != bundle:
                raise FleetCoordinatorError("promotion candidate path does not match its catalog generation")
        return generation, bundle

    def _resolve_generation_selection(
        self, catalog: CheckpointCatalog, checkpoint_id: str, *,
        generation_id: object = None, save_id: object = None,
        candidates: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        """Resolve custom UI input to an existing generation ID only."""
        selected = generation_id if generation_id not in (None, "") else save_id
        if not isinstance(selected, str) or not selected.strip():
            raise FleetCoordinatorError("custom generation selection is empty")
        selected = selected.strip()
        checkpoint = catalog.checkpoint(checkpoint_id)
        for generation in checkpoint["generations"]:
            values = {str(generation.get("generation_id")), str(generation.get("save_name")), str(generation.get("path"))}
            raw_path = Path(str(generation.get("path", "")))
            values.add(str((catalog.path.parent / raw_path).resolve()))
            if selected in values:
                return str(generation["generation_id"])
        candidate = next((item for item in candidates
                          if isinstance(item, Mapping) and item.get("candidate_id") == selected), None)
        if candidate is not None and str(candidate.get("checkpoint")) == checkpoint_id:
            return str(candidate.get("generation_id"))
        raise FleetCoordinatorError("custom save/generation is not a coordinator-listed catalog input")

    def _capture_bundle(
        self, catalog: CheckpointCatalog, checkpoint_id: str, captured: str | Path,
        *, creator_commit: str, source_run_id: str | None,
        provisional: bool,
    ) -> tuple[dict[str, Any], Path] | None:
        """Register a trusted milestone bundle and return its catalog generation.

        Captures are produced inside a lane root.  They are copied into the
        coordinator-owned catalog tree before being advertised to the UI.  A
        path supplied by a runner is never itself treated as a generation.
        """
        source = Path(str(captured)).expanduser()
        if not source.is_absolute():
            source = (self.root / source).resolve()
        else:
            source = source.resolve()
        if not source.is_dir() or source.is_symlink():
            return None
        catalog_root = catalog.path.parent.resolve()
        # Lane captures may only come from this coordinator's private tree.
        # Already registered catalog descendants are allowed for idempotent
        # refreshes, but arbitrary filesystem paths are rejected.
        if not source.is_relative_to(self.root.resolve()):
            return None
        manifest_path = source / "checkpoint.json"
        try:
            manifest = _read_json(manifest_path)
        except FleetCoordinatorError:
            return None
        if str(manifest.get("checkpoint_id")) != checkpoint_id:
            return None
        generation_id = str(manifest.get("generation_id") or "").strip()
        if not generation_id:
            return None
        # A milestone bundle carries the complete hash/provenance contract.
        # Do not register a world ZIP or a hand-written directory as a save.
        if manifest.get("kind") != "milestone-regression" or manifest.get("segment_regression_eligible") is not True:
            return None
        try:
            verify_checkpoint_bundle(source, expected_checkpoint_id=checkpoint_id)
        except (MilestoneCheckpointError, OSError, ValueError):
            return None
        destination = (catalog_root / "bundles" / checkpoint_id / generation_id).resolve()
        if not destination.is_relative_to(catalog_root) or destination == catalog_root:
            return None
        if source != destination:
            if destination.exists():
                return None
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
        existing = next(
            (item for item in catalog.checkpoint(checkpoint_id)["generations"]
             if item.get("generation_id") == generation_id), None,
        )
        if existing is None:
            existing = catalog.add_generation(checkpoint_id, {
                "generation_id": generation_id,
                "origin_checkpoint_id": checkpoint_id,
                "path": str(destination.relative_to(catalog_root)),
                "creator_commit": str(creator_commit).lower(),
                "created_at": str(manifest.get("created_at") or _now()),
                "provisional": bool(provisional),
                "pinned": True,
                "active_input": False,
                "coordinator_pinned": True,
                "source_run_id": source_run_id,
                "save_name": manifest.get("save_name"),
            })
            catalog.save()
        elif str(existing.get("path")) != str(destination.relative_to(catalog_root)):
            raise FleetCoordinatorError("catalog generation path conflicts with captured bundle")
        return existing, destination

    def _mod_sources_unchanged(self, creator_commit: str, candidate_commit: str) -> bool:
        """Return whether the candidate changes neither deployed Lua tree.

        Checkpoint replays may be auto-approved when only Python/control code
        changed.  A failed Git query is deliberately treated as changed so a
        missing or malformed compatibility record can never widen authority.
        """
        if creator_commit.lower() == candidate_commit.lower():
            return True
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_root), "diff", "--quiet",
                 f"{creator_commit}^{{commit}}", f"{candidate_commit}^{{commit}}",
                 "--", "factorio_mod", "factorio_training_lab"],
                check=False, capture_output=True, text=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    def _compatibility(
        self, generation: Mapping[str, Any], bundle: Path, *, candidate_commit: str,
    ) -> dict[str, Any]:
        supplied = generation.get("compatibility_manifest")
        path = Path(str(supplied)) if supplied else bundle / "compatibility.json"
        if not path.is_absolute():
            path = bundle / path
        path = path.resolve()
        if not path.is_relative_to(bundle.resolve()):
            raise FleetCoordinatorError("checkpoint compatibility manifest escapes its bundle")
        creator_commit = str(generation.get("creator_commit", "")).lower()
        if not creator_commit:
            raise FleetCoordinatorError("checkpoint generation has no creator commit")
        if not path.is_file():
            if not self._mod_sources_unchanged(creator_commit, candidate_commit):
                raise FleetCoordinatorError(f"checkpoint compatibility manifest is missing: {path}")
            # Python-only replays have no per-generation file to carry a
            # decision.  Synthesize the same explicit record the runtime
            # validator requires, bound to this exact candidate revision.
            try:
                checkpoint_manifest = _read_json(bundle / "checkpoint.json")
                predicate_version = str(
                    (checkpoint_manifest.get("predicate") or {}).get("version") or "unknown"
                )
            except FleetCoordinatorError:
                predicate_version = "unknown"
            compatibility = {
                "compatible": True, "checkpoint_id": generation.get("origin_checkpoint_id"),
                "creator_commit": creator_commit, "candidate_commit": candidate_commit,
                "checked_at": _now(), "predicate_version": predicate_version,
                "decision": "automatic-no-mod-diff",
            }
            return compatibility
        compatibility = _read_json(path)
        if compatibility.get("compatible") is not True:
            raise FleetCoordinatorError("checkpoint compatibility is not explicitly approved")
        manifest_candidate = str(compatibility.get("candidate_commit", "")).lower()
        if manifest_candidate == "*":
            if compatibility.get("candidate_commit_policy") != "any":
                raise FleetCoordinatorError("wildcard checkpoint compatibility needs an any-commit policy")
            if not self._mod_sources_unchanged(creator_commit, candidate_commit):
                raise FleetCoordinatorError("wildcard compatibility cannot approve changed Factorio code")
            compatibility = dict(compatibility)
            compatibility["candidate_commit"] = candidate_commit
        elif manifest_candidate != candidate_commit.lower():
            if not self._mod_sources_unchanged(creator_commit, candidate_commit):
                raise FleetCoordinatorError("checkpoint compatibility is for a different candidate commit")
            compatibility = dict(compatibility)
            compatibility.update({
                "candidate_commit": candidate_commit,
                "checked_at": _now(),
                "decision": "automatic-no-mod-diff",
            })
        return compatibility

    def _resolve_commit(self, value: str) -> str:
        try:
            return resolve_clean_commit(self.repo_root, value).lower()
        except FleetRuntimeError as error:
            raise FleetCoordinatorError(str(error)) from error

    @staticmethod
    def _mission_command(
        value: str, *, require_explicit: bool = False,
    ) -> tuple[str | None, str | None]:
        """Translate a custom mission selector into a bounded runner command."""
        mission = str(value or "default").strip()
        if mission == "default":
            return None, None
        mode, separator, target = mission.partition(":")
        if separator and mode in {"produce", "research"}:
            return mode, _safe(target, "mission target", 160)
        if require_explicit:
            raise FleetCoordinatorError(
                "mission override must be default, produce:<item>, or research:<technology>"
            )
        return None, None

    def _active_runs(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [run for run in state["runs"] if run.get("status") == "running"]

    def _used_slots(self, state: Mapping[str, Any]) -> set[int]:
        return {int(run["slot"]) for run in self._active_runs(state) if isinstance(run.get("slot"), int)}

    def _next_slot(self, state: Mapping[str, Any]) -> int | None:
        used = self._used_slots(state)
        return next((slot for slot in range(self.max_active) if slot not in used), None)

    def _suite_order(self, state: Mapping[str, Any], suite_id: str) -> int:
        order = state["coordinator"].setdefault("suite_orders", {})
        if suite_id not in order:
            next_order = int(state["coordinator"].get("next_suite_order", 0)) + 1
            state["coordinator"]["next_suite_order"] = next_order
            order[suite_id] = next_order
        return int(order[suite_id])

    def _queue_candidates(self, state: dict[str, Any], timeline: Sequence[str]) -> list[dict[str, Any]]:
        pending = [item for item in state["queue"] if item.get("status", "queued") == "queued"]
        if not pending:
            return []
        for item in pending:
            item.setdefault("waiting_opportunities", 0)
        newest_suite = max(pending, key=lambda item: self._suite_order(state, str(item.get("suite_id", "")))).get("suite_id")
        due_verification = [item for item in pending if self._queue_kind(item, timeline) == "verification"
                            and int(item.get("waiting_opportunities", 0)) >= 5]
        if due_verification:
            return sorted(due_verification, key=lambda item: int(item.get("enqueue_order", 0) or 0))
        endpoints = [item for item in pending if item.get("suite_id") == newest_suite
                     and self._queue_kind(item, timeline) == "endpoint"]
        if endpoints:
            return sorted(endpoints, key=lambda item: timeline.index(str(item["checkpoint"])))
        verification = [
            item for item in pending
            if self._queue_kind(item, timeline) == "verification"
        ]
        if verification:
            return sorted(
                verification,
                key=lambda item: int(item.get("enqueue_order", 0) or 0),
            )
        middle = [item for item in pending if item.get("suite_id") == newest_suite
                  and self._queue_kind(item, timeline) == "middle"]
        since_middle = int(state["coordinator"].get("ordinary_since_middle", 0))
        if middle and since_middle >= 5:
            return sorted(middle, key=lambda item: int(item.get("enqueue_order", 0) or 0))
        older = [item for item in pending if item.get("suite_id") != newest_suite]
        if older:
            return sorted(older, key=lambda item: int(item.get("enqueue_order", 0) or 0))
        return sorted(pending, key=lambda item: int(item.get("enqueue_order", 0) or 0))

    def _select_queue_item(self, state: dict[str, Any], timeline: Sequence[str]) -> dict[str, Any] | None:
        candidates = self._queue_candidates(state, timeline)
        return candidates[0] if candidates else None

    def _pin_queue_item(self, state: dict[str, Any], item: dict[str, Any], catalog: CheckpointCatalog) -> None:
        checkpoint = _safe(item.get("checkpoint"), "checkpoint", 32)
        commit = self._resolve_commit(_safe(item.get("commit"), "commit", 160))
        requested_generation = item.get("generation") or item.get("generation_id")
        generation, bundle = self._resolve_generation(catalog, checkpoint, requested_generation)
        compatibility = self._compatibility(generation, bundle, candidate_commit=commit)
        item["commit"] = commit
        item["generation"] = str(generation["generation_id"])
        item["generation_id"] = str(generation["generation_id"])
        item["bundle"] = str(bundle)
        item["creator_commit"] = str(generation.get("creator_commit", "")).lower()
        item["registry_version"] = str(catalog.payload.get("registry_revision", 1))
        item["mission_id"] = _safe(item.get("mission_id") or self.mission_id, "mission_id", 160)
        item["factorio_version"] = _safe(item.get("factorio_version") or self.factorio_version, "factorio_version", 160)
        item["compatibility_manifest"] = compatibility
        generation["pinned"] = True
        generation["coordinator_pinned"] = True

    def _prepare_plan(self, state: dict[str, Any], item: dict[str, Any], slot: int, catalog: CheckpointCatalog) -> tuple[LanePlan, dict[str, Any]]:
        self._pin_queue_item(state, item, catalog)
        commit = self._resolve_commit(_safe(item.get("commit"), "commit", 160))
        checkpoint = str(item["checkpoint"])
        generation = str(item["generation"])
        creator = str(item.get("creator_commit") or commit)
        suite_id = _safe(item.get("suite_id") or "suite-unknown", "suite_id")
        lane_id = f"lane-{item.get('queue_id') or uuid.uuid4().hex}"
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        plan_kwargs: dict[str, Any] = {
            "suite_id": suite_id, "attempt_id": attempt_id, "checkpoint_id": checkpoint,
            "generation_id": generation, "candidate_commit": commit, "creator_commit": creator,
            "helper_enabled": bool(item.get("helper")), "lane_id": lane_id,
            "lineage_id": f"lineage-{suite_id}-{checkpoint}", "slot": slot,
            "helper_api_url": self.helper_api_url,
            "checkpoint_ids": self._timeline(state, catalog),
            "predicate_versions": {
                str(entry["id"]): str(entry.get("predicate_version", "unknown"))
                for entry in catalog.checkpoints
            },
            "registry_version": str(catalog.payload.get("registry_revision", 1)),
            "mission_id": str(item.get("mission_id") or self.mission_id),
            "factorio_version": str(item.get("factorio_version") or self.factorio_version),
        }
        mission_mode, mission_target = self._mission_command(
            str(item.get("mission_id") or self.mission_id)
        )
        plan_kwargs.update({
            "mission_mode": mission_mode,
            "mission_target": mission_target,
        })
        parameters = inspect.signature(self.runtime.plan_lane).parameters
        if not any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            plan_kwargs = {key: value for key, value in plan_kwargs.items() if key in parameters}
        plan = self.runtime.plan_lane(
            **plan_kwargs,
        )
        compatibility = dict(item["compatibility_manifest"])
        # The immutable tracked C0 is a baseline for every candidate.  Its
        # manifest uses an explicit any-commit policy; bind the resolved SHA to
        # the derived lane manifest so the runner's normal exact-match gate
        # remains in force after preparation.
        if compatibility.get("candidate_commit") == "*":
            compatibility["candidate_commit"] = commit
        return plan, {"commit": commit, "compatibility": compatibility, "bundle": item["bundle"]}

    @staticmethod
    def _result_path(plan: LanePlan) -> Path:
        return plan.lane_root / "episode" / "fleet-result.json"

    @staticmethod
    def _log_tail(plan: LanePlan) -> list[str]:
        try:
            return plan.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LOG_TAIL:]
        except OSError:
            return []

    def _invoke(self, command: Sequence[str]) -> None:
        result = self.command_executor(command)
        if isinstance(result, int) and result != 0:
            raise FleetCoordinatorError(f"lifecycle command failed ({result}): {' '.join(command)}")
        if hasattr(result, "returncode") and result.returncode not in (None, 0):
            detail = getattr(result, "stderr", "") or ""
            raise FleetCoordinatorError(f"lifecycle command failed: {detail or ' '.join(command)}")

    def _start_commands(self, plan: LanePlan) -> tuple[list[str], list[str]]:
        return plan.server_command(), plan.runner_command()

    def _stop_commands(self, run: Mapping[str, Any]) -> tuple[list[str], list[str]]:
        root = str(run["lane_root"])
        rcon = str(run["rcon_port"])
        return (
            [str(Path(__file__).resolve().parents[1] / "scripts/manage_linux_deterministic_runner.sh"),
             "stop", "--fleet-mode", "--root", root, "--rcon-port", rcon],
            [str(Path(__file__).resolve().parents[1] / "scripts/manage_linux_deterministic_server.sh"),
             "stop", "--fleet-mode", "--root", root],
        )

    def _stop_run_once(self, run: dict[str, Any], *, execute: bool) -> None:
        """Attempt both lane shutdown commands at most once per run.

        Marking the attempt before invoking the executor matters when a stop
        command itself fails: a later poll must not start issuing duplicate
        lifecycle commands against a slot which is already being reclaimed.
        """
        if not execute or run.get("stop_reconciled_at"):
            return
        run["stop_reconciled_at"] = _now()
        errors: list[str] = []
        for command in self._stop_commands(run):
            try:
                self._invoke(command)
            except Exception as error:
                errors.append(str(error))
        if errors:
            run["stop_error"] = "; ".join(errors)[:500]

    @staticmethod
    def _success_status(status: str) -> bool:
        return status in {"completed", "passed"}

    def _apply_result_progress(
        self,
        state: dict[str, Any],
        run: dict[str, Any],
        result: Mapping[str, Any],
        *,
        timeline: Sequence[str],
    ) -> str:
        """Validate and copy both running and terminal monitor payloads."""
        status = str(result.get("status", ""))
        allowed = {"running", "completed", "passed", "failed", "functional_failed",
                   "cancelled", "timed_out", "incompatible", "infrastructure_error"}
        if status not in allowed:
            raise FleetCoordinatorError(f"unsupported fleet result status: {status}")
        reported_run = result.get("run_id")
        if reported_run is not None and str(reported_run) != str(run.get("run_id")):
            raise FleetCoordinatorError("fleet result run_id does not match lane")
        reported_commit = result.get("commit")
        if reported_commit is not None:
            expected = str(run.get("commit", "")).lower()
            actual = str(reported_commit).lower()
            if actual != expected and not (len(actual) >= 7 and expected.startswith(actual)):
                raise FleetCoordinatorError("fleet result commit does not match lane")
        reached = result.get("contiguous_reached_checkpoints", result.get("reached_checkpoints", []))
        if not isinstance(reached, list) or any(not isinstance(item, str) for item in reached):
            raise FleetCoordinatorError("fleet result reached_checkpoints must be an array of IDs")
        origin = str(run.get("origin_checkpoint"))
        if not reached:
            raise FleetCoordinatorError("fleet result must include the lane origin checkpoint")
        try:
            positions = [timeline.index(item) for item in reached]
        except ValueError as error:
            raise FleetCoordinatorError("fleet result contains an unknown checkpoint") from error
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise FleetCoordinatorError("fleet result checkpoints must be contiguous")
        origin_position = timeline.index(origin)
        if positions[0] == 0 and positions[-1] >= origin_position:
            # The monitor reports the seeded prefix for a replay starting at
            # C1/Cn.  Keep coordinator progress scoped to that lane's origin.
            reached = reached[origin_position:]
            positions = positions[origin_position:]
        if not reached or reached[0] != origin:
            raise FleetCoordinatorError("fleet result must begin at the lane origin checkpoint")
        previous = run.get("reached_checkpoints") or [origin]
        try:
            previous_end = timeline.index(str(previous[-1]))
        except (ValueError, IndexError) as error:
            raise FleetCoordinatorError("lane has invalid prior checkpoint progress") from error
        if positions[-1] < previous_end:
            raise FleetCoordinatorError("fleet result checkpoint progress regressed")

        run["reached_checkpoints"] = list(dict.fromkeys(reached))
        run["furthest_checkpoint"] = run["reached_checkpoints"][-1]
        failure_checkpoint = result.get("failure_checkpoint")
        if not failure_checkpoint and status in {
            "failed", "functional_failed", "timed_out", "incompatible",
            "infrastructure_error",
        }:
            next_index = positions[-1] + 1
            failure_checkpoint = timeline[next_index] if next_index < len(timeline) else None
        run["failure_checkpoint"] = failure_checkpoint
        run["reason"] = result.get("reason", result.get("failure_reason"))
        durations = result.get("transition_durations_seconds")
        if isinstance(durations, Mapping):
            run["transition_durations_seconds"] = dict(durations)
        elapsed = result.get("elapsed_seconds")
        if elapsed is None and isinstance(durations, Mapping):
            values = [float(value) for value in durations.values() if isinstance(value, (int, float))]
            elapsed = sum(values) if values else None
        run["elapsed_seconds"] = elapsed
        # A later healthy boundary must not erase a warning already emitted
        # for this transition.
        run["slow_warning"] = bool(run.get("slow_warning")) or bool(result.get("slow_warning"))
        warnings = result.get("warnings")
        if isinstance(warnings, list):
            run["warnings"] = list(dict.fromkeys([*(run.get("warnings") or []), *map(str, warnings)]))
        captures = result.get("captures")
        if isinstance(captures, Mapping):
            run["captures"] = dict(captures)
        log_tail = result.get("log_tail")
        run["log_tail"] = list(log_tail or [])[-MAX_LOG_TAIL:] if isinstance(log_tail, list) else run.get("log_tail", [])
        return status

    @staticmethod
    def _timing_key(checkpoint_id: str, predicate_version: str, factorio_version: str) -> str:
        # JSON object keys are intentionally plain and inspectable in the
        # dashboard/state file; the components are already validated IDs.
        return f"{checkpoint_id}|{predicate_version}|{factorio_version}"

    def _timing_samples(
        self, state: Mapping[str, Any], checkpoint_id: str, predicate_version: str,
        factorio_version: str,
    ) -> list[float]:
        raw = state.get("timing_history", {}).get(
            self._timing_key(checkpoint_id, predicate_version, factorio_version), []
        )
        if not isinstance(raw, list):
            return []
        return [float(value) for value in raw if isinstance(value, (int, float)) and float(value) > 0]

    @staticmethod
    def _trimmed_mean(values: Sequence[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        if len(ordered) >= 5:
            trim = max(1, int(len(ordered) * 0.10))
            if len(ordered) > trim * 2:
                ordered = ordered[trim:-trim]
        return sum(ordered) / len(ordered)

    def _canonical_run(self, run: Mapping[str, Any], timeline: Sequence[str]) -> bool:
        """Use successful transitions from normal, unassisted fleet lanes."""
        return (
            run.get("kind") in {"endpoint", "middle"}
            and run.get("request_kind", "default") == "default"
            and not run.get("side")
            and not run.get("helper")
        ) if timeline else False

    def _record_timing_samples(self, state: dict[str, Any], run: Mapping[str, Any], catalog: CheckpointCatalog,
                               timeline: Sequence[str]) -> None:
        if not self._canonical_run(run, timeline):
            return
        durations = run.get("transition_durations_seconds")
        if not isinstance(durations, Mapping):
            return
        recorded = set(str(item) for item in (run.get("timing_recorded") or []))
        history = state.setdefault("timing_history", {})
        for target, raw_duration in durations.items():
            target = str(target)
            if target in recorded or target not in timeline:
                continue
            if not isinstance(raw_duration, (int, float)) or float(raw_duration) <= 0:
                continue
            predicate = next((item for item in catalog.checkpoints if str(item["id"]) == target), {})
            key = self._timing_key(target, str(predicate.get("predicate_version", "unknown")),
                                    str(run.get("factorio_version") or self.factorio_version))
            samples = history.setdefault(key, [])
            if not isinstance(samples, list):
                samples = history[key] = []
            samples.append(float(raw_duration))
            del samples[:-TIMING_SAMPLE_LIMIT]
            recorded.add(target)
        if recorded:
            run["timing_recorded"] = sorted(recorded)

    def _assess_running_timing(self, state: dict[str, Any], run: dict[str, Any],
                               catalog: CheckpointCatalog, timeline: Sequence[str],
                               result: Mapping[str, Any]) -> None:
        if not timeline or run.get("status") != "running":
            return
        reached = run.get("reached_checkpoints") or [run.get("origin_checkpoint")]
        try:
            next_index = timeline.index(str(reached[-1])) + 1
        except (ValueError, IndexError):
            return
        if next_index >= len(timeline):
            return
        target = timeline[next_index]
        predicate = next((item for item in catalog.checkpoints if str(item["id"]) == target), {})
        predicate_version = str(predicate.get("predicate_version", "unknown"))
        factorio_version = str(run.get("factorio_version") or self.factorio_version)
        samples = self._timing_samples(state, target, predicate_version, factorio_version)
        average = self._trimmed_mean(samples) if len(samples) >= 3 else None
        threshold = average * SLOWDOWN_FACTOR if average is not None else None
        if threshold is None:
            return
        raw_duration = result.get("transition_elapsed_seconds")
        from_total = raw_duration is None
        if raw_duration is None:
            raw_duration = result.get("elapsed_seconds")
        if not isinstance(raw_duration, (int, float)) or float(raw_duration) <= 0:
            started = str(run.get("started_at") or "")
            try:
                raw_duration = max(0.0, datetime.fromisoformat(_now()).timestamp() - datetime.fromisoformat(started).timestamp())
            except ValueError:
                return
        if from_total:
            completed = result.get("transition_durations_seconds")
            if isinstance(completed, Mapping):
                raw_duration = float(raw_duration) - sum(float(value) for value in completed.values()
                                                         if isinstance(value, (int, float)) and float(value) > 0)
        if float(raw_duration) >= threshold:
            run["slow_warning"] = True
            warning = f"{target} transition is slow: {float(raw_duration):.1f}s >= {threshold:.1f}s (2.5x trimmed average)"
            run["warnings"] = list(dict.fromkeys([*(run.get("warnings") or []), warning]))
            run["timing_warning"] = {
                "checkpoint": target, "elapsed_seconds": float(raw_duration),
                "trimmed_average_seconds": average, "threshold_seconds": threshold,
                "sample_count": len(samples),
            }

    def _record_terminal(
        self,
        state: dict[str, Any],
        run: dict[str, Any],
        result: Mapping[str, Any],
        plan: LanePlan | None = None,
        timeline: Sequence[str] | None = None,
    ) -> None:
        timeline = list(timeline or [str(item["id"]) for item in state["checkpoints"] if isinstance(item, dict) and item.get("id")])
        status = self._apply_result_progress(state, run, result, timeline=timeline)
        if status == "running":
            raise FleetCoordinatorError("running fleet result cannot be recorded as terminal")
        run["status"] = status
        if not result.get("log_tail"):
            run["log_tail"] = self._tail_path(
                Path(str(run.get("log_path")))
            ) if run.get("log_path") else (self._log_tail(plan) if plan else run.get("log_tail", []))
        run["ended_at"] = _now()
        queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
        if queue_item is not None:
            queue_item.update({"status": status, "finished_at": run["ended_at"], "failure_reason": run.get("reason")})
        if run.get("kind") == "endpoint" and run.get("checkpoint") == run.get("frontier_checkpoint"):
            state["frontier_runs"] = [entry for entry in state["frontier_runs"] if entry.get("run_id") != run.get("run_id")]
            state["frontier_runs"].append({key: run.get(key) for key in (
                "run_id", "commit", "checkpoint", "furthest_checkpoint", "status",
                "reason", "elapsed_seconds", "log_tail", "starred",
            )})
            state["frontier_runs"] = state["frontier_runs"][-10:]

    def _dispatch_one(self, state: dict[str, Any], catalog: CheckpointCatalog, *, execute: bool) -> dict[str, Any] | None:
        timeline = self._timeline(state, catalog)
        item = self._select_queue_item(state, timeline)
        if item is None or len(self._active_runs(state)) >= self.max_active:
            return None
        slot = self._next_slot(state)
        if slot is None:
            return None
        try:
            plan, inputs = self._prepare_plan(state, item, slot, catalog)
            if not execute:
                return {"queue_id": item.get("queue_id"), "checkpoint": item.get("checkpoint"), "commit": inputs["commit"], "slot": slot, "dry_run": True}
            # Preparation is also isolated and fail-closed; only the injected
            # lifecycle executor can make external processes appear.
            self.runtime.prepare_lane(plan, Path(inputs["bundle"]), compatibility_manifest=inputs["compatibility"])
            server_command, runner_command = self._start_commands(plan)
            self._invoke(server_command)
            try:
                self._invoke(runner_command)
            except Exception:
                try:
                    self._invoke([server_command[0], "stop", "--fleet-mode", "--root", str(plan.lane_root)])
                except Exception:
                    pass
                raise
        except Exception as error:
            item["status"] = "failed"
            item["failure_reason"] = str(error)
            item["finished_at"] = _now()
            return {"queue_id": item.get("queue_id"), "status": "failed", "reason": str(error)}
        item["status"] = "running"
        item["started_at"] = _now()
        item["slot"] = slot
        item["enqueue_order"] = int(item.get("enqueue_order") or len(state["queue"]))
        state["coordinator"]["dispatch_count"] = int(state["coordinator"].get("dispatch_count", 0)) + 1
        kind = self._queue_kind(item, timeline)
        if kind == "middle":
            state["coordinator"]["ordinary_since_middle"] = 0
        else:
            state["coordinator"]["ordinary_since_middle"] = int(state["coordinator"].get("ordinary_since_middle", 0)) + 1
        for pending in state["queue"]:
            if self._queue_kind(pending, timeline) == "verification" and pending.get("status", "queued") == "queued":
                pending["waiting_opportunities"] = int(pending.get("waiting_opportunities", 0)) + 1
        run = {
            "run_id": plan.run_id, "queue_id": item.get("queue_id"), "suite_id": item.get("suite_id"),
            "commit": inputs["commit"], "checkpoint": item.get("checkpoint"),
            "kind": kind, "side": item.get("side"), "helper": bool(item.get("helper")), "status": "running",
            "request_kind": item.get("kind", "default"),
            "slot": slot, "game_port": plan.ports.game, "rcon_port": plan.ports.rcon,
            "lane_root": str(plan.lane_root), "manifest_path": str(plan.manifest_path),
            "runner_pid_path": str(plan.lane_root / "logs" / "autonomous-run.pid"),
            "log_path": str(plan.log_path), "started_at": item["started_at"],
            "origin_checkpoint": item.get("checkpoint"), "frontier_checkpoint": timeline[-1],
            "reached_checkpoints": [item.get("checkpoint")],
            "starred": item.get("checkpoint") != timeline[0],
            "generation": item.get("generation"),
            "generation_id": item.get("generation"),
            "candidate_id": item.get("candidate_id"),
            "verification_successor": item.get("verification_successor"),
            "mission_id": item.get("mission_id", self.mission_id),
            "factorio_version": item.get("factorio_version", self.factorio_version),
        }
        state["runs"].append(run)
        return run

    def apply_dashboard_action(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Apply a dashboard intent under the coordinator's file lock.

        Keeping this mutation boundary here prevents the web process and the
        persistent coordinator from maintaining competing queue semantics.
        The method only records intent; process lifecycle remains gated by
        ``run_once(execute=True)`` in the service unit.
        """
        if not isinstance(payload, Mapping):
            raise FleetCoordinatorError("fleet action payload must be an object")
        body = dict(payload)
        with self._locked() as state:
            now = _now()
            if action == "restart_latest":
                action, body = "run_default", {"commit": body.get("commit") or "HEAD"}
            if action in {"run_default", "run_custom"}:
                catalog: CheckpointCatalog | None = None
                if action == "run_default":
                    commits = [body.get("commit") or "HEAD"]
                    try:
                        catalog = self._catalog()
                        checkpoint_ids = self._runnable_timeline(state, catalog)
                    except FleetCoordinatorError:
                        checkpoint_ids = self._timeline(state)
                    helper_checkpoints = {checkpoint_ids[-1]} if checkpoint_ids else set()
                else:
                    commits = body.get("commits")
                    checkpoint_ids = body.get("checkpoints")
                    if not isinstance(commits, list) or not commits:
                        raise FleetCoordinatorError("custom run requires at least one commit")
                    if not isinstance(checkpoint_ids, list) or not checkpoint_ids:
                        raise FleetCoordinatorError("custom run requires checkpoint IDs")
                    helper_values = body.get("helper_checkpoints", body.get("helper_checkpoint", []))
                    if isinstance(helper_values, str):
                        helper_values = [helper_values]
                    if not isinstance(helper_values, list):
                        raise FleetCoordinatorError("helper_checkpoints must be an array")
                    helper_checkpoints = {str(item) for item in helper_values}
                    try:
                        catalog = self._catalog()
                    except FleetCoordinatorError:
                        # Offline dashboard callers may intentionally create
                        # intent before the source catalog exists.
                        catalog = None
                source_checkpoint = body.get("source_checkpoint")
                if source_checkpoint is not None:
                    source_checkpoint = _safe(source_checkpoint, "source_checkpoint", 32)
                    if source_checkpoint not in {str(item) for item in checkpoint_ids}:
                        raise FleetCoordinatorError("source checkpoint must be one of the selected checkpoints")
                generation_selection: str | None = None
                if action == "run_custom" and (body.get("generation_id") or body.get("save_id")):
                    if catalog is None:
                        raise FleetCoordinatorError("custom save/generation requires the checkpoint catalog")
                    if source_checkpoint is None:
                        raise FleetCoordinatorError("select a source checkpoint when using a custom save")
                    generation_selection = self._resolve_generation_selection(
                        catalog, source_checkpoint,
                        generation_id=body.get("generation_id"), save_id=body.get("save_id"),
                        candidates=state.get("promotion_candidates", []),
                    )
                custom_mission = self.mission_id
                if action == "run_custom" and body.get("mission_id"):
                    custom_mission = _safe(body.get("mission_id"), "mission_id", 160)
                    self._mission_command(custom_mission, require_explicit=True)
                known = {str(item["id"]) for item in state.get("checkpoints", []) if isinstance(item, dict)}
                if any(str(item) not in known for item in checkpoint_ids):
                    raise FleetCoordinatorError("unknown checkpoint in fleet action")
                if any(str(item) not in known for item in helper_checkpoints):
                    raise FleetCoordinatorError("unknown helper checkpoint in fleet action")
                suite = f"suite-{int(time.time() * 1000)}"
                queued_items: list[dict[str, Any]] = []
                for commit in commits:
                    commit = _safe(commit, "commit", 160)
                    resolved_commit = self._resolve_commit(commit) if catalog is not None else commit
                    state["commits"] = [item for item in state["commits"] if item.get("commit") != resolved_commit]
                    state["commits"].append({"commit": resolved_commit, "suite_id": suite, "queued_at": now})
                    for checkpoint in checkpoint_ids:
                        if len(state["queue"]) + len(queued_items) >= MAX_QUEUE:
                            raise FleetCoordinatorError("checkpoint fleet queue is full")
                        queued_items.append({
                            "queue_id": f"{suite}-{len(state['queue']) + len(queued_items) + 1}", "suite_id": suite,
                            "commit": resolved_commit, "checkpoint": str(checkpoint),
                            "helper": str(checkpoint) in helper_checkpoints,
                            "kind": "default" if action == "run_default" else "custom",
                            "queued_at": now, "status": "queued",
                            "enqueue_order": len(state["queue"]) + len(queued_items) + 1,
                            "mission_id": custom_mission,
                        })
                        if generation_selection and str(checkpoint) == source_checkpoint:
                            queued_items[-1]["generation"] = generation_selection
                if catalog is not None:
                    for queued in queued_items:
                        try:
                            self._pin_queue_item(state, queued, catalog)
                        except FleetCoordinatorError as error:
                            queued.update({
                                "status": "incompatible",
                                "failure_reason": str(error),
                                "finished_at": now,
                            })
                    catalog.save()
                state["queue"].extend(queued_items)
                self._save_unlocked(state)
                return self._project_state(state)
            if action == "verify_checkpoint":
                checkpoint = _safe(body.get("checkpoint"), "checkpoint", 32)
                commit = _safe(body.get("commit") or "HEAD", "commit", 160)
                catalog: CheckpointCatalog | None = None
                if checkpoint not in {str(item["id"]) for item in state.get("checkpoints", []) if isinstance(item, dict)}:
                    raise FleetCoordinatorError("Unknown checkpoint in verification request")
                candidate_id = _safe(body.get("candidate_id"), "candidate_id", 200)
                if catalog is None:
                    catalog = self._catalog()
                candidate = next((item for item in state.get("promotion_candidates", [])
                                  if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
                if candidate is None or str(candidate.get("checkpoint")) != checkpoint:
                    raise FleetCoordinatorError("verification candidate is not listed for this checkpoint")
                if candidate.get("provenance") not in {"c0", "provisional"}:
                    raise FleetCoordinatorError("verification candidate has no eligible C0/frontier provenance")
                candidate_generation, _candidate_bundle = self._candidate_generation(catalog, candidate, checkpoint)
                if candidate.get("verified") is True:
                    raise FleetCoordinatorError("verification candidate is already verified")
                timeline = self._timeline(state, catalog)
                try:
                    successor = timeline[timeline.index(checkpoint) + 1]
                except (ValueError, IndexError) as error:
                    raise FleetCoordinatorError("checkpoint has no successor to verify") from error
                current_generation, _current_bundle = self._resolve_generation(catalog, checkpoint)
                suite = f"verify-{int(time.time() * 1000)}"
                queued_items: list[dict[str, Any]] = []
                for side, generation_id in (("control", current_generation["generation_id"]),
                                             ("candidate", candidate_generation["generation_id"])):
                    if len(state["queue"]) + len(queued_items) >= MAX_QUEUE:
                        raise FleetCoordinatorError("checkpoint fleet queue is full")
                    queued_items.append({
                        "queue_id": f"{suite}-{side}", "suite_id": suite, "commit": commit,
                        "checkpoint": checkpoint, "kind": "verify", "side": side,
                        "helper": False, "queued_at": now, "status": "queued",
                        "enqueue_order": len(state["queue"]) + len(queued_items) + 1,
                        "generation": generation_id, "candidate_id": candidate_id,
                        "verification_successor": successor,
                        "mission_id": self.mission_id, "factorio_version": self.factorio_version,
                    })
                for queued in queued_items:
                    self._pin_queue_item(state, queued, catalog)
                catalog.save()
                state["queue"].extend(queued_items)
                self._save_unlocked(state)
                return self._project_state(state)
            if action in {"pause", "auto_run"}:
                field = "paused" if action == "pause" else "auto_run_commits"
                value = body.get(field)
                if not isinstance(value, bool):
                    raise FleetCoordinatorError(f"{field} must be boolean")
                state["settings"][field] = value
                self._save_unlocked(state)
                return self._project_state(state)
            if action == "remove_queue":
                queue_id = _safe(body.get("queue_id"), "queue_id", 160)
                for item in state["queue"]:
                    if item.get("queue_id") == queue_id:
                        item["status"] = "cancelled"
                        item["cancelled_at"] = now
                        self._save_unlocked(state)
                        return self._project_state(state)
                raise FleetCoordinatorError("queue item does not exist")
            if action in {"stop_run", "restart_helper"}:
                run_id = _safe(body.get("run_id"), "run_id", 160)
                run = next((item for item in state["runs"] if item.get("run_id") == run_id), None)
                if run is None:
                    raise FleetCoordinatorError("run does not exist")
                if action == "stop_run":
                    run["status"] = "cancelled"
                    run["stopped_at"] = now
                else:
                    if run.get("status") == "running":
                        raise FleetCoordinatorError("helper restart requires a terminal failed run")
                    if run.get("kind") != "middle":
                        raise FleetCoordinatorError("helper restart is only available for middle checkpoint runs")
                    if run.get("status") not in {
                        "failed", "functional_failed", "timed_out", "incompatible",
                        "infrastructure_error", "preempted",
                    }:
                        raise FleetCoordinatorError("helper restart requires a terminal failed run")
                    source = next((entry for entry in state["queue"] if entry.get("queue_id") == run.get("queue_id")), {})
                    queued = {
                        "queue_id": f"helper-{run_id}-{uuid.uuid4().hex[:8]}",
                        "suite_id": f"helper-{run.get('suite_id') or run_id}",
                        "commit": run.get("commit"), "checkpoint": run.get("checkpoint"),
                        "helper": True, "kind": "custom", "side": source.get("side"),
                        "queued_at": now, "status": "queued",
                        "enqueue_order": len(state["queue"]) + 1,
                        "restart_of_run_id": run_id,
                    }
                    # Keep the exact input generation used by the failed run;
                    # a helper retry must not jump to a newly promoted save.
                    for key in ("generation", "bundle", "creator_commit", "registry_version",
                                "mission_id", "factorio_version", "compatibility_manifest"):
                        if key in source:
                            queued[key] = source[key]
                    catalog = None
                    try:
                        catalog = self._catalog()
                    except FleetCoordinatorError:
                        pass
                    if catalog is not None:
                        self._pin_queue_item(state, queued, catalog)
                        catalog.save()
                    state["queue"].append(queued)
                    run["helper_restart_queue_id"] = queued["queue_id"]
                    run["helper_restart_requested_at"] = now
                self._save_unlocked(state)
                return self._project_state(state)
            if action == "promote_checkpoint":
                checkpoint = _safe(body.get("checkpoint"), "checkpoint", 32)
                candidate_id = _safe(body.get("candidate_id") or body.get("save_id"), "candidate_id", 200)
                entry = next((item for item in state["checkpoints"] if item.get("id") == checkpoint), None)
                if entry is None:
                    raise FleetCoordinatorError("unknown checkpoint")
                candidate = next((item for item in state.get("promotion_candidates", [])
                                  if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
                if candidate is None:
                    raise FleetCoordinatorError("Promotion candidate is not listed by the fleet coordinator")
                if candidate.get("checkpoint") != checkpoint or candidate.get("verified") is not True:
                    raise FleetCoordinatorError("promotion candidate is not verified for this checkpoint")
                if candidate.get("provenance") not in {"c0", "provisional"}:
                    raise FleetCoordinatorError("promotion candidate has no valid C0/provisional provenance")
                catalog = self._catalog()
                generation, _bundle = self._candidate_generation(catalog, candidate, checkpoint)
                original_catalog = json.loads(json.dumps(catalog.payload))
                try:
                    if candidate.get("provenance") == "c0":
                        catalog.promote_default(checkpoint, str(generation["generation_id"]), c0_verified=True)
                    else:
                        catalog.set_provisional_default(checkpoint, str(generation["generation_id"]))
                    catalog.save()
                except Exception:
                    catalog.payload = original_catalog
                    raise
                entry.update({"generation": str(generation["generation_id"]),
                              "generation_id": str(generation["generation_id"]),
                              "starred": candidate.get("provenance") != "c0",
                              "promoted_at": now, "creator_commit": candidate.get("creator_commit"),
                              "promotion_candidate_id": candidate_id})
                self._save_unlocked(state)
                return self._project_state(state)
            raise FleetCoordinatorError(f"unknown fleet action: {action}")

    def _poll_run(
        self,
        state: dict[str, Any],
        run: dict[str, Any],
        catalog: CheckpointCatalog,
        *,
        execute: bool,
    ) -> None:
        lane_root = Path(str(run.get("lane_root")))
        manifest_path = Path(str(run.get("manifest_path")))
        result_path = lane_root / "episode" / "fleet-result.json"
        result: dict[str, Any] | None = None
        malformed: str | None = None
        try:
            if result_path.is_file():
                result = _read_json(result_path)
            elif manifest_path.is_file():
                manifest = _read_json(manifest_path)
                if isinstance(manifest.get("fleet_result"), dict):
                    result = manifest["fleet_result"]
        except FleetCoordinatorError as error:
            malformed = str(error)
        if malformed is not None:
            run["status"] = "infrastructure_error"
            run["reason"] = f"malformed structured fleet result: {malformed}"
            run["log_tail"] = self._tail_path(Path(str(run.get("log_path"))))
            run["ended_at"] = _now()
            self._stop_run_once(run, execute=execute)
            queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
            if queue_item is not None:
                queue_item.update({"status": run["status"], "finished_at": run["ended_at"], "failure_reason": run["reason"]})
            return
        if result is None:
            pid = running_runner_pid(Path(str(run.get("runner_pid_path"))))
            if pid is not None:
                run["runner_pid"] = pid
                return
            # A dead runner with no structured terminal result is not a pass.
            run["status"] = "infrastructure_error"
            run["reason"] = "runner exited without a structured fleet result"
            run["log_tail"] = self._tail_path(Path(str(run.get("log_path"))))
            run["ended_at"] = _now()
            self._stop_run_once(run, execute=execute)
            queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
            if queue_item is not None:
                queue_item.update({"status": run["status"], "finished_at": run["ended_at"], "failure_reason": run["reason"]})
            return
        if not isinstance(result, dict):
            raise FleetCoordinatorError("fleet result must be a JSON object")
        try:
            if str(result.get("status")) == "running":
                # A monitor writes this snapshot at construction and after
                # every boundary.  Keep the lane active and its slot held.
                self._apply_result_progress(
                    state, run, result, timeline=self._timeline(state, catalog)
                )
                self._record_timing_samples(
                    state, run, catalog, self._timeline(state, catalog)
                )
                self._assess_running_timing(
                    state, run, catalog, self._timeline(state, catalog), result
                )
                return
            timeline = self._timeline(state, catalog)
            self._record_terminal(state, run, result, timeline=timeline)
            self._record_timing_samples(state, run, catalog, timeline)
            self._stop_run_once(run, execute=execute)
        except FleetCoordinatorError as error:
            run["status"] = "infrastructure_error"
            run["reason"] = str(error)
            run["log_tail"] = self._tail_path(Path(str(run.get("log_path"))))
            run["ended_at"] = _now()
            self._stop_run_once(run, execute=execute)
            queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
            if queue_item is not None:
                queue_item.update({"status": run["status"], "finished_at": run["ended_at"], "failure_reason": run["reason"]})

    @staticmethod
    def _tail_path(path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LOG_TAIL:]
        except OSError:
            return []

    def _refresh_promotion_candidates(
        self, state: dict[str, Any], *, timeline: Sequence[str], catalog: CheckpointCatalog | None = None
    ) -> None:
        """Project eligible captured saves and matched A/B verification.

        C0-origin and then-current-frontier captures are eligible inputs.  Both
        remain unverified until an explicit matched successor replay; frontier
        provenance remains provisional even after verification.
        The projection is idempotent and preserves operator-added metadata.
        """
        if not isinstance(state.get("promotion_candidates"), list):
            state["promotion_candidates"] = []
        pair_state: dict[tuple[str, str], dict[str, Any]] = {}
        for run in state["runs"]:
            side = run.get("side")
            checkpoint = str(run.get("checkpoint", ""))
            if run.get("suite_id") and side in {"control", "candidate"}:
                key = (str(run["suite_id"]), checkpoint)
                pair_state.setdefault(key, {})[str(side)] = run
        matched: dict[tuple[str, str], dict[str, str]] = {}
        for key, sides in pair_state.items():
            control, candidate = sides.get("control"), sides.get("candidate")
            if (control and candidate and self._success_status(str(control.get("status")))
                    and self._success_status(str(candidate.get("status")))):
                successor = timeline[timeline.index(key[1]) + 1] if key[1] in timeline and timeline.index(key[1]) + 1 < len(timeline) else None
                if successor is None:
                    continue
                control_reached = {str(value) for value in (control.get("reached_checkpoints") or [])}
                candidate_reached = {str(value) for value in (candidate.get("reached_checkpoints") or [])}
                if successor not in control_reached or successor not in candidate_reached:
                    continue
                matched[key] = {
                    "suite_id": key[0], "control_run_id": str(control.get("run_id")),
                    "candidate_run_id": str(candidate.get("run_id")),
                    "candidate_generation_id": str(candidate.get("generation") or candidate.get("generation_id") or ""),
                }
        indexed = {
            str(item.get("candidate_id")): item
            for item in state["promotion_candidates"] if isinstance(item, dict) and item.get("candidate_id")
        }
        for run in state["runs"]:
            origin = str(run.get("origin_checkpoint", ""))
            frontier = str(run.get("frontier_checkpoint") or (timeline[-1] if timeline else ""))
            if not timeline:
                continue
            candidate_eligible = origin in {str(timeline[0]), frontier}
            provenance = "c0" if origin == str(timeline[0]) else "provisional"
            captures = run.get("captures")
            if not isinstance(captures, Mapping) or catalog is None:
                continue
            for capture_label, captured in captures.items():
                if not isinstance(captured, (str, Path)):
                    continue
                captured_path = str(captured)
                if not captured_path.strip() or captured_path.lower() in {"true", "false"}:
                    continue
                checkpoint = str(capture_label)
                if checkpoint not in timeline:
                    try:
                        checkpoint = str(
                            _read_json(Path(captured_path) / "checkpoint.json").get("checkpoint_id")
                            or ""
                        )
                    except FleetCoordinatorError:
                        continue
                if checkpoint not in timeline:
                    continue
                registered = self._capture_bundle(
                    catalog, checkpoint, captured_path,
                    creator_commit=str(run.get("commit", "")).lower(),
                    source_run_id=str(run.get("run_id")) if run.get("run_id") else None,
                    provisional=candidate_eligible and provenance == "provisional",
                )
                if registered is None:
                    continue
                generation, bundle = registered
                try:
                    capture_reason = _read_json(bundle / "checkpoint.json").get("capture_reason")
                except FleetCoordinatorError:
                    continue
                # Rolling five-minute and terminal saves remain valid one-off
                # inputs, but only a milestone crossing may be promoted.
                if capture_reason != "milestone":
                    continue
                if not candidate_eligible:
                    continue
                candidate_id = f"{run.get('run_id')}:{checkpoint}"
                candidate = indexed.get(candidate_id, {})
                candidate.update({
                    "candidate_id": candidate_id, "checkpoint": checkpoint,
                    "save_id": str(generation["generation_id"]),
                    "generation_id": str(generation["generation_id"]),
                    "path": str(bundle),
                    "creator_commit": str(run.get("commit", "")).lower(),
                    "source_run_id": run.get("run_id"), "provenance": provenance,
                    "verified": bool(candidate.get("verified", False)),
                })
                pair = matched.get((str(run.get("suite_id")), checkpoint))
                if provenance == "provisional" and pair:
                    candidate["verified"] = True
                    candidate["verification"] = {"matched": True, **pair}
                elif provenance == "provisional":
                    candidate.setdefault("verification", {"matched": False})
                indexed[candidate_id] = candidate
        # A verification suite replays an already captured input checkpoint;
        # its result normally contains no new capture at the origin itself.
        for candidate in indexed.values():
            pair = next(
                (value for (suite, checkpoint), value in matched.items()
                 if checkpoint == str(candidate.get("checkpoint"))),
                None,
            )
            if pair and str(candidate.get("generation_id")) == str(pair.get("candidate_generation_id")):
                candidate["verified"] = True
                candidate["verification"] = {"matched": True, **pair}
        # Keep manually supplied candidates and append newly discovered ones.
        existing_ids = {
            str(item.get("candidate_id")) for item in state["promotion_candidates"]
            if isinstance(item, dict) and item.get("candidate_id")
        }
        for candidate_id, candidate in indexed.items():
            if candidate_id not in existing_ids:
                state["promotion_candidates"].append(candidate)
            else:
                for item in state["promotion_candidates"]:
                    if isinstance(item, dict) and item.get("candidate_id") == candidate_id:
                        item.update(candidate)
                        break

    def _reconcile_cancellations(self, state: dict[str, Any], *, execute: bool) -> None:
        # The dashboard marks an active run ``cancelled`` before the
        # coordinator sees it.  Reconcile that durable intent exactly once;
        # otherwise a restart could forget to stop the old process and reuse
        # its slot.
        for run in state["runs"]:
            item = next((entry for entry in state["queue"] if entry.get("queue_id") == run.get("queue_id")), None)
            requested = run.get("status") == "cancelled" or (item and item.get("status") == "cancelled")
            if not requested or run.get("stop_reconciled_at"):
                continue
            self._stop_run_once(run, execute=execute)
            run["status"] = "cancelled"
            run["reason"] = "cancelled by operator"
            run["ended_at"] = _now()
            if execute:
                run["stop_reconciled_at"] = run.get("stop_reconciled_at") or _now()
            if item is not None:
                item["status"] = "cancelled"

    def _reclaim_one(self, state: dict[str, Any], *, execute: bool) -> dict[str, Any] | None:
        pending = any(item.get("status", "queued") == "queued" for item in state["queue"])
        if not pending:
            return None
        candidates = []
        for run in self._active_runs(state):
            if run.get("kind") != "middle":
                continue
            reached = run.get("reached_checkpoints") or []
            if len(reached) < 3:
                continue
            candidates.append(run)
        if not candidates:
            return None
        run = min(candidates, key=lambda item: item.get("started_at", ""))
        self._stop_run_once(run, execute=execute)
        run["status"] = "preempted"
        run["reason"] = "reclaimed after double tick"
        run["ended_at"] = _now()
        queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
        if queue_item is not None:
            queue_item.update({"status": "preempted", "finished_at": run["ended_at"], "failure_reason": run["reason"]})
        return run

    def _preempt_slow_middle(self, state: dict[str, Any], *, execute: bool) -> dict[str, Any] | None:
        """End only a warned middle lane when queued work actually needs it."""
        if not any(item.get("status", "queued") == "queued" for item in state["queue"]):
            return None
        candidates = [run for run in self._active_runs(state)
                      if run.get("kind") == "middle" and run.get("slow_warning")]
        if not candidates:
            return None
        run = min(candidates, key=lambda item: item.get("started_at", ""))
        self._stop_run_once(run, execute=execute)
        run.update({"status": "timed_out", "reason": "transition exceeded 2.5x timing baseline while work waited",
                    "ended_at": _now()})
        queue_item = next((item for item in state["queue"] if item.get("queue_id") == run.get("queue_id")), None)
        if queue_item is not None:
            queue_item.update({"status": "timed_out", "finished_at": run["ended_at"],
                               "failure_reason": run["reason"]})
        return run

    def _refresh_rankings(self, state: dict[str, Any], timeline: Sequence[str]) -> None:
        """Persist a stability-first projection for the operations console."""
        summaries: dict[str, dict[str, Any]] = {}
        for run in state.get("runs", []):
            if not isinstance(run, Mapping) or str(run.get("status")) == "running":
                continue
            commit = str(run.get("commit") or "").lower()
            if not commit:
                continue
            entry = summaries.setdefault(commit, {
                "commit": commit, "run_count": 0, "contiguous_failure_free_prefix": len(timeline) - 1,
                "intermediate_failures": 0, "furthest_checkpoint": 0, "double_ticks": 0,
                "slow_warnings": 0, "durations": [],
            })
            entry["run_count"] += 1
            reached = [str(item) for item in (run.get("reached_checkpoints") or []) if str(item) in timeline]
            furthest = max((timeline.index(item) for item in reached), default=0)
            entry["furthest_checkpoint"] = max(entry["furthest_checkpoint"], furthest)
            entry["double_ticks"] += int(len(reached) >= 3) if run.get("kind") == "middle" else 0
            entry["slow_warnings"] += int(bool(run.get("slow_warning")))
            if isinstance(run.get("elapsed_seconds"), (int, float)) and float(run["elapsed_seconds"]) > 0:
                entry["durations"].append(float(run["elapsed_seconds"]))
            origin = str(run.get("origin_checkpoint"))
            if origin != (timeline[0] if timeline else "C0") and str(run.get("status")) in {
                "functional_failed", "failed", "timed_out", "infrastructure_error", "incompatible",
            }:
                entry["intermediate_failures"] += 1
            failure = str(run.get("failure_checkpoint") or "")
            if failure in timeline:
                entry["contiguous_failure_free_prefix"] = min(
                    entry["contiguous_failure_free_prefix"], max(0, timeline.index(failure) - 1)
                )
        ranked = []
        for entry in summaries.values():
            durations = entry.pop("durations")
            entry["contiguous_failure_free_prefix"] = min(
                int(entry["contiguous_failure_free_prefix"]),
                int(entry["furthest_checkpoint"]),
            )
            entry["normalized_duration"] = (sum(durations) / len(durations)) if durations else None
            entry["normalized_speed"] = entry["normalized_duration"] if entry["normalized_duration"] is not None else float("inf")
            ranked.append(entry)
        ranked.sort(key=lambda item: (
            -int(item["contiguous_failure_free_prefix"]), int(item["intermediate_failures"]),
            -int(item["furthest_checkpoint"]), -int(item["double_ticks"]),
            int(item["slow_warnings"]), item["normalized_speed"], item["commit"],
        ))
        for item in ranked:
            if item.get("normalized_speed") == float("inf"):
                item["normalized_speed"] = None
        state["ranked_commits"] = ranked

    def _sync_catalog_retention(self, state: dict[str, Any], catalog: CheckpointCatalog) -> None:
        """Pin live inputs, prune the catalog, and GC only owned bundles."""
        referenced: set[str] = set()
        for item in state.get("queue", []):
            if item.get("status", "queued") in {"queued", "running"} and item.get("generation"):
                referenced.add(str(item["generation"]))
        for run in state.get("runs", []):
            if run.get("status") == "running" and run.get("generation"):
                referenced.add(str(run["generation"]))
        for candidate in state.get("promotion_candidates", []):
            if (isinstance(candidate, Mapping) and candidate.get("verified") is True
                    and candidate.get("generation_id")):
                referenced.add(str(candidate["generation_id"]))
        changed = False
        for checkpoint in catalog.checkpoints:
            for generation in checkpoint["generations"]:
                if generation.get("coordinator_pinned"):
                    desired = str(generation.get("generation_id")) in referenced
                    if bool(generation.get("pinned")) != desired:
                        generation["pinned"] = desired
                        changed = True
        removed = catalog.prune()
        catalog_root = catalog.path.parent.resolve()
        for generation in removed:
            raw = Path(str(generation.get("path", "")))
            path = (catalog_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
            if (path.is_relative_to(catalog_root) and path != catalog_root
                    and path.is_dir() and not path.is_symlink()
                    and str(generation.get("generation_id")) not in referenced):
                shutil.rmtree(path)
        if changed or removed:
            catalog.save()
        surviving = {
            str(generation.get("generation_id"))
            for checkpoint in catalog.checkpoints
            for generation in checkpoint.get("generations", [])
        }
        state["promotion_candidates"] = [
            candidate for candidate in state.get("promotion_candidates", [])
            if not isinstance(candidate, Mapping)
            or not candidate.get("generation_id")
            or str(candidate.get("generation_id")) in surviving
        ]

    def run_once(self, *, execute: bool = False) -> dict[str, Any]:
        """Reconcile active lanes and dispatch available work once.

        The default is a side-effect-free planning pass.  ``execute=True`` is
        required to prepare lanes and call lifecycle commands.
        """
        with self._locked() as state:
            catalog = self._catalog()
            self._reconcile_cancellations(state, execute=execute)
            for run in list(self._active_runs(state)):
                self._poll_run(state, run, catalog, execute=execute)
            # A prior dry/reconciliation pass may have recorded a terminal
            # result without permission to execute lifecycle commands.  Make
            # those lanes safe before allowing their slots to be reused.
            terminal_statuses = {
                "completed", "passed", "failed", "functional_failed", "timed_out",
                "incompatible", "infrastructure_error", "cancelled", "preempted",
            }
            if execute:
                for run in state["runs"]:
                    if run.get("status") in terminal_statuses and not run.get("stop_reconciled_at"):
                        self._stop_run_once(run, execute=True)
            self._refresh_promotion_candidates(
                state, timeline=self._timeline(state, catalog), catalog=catalog
            )
            timeline = self._timeline(state, catalog)
            state["settings"]["active_servers"] = len(self._active_runs(state))
            if execute:
                self._sync_catalog_retention(state, catalog)
            self._refresh_rankings(state, timeline)
            if state["settings"].get("paused"):
                self._save_unlocked(state)
                return state
            if execute:
                self._preempt_slow_middle(state, execute=True)
            plans: list[dict[str, Any]] = []
            while len(self._active_runs(state)) >= self.max_active:
                if not execute or self._reclaim_one(state, execute=True) is None:
                    break
            while len(self._active_runs(state)) < self.max_active:
                plan = self._dispatch_one(state, catalog, execute=execute)
                if plan is None:
                    break
                plans.append(plan)
                if plan.get("dry_run"):
                    break
            self._save_unlocked(state)
            state["plans"] = plans
            return state

    def _git(self, *args: str) -> str:
        try:
            return subprocess.check_output(["git", "-C", str(self.repo_root), *args], text=True).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise FleetCoordinatorError(f"git query failed: {' '.join(args)}") from error

    def maybe_enqueue_auto_commit(self) -> bool:
        """Queue one default suite for a descendant HEAD, even when dirty.

        A dirty checkout is harmless because each lane uses an immutable
        detached worktree.  Only a new descendant object advances the watcher;
        branch rewinds, resets, and divergent histories are ignored.
        """
        with self._locked() as state:
            if not state["settings"].get("auto_run_commits", True):
                return False
            head = self._git("rev-parse", "HEAD").lower()
            seen = state["coordinator"].setdefault("auto_seen_commits", [])
            if seen:
                try:
                    subprocess.run(
                        ["git", "-C", str(self.repo_root), "merge-base", "--is-ancestor", seen[-1], head],
                        check=True, capture_output=True,
                    )
                except subprocess.CalledProcessError:
                    return False
                commits = [item.lower() for item in self._git("rev-list", "--reverse", f"{seen[-1]}..{head}").splitlines() if item.strip()]
            else:
                commits = [head]
            if not commits:
                return False
            catalog = self._catalog()
            checkpoints = self._runnable_timeline(state, catalog)
            if not checkpoints:
                return False
            if len(state["queue"]) + len(commits) * len(checkpoints) > MAX_QUEUE:
                raise FleetCoordinatorError("checkpoint fleet queue is full")
            now = _now()
            queued_items: list[dict[str, Any]] = []
            for commit in commits:
                suite = f"auto-{commit[:12]}"
                state["commits"] = [item for item in state["commits"] if item.get("commit") != commit]
                state["commits"].append({"commit": commit, "suite_id": suite, "queued_at": now, "automatic": True})
                for index, checkpoint in enumerate(checkpoints, 1):
                    queued_items.append({
                        "queue_id": f"{suite}-{index}", "suite_id": suite, "commit": commit,
                        "checkpoint": checkpoint, "helper": checkpoint == checkpoints[-1],
                        "kind": "default", "queued_at": now, "status": "queued",
                        "enqueue_order": len(state["queue"]) + len(queued_items) + 1,
                    })
            for queued in queued_items:
                try:
                    self._pin_queue_item(state, queued, catalog)
                except FleetCoordinatorError as error:
                    queued.update({
                        "status": "incompatible",
                        "failure_reason": str(error),
                        "finished_at": now,
                    })
            catalog.save()
            state["queue"].extend(queued_items)
            seen.extend(commits)
            state["coordinator"]["auto_seen_commits"] = seen[-512:]
            self._save_unlocked(state)
            return True

    def watch(self, *, execute: bool = False, poll_interval: float = DEFAULT_POLL_INTERVAL, iterations: int | None = None) -> dict[str, Any]:
        if not 0.1 <= poll_interval <= 300:
            raise FleetCoordinatorError("poll interval must be between 0.1 and 300 seconds")
        count = 0
        state: dict[str, Any] = {}
        self._write_heartbeat(status="running", execute=execute)
        try:
            while iterations is None or count < iterations:
                try:
                    self.maybe_enqueue_auto_commit()
                    state = self.run_once(execute=execute)
                    self._write_heartbeat(status="running", execute=execute)
                except Exception as error:
                    self._write_heartbeat(status="error", execute=execute, error=str(error))
                    raise
                count += 1
                if iterations is None or count < iterations:
                    time.sleep(poll_interval)
            self._write_heartbeat(status="stopped", execute=execute)
            return state
        except Exception as error:
            self._write_heartbeat(status="error", execute=execute, error=str(error))
            raise


# Short import name for callers which treat the module as the fleet service.
FleetCoordinator = CheckpointFleetCoordinator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="deterministic checkpoint fleet coordinator")
    parser.add_argument("--server-data", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--execute", action="store_true", help="allow lane preparation and lifecycle commands")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--helper-api-url")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("run-once")
    watch = sub.add_parser("watch")
    watch.add_argument("--iterations", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    coordinator = CheckpointFleetCoordinator(
        args.server_data, repo_root=args.repo_root, catalog_path=args.catalog,
        helper_api_url=args.helper_api_url,
    )
    if args.command == "status":
        result = coordinator.status()
    elif args.command == "run-once":
        result = coordinator.run_once(execute=args.execute)
    else:
        if args.iterations is not None and args.iterations < 1:
            raise SystemExit("--iterations must be positive")
        result = coordinator.watch(execute=args.execute, poll_interval=args.poll_interval, iterations=args.iterations)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
