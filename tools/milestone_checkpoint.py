# Path: tools/milestone_checkpoint.py
# Purpose: Capture and verify runnable deterministic milestone checkpoints.
"""First-class checkpoint bundles for deterministic segment regression.

This module is deliberately separate from :mod:`episode_checkpoint`.  The
latter is a diagnostic failed-world fixture and its contract must not change.
Bundles produced here are runnable inputs for a disposable regression lane,
but are never fresh end-to-end acceptance evidence.

Capture is intentionally a small orchestration seam.  It does not start a
Factorio process, stop one, or deploy a mod.  Callers supply the controller
quiescence and handshake checks and an already-connected RCON client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import zipfile
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools.runner_process import running_runner_pid


SCHEMA_VERSION = 1
BUNDLE_KIND = "milestone-regression"
RESTORE_KIND = "milestone-regression-restore"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_SAVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHECKPOINT_ID = re.compile(r"^C[0-9]+[A-Za-z0-9]*$")


class MilestoneCheckpointError(ValueError):
    """The checkpoint cannot safely be captured, verified, or restored."""


@dataclass(frozen=True)
class SidecarSpec:
    """One registered durable sidecar source.

    ``recursive`` copies every regular file below a directory.  A required
    recursive entry requires at least one file; optional entries are useful
    for ledgers which are not created before the corresponding milestone.
    """

    path: str
    required: bool = True
    recursive: bool = False

    def __post_init__(self) -> None:
        relative = _relative_path(self.path, field="sidecar path")
        if relative != self.path:
            object.__setattr__(self, "path", relative)


def default_sidecar_registry() -> tuple[SidecarSpec, ...]:
    """Return the durable controller-state registry used by new captures.

    Optional entries are intentionally registered even when a low-level
    checkpoint has not created them yet.  If a caller requires a particular
    ledger, pass a ``SidecarSpec(..., required=True)`` explicitly.
    """

    return (
        SidecarSpec("episode/current.json"),
        SidecarSpec("logs/deterministic-mission-state.json", required=False),
        SidecarSpec("logs/autonomous-priorities.json", required=False),
        SidecarSpec("logs/research-queue.json", required=False),
        SidecarSpec("logs/production-acceptance.json", required=False),
        SidecarSpec("logs/deterministic-blockers.jsonl", required=False),
        SidecarSpec("logs/deterministic-events.jsonl", required=False),
        SidecarSpec("logs/deterministic-material-reservations", required=False, recursive=True),
        SidecarSpec("logs/deterministic-bootstrap-districts", required=False, recursive=True),
        SidecarSpec("logs/deterministic-bootstrap-work", required=False, recursive=True),
        SidecarSpec("logs/oil-transactions", required=False, recursive=True),
        # Power, resource-district, steam-bootstrap and persisted construction
        # plans live below the server-owned script-output tree.
        SidecarSpec("script-output/logs", required=False, recursive=True),
    )


def _relative_path(value: str | Path, *, field: str = "path") -> str:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise MilestoneCheckpointError(f"{field} must be a safe relative path")
    if any(part in ("", ".") for part in path.parts):
        raise MilestoneCheckpointError(f"{field} must not contain empty or dot components")
    return path.as_posix()


# A named immutable registry is useful to coordinators that want to record the
# exact set used for a capture.  ``SidecarSpec`` is frozen, so callers cannot
# mutate this default through the exported constant.
DURABLE_SIDECAR_REGISTRY = default_sidecar_registry()


def _safe_id(value: str, field: str) -> str:
    value = str(value).strip()
    if not value or not _SAFE_ID.fullmatch(value):
        raise MilestoneCheckpointError(f"{field} must be a non-empty safe identifier")
    return value


def _checkpoint_id(value: str) -> str:
    value = _safe_id(value, "checkpoint_id")
    if not _CHECKPOINT_ID.fullmatch(value):
        raise MilestoneCheckpointError("checkpoint_id must look like C0 or C1a")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise MilestoneCheckpointError(f"unable to hash checkpoint file: {path}") from error
    return digest.hexdigest()


def _safe_existing(root: Path, relative: str | Path, *, field: str = "path") -> Path:
    relative_text = _relative_path(relative, field=field)
    root = Path(root).resolve()
    current = root
    for part in Path(relative_text).parts:
        current = current / part
        if current.is_symlink():
            raise MilestoneCheckpointError(f"{field} cannot traverse a symlink: {relative_text}")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise MilestoneCheckpointError(f"{field} escapes its root: {relative_text}")
    return resolved


def _safe_source(root: Path, relative: str, *, required: bool, recursive: bool) -> list[tuple[Path, str]]:
    source = _safe_existing(root, relative, field="registered sidecar")
    if not source.exists():
        if required:
            raise MilestoneCheckpointError(f"registered sidecar is missing: {relative}")
        return []
    if recursive:
        if not source.is_dir():
            raise MilestoneCheckpointError(f"registered sidecar directory is not a directory: {relative}")
        paths: list[tuple[Path, str]] = []
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(root).as_posix()
            _safe_existing(root, rel, field="registered sidecar")
            if path.is_symlink():
                raise MilestoneCheckpointError(f"registered sidecar cannot be a symlink: {rel}")
            if path.is_file():
                paths.append((path, rel))
        if required and not paths:
            raise MilestoneCheckpointError(f"registered sidecar directory is empty: {relative}")
        return paths
    if source.is_symlink() or not source.is_file():
        raise MilestoneCheckpointError(f"registered sidecar is not a regular file: {relative}")
    return [(source, relative)]


def _registry(value: Sequence[SidecarSpec | str] | None) -> tuple[SidecarSpec, ...]:
    specs = default_sidecar_registry() if value is None else tuple(
        item if isinstance(item, SidecarSpec) else SidecarSpec(str(item)) for item in value
    )
    seen: set[str] = set()
    for spec in specs:
        if spec.path in seen:
            raise MilestoneCheckpointError(f"duplicate registered sidecar: {spec.path}")
        seen.add(spec.path)
    return specs


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _check_gate(name: str, gate: Callable[[], Any] | None, *, required: bool = True) -> None:
    if gate is None:
        if required:
            raise MilestoneCheckpointError(f"{name} check is required")
        return
    try:
        result = gate()
    except Exception as error:
        raise MilestoneCheckpointError(f"{name} check failed") from error
    if isinstance(result, Mapping):
        result = result.get("ok", result.get("quiesced", False))
    if result is not True:
        raise MilestoneCheckpointError(f"{name} check did not confirm success")


def _output_pairing(root: Path, client: Any, timeout: float) -> None:
    output = root / "script-output"
    output.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    name = f"checkpoint-pair-{nonce}.json"
    path = _safe_existing(root, f"script-output/{name}", field="output probe")
    try:
        command = (
            '/sc helpers.write_file("' + name + '",helpers.table_to_json({nonce="' + nonce + '"}),false)'
        )
        client.command(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_symlink():
                raise MilestoneCheckpointError("runtime output probe is symlinked")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.02)
                continue
            if payload.get("nonce") != nonce:
                raise MilestoneCheckpointError("runtime/output pairing nonce mismatch")
            return
        raise MilestoneCheckpointError("runtime/output pairing timed out")
    finally:
        path.unlink(missing_ok=True)


def _wait_for_save(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is None:
                    return
        except (OSError, zipfile.BadZipFile):
            time.sleep(0.02)
            continue
    raise MilestoneCheckpointError("checkpoint world save did not finish")


def _save_record(path: Path, relative: str) -> dict[str, Any]:
    return {"path": relative, "sha256": sha256(path), "size": path.stat().st_size}


def readable_save_name(checkpoint_id: str, creator_commit: str, started_at: datetime | str, *, sequence: int = 1) -> str:
    """Build the requested human-readable save name without shell metacharacters."""
    checkpoint_id = _checkpoint_id(checkpoint_id)
    commit = str(creator_commit).lower()
    _safe_id(commit, "creator_commit")
    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise MilestoneCheckpointError("started_at must be ISO-8601") from error
    if not isinstance(started_at, datetime):
        raise MilestoneCheckpointError("started_at must be datetime or ISO-8601")
    if not isinstance(sequence, int) or sequence < 0:
        raise MilestoneCheckpointError("save sequence must be non-negative")
    number = checkpoint_id[1:]
    return f"checkpoint{number}_{commit[:8]}_{started_at.strftime('%m_%d_%I%p').lower()}_s{sequence:02d}"


def capture(
    root: Path,
    *,
    client: Any,
    checkpoint_id: str,
    generation_id: str,
    run_id: str,
    lineage_id: str,
    capture_tick: int | None = None,
    predicate_version: str,
    predicate_evidence: Mapping[str, Any],
    creator_commit: str,
    mod_hashes: Mapping[str, str],
    reason: str = "milestone",
    save_name: str | None = None,
    started_at: datetime | str | None = None,
    sequence: int = 1,
    quiescence_check: Callable[[], Any] | None = None,
    controller_handshake: Callable[[], Any] | None = None,
    sidecar_registry: Sequence[SidecarSpec | str] | None = None,
    timeout: float = 120,
) -> Path:
    """Capture one immutable milestone bundle from a quiesced runtime.

    The function only talks to the supplied client and copies local files.  A
    caller owns process lifecycle and supplies explicit quiescence/handshake
    functions; no server or runner is started, stopped, or deployed here.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise MilestoneCheckpointError(f"checkpoint root is missing: {root}")
    checkpoint_id = _checkpoint_id(checkpoint_id)
    generation_id = _safe_id(generation_id, "generation_id")
    run_id = _safe_id(run_id, "run_id")
    lineage_id = _safe_id(lineage_id, "lineage_id")
    predicate_version = _safe_id(predicate_version, "predicate_version")
    creator_commit = _safe_id(creator_commit, "creator_commit")
    if not isinstance(predicate_evidence, Mapping):
        raise MilestoneCheckpointError("predicate evidence must be an object")
    if not isinstance(mod_hashes, Mapping) or not mod_hashes or any(not str(v) for v in mod_hashes.values()):
        raise MilestoneCheckpointError("mod hashes are required")
    if reason not in {"5-minute", "five-minute", "periodic", "milestone", "terminal", "manual"}:
        raise MilestoneCheckpointError("unsupported checkpoint capture reason")
    if quiescence_check is None:
        quiescence_check = lambda: running_runner_pid(root / "logs/autonomous-run.pid") is None
    _check_gate("runner/controller quiescence", quiescence_check)
    _check_gate("controller handshake", controller_handshake)
    _output_pairing(root, client, timeout=min(float(timeout), 10.0))

    started = started_at or datetime.now(timezone.utc)
    name = save_name or readable_save_name(checkpoint_id, creator_commit, started, sequence=sequence)
    if not _SAFE_SAVE.fullmatch(name):
        raise MilestoneCheckpointError("save_name contains unsafe characters")
    saves = root / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    saved = saves / f"{name}.zip"
    if saved.exists() or saved.is_symlink():
        raise MilestoneCheckpointError(f"checkpoint save already exists: {name}")
    try:
        response = str(client.command(f'/sc game.server_save("{name}");rcon.print(game.tick)')).strip()
    except Exception as error:
        raise MilestoneCheckpointError("Factorio server_save command failed") from error
    try:
        tick = int(response) if capture_tick is None else int(capture_tick)
    except (TypeError, ValueError) as error:
        raise MilestoneCheckpointError("server_save did not return a numeric capture tick") from error
    if tick < 0:
        raise MilestoneCheckpointError("capture tick cannot be negative")
    _wait_for_save(saved, timeout)

    registry = _registry(sidecar_registry)
    bundle_root = root / "checkpoints" / checkpoint_id / generation_id
    if bundle_root.exists():
        raise MilestoneCheckpointError(f"checkpoint generation already exists: {generation_id}")
    staging = bundle_root.with_name(f".{bundle_root.name}.{uuid.uuid4().hex}.staging")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copy2(saved, staging / "world.zip")
        sidecar_records: list[dict[str, Any]] = []
        copied: set[str] = set()
        for spec in registry:
            for source, relative in _safe_source(root, spec.path, required=spec.required, recursive=spec.recursive):
                if relative in copied:
                    continue
                target = staging / "state" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.add(relative)
                sidecar_records.append(_save_record(target, f"state/{relative}"))
        if "state/episode/current.json" not in {record["path"] for record in sidecar_records}:
            raise MilestoneCheckpointError("checkpoint requires episode/current.json")
        creator_manifest = json.loads((staging / "state/episode/current.json").read_text(encoding="utf-8"))
        if not isinstance(creator_manifest, dict):
            raise MilestoneCheckpointError("creator episode manifest must be an object")
        creator_manifest_sha = sha256(staging / "state/episode/current.json")
        manifest = {
            "version": SCHEMA_VERSION,
            "kind": BUNDLE_KIND,
            "acceptance_eligible": False,
            "segment_regression_eligible": True,
            "bundle_id": f"{checkpoint_id}-{generation_id}-{uuid.uuid4().hex}",
            "checkpoint_id": checkpoint_id,
            "generation_id": generation_id,
            "run_id": run_id,
            "lineage_id": lineage_id,
            "capture_tick": tick,
            "capture_reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "save_name": name,
            "predicate": {"version": predicate_version, "evidence": dict(predicate_evidence)},
            "provenance": {
                "creator_commit": creator_commit,
                "mod_hashes": dict(mod_hashes),
                "surface": "nauvis",
                "force": "player",
                "predicate_version": predicate_version,
                "source_run_id": run_id,
                "source_lineage_id": lineage_id,
                "creator_manifest": {"path": "state/episode/current.json", "sha256": creator_manifest_sha},
            },
            "world": _save_record(staging / "world.zip", "world.zip"),
            "sidecars": sidecar_records,
        }
        all_records = [manifest["world"], *sidecar_records]
        manifest["files"] = {record["path"]: record["sha256"] for record in all_records}
        manifest["file_manifest"] = {
            record["path"]: {"sha256": record["sha256"], "size": record["size"]}
            for record in all_records
        }
        _atomic_json(staging / "checkpoint.json", manifest)
        verify(staging)
        staging.rename(bundle_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    saved.unlink(missing_ok=True)
    return bundle_root


def verify(bundle: Path, *, expected_checkpoint_id: str | None = None,
           sidecar_registry: Sequence[SidecarSpec | str] | None = None) -> dict[str, Any]:
    """Verify identity, complete file manifest, sidecars, and ZIP integrity."""
    bundle = Path(bundle).resolve()
    if not bundle.is_dir() or bundle.is_symlink():
        raise MilestoneCheckpointError("checkpoint bundle is missing or symlinked")
    try:
        payload = json.loads((bundle / "checkpoint.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MilestoneCheckpointError("checkpoint manifest is unreadable") from error
    if payload.get("version") != SCHEMA_VERSION or payload.get("kind") != BUNDLE_KIND:
        raise MilestoneCheckpointError("invalid milestone checkpoint manifest")
    if payload.get("acceptance_eligible") is not False:
        raise MilestoneCheckpointError("milestone checkpoint cannot be fresh acceptance eligible")
    if payload.get("segment_regression_eligible") is not True:
        raise MilestoneCheckpointError("milestone checkpoint is not eligible for segment regression")
    checkpoint_id = payload.get("checkpoint_id")
    _checkpoint_id(str(checkpoint_id))
    if expected_checkpoint_id is not None and checkpoint_id != expected_checkpoint_id:
        raise MilestoneCheckpointError("checkpoint ID does not match expected lane")
    for field in ("generation_id", "run_id", "lineage_id", "capture_reason", "save_name"):
        _safe_id(str(payload.get(field)), field) if field != "capture_reason" else None
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise MilestoneCheckpointError(f"checkpoint identity is missing {field}")
    if not isinstance(payload.get("capture_tick"), int) or payload["capture_tick"] < 0:
        raise MilestoneCheckpointError("checkpoint capture tick is invalid")
    predicate = payload.get("predicate")
    provenance = payload.get("provenance")
    if not isinstance(predicate, dict) or not isinstance(predicate.get("version"), str) or not isinstance(predicate.get("evidence"), dict):
        raise MilestoneCheckpointError("checkpoint predicate evidence is invalid")
    if not isinstance(provenance, dict) or provenance.get("surface") != "nauvis" or provenance.get("force") != "player":
        raise MilestoneCheckpointError("checkpoint provenance scope is invalid")
    creator = provenance.get("creator_commit")
    hashes = provenance.get("mod_hashes")
    if not isinstance(creator, str) or not creator or not isinstance(hashes, dict) or not hashes or any(not isinstance(v, str) or not v for v in hashes.values()):
        raise MilestoneCheckpointError("checkpoint creator identity is incomplete")
    files = payload.get("files")
    world = payload.get("world")
    sidecars = payload.get("sidecars")
    if not isinstance(files, dict) or not isinstance(world, dict) or not isinstance(sidecars, list) or not sidecars:
        raise MilestoneCheckpointError("checkpoint file manifest is incomplete")
    records = [world, *sidecars]
    listed: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not isinstance(record.get("sha256"), str) or not isinstance(record.get("size"), int):
            raise MilestoneCheckpointError("checkpoint file record is invalid")
        relative = _relative_path(record["path"], field="checkpoint file")
        if relative in listed or record["size"] < 0:
            raise MilestoneCheckpointError("checkpoint file manifest contains duplicate/invalid records")
        listed.add(relative)
        path = _safe_existing(bundle, relative, field="checkpoint file")
        if not path.is_file() or path.stat().st_size != record["size"] or sha256(path) != record["sha256"]:
            raise MilestoneCheckpointError(f"checkpoint file hash/size mismatch: {relative}")
        if relative == "world.zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:
                        raise MilestoneCheckpointError("checkpoint world ZIP is corrupt")
            except zipfile.BadZipFile as error:
                raise MilestoneCheckpointError("checkpoint world ZIP is corrupt") from error
    if world.get("path") != "world.zip" or files.get("world.zip") != world.get("sha256"):
        raise MilestoneCheckpointError("checkpoint world manifest is inconsistent")
    if set(files) != listed:
        raise MilestoneCheckpointError("checkpoint hash manifest has missing or extra paths")
    if any(files.get(path) != next(record["sha256"] for record in records if record["path"] == path) for path in listed):
        raise MilestoneCheckpointError("checkpoint hash manifest is inconsistent")
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "checkpoint.json"
    }
    if actual != listed:
        raise MilestoneCheckpointError("checkpoint contains unmanifested or missing files")
    creator_record = provenance.get("creator_manifest")
    if not isinstance(creator_record, dict) or creator_record.get("path") != "state/episode/current.json" or creator_record.get("sha256") != files.get("state/episode/current.json"):
        raise MilestoneCheckpointError("creator manifest is not immutably recorded")
    try:
        creator_manifest = json.loads((bundle / "state/episode/current.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MilestoneCheckpointError("creator manifest is unreadable") from error
    if not isinstance(creator_manifest, dict):
        raise MilestoneCheckpointError("creator manifest must be an object")
    recorded_commit = creator_manifest.get("repository_revision")
    if recorded_commit is not None and str(recorded_commit).lower() != creator.lower():
        raise MilestoneCheckpointError("creator manifest commit does not match checkpoint provenance")
    for key, expected in hashes.items():
        recorded_hash = creator_manifest.get(key)
        if recorded_hash is not None and str(recorded_hash) != expected:
            raise MilestoneCheckpointError(f"creator manifest mod hash does not match checkpoint provenance: {key}")
    return payload


def extract(bundle: Path, destination: Path, *, expected_checkpoint_id: str | None = None) -> Path:
    """Verify and copy a bundle into a new, isolated lane directory."""
    payload = verify(bundle, expected_checkpoint_id=expected_checkpoint_id)
    bundle = Path(bundle).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise MilestoneCheckpointError("restore destination must be a new directory")
    if destination.is_relative_to(bundle):
        raise MilestoneCheckpointError("restore destination cannot be inside the checkpoint bundle")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for relative in payload["files"]:
            source = _safe_existing(bundle, relative, field="checkpoint file")
            target = _safe_existing(destination, relative, field="restore file")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        _atomic_json(destination / "MILESTONE_RESTORE.json", {
            "version": SCHEMA_VERSION,
            "kind": RESTORE_KIND,
            "acceptance_eligible": False,
            "source_bundle_id": payload["bundle_id"],
            "checkpoint_id": payload["checkpoint_id"],
            "generation_id": payload["generation_id"],
            "creator_manifest": payload["provenance"]["creator_manifest"],
            "files": dict(payload["files"]),
        })
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


restore = extract
capture_milestone = capture
verify_bundle = verify
extract_bundle = extract


__all__ = [
    "BUNDLE_KIND", "DURABLE_SIDECAR_REGISTRY", "MilestoneCheckpointError", "RESTORE_KIND",
    "SCHEMA_VERSION", "SidecarSpec", "capture", "capture_milestone", "default_sidecar_registry",
    "extract", "extract_bundle", "readable_save_name", "restore", "sha256", "verify", "verify_bundle",
]
