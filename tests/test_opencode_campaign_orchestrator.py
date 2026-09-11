"""Verify the automated OpenCode deterministic campaign controller contracts."""

from pathlib import Path

import pytest

from tools import opencode_campaign_orchestrator as campaign


def test_session_reader_accepts_nested_opencode_event() -> None:
    assert campaign._session_id('{"properties":{"sessionID":"ses_123"}}\n') == "ses_123"


def test_session_reader_ignores_unrelated_ids() -> None:
    assert campaign._session_id('{"properties":{"id":"tool_123"}}\n') is None


def test_latest_run_scopes_end_marker_to_newest_run(tmp_path: Path) -> None:
    log = tmp_path / "autonomous-run.log"
    log.write_text("RUN START: old\n+1s RUN END\nRUN START: new\nworking\n", encoding="utf-8")
    assert campaign._latest_run(log) == (False, "RUN START: new\nworking\n")
    log.write_text("RUN START: old\n+1s RUN END\nRUN START: new\nSTUCK: belts 4\n+2s RUN END\n", encoding="utf-8")
    complete, text = campaign._latest_run(log)
    assert complete is True
    assert "STUCK: belts 4" in text


def test_inventory_summary_is_ordered(monkeypatch) -> None:
    class Response:
        def read(self):
            return b'{"ok":true,"tick":17,"networks":[{}],"disconnected_roboports":2,"total_items":{"iron-plate":42,"copper-plate":84}}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(campaign, "urlopen", lambda *_args, **_kwargs: Response())
    summary = campaign._inventory("http://127.0.0.1:9137/api/logistic-inventory")
    assert "tick=17; networks=1; disconnected_roboports=2" in summary
    assert summary.index("copper-plate=84") < summary.index("iron-plate=42")


def test_completion_prompt_requires_comparison_and_one_fix(tmp_path: Path) -> None:
    config = campaign.Config(
        state_root=tmp_path, source_save=tmp_path / "save.zip", observations=tmp_path / "notes.md",
        state_file=tmp_path / "state.json", opencode_log_dir=tmp_path / "logs", technology="target",
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=0, max_runtime_seconds=0,
        model="model", variant="xhigh", opencode_bin="opencode", python=tmp_path / "python",
        campaign_manager=tmp_path / "campaign", dashboard_url="http://127.0.0.1:9137/api/logistic-inventory",
        dry_run=False, resume_active_run=False,
    )
    prompt = campaign._completion_prompt(config, 3)
    assert "compare this\ncompleted run against three references" in prompt
    assert "exactly one small reusable fix" in prompt
    assert "competing explanation" in prompt
    assert "obtain the missing read-only observation or local reproduction yourself" in prompt
    assert "without committing" in prompt
    assert "primary orchestrator integrates and commits" in prompt
    assert "verify subagent findings" in prompt
    assert "CAMPAIGN_DECISION:" in prompt


def test_telemetry_prompt_allows_only_a_zero_behavior_discriminator(tmp_path: Path) -> None:
    config = campaign.Config(
        state_root=tmp_path, source_save=tmp_path / "save.zip", observations=tmp_path / "notes.md",
        state_file=tmp_path / "state.json", opencode_log_dir=tmp_path / "logs", technology="target",
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=0, max_runtime_seconds=0,
        model="model", variant="xhigh", opencode_bin="opencode", python=tmp_path / "python",
        campaign_manager=tmp_path / "campaign", dashboard_url="http://127.0.0.1:9137/api/logistic-inventory",
        dry_run=False, resume_active_run=False,
    )

    prompt = campaign._telemetry_prompt(config, 4)

    assert "zero-behavior" in prompt
    assert "Do not alter planning behavior" in prompt
    assert "Never manufacture telemetry just to unlock another run" in prompt


def test_dry_run_completes_without_touching_live_services(tmp_path: Path) -> None:
    observations = tmp_path / "observations.md"

    result = campaign.main([
        "--dry-run", "--max-cycles", "1", "--state-root", str(tmp_path / "state"),
        "--source-save", str(tmp_path / "source.zip"), "--observations", str(observations),
    ])

    assert result == 0
    assert "Dry run: status probe skipped." in observations.read_text(encoding="utf-8")


def test_state_preserves_active_session_for_long_run_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    campaign._save_state(state_path, campaign.State(
        completed_cycles=2, active_cycle=3, active_session_id="ses_long_run",
    ))

    restored = campaign._load_state(state_path)

    assert restored.active_cycle == 3
    assert restored.active_session_id == "ses_long_run"


