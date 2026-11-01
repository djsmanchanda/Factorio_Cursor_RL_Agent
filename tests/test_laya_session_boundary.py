# Path: tests/test_laya_session_boundary.py | Purpose: Keep operation sessions explicit and confined to verified disposable worlds.
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from training.laya_ops_runner import run
from training.laya_saved_world import SavedWorldError


def test_runner_rejects_missing_session_before_observation(tmp_path):
    with pytest.raises(ValueError, match="explicit disposable operation session"):
        run(None, None, journal=tmp_path / "journal.jsonl", decisions=1, measure_ticks=600,
            seed=1, run_id="test")
    assert not (tmp_path / "journal.jsonl").exists()


def test_session_rejects_non_disposable_root_before_connecting(tmp_path, monkeypatch):
    import tools.laya_deterministic_session as session_module
    monkeypatch.setattr(session_module, "RconClient", lambda *_args, **_kwargs: pytest.fail("unexpected RCON"))
    monkeypatch.setattr(session_module, "GameBridge", lambda *_args, **_kwargs: pytest.fail("unexpected bridge"))
    with pytest.raises(SavedWorldError, match="dedicated children"):
        session_module.DeterministicSession(tmp_path)


@pytest.mark.parametrize("helper_args,expected_helper", [([], False), (["--no-helper"], False), (["--helper"], True)])
def test_disposable_cli_passes_the_verified_adapter_root_to_the_session(tmp_path, monkeypatch,
                                                                        helper_args, expected_helper):
    import json
    import tools.laya_deterministic_session as session_module
    import tools.run_laya_clean as cli
    import training.laya_ops_runner as runner
    from training.laya_clean_runner import Settings

    (tmp_path / "manifest.json").write_text(json.dumps({"game_port": 34230, "rcon_port": 27130}))
    (tmp_path / "evidence").mkdir()
    calls = []
    class Adapter:
        def __init__(self, root):
            self.root = root
            calls.append("adapter_verified")
        def close(self):
            calls.append("adapter_closed")
    class Log:
        def __init__(self, *_args): pass
        def __call__(self, *_args): pass
        def start(self, **_kwargs): pass
        def end(self): pass
    operation_session = object()
    def session_factory(root, **_kwargs):
        expected = ["adapter_verified"] + (["helper_started"] if expected_helper else [])
        assert calls == expected and root == tmp_path
        calls.append("session_created")
        return operation_session
    def run_ops(adapter, _policy, **kwargs):
        assert adapter.root == tmp_path and kwargs["session"] is operation_session
        calls.append("runner_called")
        return {"decisions": 1, "failures": 0, "phases_reached": [], "stop": "decision_budget"}
    monkeypatch.setattr(cli, "owned_root", lambda path: path)
    monkeypatch.setattr(cli, "SavedWorldAdapter", Adapter)
    monkeypatch.setattr(cli, "RunLog", Log)
    monkeypatch.setattr(cli, "runner_pid_record", lambda _path: nullcontext())
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(cli, "file_hash", lambda _path: "unchanged")
    monkeypatch.setattr(cli, "load_variant", lambda _path: {"variant_id": "test"})
    monkeypatch.setattr(cli, "variant_hash", lambda _variant: "a" * 64)
    monkeypatch.setattr(cli, "apply_variant", lambda _variant: (Settings(), {}, {}))
    monkeypatch.setattr(cli, "load_preferences", lambda _path: {})
    monkeypatch.setattr(cli, "GuidedPolicy", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="abc"))
    monkeypatch.setattr(cli, "RUN_INDEX", tmp_path / "index.jsonl")
    monkeypatch.setattr(cli, "record_laya_run", lambda _index: {"outcome": "test", "highest_milestone": "none"})
    def launch_helper(*_args):
        calls.append("helper_started")
        return "mock helper"
    monkeypatch.setattr(cli, "launch_helper", launch_helper)
    monkeypatch.setattr(session_module, "DeterministicSession", session_factory)
    monkeypatch.setattr(runner, "run", run_ops)
    result = cli.main(["run", "--root", str(tmp_path), "--actions", "deterministic", "--stepped",
                       "--decisions", "1", *helper_args])
    expected = ["adapter_verified"] + (["helper_started"] if expected_helper else [])
    assert result == 0 and calls == expected + ["session_created", "runner_called", "adapter_closed"]


