# Path: tests/test_persistent_laya_run.py | Purpose: Protect fresh persistent worlds, frozen launches and useful console errors.
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import persistent_factorio, persistent_laya_run, run_laya_clean, run_laya_saved_world
from tools import laya_ops_server as console
from training import laya_saved_world

pytestmark = pytest.mark.fast


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(persistent_laya_run, "ROOT", tmp_path)
    monkeypatch.setattr(persistent_factorio, "ROOT", tmp_path)
    root = tmp_path / "data/laya-saved-world/persistent-laya"
    server = root / "server"
    (server / "saves").mkdir(parents=True)
    (server / "saves/mod_playground.zip").write_bytes(b"previous world")
    (root / "baseline.zip").write_bytes(b"old baseline")
    for parent in [root, server]:
        for name in ["logs", "evidence"] if parent == root else ["logs", "script-output"]:
            (parent / name).mkdir()
            (parent / name / "old.json").write_text('{"claim":"old"}')
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves/mod_playground.zip").write_bytes(b"fresh baseline")
    registry = tmp_path / "data/persistent-factorio"
    registry.mkdir()
    profile = {"runtime": "laya", "server_root": str(server), "runtime_root": "/private/runtime",
               "game_port": 34992, "rcon_port": 27992, "speed": 2,
               "server_unit": "factorio-laya-persistent.service"}
    (registry / "laya.json").write_text(json.dumps(profile))
    manifest = {"persistent_server": True, "production_authority": False, "runtime_selection": "laya",
                "server_unit": profile["server_unit"], "game_port": 34992, "rcon_port": 27992,
                "mod_hashes": {}, "continuation_origin": {"old": True}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_fresh_archives_save_and_claims_before_reset_on_same_ports(world, monkeypatch):
    calls = []
    def service(profile, action):
        calls.append(action)
        if action == "stop":
            (world / "server/saves/mod_playground.zip").write_bytes(b"gracefully saved previous world")
        else:
            assert (world / "server/saves/mod_playground.zip").read_bytes() == b"fresh baseline"
            assert list((world / "server/logs").iterdir()) == []
    monkeypatch.setattr(persistent_laya_run, "service", service)
    backup = persistent_laya_run.fresh_world(world)
    assert calls == ["stop", "start"]
    assert (backup / "server/saves/mod_playground.zip").read_bytes() == b"gracefully saved previous world"
    assert (backup / "server/logs/old.json").exists()
    assert (backup / "world/evidence/old.json").exists()
    manifest = json.loads((world / "manifest.json").read_text())
    assert (manifest["game_port"], manifest["rcon_port"]) == (34992, 27992)
    assert "continuation_origin" not in manifest
    assert laya_saved_world.file_hash(world / "baseline.zip") == manifest["source_sha256"]
    assert json.loads((backup / "fresh-transaction.json").read_text())["phase"] == "started"


def test_fresh_refuses_foreign_profile_before_any_service_action(world, monkeypatch):
    path = persistent_laya_run.ROOT / "data/persistent-factorio/laya.json"
    profile = json.loads(path.read_text());profile["server_root"] += "-other"
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(persistent_laya_run, "service", lambda *_: pytest.fail("foreign service operated"))
    with pytest.raises(ValueError, match="does not match"):
        persistent_laya_run.fresh_world(world)
    assert (world / "server/saves/mod_playground.zip").read_bytes() == b"previous world"


def test_fresh_run_comparator_follows_archived_evidence(world, monkeypatch):
    index = persistent_laya_run.ROOT / "data/persistent-factorio/laya-runs.jsonl"
    index.write_text(json.dumps({"run_id": "previous", "root": str(world)}) + "\n")
    monkeypatch.setattr(persistent_laya_run, "service", lambda *_: None)
    backup = persistent_laya_run.fresh_world(world)
    record = json.loads(index.read_text())
    assert record["root"] == str(backup / "world")
    assert (Path(record["root"]) / "evidence/old.json").exists()


def test_frozen_oc_run_enables_helper_with_shared_evidence_paths(world, monkeypatch, tmp_path):
    owner = persistent_laya_run.ROOT
    episode = tmp_path / "episode";code = episode / "code";code.mkdir(parents=True)
    freeze = episode / "freeze.json"
    freeze.write_text(json.dumps({"world": str(world), "owner_repository": str(owner), "code_hashes": {},
                                 "mod_hashes": {}, "source_sha256": laya_saved_world.file_hash(world / "baseline.zip"),
                                 "actions": "deterministic", "goal": "mining-productivity-4", "speed": 2, "seed": 50616}))
    monkeypatch.setattr(persistent_laya_run, "ROOT", code)
    monkeypatch.setattr(laya_saved_world, "PERSISTENT_WORLD", None)
    monkeypatch.setattr(persistent_laya_run.sys, "argv", ["runner", "--freeze", str(freeze)])
    calls = []
    monkeypatch.setattr(run_laya_clean, "main", lambda argv: calls.append(argv) or 0)
    assert persistent_laya_run.main() == 0
    command = calls[0]
    assert "--no-helper" not in command
    assert command[command.index("--helper-report-root") + 1] == str(owner / "docs/rl/laya_helper_runs")
    assert command[command.index("--helper-data-root") + 1] == str(owner / "data/persistent-factorio/helper-state")
    assert command[command.index("--run-index") + 1] == str(owner / "data/persistent-factorio/laya-runs.jsonl")


def test_launch_executes_frozen_copy_under_separate_user_unit(world, monkeypatch, tmp_path):
    freeze = tmp_path / "run/freeze.json"
    freeze.parent.mkdir();freeze.write_text('{"game_port":34992}')
    monkeypatch.setattr(persistent_laya_run, "freeze_runner", lambda *args: freeze)
    calls = []
    monkeypatch.setattr(persistent_laya_run.subprocess, "run", lambda args, **kwargs: calls.append(args))
    persistent_laya_run.launch(world, "/private/python", 2)
    command = calls[0]
    assert command[0:2] == ["systemd-run", "--user"]
    assert str(freeze.parent / "code/tools/persistent_laya_run.py") in command
    assert "Restart=no" in command and "TimeoutStopSec=120" in command
    assert json.loads((world / "run-owner.json").read_text())["freeze"] == str(freeze)


def test_frozen_runner_registers_only_explicit_isolated_world(world, monkeypatch):
    monkeypatch.setattr(laya_saved_world, "PERSISTENT_WORLD", None)
    owner = persistent_laya_run.ROOT
    laya_saved_world.register_persistent_world(world, owner)
    assert laya_saved_world.owned_root(world) == world
    with pytest.raises(laya_saved_world.SavedWorldError, match="owning repository"):
        laya_saved_world.register_persistent_world(world / "other", owner)
    manifest = json.loads((world / "manifest.json").read_text());manifest["production_authority"] = True
    (world / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(laya_saved_world.SavedWorldError, match="isolated"):
        laya_saved_world.register_persistent_world(world, owner)


def test_console_fresh_reuses_persistent_world_instead_of_prepare(world, monkeypatch):
    ops = console.Operations("python", 2)
    monkeypatch.setattr(ops, "root", lambda: world)
    calls = []
    monkeypatch.setattr(ops, "_stop_runner", lambda: calls.append("stop"))
    monkeypatch.setattr(persistent_laya_run, "fresh_world", lambda root: calls.append(root) or world / "backup")
    monkeypatch.setattr(ops, "_start_runner", lambda: calls.append("run") or "started")
    monkeypatch.setattr(ops, "_cli", lambda *_: pytest.fail("legacy prepare invoked"))
    assert "started" in ops._fresh_episode()
    assert calls == ["stop", world, "run"]


def test_console_start_uses_frozen_persistent_launcher(world, monkeypatch):
    ops = console.Operations("python", 2)
    monkeypatch.setattr(ops, "root", lambda: world)
    monkeypatch.setattr(console, "_pid_alive", lambda *_: 123)
    monkeypatch.setattr(console.subprocess, "Popen", lambda *_a, **_k: pytest.fail("mutable runner launched"))
    monkeypatch.setattr(persistent_laya_run, "launch", lambda root, python, speed: f"{root}:{speed}")
    assert ops._start_runner() == f"{world}:2"


@pytest.mark.parametrize("action", ["start", "stop"])
def test_console_server_controls_use_persistent_owner(world, monkeypatch, action):
    ops = console.Operations("python", 2)
    monkeypatch.setattr(ops, "root", lambda: world)
    monkeypatch.setattr(console, "_pid_alive", lambda *_: 123 if action == "stop" else None)
    monkeypatch.setattr(ops, "_stop_runner", lambda: "Runner stopped.")
    calls = []
    monkeypatch.setattr(persistent_laya_run, "service", lambda profile, operation: calls.append(operation))
    monkeypatch.setattr(ops, "_cli", lambda *_: pytest.fail("episode server manager invoked"))
    getattr(ops, f"_{action}_server")()
    assert calls == [action]


def test_saved_world_cli_preserves_inner_error(monkeypatch):
    monkeypatch.setattr(run_laya_clean.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=1, stdout="", stderr="missing required mods: factorio_laya_agent"))
    with pytest.raises(RuntimeError, match="missing required mods: factorio_laya_agent"):
        run_laya_clean.saved_world("prepare", Path("unused"))


def test_prepare_preflights_mod_set_before_creating_partial_root(tmp_path, monkeypatch):
    root = tmp_path / "worlds/new"
    source = tmp_path / "source.zip";source.write_bytes(b"source")
    mods = tmp_path / "mods";mods.mkdir()
    monkeypatch.setattr(run_laya_saved_world, "owned_root", lambda path: path)
    from tools import laya_admission
    monkeypatch.setattr(laya_admission, "wait_for_admission", lambda *_: None)
    args = SimpleNamespace(root=root, source_save=source, mods_source=mods)
    with pytest.raises(laya_saved_world.SavedWorldError, match="missing required mods.*factorio_laya_agent"):
        run_laya_saved_world.prepare(args)
    assert not root.exists()
