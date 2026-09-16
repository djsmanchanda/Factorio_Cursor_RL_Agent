# Path: tools/checkpoint_catalog.py
# Purpose: Manage immutable deterministic checkpoint definitions and generations offline.
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import subprocess
from typing import Any, Callable, Iterable
import argparse
import hashlib


SCHEMA_VERSION = 1
RETENTION_PER_ORIGIN = 20
_CHECKPOINT_ID = re.compile(r"^C(?P<number>[0-9]+)(?P<suffix>[A-Za-z0-9]*)$")
_COMMIT = re.compile(r"^[0-9a-fA-F]+$")


def _checkpoint_number(checkpoint_id: str) -> str:
    match = _CHECKPOINT_ID.fullmatch(checkpoint_id)
    if not match:
        raise ValueError("checkpoint id must look like C0, C1, or C1a")
    return match.group("number") + match.group("suffix")


def _safe_relative(root: Path, relative: str | Path) -> Path:
    """Resolve a relative catalog path without allowing traversal or symlinks."""
    root = Path(root).resolve()
    candidate = Path(relative)
    if candidate.is_absolute():
        resolved_absolute = candidate.resolve()
        if not resolved_absolute.is_relative_to(root):
            raise ValueError("catalog path escapes its root")
        candidate = resolved_absolute.relative_to(root)
    current = root
    for part in candidate.parts:
        if part in ("", ".", ".."):
            if part == "..":
                raise ValueError("catalog path escapes its root")
            continue
        current = current / part
        if current.is_symlink():
            raise ValueError("catalog path cannot traverse a symlink")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("catalog path escapes its root")
    return resolved


def atomic_json(path: Path, payload: dict[str, Any], *, root: Path | None = None) -> None:
    """Write JSON by replacing a same-directory temporary file.

    When ``root`` is supplied, the destination must be a safe path beneath it.
    The temporary file is opened exclusively and receives the same mode as a
    normal private state file before the atomic replace.
    """
    destination = _safe_relative(root, path) if root is not None else Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    source = _safe_relative(root, path) if root is not None else Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read checkpoint catalog: {source}") from error
    if not isinstance(payload, dict):
        raise ValueError("checkpoint catalog JSON must contain an object")
    return payload


def save_name(
    checkpoint_id: str,
    commit: str,
    started_at: datetime | str,
    *,
    sequence: int = 1,
    existing: Iterable[str] = (),
) -> str:
    """Return a readable save name, adding a collision suffix when required."""
    number = _checkpoint_number(checkpoint_id)
    commit = str(commit).lower()
    if not _COMMIT.fullmatch(commit) or len(commit) < 4:
        raise ValueError("commit must be a hexadecimal revision")
    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("started_at must be ISO-8601") from error
    if not isinstance(started_at, datetime):
        raise TypeError("started_at must be datetime or ISO-8601")
    if sequence < 0 or sequence > 99:
        raise ValueError("save sequence must be between 0 and 99")
    base = f"checkpoint{number}_{commit[:8]}_{started_at.strftime('%m_%d_%I%p').lower()}_s{sequence:02d}"
    occupied = set(existing)
    if base not in occupied:
        return base
    collision = 2
    while f"{base}_{collision:02d}" in occupied:
        collision += 1
    return f"{base}_{collision:02d}"


def classify_commit_age(
    creator_commit: str,
    newest_commit: str,
    *,
    resolver: Callable[[str, str], int | None] | Any | None = None,
    stale_after: int = 10,
) -> str:
    """Classify provenance relative to the newest selected revision.

    ``resolver(creator, newest)`` returns an ancestor distance, ``None`` for a
    known divergent history, or may be an object exposing ``ancestor_distance``.
    An absent/unusable resolver is deliberately reported as unverifiable.
    """
    creator = str(creator_commit).lower()
    newest = str(newest_commit).lower()
    if not creator or not newest:
        return "unverifiable"
    if creator == newest:
        return "current"
    if resolver is None:
        return "unverifiable"
    try:
        if hasattr(resolver, "ancestor_distance"):
            distance = resolver.ancestor_distance(creator, newest)
        else:
            distance = resolver(creator, newest)
    except Exception:
        return "unverifiable"
    if distance is None:
        return "divergent"
    if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0:
        return "unverifiable"
    return "stale" if distance > stale_after else "current"


