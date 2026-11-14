# Path: training/laya_runtime_components.py | Purpose: Bind Track B episode outputs to immutable source identity.
"""Startup-only component provenance; declarations are not loaded-runtime attestation."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "laya-runtime-components-v1"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_bytes(*args: str) -> bytes | None:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                             capture_output=True, check=True).stdout.decode().strip()
        if Path(top).resolve() != ROOT.resolve():
            # Frozen copies nested in another repo do not inherit that repo's identity.
            return None
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
        return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def _git(*args: str) -> str | None:
    raw = _git_bytes(*args)
    return None if raw is None else raw.decode().strip()


def build_manifest(*, entrypoint: str, mission: dict | None = None,
                   world_root: Path | None = None, episode: dict | None = None,
                   runner_contracts: dict | None = None) -> dict:
    """Build a deterministic, portable JSON object from local source and declared artifacts."""
    patterns = ("training/**/*.py", "training/**/*.lua", "orchestrator/**/*.py",
                "planners/**/*.py", "tools/**/*.py", "core/**/*.py", "schemas/**/*.json",
                "factorio_mod/**/*.lua", "factorio_training_lab/**/*.lua")
    paths = sorted({p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()})
    # Catalog paths alone do not identify the rules in a frozen copy without Git.
    # Record both catalogs the selected path reads, and all their quoted sources.
    for relative in set(((mission or {}).get("catalogs") or {}).values()):
        catalog = (ROOT / relative).resolve()
        if not catalog.is_relative_to((ROOT / "docs/rl").resolve()):
            raise ValueError("catalog must be under docs/rl")
        paths.append(catalog)
        for rule in json.loads(catalog.read_bytes())["rules"]:
            source = (ROOT / rule["source"]).resolve()
            if (not source.is_relative_to((ROOT / "docs").resolve())
                    or source.is_relative_to((ROOT / "docs/archive").resolve())):
                raise ValueError("catalog source must be an active document")
            paths.append(source)
    paths = sorted(set(paths))
    files = {p.relative_to(ROOT).as_posix(): _digest(p.read_bytes()) for p in paths}
    status = _git("status", "--porcelain", "--untracked-files=no")
    diff = _git_bytes("diff", "--binary", "HEAD")
    declared = {"state": "unknown", "cause": "world manifest not supplied or absent"}
    if world_root is not None and (Path(world_root) / "manifest.json").is_file():
        raw = (Path(world_root) / "manifest.json").read_bytes()
        obj = json.loads(raw)
        # Only declared identity fields, never credentials or an assumed loaded version.
        keys = ("world_id", "engine_version", "engine_sha256", "mod_version", "mod_hashes",
                "surface", "force", "episode_id", "seed", "production_authority", "source_sha256",
                "ancestor_source_sha256", "version")
        declared = {"state": "declared", "artifact_sha256": _digest(raw),
                    "identity": {k: obj[k] for k in keys if k in obj},
                    "missing_cause": "field absent from declared world manifest"}
    return {"version": VERSION,
            "source": {"branch": _git("branch", "--show-current"), "commit": _git("rev-parse", "HEAD"),
                       "tracked_dirty": None if status is None else bool(status),
                       "tracked_diff_sha256": None if diff is None else _digest(diff),
                       "untracked_provenance": "not enumerated; component files are hashed",
                       "files_sha256": files},
            "execution": {"entrypoint": entrypoint, "python_executable": sys.executable,
                          "python_version": platform.python_version(), "invoked_program": sys.argv[0], "mission": mission,
                          "episode": episode},
            "contracts": runner_contracts or {},
            "runtime": {"declared_world": declared,
                        "loaded_engine": {"state": "unknown", "cause": "no runtime attestation supplied"},
                        "loaded_mods": {"state": "unknown", "cause": "source hashes do not attest installed or loaded mods"}}}


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


class ManifestMismatch(ValueError):
    """A startup attempted to replace evidence from a different component identity."""


def install_manifest(path: Path, manifest: dict, *, legacy: bool = False) -> dict:
    """Persist once; legacy resumes with no artifact retain explicitly unknown provenance."""
    path = Path(path)
    data = manifest_bytes(manifest)
    if path.exists():
        old = path.read_bytes()
        if old != data:
            raise ManifestMismatch(f"runtime component manifest mismatch: {path}")
    elif legacy:
        return {"state": "unknown", "cause": "legacy resume has no runtime component manifest"}
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Publish complete bytes exclusively: replacement would destroy evidence.
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ManifestMismatch(f"runtime component manifest mismatch: {path}") from None
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)
    return {"state": "known", "path": str(path), "sha256": _digest(data)}