@pytest.mark.parametrize(
    "decision,changed,expected_cycles",
    [("stop", True, 0), ("no-change", True, 0),
     ("change", False, 0), ("change", True, 2)],
)
def test_campaign_requires_explicit_change_and_edit_to_retry(
    tmp_path, monkeypatch, decision, changed, expected_cycles,
):
    fingerprints = iter(["before", "after" if changed else "before"] * 2)
    monkeypatch.setattr(campaign, "_tree_fingerprint", lambda *_: next(fingerprints))
    monkeypatch.setattr(campaign, "_ask", lambda *_: (
        "session", f"CAMPAIGN_DECISION:\nstatus: {decision}\nfiles: file.py\ntest: passed\nreason: test\n",
    ))
    state_root = tmp_path / "state"
    assert campaign.main([
        "--dry-run", "--max-cycles", "2", "--state-root", str(state_root),
        "--source-save", str(tmp_path / "source.zip"),
        "--observations", str(tmp_path / "notes.md"),
    ]) == 0
    assert campaign._load_state(state_root / "logs/opencode-campaign-state.json").completed_cycles == expected_cycles


def test_checkpoint_links_bounded_packet_without_copying_raw_log(tmp_path, monkeypatch):
    from types import SimpleNamespace
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "autonomous-run.log"
    log.write_text("RUN START: ts=2026-09-11T00:00:00Z\n+1s raw-repeat " + "x" * 15000)
    monkeypatch.setattr(campaign, "_status_command", lambda _: [])
    monkeypatch.setattr(campaign, "_run", lambda *_, **__: SimpleNamespace(stdout="stopped", stderr=""))
    monkeypatch.setattr(campaign, "_inventory", lambda _: "UNKNOWN")
    cfg = SimpleNamespace(dry_run=False, state_root=tmp_path, dashboard_url="unused")
    snapshot, offset = campaign._snapshot(cfg, 1, 1, 0)
    assert offset == log.stat().st_size
    assert "raw-repeat" not in snapshot
    assert str(log_dir / "latest-context.md") in snapshot
    assert len((log_dir / "latest-context.md").read_text()) <= 12000


def test_fingerprint_detects_staged_and_committed_fix(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)

    git('init')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    code = tmp_path / 'fix.py'
    code.write_text('value = 1\n')
    git('add', 'fix.py')
    git('commit', '-m', 'baseline')
    monkeypatch.setattr(campaign, 'REPO_ROOT', tmp_path)
    notes = tmp_path / 'notes.md'
    before = campaign._tree_fingerprint(notes)
    code.write_text('value = 2\n')
    git('add', 'fix.py')
    assert campaign._tree_fingerprint(notes) != before
    git('commit', '-m', 'verified fix')
    after = campaign._tree_fingerprint(notes)
    assert after != before
    assert after == campaign._tree_fingerprint(notes)
    notes.write_text('checkpoint only\n')
    assert campaign._tree_fingerprint(notes) == after


def _restart_config(tmp_path):
    return campaign.Config(
        state_root=tmp_path, source_save=tmp_path / 'source.zip', observations=tmp_path / 'notes.md',
        state_file=tmp_path / 'state.json', opencode_log_dir=tmp_path / 'opencode', technology='target',
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=1, max_runtime_seconds=600,
        model='model', variant='xhigh', opencode_bin='opencode', python=tmp_path / 'python',
        campaign_manager=tmp_path / 'manager', dashboard_url='unused', dry_run=True, resume_active_run=False,
    )


def test_model_failure_after_fresh_does_not_reset_episode_on_restart(tmp_path, monkeypatch):
    cfg = _restart_config(tmp_path)
    calls = []
    def ask(*args):
        calls.append(args)
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(campaign, '_ask', ask)
    with pytest.raises(RuntimeError, match='provider'):
        campaign.run_campaign(cfg)
    state = campaign._load_state(cfg.state_file)
    assert state.active_cycle == 1 and state.active_session_id is None
    assert state.lifecycle_intent is None
    monkeypatch.setattr(campaign, '_fresh_command', lambda _: pytest.fail('must not repeat reset'))
    with pytest.raises(RuntimeError, match='provider'):
        campaign.run_campaign(cfg)
    assert len(calls) == 2


def test_unresolved_fresh_intent_blocks_second_reset(tmp_path, monkeypatch):
    cfg = _restart_config(tmp_path)
    campaign._save_state(cfg.state_file, campaign.State(lifecycle_intent={'previous_episode': None, 'cycle': 1}))
    monkeypatch.setattr(campaign, '_fresh_command', lambda _: pytest.fail('must not repeat reset'))
    with pytest.raises(RuntimeError, match='unresolved'):
        campaign.run_campaign(cfg)


def test_read_only_model_denies_mutating_tools_and_preserves_timeout_transport(tmp_path, monkeypatch):
    import json
    import subprocess
    from dataclasses import replace
    cfg = replace(_restart_config(tmp_path), dry_run=False, read_only=True, model_timeout_seconds=7)
    def run(command, **kwargs):
        assert '--auto' not in command
        policy = json.loads(command[1].split('=', 1)[1])
        assert policy['permission']['*'] == 'deny'
        assert policy['permission']['read'] == 'allow'
        assert policy['agent']['build']['permission'] == policy['permission']
        assert kwargs['timeout'] == 7
        raise subprocess.TimeoutExpired(command, 7, output=b'{"sessionID":"recoverable"}\n')
    monkeypatch.setattr(campaign, '_run', run)
    with pytest.raises(subprocess.TimeoutExpired):
        campaign._ask(cfg, 'inspect', None, 4)
    assert campaign._session_id((cfg.opencode_log_dir / 'opencode-0004.jsonl').read_text()) == 'recoverable'


