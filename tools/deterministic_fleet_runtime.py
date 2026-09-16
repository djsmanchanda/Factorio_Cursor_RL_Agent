# Path: tools/deterministic_fleet_runtime.py
# Purpose: Build isolated deterministic checkpoint lanes without starting live processes.
"""Offline lane preparation for the deterministic checkpoint fleet.

The fleet coordinator owns process lifecycle, but this module deliberately does
not start Factorio, RCON, systemd, or a GUI client.  It resolves a clean
detached candidate revision, copies a checkpoint bundle into a private lane,
and emits the exact server/runner commands that a coordinator may execute.

Keeping preparation separate from lifecycle is important: a checkpoint replay
must never overwrite ``saves/mod_playground.zip`` in the repository, deploy to
the GUI profile, or accidentally reuse another lane's helper state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from typing import Any, Mapping, Sequence

from orchestrator.checkpoint_milestones import default_milestones


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLEET_ROOT = Path.home() / ".local/share/factorio-rl/deterministic/checkpoint-fleet"
MAX_ACTIVE_SERVERS = 8
DEFAULT_GAME_PORT = 34200
DEFAULT_RCON_PORT = 27100
CHECKPOINT_ID_PREFIX = "C"


class FleetRuntimeError(RuntimeError):
    """A lane cannot be prepared safely."""


def _safe_id(value: str, field: str) -> str:
    value = str(value).strip()
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise FleetRuntimeError(f"{field} must be a non-empty safe identifier")
    return value


def _checkpoint_id(value: str) -> str:
    value = _safe_id(value, "checkpoint_id")
    if not value.startswith(CHECKPOINT_ID_PREFIX) or not value[1:].isalnum():
        raise FleetRuntimeError("checkpoint_id must look like C0 or C1a")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_hash(root: Path) -> str:
    """Match the manager/autonomous-run directory hash contract."""
    root = root.resolve()
    if not root.is_dir():
        raise FleetRuntimeError(f"candidate mod directory is missing: {root}")
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        file_digest = sha256(path)
        digest.update(f"{file_digest}  ./{path.relative_to(root)}\n".encode("utf-8"))
    return digest.hexdigest()


def _safe_copy(source: Path, destination: Path, *, root: Path) -> None:
    """Copy one file while rejecting links and paths outside the bundle."""
    source = source.resolve()
    root = root.resolve()
    if not source.is_file() or not source.is_relative_to(root):
        raise FleetRuntimeError(f"unsafe or missing checkpoint file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _git(repo: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise FleetRuntimeError(f"git command failed: {' '.join(arguments)}: {detail.strip()}") from error
    return result.stdout.strip()


def resolve_clean_commit(repo: Path, commit: str) -> str:
    """Resolve a revision and reject dirty source trees before a candidate run."""
    repo = Path(repo).resolve()
    if not (repo / ".git").exists():
        raise FleetRuntimeError(f"candidate repository is not a Git checkout: {repo}")
    # The operator checkout may contain unrelated work in progress.  A lane
    # is pinned to the immutable object below; cleanliness is enforced only on
    # the detached worktree created for that lane.
    return _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")


def create_detached_checkout(repo: Path, destination: Path, commit: str) -> str:
    """Create a clean detached worktree for one lane and return its full SHA.

    This function only creates a source checkout.  It never starts a server or
    synchronizes a GUI mod directory.  A pre-existing destination is rejected
    to prevent accidental reuse of another candidate's mutable tree.
    """
    repo = Path(repo).resolve()
    destination = Path(destination).resolve()
    full_sha = resolve_clean_commit(repo, commit)
    if destination.exists():
        raise FleetRuntimeError(f"candidate checkout already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(repo, "worktree", "add", "--detach", str(destination), full_sha)
        if _git(destination, "status", "--porcelain", "--untracked-files=normal"):
            raise FleetRuntimeError("detached candidate checkout is dirty")
    except Exception:
        # Do not hide the original failure.  A failed worktree is left for
        # explicit operator inspection rather than using a broad destructive
        # cleanup operation.
        raise
    return full_sha


@dataclass(frozen=True)
class LanePorts:
    game: int
    rcon: int

    def __post_init__(self) -> None:
        for name, value in (("game", self.game), ("rcon", self.rcon)):
            if not isinstance(value, int) or not 1 <= value <= 65535:
                raise FleetRuntimeError(f"{name} port must be between 1 and 65535")
        if self.game == self.rcon:
            raise FleetRuntimeError("game and RCON ports must differ")


@dataclass(frozen=True)
class LanePlan:
    lane_id: str
    lineage_id: str
    suite_id: str
    attempt_id: str
    checkpoint_id: str
    generation_id: str
    lane_root: Path
    candidate_checkout: Path
    candidate_commit: str
    creator_commit: str
    ports: LanePorts
    helper_enabled: bool
    helper_data_root: Path
    helper_report_root: Path
    helper_api_url: str
    run_id: str
    manifest_path: Path
    mission_mode: str | None = None
    mission_target: str | None = None
    checkpoint_ids: tuple[str, ...] = ()
    predicate_versions: Mapping[str, str] | None = None
    registry_version: str = "default-v1"
    capture_policy: Mapping[str, Any] | None = None
    result_path: str = "episode/fleet-result.json"

    @property
    def save_path(self) -> Path:
        return self.lane_root / "saves" / "mod_playground.zip"

    @property
    def source_save_path(self) -> Path:
        return self.lane_root / "saves" / "checkpoint-input.zip"

    @property
    def script_output_path(self) -> Path:
        return self.lane_root / "script-output"

    @property
    def log_path(self) -> Path:
        return self.lane_root / "logs" / "autonomous-run.log"

    @property
    def rcon_secret_path(self) -> Path:
        return self.lane_root / "rcon-password"

    def server_command(
        self,
        *,
        server_manager: Path | None = None,
        runtime_root: Path | None = None,
        factorio_bin: Path | None = None,
        read_data: Path | None = None,
    ) -> list[str]:
        manager = server_manager or REPO_ROOT / "scripts/manage_linux_deterministic_server.sh"
        command = [str(manager), "start", "--fleet-mode", "--root", str(self.lane_root),
                   "--game-port", str(self.ports.game), "--rcon-port", str(self.ports.rcon)]
        if runtime_root is not None:
            command += ["--runtime-root", str(runtime_root)]
        if factorio_bin is not None:
            command += ["--factorio-bin", str(factorio_bin)]
        if read_data is not None:
            command += ["--read-data", str(read_data)]
        return command

    def runner_command(
        self,
        *,
        runner_manager: Path | None = None,
        python_bin: Path | None = None,
        produce: str | None = None,
        technology: str | None = None,
    ) -> list[str]:
        manager = runner_manager or REPO_ROOT / "scripts/manage_linux_deterministic_runner.sh"
        command = [str(manager), "start", "--fleet-mode", "--root", str(self.lane_root),
                   "--rcon-port", str(self.ports.rcon), "--episode-manifest", str(self.manifest_path),
                   "--candidate-checkout", str(self.candidate_checkout),
                   "--run-id", self.run_id, "--opencode-helper-api-url", self.helper_api_url,
                   "--opencode-helper-data-root", str(self.helper_data_root),
                   "--opencode-helper-report-root", str(self.helper_report_root)]
        if not self.helper_enabled:
            command.append("--no-opencode-helper")
        if python_bin is not None:
            command += ["--python", str(python_bin)]
        if produce is not None:
            command += ["--produce", produce]
        elif technology is not None:
            command += ["--technology", technology]
        elif self.mission_mode == "produce" and self.mission_target:
            command += ["--produce", self.mission_target]
        elif self.mission_mode == "research" and self.mission_target:
            command += ["--technology", self.mission_target]
        return command


def _load_checkpoint(bundle: Path, checkpoint_id: str) -> tuple[dict[str, Any], Path]:
    bundle = Path(bundle).resolve()
    if not bundle.is_dir():
        raise FleetRuntimeError(f"checkpoint bundle is missing: {bundle}")
    try:
        payload = json.loads((bundle / "checkpoint.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FleetRuntimeError(f"checkpoint manifest is unreadable: {bundle}") from error
    if payload.get("version") != 1 or payload.get("checkpoint_id", checkpoint_id) != checkpoint_id:
        raise FleetRuntimeError("checkpoint manifest is incompatible with requested checkpoint")
    files = payload.get("files")
    if not isinstance(files, dict) or "world.zip" not in files:
        raise FleetRuntimeError("checkpoint manifest must contain a hashed world.zip")
    for relative, expected in files.items():
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise FleetRuntimeError("checkpoint contains an unsafe relative path")
        path = (bundle / relative).resolve()
        if not path.is_file() or not path.is_relative_to(bundle) or sha256(path) != expected:
            raise FleetRuntimeError(f"checkpoint file hash/path mismatch: {relative}")
    return payload, bundle


def validate_compatibility_manifest(
    compatibility: Mapping[str, Any] | Path,
    *,
    checkpoint_id: str,
    creator_commit: str,
    candidate_commit: str,
) -> dict[str, Any]:
    """Require an explicit compatibility decision for newer-code replays."""
    if isinstance(compatibility, Path):
        try:
            compatibility = json.loads(compatibility.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FleetRuntimeError("compatible-code manifest is unreadable") from error
    record = dict(compatibility)
    if record.get("compatible") is not True:
        raise FleetRuntimeError("checkpoint/code compatibility is not explicitly approved")
    if record.get("checkpoint_id") != checkpoint_id:
        raise FleetRuntimeError("compatibility manifest checkpoint does not match lane")
    if str(record.get("creator_commit", "")).lower() != creator_commit.lower():
        raise FleetRuntimeError("compatibility manifest creator commit does not match checkpoint")
    candidate_value = str(record.get("candidate_commit", "")).lower()
    if candidate_value != candidate_commit.lower() and not (
        candidate_value == "*" and record.get("candidate_commit_policy") == "any"
    ):
        raise FleetRuntimeError("compatibility manifest candidate commit does not match lane")
    if not record.get("checked_at") or not record.get("predicate_version"):
        raise FleetRuntimeError("compatibility manifest needs checked_at and predicate_version")
    return record


class DeterministicFleetRuntime:
    """Prepare independent server/runner lanes for a pinned candidate suite."""

    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        fleet_root: Path = DEFAULT_FLEET_ROOT,
        game_port_base: int = DEFAULT_GAME_PORT,
        rcon_port_base: int = DEFAULT_RCON_PORT,
        max_servers: int = MAX_ACTIVE_SERVERS,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.fleet_root = Path(fleet_root).resolve()
        if not 1 <= max_servers <= MAX_ACTIVE_SERVERS:
            raise FleetRuntimeError(f"max_servers must be between 1 and {MAX_ACTIVE_SERVERS}")
        self.max_servers = max_servers
        self.game_port_base = game_port_base
        self.rcon_port_base = rcon_port_base

    def plan_lane(
        self,
        *,
        suite_id: str,
        attempt_id: str,
        checkpoint_id: str,
        generation_id: str,
        candidate_commit: str,
        creator_commit: str,
        helper_enabled: bool = False,
        lane_id: str | None = None,
        lineage_id: str | None = None,
        slot: int = 0,
        helper_api_url: str | None = None,
        checkpoint_ids: Sequence[str] | None = None,
        predicate_versions: Mapping[str, str] | None = None,
        registry_version: str = "default-v1",
        capture_policy: Mapping[str, Any] | None = None,
        result_path: str = "episode/fleet-result.json",
        mission_mode: str | None = None,
        mission_target: str | None = None,
    ) -> LanePlan:
        suite_id = _safe_id(suite_id, "suite_id")
        attempt_id = _safe_id(attempt_id, "attempt_id")
        checkpoint_id = _checkpoint_id(checkpoint_id)
        generation_id = _safe_id(generation_id, "generation_id")
        lane_id = _safe_id(lane_id or f"lane-{uuid.uuid4().hex}", "lane_id")
        lineage_id = _safe_id(lineage_id or f"lineage-{uuid.uuid4().hex}", "lineage_id")
        if not isinstance(slot, int) or not 0 <= slot < self.max_servers:
            raise FleetRuntimeError("slot must be within the configured fleet capacity")
        ports = LanePorts(self.game_port_base + slot * 2, self.rcon_port_base + slot * 2)
        lane_root = self.fleet_root / "lanes" / lane_id
        checkout = lane_root / "candidate"
        run_id = f"{suite_id}-{attempt_id}"
        helper_data = lane_root / "helper" / "data"
        helper_reports = lane_root / "helper" / "reports"
        if helper_enabled and not helper_api_url:
            raise FleetRuntimeError(
                "enabled lane helpers require an explicit served dashboard URL"
            )
        api = helper_api_url or ""
        if mission_mode not in {None, "produce", "research"}:
            raise FleetRuntimeError("mission_mode must be produce or research")
        if mission_mode is not None:
            mission_target = _safe_id(str(mission_target or ""), "mission_target")
        elif mission_target is not None:
            raise FleetRuntimeError("mission_target requires mission_mode")
        definitions = tuple(checkpoint_ids or (item.checkpoint_id for item in default_milestones()))
        versions = dict(predicate_versions or {
            item.checkpoint_id: item.predicate_version for item in default_milestones()
        })
        return LanePlan(
            lane_id=lane_id, lineage_id=lineage_id, suite_id=suite_id,
            attempt_id=attempt_id, checkpoint_id=checkpoint_id,
            generation_id=generation_id, lane_root=lane_root,
            candidate_checkout=checkout, candidate_commit=candidate_commit,
            creator_commit=creator_commit, ports=ports,
            helper_enabled=bool(helper_enabled), helper_data_root=helper_data,
            helper_report_root=helper_reports, helper_api_url=api,
            run_id=run_id, manifest_path=lane_root / "episode" / "current.json",
            mission_mode=mission_mode, mission_target=mission_target,
            checkpoint_ids=definitions, predicate_versions=versions,
            registry_version=registry_version,
            capture_policy=dict(capture_policy or {
                "on_milestone": True, "on_terminal": True,
                "periodic_seconds": 300,
            }),
            result_path=result_path,
        )

    def prepare_lane(
        self,
        plan: LanePlan,
        checkpoint_bundle: Path,
        *,
        compatibility_manifest: Mapping[str, Any] | Path,
        candidate_checkout: Path | None = None,
        read_data: Path | None = None,
    ) -> dict[str, Any]:
        """Prepare one lane and remove only a newly-created partial root on error."""
        existed = plan.lane_root.exists()
        checkout_path = Path(candidate_checkout or plan.candidate_checkout).resolve()
        checkout_existed = checkout_path.exists()
        try:
            return self._prepare_lane_unchecked(
                plan, checkpoint_bundle,
                compatibility_manifest=compatibility_manifest,
                candidate_checkout=candidate_checkout,
                read_data=read_data,
            )
        except Exception:
            # The lane path is an exact, newly allocated target.  Never remove
            # an operator-provided pre-existing root while trying to recover a
            # failed preparation.
            if not checkout_existed and checkout_path.exists():
                # Remove only a worktree created by this invocation; an
                # externally supplied candidate checkout is never touched.
                try:
                    _git(self.repo_root, "worktree", "remove", "--force", str(checkout_path))
                except FleetRuntimeError:
                    # Preserve the original preparation error.  The exact
                    # path is still visible for explicit operator cleanup.
                    pass
            if not existed and plan.lane_root.exists():
                shutil.rmtree(plan.lane_root)
            raise

    def _prepare_lane_unchecked(
        self,
        plan: LanePlan,
        checkpoint_bundle: Path,
        *,
        compatibility_manifest: Mapping[str, Any] | Path,
        candidate_checkout: Path | None = None,
        read_data: Path | None = None,
    ) -> dict[str, Any]:
        """Materialize one lane and return its derived manifest.

        The creator's checkpoint manifest is read only.  All mutable files are
        copied beneath ``plan.lane_root`` and candidate mod copies are sourced
        from the detached checkout.  A compatibility decision is mandatory,
        including when candidate and creator happen to be the same revision.
        """
        payload, bundle = _load_checkpoint(checkpoint_bundle, plan.checkpoint_id)
        creator_manifest = payload.get("manifest")
        if not isinstance(creator_manifest, dict):
            manifest_file = bundle / "state/episode/current.json"
            try:
                creator_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise FleetRuntimeError("checkpoint has no creator episode manifest") from error
        creator_commit = str(creator_manifest.get("repository_revision") or plan.creator_commit)
        creator_commit = creator_commit.lower()
        candidate_commit = plan.candidate_commit.lower()
        compatibility = validate_compatibility_manifest(
            compatibility_manifest, checkpoint_id=plan.checkpoint_id,
            creator_commit=creator_commit, candidate_commit=candidate_commit,
        )
        source_world = bundle / "world.zip"
        lane = plan.lane_root.resolve()
        lane.mkdir(parents=True, exist_ok=False)
        checkout = Path(candidate_checkout or plan.candidate_checkout).resolve()
        if not checkout.is_dir():
            # The normal path creates a detached worktree inside the lane.  A
            # caller may provide an already-created checkout for dry-run tests
            # or a coordinator that shares read-only Git worktrees.
            create_detached_checkout(self.repo_root, checkout, candidate_commit)
        if _git(checkout, "status", "--porcelain", "--untracked-files=normal"):
            raise FleetRuntimeError("candidate checkout is dirty")
        resolved_candidate = _git(checkout, "rev-parse", "HEAD").lower()
        if resolved_candidate != candidate_commit:
            raise FleetRuntimeError("candidate checkout revision does not match lane manifest")
        for directory in ("saves", "mods", "logs", "script-output", "episode"):
            (lane / directory).mkdir(parents=True, exist_ok=True)
        # These files are lane-local equivalents of the server manager's
        # bootstrap output.  No GUI profile or repository save is touched.
        (lane / "config.ini").write_text(
            "[path]\nread-data=" + str(Path(read_data or "").resolve()) +
            "\nwrite-data=" + str(lane) + "\n",
            encoding="utf-8",
        )
        _atomic_json(lane / "server-settings.json", {
            "name": f"Deterministic checkpoint lane {plan.lane_id}",
            "description": "Private isolated headless checkpoint replay",
            "visibility": {"public": False, "lan": False},
            "game_password": "", "require_user_verification": False,
            "auto_pause": False, "auto_pause_when_players_connect": False,
        })
        secret = lane / "rcon-password"
        secret.write_text(uuid.uuid4().hex + uuid.uuid4().hex, encoding="ascii")
        secret.chmod(0o600)
        _safe_copy(source_world, plan.source_save_path, root=bundle)
        _safe_copy(source_world, plan.save_path, root=bundle)
        # Sidecars are copied as data, never linked.  The original creator
        # manifest remains in the immutable checkpoint bundle.
        state = bundle / "state"
        if state.is_dir():
            for source in state.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(state)
                    _safe_copy(source, lane / relative, root=state)
        for name, source_name in (("factorio_cursor_rl_agent", "factorio_mod"), ("factorio_training_lab", "factorio_training_lab")):
            source = checkout / source_name
            if not source.is_dir():
                raise FleetRuntimeError(f"candidate checkout is missing mod source: {source_name}")
            destination = lane / "mods" / name
            shutil.copytree(source, destination, symlinks=False)
        for path in (lane / "mods").glob("*"):
            if path.name not in {"factorio_cursor_rl_agent", "factorio_training_lab"}:
                continue
        creator_world_hash = sha256(source_world)
        derived = dict(creator_manifest)
        derived.update({
            "schema_version": creator_manifest.get("schema_version", "1.0.0"),
            # Durable controller/material/bootstrap state remains scoped to
            # the creator episode.  ``run_id`` identifies this replay attempt
            # and is intentionally separate from that lineage identity.
            "episode_id": creator_manifest.get("episode_id", plan.lineage_id),
            "run_id": plan.run_id,
            "lineage_id": plan.lineage_id,
            "suite_id": plan.suite_id,
            "attempt_id": plan.attempt_id,
            "checkpoint_id": plan.checkpoint_id,
            "checkpoint_generation_id": plan.generation_id,
            "creator_repository_revision": creator_commit,
            "candidate_repository_revision": candidate_commit,
            "creator_source_save_sha256": creator_manifest.get("source_save_sha256"),
            # Keep the legacy field candidate-pinned so autonomous_run's
            # existing fail-closed gate remains effective.
            "repository_revision": candidate_commit,
            "source_save": str(plan.source_save_path),
            "source_save_sha256": creator_world_hash,
            "isolated_save": str(plan.save_path),
            "isolated_save_sha256": creator_world_hash,
            "baseline_world_fingerprint": f"sha256:{creator_world_hash}",
            "candidate_checkout": str(checkout),
            "compatible_code_manifest": compatibility,
            "surface": "nauvis",
            "force": "player",
            "fleet_lane_root": str(lane),
            "checkpoint_fleet": {
                "ordered_checkpoint_ids": list(plan.checkpoint_ids),
                "predicate_versions": dict(plan.predicate_versions or {}),
                "origin_checkpoint_id": plan.checkpoint_id,
                "registry_version": plan.registry_version,
                "factorio_version": compatibility.get("factorio_version", "unknown"),
                "result_path": plan.result_path,
                "capture_policy": dict(plan.capture_policy or {}),
                "mission": {
                    "mode": plan.mission_mode,
                    "target": plan.mission_target,
                },
            },
            "helper": {
                "enabled": plan.helper_enabled,
                "run_id": plan.run_id,
                "data_root": str(plan.helper_data_root),
                "report_root": str(plan.helper_report_root),
                "dashboard_url": plan.helper_api_url,
            },
        })
        derived["deployed_factorio_mod_sha256"] = directory_hash(lane / "mods/factorio_cursor_rl_agent")
        derived["deployed_factorio_training_lab_sha256"] = directory_hash(lane / "mods/factorio_training_lab")
        _atomic_json(plan.manifest_path, derived)
        _atomic_json(lane / "episode/creator.json", creator_manifest)
        _atomic_json(lane / "episode/compatibility.json", compatibility)
        return derived
