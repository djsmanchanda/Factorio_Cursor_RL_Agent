# Path: training/laya_checkpoint_bundle.py | Purpose: Fail-closed disposable checkpoint capture and restoration contract.
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zipfile import is_zipfile

from tools.checkpoint_catalog import _safe_relative, atomic_json

VERSION = "laya-regression-checkpoint-v2"
ROLES = ("controller", "ownership", "construction", "material_claims", "policy_rng", "phase_baselines", "strict_evaluator")
IDENTITIES = ("source_sha256", "creator_code_sha256", "creator_commit", "mod_sha256", "factorio_version", "predicate_version", "predicate_sha256", "state_schema", "ancestor_sha256")
HEX = frozenset("0123456789abcdef")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _identity(identity: dict) -> None:
    for key in IDENTITIES:
        value = identity.get(key)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError(f"missing identity: {key}")
        if key.endswith("sha256") and (len(value) != 64 or not set(value) <= HEX):
            raise ValueError(f"invalid sha256: {key}")


def capture(catalog: Path, generation: str, hook, *, checkpoint: str, identity: dict, lineage: str) -> dict:
    """Capture only via a quiescent runner hook; never accepts a raw ZIP as state.

    Hook returns a paused snapshot and named serialized durable state. It owns
    pausing, save completion and resumption; the caller must serialize decisions.
    """
    _identity(identity)
    if checkpoint not in {f"C{number}" for number in range(1, 7)} or not lineage:
        raise ValueError("invalid checkpoint or lineage")
    catalog = Path(catalog).resolve()
    if not generation or Path(generation).name != generation or generation in (".", ".."):
        raise ValueError("invalid generation")
    target = _safe_relative(catalog, generation)
    if target.exists():
        raise ValueError("generation already exists")
    catalog.mkdir(parents=True, exist_ok=True)
    snapshot = hook.capture_quiescent()
    if snapshot.get("paused") is not True or not isinstance(snapshot.get("tick"), int) or snapshot["tick"] < 0:
        raise ValueError("runner did not attest a quiescent tick")
    if snapshot.get("before_fingerprint") != snapshot.get("after_fingerprint") or not snapshot.get("before_fingerprint"):
        raise ValueError("world changed during capture")
    if snapshot.get("checkpoint") != checkpoint or snapshot.get("strict_predicate_version") != f"{checkpoint.lower()}-v1" or snapshot.get("strict_earned") is not True:
        raise ValueError("checkpoint not strictly earned")
    sidecars = snapshot.get("sidecars")
    if not isinstance(sidecars, dict) or set(sidecars) != set(ROLES):
        raise ValueError("incomplete registered durable state")
    save = Path(snapshot["save"])
    if save.is_symlink() or not is_zipfile(save):
        raise ValueError("invalid captured save")
    temporary = Path(tempfile.mkdtemp(prefix=".capture-", dir=catalog))
    try:
        shutil.copyfile(save, temporary / "source.zip")
        for role in ROLES:
            if not isinstance(sidecars[role], dict):
                raise ValueError(f"invalid state: {role}")
            atomic_json(temporary / f"{role}.json", sidecars[role])
        files = {p.name: digest(p) for p in sorted(temporary.iterdir())}
        manifest = {"version": VERSION, "generation": generation, "checkpoint": checkpoint,
                    "tick": snapshot["tick"], "fingerprint": snapshot["before_fingerprint"],
                    "identity": identity, "lineage": lineage, "production_authority": False,
                    "files": files}
        atomic_json(temporary / "checkpoint.json", manifest)
        os.rename(temporary, target)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def validate(catalog: Path, generation: str, *, expected: dict | None = None) -> dict:
    root = _safe_relative(Path(catalog), generation)
    if root.name != generation or not root.is_dir():
        raise ValueError("unknown catalog generation")
    try:
        manifest = json.loads(_safe_relative(root, "checkpoint.json").read_text())
        if manifest.get("version") != VERSION or manifest.get("production_authority") is not False or manifest.get("generation") != generation:
            raise ValueError("incompatible bundle")
        _identity(manifest["identity"])
        if manifest.get("checkpoint") not in {f"C{number}" for number in range(1, 7)} or not isinstance(manifest.get("tick"), int) or manifest["tick"] < 0 or not manifest.get("fingerprint") or not manifest.get("lineage"):
            raise ValueError("invalid checkpoint metadata")
        files = manifest["files"]
        if set(files) != {"source.zip", *(f"{role}.json" for role in ROLES)}:
            raise ValueError("incomplete registered durable state")
        if {p.name for p in root.iterdir()} != set(files) | {"checkpoint.json"}:
            raise ValueError("unexpected bundle content")
        for name, checksum in files.items():
            path = _safe_relative(root, name)
            if not path.is_file() or digest(path) != checksum:
                raise ValueError(f"hash mismatch: {name}")
            if name.endswith(".json") and not isinstance(json.loads(path.read_text()), dict):
                raise ValueError(f"invalid sidecar: {name}")
        if not is_zipfile(root / "source.zip"):
            raise ValueError("invalid save")
        for key, value in (expected or {}).items():
            if manifest["identity"].get(key) != value:
                raise ValueError(f"incompatible identity: {key}")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid or incomplete checkpoint") from error
    return manifest


def restore(catalog: Path, generation: str, hook, *, expected: dict, attempt_id: str) -> dict:
    manifest = validate(catalog, generation, expected=expected)
    root = _safe_relative(Path(catalog), generation)
    state = {role: json.loads((root / f"{role}.json").read_text()) for role in ROLES}
    if not attempt_id or attempt_id == manifest["lineage"]:
        raise ValueError("new attempt identity required")
    hook.restore_quiescent(root / "source.zip", state, manifest, attempt_id)
    return manifest
