# Path: tests/test_laya_director_cli.py
# Purpose: Verify explicit opt-in and checkpoint capability gates for the Laya CLI.

from __future__ import annotations

import json

import pytest

from tools.run_laya_director import parser
from training.laya_runner import LayaRunnerError, load_checkpoint_capabilities


def test_cli_requires_exact_disposable_opt_in_and_worker_identity() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args([])
    with pytest.raises(SystemExit):
        parser().parse_args([
            "--mode", "production", "--worker-id", "worker-1", "--scenario", "s.json",
            "--checkpoint", "c", "--script-output", "o", "--port", "27015",
        ])


def test_checkpoint_gate_rejects_mining_only_or_unversioned_checkpoint(tmp_path) -> None:
    (tmp_path / "adapter_model.safetensors").write_bytes(b"weights")
    with pytest.raises(LayaRunnerError, match="missing"):
        load_checkpoint_capabilities(tmp_path, "mining_delivery")


def test_checkpoint_gate_accepts_only_declared_director_contract(tmp_path) -> None:
    (tmp_path / "adapter_model.safetensors").write_bytes(b"weights")
    (tmp_path / "laya_director_capabilities.json").write_text(json.dumps({
        "request_version": "factorio-decision-v2",
        "model_id": "laya-test-director",
        "scenario_families": ["mining_delivery"],
        "decision_kinds": [
            "choose_operation", "select_candidate", "wait_or_act", "recovery",
        ],
    }))

    result = load_checkpoint_capabilities(tmp_path, "mining_delivery")

    assert result["request_version"] == "factorio-decision-v2"
    assert result["model_hash"].startswith("sha256:")


def test_cli_rejects_an_endpoint_outside_worker_roster(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tools import run_laya_director
    monkeypatch.setattr(run_laya_director, "load_worker_specs", lambda _path: [
        SimpleNamespace(worker_id="training-01", host="127.0.0.1", rcon_port=28001, script_output=tmp_path),
    ])
    with pytest.raises(SystemExit):
        run_laya_director.main([
            "--mode", "laya-primary-disposable", "--worker-id", "training-01",
            "--scenario", "not-read.json", "--checkpoint", "not-loaded",
            "--script-output", str(tmp_path), "--port", "27017",
        ])


def test_cli_exploration_options_belong_only_to_exploration_mode(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from tools import run_laya_director
    monkeypatch.setattr(run_laya_director, "load_worker_specs", lambda _path: [
        SimpleNamespace(worker_id="training-01", host="127.0.0.1", rcon_port=27017, script_output=tmp_path),
    ])
    common = [
        "--worker-id", "training-01", "--scenario", "not-read.json", "--checkpoint", "not-loaded",
        "--script-output", str(tmp_path), "--port", "27017",
    ]
    with pytest.raises(SystemExit):
        run_laya_director.main(["--mode", "laya-exploration-disposable", *common, "--exploration-seed", "1"])
    with pytest.raises(SystemExit):
        run_laya_director.main([
            "--mode", "laya-primary-disposable", *common,
            "--exploration-seed", "1", "--exploration-epsilon", "0.2",
        ])