def test_fixer_interruption_retains_original_dirty_baseline(tmp_path, monkeypatch):
    cfg = _restart_config(tmp_path)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: 'original')
    monkeypatch.setattr(campaign, '_snapshot', lambda *_: ('snapshot', 0))
    def ask(config, prompt, session, sequence):
        if config.read_only:
            return 'observer', ''
        raise RuntimeError('fixer interrupted after partial edit')
    monkeypatch.setattr(campaign, '_ask', ask)
    with pytest.raises(RuntimeError, match='fixer interrupted'):
        campaign.run_campaign(cfg)
    state = campaign._load_state(cfg.state_file)
    assert state.active_cycle == 1
    assert state.pending_fix['phase'] == 'fixing'
    assert state.pending_fix['before'] == 'original'
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: pytest.fail('must not replace original baseline'))
    with pytest.raises(RuntimeError, match='fixer interrupted'):
        campaign.run_campaign(cfg)


def test_reset_manifest_without_matching_run_cannot_be_adopted(tmp_path, monkeypatch):
    import json
    cfg = _restart_config(tmp_path)
    (tmp_path / 'episode').mkdir()
    (tmp_path / 'episode/current.json').write_text(json.dumps({'episode_id': 'expected'}))
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/autonomous-run.log').write_text('RUN START: episode=old\n+10s RUN END\n')
    campaign._save_state(cfg.state_file, campaign.State(lifecycle_intent={'previous_episode': 'old', 'expected_episode': 'expected', 'cycle': 1}))
    monkeypatch.setattr(campaign, '_fresh_command', lambda _: pytest.fail('must not reset'))
    with pytest.raises(RuntimeError, match='unresolved'):
        campaign.run_campaign(cfg)


def test_reviewer_receives_diff_and_prediction_artifact(tmp_path, monkeypatch):
    import subprocess
    cfg = _restart_config(tmp_path)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda _: 'same')
    monkeypatch.setattr(campaign, '_run', lambda command, **_: subprocess.CompletedProcess(command, 0,
        '+ producer.restore()\n' if command[:2] == ['git', 'diff'] and 'HEAD' in command and '--name-only' not in command else '', ''))
    def ask(config, prompt, session, sequence):
        assert config.read_only
        evidence = config.opencode_log_dir / f'review-{sequence:04d}.md'
        assert str(evidence) in prompt
        assert 'prediction: sustained plastic' in evidence.read_text()
        assert '+ producer.restore()' in evidence.read_text()
        return 'review', 'REVIEW_DECISION:\nstatus: rejected\nreason: missing coverage'
    monkeypatch.setattr(campaign, '_ask', ask)
    assert not campaign._review_and_commit(cfg,
        'CAMPAIGN_DECISION:\nstatus: change\nfiles: tools/run_context.py\nprediction: sustained plastic', set(), 1)


def test_declined_partial_fix_resumes_investigation_without_new_episode(tmp_path, monkeypatch):
    cfg = _restart_config(tmp_path)
    hashes = iter(['original', 'partial'])
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: next(hashes))
    monkeypatch.setattr(campaign, '_snapshot', lambda *_: ('snapshot', 0))
    monkeypatch.setattr(campaign, '_ask', lambda config, *_: ('observer' if config.read_only else 'fixer',
        '' if config.read_only else 'CAMPAIGN_DECISION:\nstatus: no-change\nreason: partial candidate needs investigation'))
    assert campaign.run_campaign(cfg) == 0
    state = campaign._load_state(cfg.state_file)
    assert state.active_cycle == 1
    assert state.pending_fix['phase'] == 'fixing'
    assert state.pending_fix['before'] == 'original'
    monkeypatch.setattr(campaign, '_fresh_command', lambda _: pytest.fail('must not reset unverified candidate'))
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: 'partial')
    assert campaign.run_campaign(cfg) == 0
    assert campaign._load_state(cfg.state_file).completed_cycles == 0


def test_review_rejects_undeclared_new_code_before_model_or_commit(tmp_path, monkeypatch):
    cfg = _restart_config(tmp_path)
    monkeypatch.setattr(campaign, '_dirty_paths', lambda: {'tools/run_context.py', 'tools/hidden_change.py'})
    monkeypatch.setattr(campaign, '_ask', lambda *_: pytest.fail('undeclared candidate must not reach review'))
    monkeypatch.setattr(campaign, '_run', lambda *_a, **_k: pytest.fail('must not stage or commit'))
    assert not campaign._review_and_commit(cfg,
        'CAMPAIGN_DECISION:\nstatus: change\nfiles: tools/run_context.py', set(), 1)
    assert 'tools/hidden_change.py' in cfg.observations.read_text()
