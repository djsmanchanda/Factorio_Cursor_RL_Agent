# Path: tests/test_milestone_checkpoint.py
# Purpose: Verify immutable, segment-regression checkpoint bundle contracts.
import json
from pathlib import Path
import zipfile

import pytest

from tools import milestone_checkpoint as checkpoint


class FakeClient:
    def __init__(self, root: Path):
        self.root = root

    def command(self, command: str) -> str:
        if "helpers.write_file" in command:
            name = command.split('write_file("', 1)[1].split('"', 1)[0]
            nonce = command.split('nonce="', 1)[1].split('"', 1)[0]
            output = self.root / "script-output"
            output.mkdir(parents=True, exist_ok=True)
            (output / name).write_text(json.dumps({"nonce": nonce}), encoding="utf-8")
            return ""
        name = command.split('server_save("', 1)[1].split('"', 1)[0]
        with zipfile.ZipFile(self.root / "saves" / f"{name}.zip", "w") as archive:
            archive.writestr("level.dat", "world")
        return "1234"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "lane"
    for directory in ("episode", "logs", "saves", "script-output"):
        (root / directory).mkdir(parents=True)
    (root / "episode/current.json").write_text(json.dumps({
        "episode_id": "run-1", "repository_revision": "abcdef0123456789",
    }), encoding="utf-8")
    (root / "logs/deterministic-mission-state.json").write_text('{"reserved": 1}', encoding="utf-8")
    for name in (
        "deterministic-material-reservations",
        "deterministic-bootstrap-districts",
        "deterministic-bootstrap-work",
        "oil-transactions",
    ):
        directory = root / "logs" / name
        directory.mkdir()
        (directory / "run-1.json").write_text('{"run":"run-1"}', encoding="utf-8")
    return root


def test_capture_verify_and_extract_milestone_bundle(tmp_path):
    root = _root(tmp_path)
    bundle = checkpoint.capture(
        root,
        client=FakeClient(root),
        checkpoint_id="C2",
        generation_id="g-2",
        run_id="run-1",
        lineage_id="lineage-1",
        predicate_version="metal-v1",
        predicate_evidence={"iron_output": 12},
        creator_commit="abcdef0123456789",
        mod_hashes={"factorio_cursor_rl_agent": "mod-hash"},
        reason="milestone",
        quiescence_check=lambda: True,
        controller_handshake=lambda: True,
        timeout=1,
    )
    payload = checkpoint.verify(bundle, expected_checkpoint_id="C2")
    assert payload["kind"] == "milestone-regression"
    assert payload["acceptance_eligible"] is False
    assert payload["segment_regression_eligible"] is True
    assert payload["capture_tick"] == 1234
    assert payload["save_name"].startswith("checkpoint2_abcdef01_")
    assert "state/logs/deterministic-material-reservations/run-1.json" in payload["files"]
    assert not (root / "saves" / f"{payload['save_name']}.zip").exists()
    lane = checkpoint.extract(bundle, tmp_path / "lane-copy")
    assert (lane / "world.zip").is_file()
    assert (lane / "state/episode/current.json").is_file()
    assert json.loads((lane / "MILESTONE_RESTORE.json").read_text())["acceptance_eligible"] is False


def test_capture_requires_quiescence_and_registered_sidecars(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(checkpoint.MilestoneCheckpointError, match="quiescence"):
        checkpoint.capture(
            root,
            client=FakeClient(root),
            checkpoint_id="C1",
            generation_id="g-1",
            run_id="run-1",
            lineage_id="lineage-1",
            predicate_version="v1",
            predicate_evidence={},
            creator_commit="abcdef0123456789",
            mod_hashes={"mod": "hash"},
            quiescence_check=lambda: False,
            controller_handshake=lambda: True,
        )
    with pytest.raises(checkpoint.MilestoneCheckpointError, match="registered sidecar is missing"):
        checkpoint.capture(
            root,
            client=FakeClient(root),
            checkpoint_id="C1",
            generation_id="g-1",
            run_id="run-1",
            lineage_id="lineage-1",
            predicate_version="v1",
            predicate_evidence={},
            creator_commit="abcdef0123456789",
            mod_hashes={"mod": "hash"},
            quiescence_check=lambda: True,
            controller_handshake=lambda: True,
            sidecar_registry=(checkpoint.SidecarSpec("episode/current.json"), checkpoint.SidecarSpec("logs/required.json")),
        )


def test_verify_rejects_creator_manifest_tampering_and_path_traversal(tmp_path):
    root = _root(tmp_path)
    bundle = checkpoint.capture(
        root,
        client=FakeClient(root),
        checkpoint_id="C0",
        generation_id="g-0",
        run_id="run-1",
        lineage_id="lineage-1",
        predicate_version="base-v1",
        predicate_evidence={"verified": True},
        creator_commit="abcdef0123456789",
        mod_hashes={"mod": "hash"},
        quiescence_check=lambda: True,
        controller_handshake=lambda: True,
        timeout=1,
    )
    manifest = bundle / "state/episode/current.json"
    manifest.write_text(manifest.read_text() + "\n", encoding="utf-8")
    with pytest.raises(checkpoint.MilestoneCheckpointError, match="hash/size mismatch"):
        checkpoint.verify(bundle)
    payload = json.loads((bundle / "checkpoint.json").read_text())
    payload["files"]["state/episode/current.json"] = "../outside"
    (bundle / "checkpoint.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(checkpoint.MilestoneCheckpointError):
        checkpoint.verify(bundle)