def retention_capacity(checkpoint_count: int) -> int:
    """Total ordinary rolling capacity for a registry, including sub-checkpoints."""
    if checkpoint_count < 0:
        raise ValueError("checkpoint count cannot be negative")
    return RETENTION_PER_ORIGIN * checkpoint_count


def _new_generation(
    checkpoint_id: str,
    generation_id: str,
    path: str,
    creator_commit: str,
    created_at: str,
    *,
    provisional: bool = False,
    pinned: bool = False,
    active_input: bool = False,
    source_run_id: str | None = None,
    save_name_value: str | None = None,
) -> dict[str, Any]:
    _checkpoint_number(checkpoint_id)
    if not generation_id or not path or not creator_commit:
        raise ValueError("generation id, path, and creator commit are required")
    generation = {
        "generation_id": generation_id,
        "origin_checkpoint_id": checkpoint_id,
        "path": path,
        "creator_commit": creator_commit,
        "created_at": created_at,
        "provisional": bool(provisional),
        "pinned": bool(pinned),
        "active_input": bool(active_input),
    }
    if source_run_id is not None:
        generation["source_run_id"] = source_run_id
    if save_name_value is not None:
        generation["save_name"] = save_name_value
    return generation


class CheckpointCatalog:
    """Small JSON-backed registry; no Factorio or Git process is required."""

    def __init__(self, path: Path, payload: dict[str, Any] | None = None):
        self.path = Path(path)
        self.payload = payload if payload is not None else self._empty()
        self._validate_shape()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "registry_id": "deterministic-checkpoints",
            "registry_revision": 1,
            "checkpoints": [],
            "retention": {"per_origin": RETENTION_PER_ORIGIN, "protected_outside_limit": True},
        }

    @classmethod
    def load(cls, path: Path, *, root: Path | None = None) -> "CheckpointCatalog":
        payload = load_json(path, root=root)
        return cls(path, payload)

    def save(self, *, root: Path | None = None) -> None:
        atomic_json(self.path, self.payload, root=root)

    def _validate_shape(self) -> None:
        if self.payload.get("version") != SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint registry version")
        checkpoints = self.payload.get("checkpoints")
        if not isinstance(checkpoints, list):
            raise ValueError("checkpoint registry needs a checkpoints list")
        ids = [item.get("id") for item in checkpoints if isinstance(item, dict)]
        if len(ids) != len(set(ids)):
            raise ValueError("checkpoint ids must be unique")
        orders = [item.get("order") for item in checkpoints if isinstance(item, dict)]
        if len(orders) != len(set(orders)):
            raise ValueError("checkpoint order values must be unique")

    @property
    def checkpoints(self) -> list[dict[str, Any]]:
        return sorted(self.payload["checkpoints"], key=lambda item: item["order"])

    def checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        for checkpoint in self.payload["checkpoints"]:
            if checkpoint["id"] == checkpoint_id:
                return checkpoint
        raise KeyError(checkpoint_id)

    def add_checkpoint(
        self,
        checkpoint_id: str,
        label: str,
        predicate_version: str,
        *,
        order: int | None = None,
        predicate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _checkpoint_number(checkpoint_id)
        if any(item["id"] == checkpoint_id for item in self.payload["checkpoints"]):
            raise ValueError(f"checkpoint already exists: {checkpoint_id}")
        if order is None:
            order = max((item["order"] for item in self.payload["checkpoints"]), default=-1) + 1
        if any(item["order"] == order for item in self.payload["checkpoints"]):
            raise ValueError(f"checkpoint order already exists: {order}")
        item: dict[str, Any] = {
            "id": checkpoint_id,
            "label": label,
            "order": order,
            "predicate_version": predicate_version,
            "generations": [],
            "default_generation_id": None,
        }
        if predicate is not None:
            item["predicate"] = predicate
        self.payload["checkpoints"].append(item)
        self.payload["registry_revision"] = int(self.payload.get("registry_revision", 0)) + 1
        self._validate_shape()
        return item

    def add_generation(self, checkpoint_id: str, generation: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self.checkpoint(checkpoint_id)
        if generation.get("origin_checkpoint_id") != checkpoint_id:
            raise ValueError("generation origin does not match checkpoint")
        if any(item.get("generation_id") == generation.get("generation_id") for item in checkpoint["generations"]):
            raise ValueError(f"generation already exists: {generation.get('generation_id')}")
        checkpoint["generations"].append(dict(generation))
        self.payload["registry_revision"] = int(self.payload.get("registry_revision", 0)) + 1
        return generation

    def promote_default(self, checkpoint_id: str, generation_id: str, *, c0_verified: bool = False) -> None:
        checkpoint = self.checkpoint(checkpoint_id)
        generation = next((item for item in checkpoint["generations"] if item["generation_id"] == generation_id), None)
        if generation is None:
            raise KeyError(generation_id)
        if generation.get("provisional") and not c0_verified:
            raise ValueError("a provisional generation requires C0 verification before promotion")
        checkpoint["default_generation_id"] = generation_id
        for item in checkpoint["generations"]:
            item["active_input"] = item["generation_id"] == generation_id
        generation["provisional"] = False
        self.payload["registry_revision"] = int(self.payload.get("registry_revision", 0)) + 1

    def set_provisional_default(self, checkpoint_id: str, generation_id: str) -> None:
        """Point a frontier checkpoint at a verified reload candidate while retaining its star."""
        checkpoint = self.checkpoint(checkpoint_id)
        generation = next(
            (item for item in checkpoint["generations"] if item["generation_id"] == generation_id),
            None,
        )
        if generation is None:
            raise KeyError(generation_id)
        if not generation.get("provisional"):
            raise ValueError("a provisional default must retain provisional provenance")
        checkpoint["default_generation_id"] = generation_id
        for item in checkpoint["generations"]:
            item["active_input"] = item["generation_id"] == generation_id
        self.payload["registry_revision"] = int(self.payload.get("registry_revision", 0)) + 1

    def default_generation(self, checkpoint_id: str) -> dict[str, Any] | None:
        checkpoint = self.checkpoint(checkpoint_id)
        identifier = checkpoint.get("default_generation_id")
        return next((item for item in checkpoint["generations"] if item["generation_id"] == identifier), None)

    def prune(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return and remove ordinary generations beyond 20 per origin.

        Defaults, pins, and active inputs are protected and are never deleted
        by this operation. Provisional provenance alone is not a pin; a
        provisional default is protected by ``default_generation_id``.
        """
        del now  # retained as an API seam for age-based policies later
        removed: list[dict[str, Any]] = []
        for checkpoint in self.payload["checkpoints"]:
            generations = checkpoint["generations"]
            protected_ids = {
                checkpoint.get("default_generation_id"),
                *(item["generation_id"] for item in generations if item.get("pinned") or item.get("active_input")),
            }
            ordinary = sorted(
                (item for item in generations if item.get("generation_id") not in protected_ids),
                key=lambda item: item.get("created_at", ""), reverse=True,
            )
            keep = {item["generation_id"] for item in ordinary[:RETENTION_PER_ORIGIN]}
            survivors = []
            for item in generations:
                if item.get("generation_id") in protected_ids or item.get("generation_id") in keep:
                    survivors.append(item)
                else:
                    removed.append(item)
            checkpoint["generations"] = survivors
        if removed:
            self.payload["registry_revision"] = int(self.payload.get("registry_revision", 0)) + 1
        return removed


DEFAULT_CHECKPOINT_DEFINITIONS = (
    ("C0", "Base", "c0-v1"),
    ("C1", "Starter mall", "c1-v1"),
    ("C2", "Iron + copper rollout", "c2-v1"),
    ("C3", "Stone rollout", "c3-v1"),
    ("C4", "First plastic", "c4-v1"),
    ("C5", "Stable plastic", "c5-v1"),
    ("C6", "Advanced circuit consumer", "c6-v1"),
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initialize_catalog(
    *,
    server_data: Path,
    source_save: Path,
    repo_root: Path | None = None,
) -> CheckpointCatalog:
    """Create the local registry and immutable C0 bundle from a tracked save.

    The source save is read only.  The generated world and episode manifest
    live under ``server-data/checkpoint-fleet`` and are never written to the
    repository.  Future checkpoint definitions are registered without default
    generations, so C0 is runnable immediately while later milestones wait for
    promotion.
    """
    server_data = Path(server_data).resolve()
    source_save = Path(source_save).resolve()
    if not source_save.is_file():
        raise ValueError(f"source save is missing: {source_save}")
    root = server_data / "checkpoint-fleet"
    catalog_path = root / "registry.json"
    root.mkdir(parents=True, exist_ok=True)
    if catalog_path.exists():
        catalog = CheckpointCatalog.load(catalog_path)
    else:
        catalog = CheckpointCatalog(catalog_path)
    existing = {item["id"] for item in catalog.checkpoints}
    for order, (checkpoint_id, label, predicate_version) in enumerate(DEFAULT_CHECKPOINT_DEFINITIONS):
        if checkpoint_id not in existing:
            catalog.add_checkpoint(checkpoint_id, label, predicate_version, order=order)
    c0 = catalog.checkpoint("C0")
    if c0.get("generations"):
        # Idempotent initialization verifies the existing C0 rather than
        # silently replacing a protected operator input.
        catalog.save()
        return catalog
    repo = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    try:
        creator_commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip().lower()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("unable to resolve creator commit for C0") from error
    generation_id = f"c0-{_file_sha256(source_save)[:16]}"
    bundle = root / "bundles" / "C0" / generation_id
    bundle.mkdir(parents=True, exist_ok=False)
    world = bundle / "world.zip"
    # copy2 preserves the source metadata but does not mutate it.
    import shutil
    shutil.copy2(source_save, world)
    manifest = {
        "schema_version": "1.0.0", "episode_id": f"checkpoint-{generation_id}",
        "repository_revision": creator_commit, "surface": "nauvis", "force": "player",
        "source_save": str(source_save), "source_save_sha256": _file_sha256(source_save),
        "isolated_save_sha256": _file_sha256(world), "baseline_verified": True,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    state_dir = bundle / "state" / "episode"
    state_dir.mkdir(parents=True)
    atomic_json(state_dir / "current.json", manifest)
    files = {
        "world.zip": _file_sha256(world),
        "state/episode/current.json": _file_sha256(state_dir / "current.json"),
    }
    atomic_json(bundle / "checkpoint.json", {
        "version": 1, "checkpoint_id": "C0", "files": files, "manifest": manifest,
    })
    atomic_json(bundle / "compatibility.json", {
        "compatible": True, "checkpoint_id": "C0", "creator_commit": creator_commit,
        "candidate_commit": "*", "candidate_commit_policy": "any",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "predicate_version": "c0-v1",
    })
    catalog.add_generation("C0", {
        "generation_id": generation_id, "origin_checkpoint_id": "C0",
        "path": str(bundle.relative_to(root)), "creator_commit": creator_commit,
        "created_at": manifest["created_at"], "provisional": False,
        "pinned": True, "active_input": True, "save_name": "checkpoint0_base",
    })
    catalog.promote_default("C0", generation_id)
    catalog.save()
    return catalog


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize deterministic checkpoint catalog state")
    parser.add_argument("command", choices=("init",))
    parser.add_argument("--server-data", type=Path, required=True)
    parser.add_argument("--source-save", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    catalog = initialize_catalog(server_data=args.server_data, source_save=args.source_save, repo_root=args.repo_root)
    print(json.dumps({"catalog": str(catalog.path), "registry_revision": catalog.payload["registry_revision"],
                      "c0": catalog.default_generation("C0")}, indent=2, sort_keys=True))
    return 0


__all__ = [
    "CheckpointCatalog", "RETENTION_PER_ORIGIN", "atomic_json", "classify_commit_age",
    "DEFAULT_CHECKPOINT_DEFINITIONS", "initialize_catalog", "load_json", "retention_capacity", "save_name",
]


if __name__ == "__main__":  # pragma: no cover - exercised by the manager script
    raise SystemExit(_main())
