# Path: tests/test_saved_world_process_identity.py | Purpose: Reject ambiguous saved-world process scope before connecting.
import hashlib
import json
from pathlib import Path

import pytest

from training import laya_saved_world as saved_world


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(saved_world, "TEST_ROOT", tmp_path)
    root = tmp_path / "copy"
    server = root / "server"
    (server / "mods" / "sample").mkdir(parents=True)
    baseline = b"disposable baseline"
    (root / "baseline.zip").write_bytes(baseline)
    (server / "factorio.pid").write_text("12345")
    (server / "mods" / "sample" / "control.lua").write_bytes(b"mod")
    (root / "manifest.json").write_text(json.dumps({
        "runtime": "disposable_saved_world", "production_authority": False,
        "source_sha256": hashlib.sha256(baseline).hexdigest(),
        "game_port": 34198, "rcon_port": 27018,
        "mod_hashes": {"sample/control.lua": hashlib.sha256(b"mod").hexdigest()},
    }))
    arguments = ["factorio", "--config", str(server / "config.ini"), "--start-server",
                 str(server / "saves/mod_playground.zip"), "--mod-directory", str(server / "mods"),
                 "--rcon-bind", "127.0.0.1:27018", "--bind", "127.0.0.1:34198"]
    real_read_bytes = Path.read_bytes
    def read_bytes(path):
        if str(path) == "/proc/12345/cmdline":
            return ("\0".join(arguments) + "\0").encode()
        return real_read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return root, arguments


def test_scoped_process_arguments_match(instance):
    root, argv = instance
    assert saved_world.verify_instance(root)["runtime"] == "disposable_saved_world"
    index = argv.index("--rcon-bind")
    argv[index:index + 2] = ["--rcon-bind=127.0.0.1:27018"]
    assert saved_world.verify_instance(root)["rcon_port"] == 27018


@pytest.mark.parametrize("extra", [
    ["--rcon-bind", "127.0.0.1:27017"],
    ["--rcon-bind=127.0.0.1:27017"],
    ["--rcon-bind", "127.0.0.1:27018"],
    ["--start-server=/different/save.zip"],
])
def test_duplicate_flags_are_rejected_even_when_first_value_matches(instance, extra):
    root, argv = instance
    argv.extend(extra)
    with pytest.raises(saved_world.SavedWorldError, match="duplicate flag"):
        saved_world.verify_instance(root)


@pytest.mark.parametrize("mutation", ["missing", "wrong", "trailing"])
def test_missing_mismatched_or_truncated_flags_are_rejected(instance, mutation):
    root, argv = instance
    index = argv.index("--rcon-bind")
    if mutation == "missing":
        del argv[index:index + 2]
    elif mutation == "wrong":
        argv[index + 1] = "127.0.0.1:27017"
    else:
        argv[:] = argv[:index + 1]
    with pytest.raises(saved_world.SavedWorldError, match="test instance mismatch"):
        saved_world.verify_instance(root)


def test_option_terminator_cannot_hide_checked_flags(instance):
    root, argv = instance
    argv.insert(1, "--")
    with pytest.raises(saved_world.SavedWorldError, match="option terminator"):
        saved_world.verify_instance(root)