@pytest.mark.parametrize("valid", [True, False])
def test_geometry_cli_checks_optimizer_config_before_unpausing(tmp_path, monkeypatch, valid):
    import json
    import tools.optimizer_saved_world as optimizer
    import tools.run_laya_clean as cli
    from training.laya_clean_runner import Settings

    (tmp_path / "manifest.json").write_text(json.dumps({"game_port": 34230, "rcon_port": 27130}))
    (tmp_path / "evidence").mkdir()
    calls = []
    provider = object()
    metadata = {"plan_sha256": "a" * 64, "funding_context": {"episode_id": "episode-1"},
                "dispatch_authorized": False}
    class Adapter:
        def __init__(self, root, speed):
            self.root = root
            calls.append("adapter_verified")
        def go_live(self): calls.append("unpaused")
        def close(self): calls.append("closed")
    class Log:
        def __init__(self, *_args): pass
        def __call__(self, *_args): pass
        def start(self, **_kwargs): pass
        def end(self): pass
    def load(path, adapter):
        assert path == tmp_path / "candidate.json" and adapter.root == tmp_path
        calls.append("config_checked")
        if not valid:
            raise ValueError("invalid candidate")
        return provider, metadata
    def run_geometry(_adapter, _policy, **kwargs):
        assert kwargs["candidate_provider"] is provider
        calls.append("runner_called")
        return {"decisions": 1, "failures": 0, "phases_reached": [], "stop": "decision_budget"}
    monkeypatch.setattr(optimizer, "load_optimizer_candidate_config", load)
    monkeypatch.setattr(cli, "owned_root", lambda path: path)
    monkeypatch.setattr(cli, "LiveWorld", Adapter)
    monkeypatch.setattr(cli, "run", run_geometry)
    monkeypatch.setattr(cli, "RunLog", Log)
    monkeypatch.setattr(cli, "runner_pid_record", lambda _path: nullcontext())
    monkeypatch.setattr(cli.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(cli, "file_hash", lambda _path: "unchanged")
    monkeypatch.setattr(cli, "load_variant", lambda _path: {"variant_id": "test"})
    monkeypatch.setattr(cli, "variant_hash", lambda _variant: "a" * 64)
    monkeypatch.setattr(cli, "apply_variant", lambda _variant: (Settings(), {}, {}))
    monkeypatch.setattr(cli, "load_preferences", lambda _path: {})
    monkeypatch.setattr(cli, "GuidedPolicy", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="abc"))
    monkeypatch.setattr(cli, "RUN_INDEX", tmp_path / "index.jsonl")
    monkeypatch.setattr(cli, "record_laya_run", lambda _index: {"outcome": "test", "highest_milestone": "none"})
    monkeypatch.setattr(cli, "launch_helper", lambda *_args: pytest.fail("invalid config started a helper"))
    argv = ["run", "--root", str(tmp_path), "--optimizer-candidate", str(tmp_path / "candidate.json")]
    if not valid:
        argv.append("--helper")
    if valid:
        assert cli.main(argv) == 0
        assert calls == ["adapter_verified", "config_checked", "unpaused", "runner_called", "closed"]
        index = json.loads((tmp_path / "index.jsonl").read_text())
        assert index["optimizer_candidate"] == metadata
    else:
        with pytest.raises(ValueError, match="invalid candidate"):
            cli.main(argv)
        assert calls == ["adapter_verified", "config_checked", "closed"]


def test_cli_rejects_optimizer_candidate_for_other_modes(tmp_path):
    from tools.run_laya_clean import main
    for args in (["prepare"], ["stop"], ["run", "--actions", "deterministic"]):
        with pytest.raises(SystemExit):
            main([*args, "--root", str(tmp_path), "--optimizer-candidate", "candidate.json"])
