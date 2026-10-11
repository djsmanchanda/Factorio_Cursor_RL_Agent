# Path: tests/test_persistent_factorio.py | Purpose: Protect persistent server isolation and runner-independent lifetime.
from pathlib import Path
import json

import pytest

from tools import persistent_factorio as persistent
from tools import run_laya_saved_world as saved_world

pytestmark = pytest.mark.fast


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(persistent, "ROOT", tmp_path)
    return {"runtime": "laya", "server_root": str(tmp_path / "data/laya-saved-world/persistent-laya/server"),
            "runtime_root": "/private/factorio-2.1.21", "game_port": 34992, "rcon_port": 27992, "speed": 2}


@pytest.mark.parametrize("port", [34199, 27017])
def test_persistent_profiles_reject_protected_ports(profile, port):
    profile["game_port"] = port
    with pytest.raises(ValueError, match="protected"):
        persistent.manager_command(profile, "start")


def test_persistent_profile_cannot_write_outside_dedicated_data(profile, tmp_path):
    profile["server_root"] = str(tmp_path / "user-save")
    with pytest.raises(ValueError, match="isolated"):
        persistent.validate(profile)


def test_server_unit_preserves_save_and_is_independent_of_runner(profile, tmp_path):
    text = persistent.unit_text(tmp_path / "profile.json", profile, python="/private/python")
    assert f"WorkingDirectory={persistent.ROOT}\n" in text
    assert f"PIDFile={profile['server_root']}/factorio.pid\n" in text
    assert "Type=forking" in text and "Restart=on-failure" in text
    assert "RuntimeMaxSec=infinity" in text and "WantedBy=default.target" in text
    assert "run_laya_clean" not in text
    command = persistent.manager_command(profile, "start")
    assert "--fleet-mode" in command
    assert not {"reset", "deploy", "--source-save"}.intersection(command)


def test_persistent_stop_retains_server_without_invoking_manager(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"persistent_server": True}))
    monkeypatch.setattr(saved_world.subprocess, "run", lambda *_a, **_k: pytest.fail("runner stopped its server"))
    assert "retained" in saved_world.manager(tmp_path, "stop")


def test_persistent_start_routes_to_its_own_service(tmp_path, monkeypatch):
    from types import SimpleNamespace
    (tmp_path / "manifest.json").write_text(json.dumps({"persistent_server": True,
                                                       "server_unit": "factorio-laya-persistent.service"}))
    calls = []
    monkeypatch.setattr(saved_world.subprocess, "run", lambda args, **_k:
                        calls.append(args) or SimpleNamespace(stdout="started"))
    assert saved_world.manager(tmp_path, "start") == "started"
    assert calls == [["systemctl", "--user", "start", "factorio-laya-persistent.service"]]


def test_protected_pid_file_never_authorizes_stop(profile):
    root = Path(profile["server_root"]);root.mkdir(parents=True)
    (root / "factorio.pid").write_text("1502810")
    with pytest.raises(ValueError, match="protected"):
        persistent.verify_process(profile)
