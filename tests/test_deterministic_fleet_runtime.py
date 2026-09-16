# Path: tests/test_deterministic_fleet_runtime.py
# Purpose: Verify isolated lane preparation and fail-closed candidate provenance.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.deterministic_fleet_runtime import (
    DeterministicFleetRuntime,
    FleetRuntimeError,
    validate_compatibility_manifest,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def _checkpoint(tmp_path: Path, creator: str) -> Path:
    bundle = tmp_path / "checkpoint"
    (bundle / "state/episode").mkdir(parents=True)
    world = bundle / "world.zip"
    world.write_bytes(b"world")
    manifest = {
        "schema_version": "1.0.0", "episode_id": "creator-run",
        "repository_revision": creator, "surface": "nauvis", "force": "player",
        "bootstrap_profile": "reduced-v1",
    }
    (bundle / "state/episode/current.json").write_text(json.dumps(manifest), encoding="utf-8")
    files = {
        "world.zip": hashlib.sha256(world.read_bytes()).hexdigest(),
        "state/episode/current.json": hashlib.sha256(
            (bundle / "state/episode/current.json").read_bytes()
        ).hexdigest(),
    }
    (bundle / "checkpoint.json").write_text(json.dumps({
        "version": 1, "checkpoint_id": "C2", "files": files,
        "manifest": manifest,
    }), encoding="utf-8")
    return bundle


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fleet Test"], cwd=repo, check=True)
    (repo / "factorio_mod").mkdir()
    (repo / "factorio_mod/control.lua").write_text("-- candidate\n", encoding="utf-8")
    (repo / "factorio_mod/info.json").write_text("{}\n", encoding="utf-8")
    (repo / "factorio_training_lab").mkdir()
    (repo / "factorio_training_lab/control.lua").write_text("-- lab\n", encoding="utf-8")
    (repo / "factorio_training_lab/info.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "candidate"], cwd=repo, check=True)
    return repo, _git(repo, "rev-parse", "HEAD")


def test_lane_preparation_isolated_and_preserves_creator_manifest(tmp_path: Path):
    repo, commit = _repo(tmp_path)
    bundle = _checkpoint(tmp_path, commit)
    runtime = DeterministicFleetRuntime(repo_root=repo, fleet_root=tmp_path / "fleet")
    plan = runtime.plan_lane(
        suite_id="suite-1", attempt_id="attempt-1", checkpoint_id="C2",
        generation_id="generation-1", candidate_commit=commit,
        creator_commit=commit, helper_enabled=False, slot=0,
    )
    compatibility = {
        "compatible": True, "checkpoint_id": "C2", "creator_commit": commit,
        "candidate_commit": commit, "checked_at": "2026-09-15T00:00:00Z",
        "predicate_version": "metals-v1",
    }
    derived = runtime.prepare_lane(plan, bundle, compatibility_manifest=compatibility)
    assert plan.save_path.is_file()
    assert plan.source_save_path.is_file()
    assert plan.manifest_path.is_file()
    assert derived["lineage_id"] == plan.lineage_id
    assert derived["suite_id"] == "suite-1"
    assert derived["attempt_id"] == "attempt-1"
    assert derived["episode_id"] == "creator-run"
    assert derived["run_id"] == plan.run_id
    assert derived["run_id"] != derived["episode_id"]
    assert derived["creator_repository_revision"] == commit
    assert derived["candidate_repository_revision"] == commit
    assert derived["helper"]["enabled"] is False
    assert "--fleet-mode" in plan.server_command()
    assert "--no-opencode-helper" in plan.runner_command()
    creator = json.loads((bundle / "state/episode/current.json").read_text())
    assert creator["episode_id"] == "creator-run"
    # Episode-scoped sidecars remain discoverable under creator identity.
    assert (plan.lane_root / "episode/current.json").is_file()
    assert json.loads((plan.lane_root / "episode/current.json").read_text())["episode_id"] == "creator-run"


def test_dirty_operator_checkout_is_allowed_but_lane_candidate_must_be_clean(tmp_path: Path):
    repo, commit = _repo(tmp_path)
    (repo / "operator-notes.txt").write_text("in progress", encoding="utf-8")
    runtime = DeterministicFleetRuntime(repo_root=repo, fleet_root=tmp_path / "fleet")
    plan = runtime.plan_lane(
        suite_id="suite", attempt_id="attempt", checkpoint_id="C2",
        generation_id="generation", candidate_commit=commit, creator_commit=commit,
        slot=0,
    )
    # Resolving an immutable commit must not reject unrelated operator work.
    from tools.deterministic_fleet_runtime import resolve_clean_commit
    assert resolve_clean_commit(repo, commit) == commit
    with pytest.raises(FleetRuntimeError, match="checkpoint bundle"):
        runtime.prepare_lane(plan, tmp_path / "missing", compatibility_manifest={})


def test_enabled_helper_requires_explicit_served_api_url(tmp_path: Path):
    runtime = DeterministicFleetRuntime(repo_root=tmp_path, fleet_root=tmp_path / "fleet")
    with pytest.raises(FleetRuntimeError, match="explicit served dashboard URL"):
        runtime.plan_lane(
            suite_id="suite", attempt_id="attempt", checkpoint_id="C2",
            generation_id="generation", candidate_commit="abc", creator_commit="abc",
            helper_enabled=True,
        )


def test_custom_mission_selects_bounded_runner_command(tmp_path: Path):
    runtime = DeterministicFleetRuntime(repo_root=tmp_path, fleet_root=tmp_path / "fleet")
    plan = runtime.plan_lane(
        suite_id="suite", attempt_id="attempt", checkpoint_id="C2",
        generation_id="generation", candidate_commit="abc", creator_commit="abc",
        mission_mode="produce", mission_target="plastic-bar",
    )
    command = plan.runner_command()
    assert command[-2:] == ["--produce", "plastic-bar"]
    with pytest.raises(FleetRuntimeError, match="mission_mode"):
        runtime.plan_lane(
            suite_id="suite-2", attempt_id="attempt", checkpoint_id="C2",
            generation_id="generation", candidate_commit="abc", creator_commit="abc",
            mission_mode="shell", mission_target="anything",
        )


def test_newer_code_replay_requires_explicit_compatible_manifest():
    with pytest.raises(FleetRuntimeError, match="not explicitly approved"):
        validate_compatibility_manifest(
            {"compatible": False}, checkpoint_id="C2",
            creator_commit="old", candidate_commit="new",
        )
    with pytest.raises(FleetRuntimeError, match="candidate commit"):
        validate_compatibility_manifest(
            {"compatible": True, "checkpoint_id": "C2", "creator_commit": "old",
             "candidate_commit": "other", "checked_at": "now", "predicate_version": "v1"},
            checkpoint_id="C2", creator_commit="old", candidate_commit="new",
        )
