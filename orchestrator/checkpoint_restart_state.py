# Path: orchestrator/checkpoint_restart_state.py | Purpose: Transfer bounded strict acceptance and episode-owned sidecars for isolated restart segments.
"""Restart-conditioned regression state, not a controller or world checkpoint."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot, REEARNED_VERSIONS
from orchestrator.oil_transaction import OilTransactionStore
from orchestrator.operation_invocations import decode_record
from orchestrator.loan_gate_retries import (
    retry_path, decode_state as decode_gate_retries, empty_state as empty_gate_retries,
)

VERSION = 4
MAX_FILES = 256
MAX_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 20
SURFACE, FORCE, PROFILE = "nauvis", "player", "reduced-v1"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_INVOCATION_FILE = re.compile(r"[0-9a-f]{32}\.json\Z", re.ASCII)
_DISTRICT_FILE = re.compile(r"[0-9a-f]{16}\.json\Z", re.ASCII)
_DIRECTORY_ROLES = {'bootstrap_district', 'operation_invocations'}
#: Snapshot fields added after bundles were already captured: absent means "not observed" (None).
#: Export omits them while None so a restored older bundle re-exports byte-identically.
_LATER_SNAPSHOT_FIELDS = frozenset({'plastic'})


def _valid_name(role, name, path):
    if not isinstance(name, str):
        return False
    if role == 'bootstrap_district':
        return bool(_DISTRICT_FILE.fullmatch(name))
    if role == 'operation_invocations':
        return bool(_INVOCATION_FILE.fullmatch(name)) or name == 'legacy-unknown.json'
    return name == path.name


def validate_episode_id(value: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Unsafe episode identity")
    return value


def _scope(episode_id: str, mod_scope: str) -> dict:
    validate_episode_id(episode_id)
    if not isinstance(mod_scope, str) or not mod_scope.strip() or len(mod_scope) > 512:
        raise ValueError("An explicit bounded mod scope is required")
    return {"episode_id": episode_id, "surface": SURFACE, "force": FORCE,
            "bootstrap_profile": PROFILE, "mod_scope": mod_scope}


def sidecar_registry(script_output: Path, episode_id: str) -> dict[str, Path]:
    """Exact ownership locations; bootstrap_district is a directory, others files."""
    validate_episode_id(episode_id)
    logs = Path(script_output).parent / "logs"
    material = logs / "deterministic-material-reservations" / f"{episode_id}.json"
    return {"material_reservations": material,
            "steel_seed": material.with_suffix(".steel-seed.json"),
            "steel_district": material.with_suffix(".steel-district.json"),
            "bootstrap_work": logs / "deterministic-bootstrap-work" / f"{episode_id}.json",
            "bootstrap_district": logs / "deterministic-bootstrap-districts" / episode_id,
            "loan_gate_retries": retry_path(script_output, episode_id),
            "operation_invocations": Path(script_output) / ("operation-invocations-" + hashlib.sha256(episode_id.encode()).hexdigest()[:20]),
            "oil_transaction": OilTransactionStore(Path(script_output), episode_id, SURFACE, FORCE).path}


def _no_symlinks(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"Symlink in sidecar path: {path}")


def _files(path: Path, role: str) -> list[Path]:
    _no_symlinks(path)
    if not path.exists():
        return []
    if role not in _DIRECTORY_ROLES:
        if not path.is_file():
            raise ValueError(f"Invalid sidecar file: {path}")
        return [path]
    if not path.is_dir():
        raise ValueError(f"Invalid sidecar directory: {path}")
    result = sorted(path.iterdir())
    for entry in result:
        _no_symlinks(entry)
        if not _valid_name(role, entry.name, path) or not entry.is_file():
            raise ValueError(f"Unexpected {role} sidecar: {entry}")
    return result


def _read_sidecars(registry: dict[str, Path], episode_id: str) -> dict:
    result = {}
    total = count = 0
    for role, path in registry.items():
        entries = {}
        for entry in _files(path, role):
            if entry.stat().st_size > MAX_BYTES - total:
                raise ValueError("Sidecar bundle exceeds size limits")
            data = entry.read_bytes()
            total += len(data)
            count += 1
            if total > MAX_BYTES or count > MAX_FILES:
                raise ValueError("Sidecar bundle exceeds size limits")
            try:
                if role == "operation_invocations":
                    decode_record(data, entry.name, episode_id)
                elif role == "loan_gate_retries":
                    decode_gate_retries(data, episode_id)
                else:
                    json.loads(data)
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError(f"Invalid JSON sidecar: {entry}") from error
            entries[entry.name] = {"sha256": hashlib.sha256(data).hexdigest(),
                                   "base64": base64.b64encode(data).decode("ascii")}
        result[role] = entries
    return result


def _decode_typed(value, annotation):
    if annotation is Any:
        # Foundation census rows contain JSON evidence, including nested
        # positions and nullable counters. Retain it without scalar coercion.
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, list):
            return [_decode_typed(item, Any) for item in value]
        if isinstance(value, dict) and all(type(key) is str for key in value):
            return {key: _decode_typed(item, Any) for key, item in value.items()}
        raise ValueError("Invalid JSON snapshot evidence")
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (types.UnionType, Union):
        for option in args:
            try:
                return _decode_typed(value, option)
            except ValueError:
                pass
        raise ValueError("Invalid snapshot field")
    if annotation is type(None):
        if value is None:
            return None
    elif annotation in (str, int, bool):
        if type(value) is annotation:
            return value
    elif origin is tuple:
        if isinstance(value, list):
            return tuple(_decode_typed(item, args[0]) for item in value)
    elif origin is Mapping:
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return {key: _decode_typed(item, args[1]) for key, item in value.items()}
    elif isinstance(annotation, type) and is_dataclass(annotation):
        hints = get_type_hints(annotation)
        names = {item.name for item in fields(annotation)}
        optional = _LATER_SNAPSHOT_FIELDS if annotation is FactorySnapshot else frozenset()
        if isinstance(value, dict) and names - optional <= set(value) <= names:
            return annotation(**{name: _decode_typed(value.get(name), hints[name]) for name in names})
    raise ValueError("Invalid snapshot shape or field type")


def _snapshot_record(snapshot: FactorySnapshot) -> dict:
    record = asdict(snapshot)
    for name in _LATER_SNAPSHOT_FIELDS:
        if record.get(name) is None:
            record.pop(name, None)
    return record


def reearned_milestones(bundle_milestones, reached) -> list[dict]:
    """Reached milestones whose recorded version the current code re-earns instead of refusing.

    A bundle that reached C5 under c5-v1 keeps every other reached milestone; C5 must be earned again
    under c5-v2. Returns one ``{id, from_version, to_version}`` record per dropped milestone.
    """
    current = {item.checkpoint_id: item.predicate_version for item in CheckpointEvaluator().milestones}
    recorded = {item.get("id"): item.get("predicate_version") for item in bundle_milestones or ()
                if isinstance(item, dict)}
    return [{"id": cid, "from_version": recorded[cid], "to_version": current[cid]}
            for cid in sorted(set(reached or ()))
            if cid in current and recorded.get(cid) != current[cid]
            and recorded.get(cid) in REEARNED_VERSIONS.get(cid, ())]


def migrate_reached(state: dict) -> tuple[dict, list[dict]]:
    """``state`` with re-earned milestones removed from ``reached`` (a copy), and the migration records."""
    reached = state.get("reached")
    if not isinstance(reached, list) or not all(isinstance(cid, str) for cid in reached):
        return state, []  # malformed: left for the importer's own validation to refuse
    dropped = reearned_milestones(state.get("milestones"), reached)
    if not dropped:
        return state, []
    ids = {item["id"] for item in dropped}
    return {**state, "reached": [cid for cid in state.get("reached") or () if cid not in ids]}, dropped


def _milestones(evaluator: CheckpointEvaluator) -> list[dict]:
    return [{"id": item.checkpoint_id, "predicate_version": item.predicate_version}
            for item in evaluator.milestones]


@dataclass(frozen=True)
class RestartState:
    episode_id: str
    script_output: Path
    baseline: FactorySnapshot
    history: tuple[FactorySnapshot, ...]
    evaluator: CheckpointEvaluator
    evidence_state: dict
    # Reached milestones dropped on import to be re-earned under a newer version (see migrate_reached).
    reearned: tuple = ()


def export_restart_state(script_output: Path, episode_id: str, mod_scope: str,
                         baseline: FactorySnapshot, history: list[FactorySnapshot],
                         evaluator: CheckpointEvaluator, evidence_state: dict | None = None) -> dict:
    scope = _scope(episode_id, mod_scope)
    if not isinstance(baseline, FactorySnapshot) or not 1 <= len(history) <= MAX_HISTORY:
        raise ValueError("A baseline and bounded, nonempty strict history are required")
    if not all(isinstance(sample, FactorySnapshot) for sample in history):
        raise ValueError("Strict history must contain snapshots")
    if not isinstance(evaluator, CheckpointEvaluator) or _milestones(evaluator) != _milestones(CheckpointEvaluator()):
        raise ValueError("Only the current default strict evaluator is supported")
    if not evaluator.reached <= {item.checkpoint_id for item in evaluator.milestones}:
        raise ValueError("Unknown reached checkpoint")
    from orchestrator.checkpoint_evidence import new_evidence_state, validate_evidence_state
    evidence_state = validate_evidence_state(evidence_state if evidence_state is not None else new_evidence_state(origin="unknown"))
    bundle = {"evidence_state": evidence_state, "version": VERSION, "scope": scope, "milestones": _milestones(evaluator),
              "baseline": _snapshot_record(baseline), "history": [_snapshot_record(sample) for sample in history],
              "reached": sorted(evaluator.reached),
              "sidecars": _read_sidecars(sidecar_registry(script_output, episode_id), episode_id)}
    try:
        encoded = json.dumps(bundle, allow_nan=False)
        decoded = json.loads(encoded)
        _decode_typed(decoded["baseline"], FactorySnapshot)
        for sample in decoded["history"]:
            _decode_typed(sample, FactorySnapshot)
    except (TypeError, ValueError) as error:
        raise ValueError("Strict state is not serializable") from error
    if len(encoded.encode()) > MAX_BYTES * 2:
        raise ValueError("Restart bundle exceeds size limit")
    return decoded


def plan_sidecars(bundle: dict, script_output: Path, *, episode_id: str) -> list[tuple[Path, bytes]]:
    """Validate a bundle's embedded sidecars and return their writes; touches nothing.

    Shared by the strict importer and the PM replay, which cannot decode older
    snapshot shapes but must install the same ownership state: invocation
    journals (uncertain-dispatch fences), loan-gate retry budgets and the
    unknown-provenance markers of pre-v3/v4 bundles. Every destination role
    must be empty.
    """
    validate_episode_id(episode_id)
    if not isinstance(bundle, dict) or type(bundle.get("version")) is not int or bundle["version"] not in (1, 2, 3, VERSION):
        raise ValueError("Restart version or scope mismatch")
    if not isinstance(bundle.get("scope"), dict) or bundle["scope"].get("episode_id") != episode_id:
        raise ValueError("Restart version or scope mismatch")
    registry = sidecar_registry(script_output, episode_id)
    sidecars = bundle.get("sidecars")
    expected_roles = set(registry)
    if bundle['version'] < 4:
        expected_roles.remove('loan_gate_retries')
    if bundle['version'] < 3:
        expected_roles.remove('operation_invocations')
    if not isinstance(sidecars, dict) or set(sidecars) != expected_roles:
        raise ValueError("Incomplete sidecar registry")
    pending = []
    total = 0
    for role, path in registry.items():
        if _files(path, role):
            raise ValueError(f"Destination sidecar is not empty: {role}")
        entries = sidecars.get(role, {})
        if not isinstance(entries, dict):
            raise ValueError("Invalid sidecar entries")
        for name, payload in entries.items():
            if not _valid_name(role, name, path):
                raise ValueError("Invalid sidecar name")
            if not isinstance(payload, dict) or set(payload) != {"sha256", "base64"}:
                raise ValueError("Invalid sidecar payload")
            try:
                data = base64.b64decode(payload["base64"], validate=True)
                json.loads(data)
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                raise ValueError("Invalid sidecar encoding or JSON") from error
            total += len(data)
            if total > MAX_BYTES or len(pending) >= MAX_FILES or hashlib.sha256(data).hexdigest() != payload["sha256"]:
                raise ValueError("Sidecar limit or digest mismatch")
            if role == "operation_invocations":
                decode_record(data, name, episode_id)
            elif role == "loan_gate_retries":
                decode_gate_retries(data, episode_id)
            pending.append((path / name if role in _DIRECTORY_ROLES else path, data))
    if bundle["version"] < 3:
        pending.append((registry["operation_invocations"] / "legacy-unknown.json", b'{"provenance":"unknown"}'))
        total += len(pending[-1][1])
    if bundle['version'] < 4:
        data = json.dumps(empty_gate_retries(episode_id, provenance='unknown')).encode()
        pending.append((registry['loan_gate_retries'], data))
        total += len(data)
    if len(pending) > MAX_FILES or total > MAX_BYTES:
        raise ValueError('Legacy provenance marker exceeds sidecar limit')
    return pending


def install_sidecars(*groups: list[tuple[Path, bytes]]) -> list[Path]:
    """Create every validated file exclusively and durably; remove all of them on any failure.

    Groups may name the same path (an embedded role that is also a registered
    auxiliary file) only with identical bytes; it is written once.
    """
    writes: dict[Path, bytes] = {}
    for path, data in (item for group in groups for item in group):
        if writes.setdefault(path, data) != data:
            raise ValueError(f"Conflicting restart sidecar sources: {path}")
    pending = list(writes.items())
    created = []
    directories = set()
    try:
        for path, data in pending:
            parent = path.parent
            directories.add(parent)
            while not parent.exists():
                parent = parent.parent
                directories.add(parent)
            path.parent.mkdir(parents=True, exist_ok=True)
            _no_symlinks(path)
            with path.open("xb") as handle:
                created.append(path)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        # Sync newly created parent entries as well as the final file directories.
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return [path for path, _ in pending]


def import_restart_state(bundle: dict, script_output: Path, *, episode_id: str, mod_scope: str,
                         extra_writes: list[tuple[Path, bytes]] = ()) -> RestartState:
    """Preflight the complete bundle, then install sidecars into an empty isolated target.

    ``extra_writes`` are already validated auxiliary files installed in the
    same all-or-nothing step, so nothing is written unless everything passed.
    """
    scope = _scope(episode_id, mod_scope)
    try:
        encoded = json.dumps(bundle, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Restart bundle must be JSON") from error
    if len(encoded.encode()) > MAX_BYTES * 2:
        raise ValueError("Restart bundle exceeds size limit")
    if not isinstance(bundle, dict) or set(bundle) != ({"version", "scope", "milestones", "baseline", "history", "reached", "sidecars"} | ({"evidence_state"} if bundle.get("version") in (2, 3, 4) else set())):
        raise ValueError("Invalid restart bundle shape")
    if type(bundle["version"]) is not int or bundle["version"] not in (1, 2, 3, VERSION) or bundle["scope"] != scope:
        raise ValueError("Restart version or scope mismatch")
    evaluator = CheckpointEvaluator()
    current = _milestones(evaluator)
    # A milestone reached under a version the current code re-earns (c5-v1 -> c5-v2) is dropped from
    # reached here and recorded on the state; any other reached difference is still refused below.
    bundle, reearned = migrate_reached(bundle)
    if bundle["milestones"] != current:
        # A newer version of an unreached milestone (c6-v2) may differ; reached ones must not.
        reached_ids = set(bundle.get("reached") or ())
        current_by_id = {item["id"]: item for item in current}
        if any(item["id"] in reached_ids and current_by_id.get(item["id"]) != item
               for item in bundle["milestones"]):
            raise ValueError("Strict milestone definitions differ")
    baseline = _decode_typed(bundle["baseline"], FactorySnapshot)
    history = bundle["history"]
    if not isinstance(history, list) or not 1 <= len(history) <= MAX_HISTORY:
        raise ValueError("Invalid strict history")
    history = tuple(_decode_typed(sample, FactorySnapshot) for sample in history)
    reached = bundle["reached"]
    if (not isinstance(reached, list) or not all(isinstance(item, str) for item in reached)
            or reached != sorted(set(reached)) or not set(reached) <= {m.checkpoint_id for m in evaluator.milestones}):
        raise ValueError("Invalid reached checkpoints")
    evaluator.reached = set(reached)
    from orchestrator.checkpoint_evidence import new_evidence_state, validate_evidence_state
    evidence_state = validate_evidence_state(bundle["evidence_state"]) if bundle["version"] >= 2 else new_evidence_state(origin="unknown")
    install_sidecars(plan_sidecars(bundle, script_output, episode_id=episode_id), list(extra_writes))
    return RestartState(episode_id, Path(script_output).resolve(), baseline, history, evaluator, evidence_state,
                        tuple(reearned))
