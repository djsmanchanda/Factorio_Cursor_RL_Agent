# Path: training/laya_restart_state.py | Purpose: Transfer bounded strict acceptance and episode-owned sidecars for isolated restart segments.
"""Restart-conditioned regression state, not a controller or world checkpoint."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import types
from typing import Any, get_args, get_origin, get_type_hints

from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
from orchestrator.oil_transaction import OilTransactionStore

VERSION = 1
MAX_FILES = 256
MAX_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 20
SURFACE, FORCE, PROFILE = "nauvis", "player", "reduced-v1"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_DISTRICT_FILE = re.compile(r"[0-9a-f]{16}\.json\Z", re.ASCII)


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
            "oil_transaction": OilTransactionStore(Path(script_output), episode_id, SURFACE, FORCE).path}


def _no_symlinks(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"Symlink in sidecar path: {path}")


def _files(path: Path, directory: bool) -> list[Path]:
    _no_symlinks(path)
    if not path.exists():
        return []
    if not directory:
        if not path.is_file():
            raise ValueError(f"Invalid sidecar file: {path}")
        return [path]
    if not path.is_dir():
        raise ValueError(f"Invalid sidecar directory: {path}")
    result = sorted(path.iterdir())
    for entry in result:
        _no_symlinks(entry)
        if not _DISTRICT_FILE.fullmatch(entry.name) or not entry.is_file():
            raise ValueError(f"Unexpected district sidecar: {entry}")
    return result


def _read_sidecars(registry: dict[str, Path]) -> dict:
    result = {}
    total = count = 0
    for role, path in registry.items():
        entries = {}
        for entry in _files(path, role == "bootstrap_district"):
            if entry.stat().st_size > MAX_BYTES - total:
                raise ValueError("Sidecar bundle exceeds size limits")
            data = entry.read_bytes()
            total += len(data)
            count += 1
            if total > MAX_BYTES or count > MAX_FILES:
                raise ValueError("Sidecar bundle exceeds size limits")
            try:
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
    if origin in (types.UnionType,):
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
        if isinstance(value, dict) and set(value) == names:
            return annotation(**{name: _decode_typed(value[name], hints[name]) for name in names})
    raise ValueError("Invalid snapshot shape or field type")


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


def export_restart_state(script_output: Path, episode_id: str, mod_scope: str,
                         baseline: FactorySnapshot, history: list[FactorySnapshot],
                         evaluator: CheckpointEvaluator) -> dict:
    scope = _scope(episode_id, mod_scope)
    if not isinstance(baseline, FactorySnapshot) or not 1 <= len(history) <= MAX_HISTORY:
        raise ValueError("A baseline and bounded, nonempty strict history are required")
    if not all(isinstance(sample, FactorySnapshot) for sample in history):
        raise ValueError("Strict history must contain snapshots")
    if not isinstance(evaluator, CheckpointEvaluator) or _milestones(evaluator) != _milestones(CheckpointEvaluator()):
        raise ValueError("Only the current default strict evaluator is supported")
    if not evaluator.reached <= {item.checkpoint_id for item in evaluator.milestones}:
        raise ValueError("Unknown reached checkpoint")
    bundle = {"version": VERSION, "scope": scope, "milestones": _milestones(evaluator),
              "baseline": asdict(baseline), "history": [asdict(sample) for sample in history],
              "reached": sorted(evaluator.reached),
              "sidecars": _read_sidecars(sidecar_registry(script_output, episode_id))}
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


def import_restart_state(bundle: dict, script_output: Path, *, episode_id: str, mod_scope: str) -> RestartState:
    """Preflight the complete bundle, then install sidecars into an empty isolated target."""
    scope = _scope(episode_id, mod_scope)
    try:
        encoded = json.dumps(bundle, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Restart bundle must be JSON") from error
    if len(encoded.encode()) > MAX_BYTES * 2:
        raise ValueError("Restart bundle exceeds size limit")
    if not isinstance(bundle, dict) or set(bundle) != {"version", "scope", "milestones", "baseline", "history", "reached", "sidecars"}:
        raise ValueError("Invalid restart bundle shape")
    if type(bundle["version"]) is not int or bundle["version"] != VERSION or bundle["scope"] != scope:
        raise ValueError("Restart version or scope mismatch")
    evaluator = CheckpointEvaluator()
    if bundle["milestones"] != _milestones(evaluator):
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
    registry = sidecar_registry(script_output, episode_id)
    sidecars = bundle["sidecars"]
    if not isinstance(sidecars, dict) or set(sidecars) != set(registry):
        raise ValueError("Incomplete sidecar registry")
    pending = []
    total = 0
    for role, path in registry.items():
        if _files(path, role == "bootstrap_district"):
            raise ValueError(f"Destination sidecar is not empty: {role}")
        entries = sidecars[role]
        if not isinstance(entries, dict):
            raise ValueError("Invalid sidecar entries")
        for name, payload in entries.items():
            if not isinstance(name, str) or (not _DISTRICT_FILE.fullmatch(name) if role == "bootstrap_district" else name != path.name):
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
            pending.append((path / name if role == "bootstrap_district" else path, data))
    created = []
    try:
        for path, data in pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            _no_symlinks(path)
            with path.open("xb") as handle:
                created.append(path)
                handle.write(data)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return RestartState(episode_id, Path(script_output).resolve(), baseline, history, evaluator)
