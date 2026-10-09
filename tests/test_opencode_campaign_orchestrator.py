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


def test_fixer_bootstrap_requires_one_fix_prediction_and_no_commit(tmp_path: Path) -> None:
    from tools.campaign_prompts import fixer_bootstrap, fixer_episode_invocation
    config = campaign.Config(
        state_root=tmp_path, source_save=tmp_path / "save.zip", observations=tmp_path / "notes.md",
        state_file=tmp_path / "state.json", opencode_log_dir=tmp_path / "logs", technology="target",
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=0, max_runtime_seconds=0,
        model="model", variant="xhigh", opencode_bin="opencode", python=tmp_path / "python",
        campaign_manager=tmp_path / "campaign", dashboard_url="http://127.0.0.1:9137/api/logistic-inventory",
        dry_run=False, resume_active_run=False,
    )
    prompt = fixer_bootstrap(config)
    assert "persistent FINAL FIXER" in prompt
    assert "lives for the whole campaign window" in prompt
    assert "one focused evidence-backed correction" in prompt
    assert "never commit" in prompt
    assert "CAMPAIGN_DECISION:" in prompt
    episode = fixer_episode_invocation(
        "episode-1", tmp_path / "synthesis.md", tmp_path / "board.md", None,
        tmp_path / "context.md", tmp_path / "git.md", "none", tmp_path)
    assert "NEW episode" in episode
    assert "OpenCode helper findings: unavailable" in episode


def test_primary_bootstrap_used_once_and_transitions_are_concise(tmp_path: Path) -> None:
    from tools.campaign_prompts import primary_bootstrap, primary_run_transition
    config = campaign.Config(
        state_root=tmp_path, source_save=tmp_path / "save.zip", observations=tmp_path / "notes.md",
        state_file=tmp_path / "state.json", opencode_log_dir=tmp_path / "logs", technology="target",
        interval_seconds=120, post_run_wait_seconds=120, max_cycles=0, max_runtime_seconds=0,
        model="model", variant="xhigh", opencode_bin="opencode", python=tmp_path / "python",
        campaign_manager=tmp_path / "campaign", dashboard_url="http://127.0.0.1:9137/api/logistic-inventory",
        dry_run=False, resume_active_run=False,
    )
    bootstrap = primary_bootstrap(config, 1)
    assert "persistent PRIMARY campaign agent" in bootstrap
    assert "SYNTHESIS:" in bootstrap
    transition = primary_run_transition(
        config, 2, "episode-2", "ses_fixer", tmp_path / "context.md", tmp_path, None)
    assert "NEW four-agent observation team" in transition
    assert "Do not reuse observers" in transition
    assert "Continue campaign orchestration." in transition
    # The transition must stay concise: no full re-orchestration brief.
    assert len(transition) < len(bootstrap) / 2


def test_dry_run_completes_without_touching_live_services(tmp_path: Path) -> None:
    observations = tmp_path / "observations.md"

    result = campaign.main([
        "--dry-run", "--max-cycles", "1", "--state-root", str(tmp_path / "state"),
        "--source-save", str(tmp_path / "source.zip"), "--observations", str(observations),
    ])

    assert result == 0
    assert "Dry run: status probe skipped." in observations.read_text(encoding="utf-8")


def test_state_preserves_sessions_for_long_run_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    campaign._save_state(state_path, campaign.State(
        completed_cycles=2, active_cycle=3, primary_session_id="ses_primary",
        fixer_session_id="ses_fixer",
    ))

    restored = campaign._load_state(state_path)

    assert restored.active_cycle == 3
    assert restored.primary_session_id == "ses_primary"
    assert restored.fixer_session_id == "ses_fixer"


@pytest.mark.parametrize(
    "decision,changed,expected_cycles",
    [("stop", True, 2), ("no-change", True, 2),
     ("change", False, 2), ("change", True, 2)],
)
def test_campaign_records_verdicts_and_continues_without_parking(
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
    state = campaign._load_state(state_root / "logs/opencode-campaign-state.json")
    # Every verdict completes its cycle; the loop keeps running instead of parking.
    assert state.completed_cycles == expected_cycles
    assert state.primary_session_id == "session"
    assert state.fixer_session_id == "session"


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
    snapshot, offset, tick = campaign._snapshot(cfg, 1, 1, 0)
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
    def ask(*args, **kwargs):
        calls.append(args)
        # A permanently failing provider is retried by the recovery loop until
        # the campaign deadline; KeyboardInterrupt terminates it immediately.
        raise KeyboardInterrupt
    monkeypatch.setattr(campaign, '_ask', ask)
    with pytest.raises(KeyboardInterrupt):
        campaign.run_campaign(cfg)
    state = campaign._load_state(cfg.state_file)
    assert state.active_cycle == 1 and state.primary_session_id is None
    assert state.lifecycle_intent is None
    monkeypatch.setattr(campaign, '_fresh_command', lambda _: pytest.fail('must not repeat reset'))
    with pytest.raises(KeyboardInterrupt):
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
    monkeypatch.setattr(campaign, '_snapshot', lambda *_: ('snapshot', 0, None))
    def ask(config, prompt, session, sequence):
        if config.read_only:
            return 'observer', ''
        raise KeyboardInterrupt('fixer interrupted after partial edit')
    monkeypatch.setattr(campaign, '_ask', ask)
    with pytest.raises(KeyboardInterrupt, match='fixer interrupted'):
        campaign.run_campaign(cfg)
    state = campaign._load_state(cfg.state_file)
    assert state.active_cycle == 1
    assert state.pending_fix['phase'] == 'fixing'
    assert state.pending_fix['before'] == 'original'
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: pytest.fail('must not replace original baseline'))
    with pytest.raises(KeyboardInterrupt, match='fixer interrupted'):
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
    from dataclasses import replace as dataclass_replace
    from tools import campaign_fix_flow as fix_flow
    from tools.campaign_loop_log import fixes as fix_ledger
    cfg = dataclass_replace(_restart_config(tmp_path), dry_run=False)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda _: 'same')
    monkeypatch.setattr(campaign, '_dirty_paths', lambda: {'tools/run_context.py'})
    monkeypatch.setattr(campaign, '_run', lambda command, **_: subprocess.CompletedProcess(command, 0,
        '+ producer.restore()\n' if command[:2] == ['git', 'diff'] and 'HEAD' in command and '--name-only' not in command else '', ''))
    (cfg.opencode_log_dir).mkdir(parents=True, exist_ok=True)
    (cfg.state_root / 'logs/campaign-loop').mkdir(parents=True, exist_ok=True)
    synthesis = cfg.state_root / 'logs/campaign-loop/synthesis-0001.md'
    synthesis.write_text('hypothesis: producer restore')
    board = cfg.opencode_log_dir / 'observers/episode-1/board.md'
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text('board')
    def ask(config, prompt, session, sequence, **_):
        if 'REVIEW_DECISION' in prompt and 'Primary review rejected' not in prompt:
            assert config.read_only
            evidence = config.opencode_log_dir / f'review-{sequence:04d}.md'
            assert str(evidence) in prompt
            assert '"prediction": "sustained plastic"' in evidence.read_text()
            assert '+ producer.restore()' in evidence.read_text()
            return 'primary', 'REVIEW_DECISION:\nstatus: rejected\nreason: missing coverage'
        return 'fixer', 'CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: tools/run_context.py\ntest: pytest passed\nprediction: sustained plastic\nreason: test'
    monkeypatch.setattr(campaign, '_ask', ask)
    state = campaign.State(campaign_id='c', active_cycle=1, active_episode_id='episode-1')
    outcome = fix_flow.run_fix_cycle(cfg, state, 1, 'episode-1', synthesis, board,
        {'status': 'unavailable', 'path': None, 'reason': 'none'}, 4)
    assert outcome == 'abandoned'
    assert fix_ledger(cfg.state_root)[list(fix_ledger(cfg.state_root))[-1]]['status'] == 'abandoned'


def test_declined_partial_fix_is_abandoned_and_next_run_starts(tmp_path, monkeypatch):
    import subprocess
    from dataclasses import replace as dataclass_replace
    cfg = _restart_config(tmp_path)
    hashes = iter(['original', 'partial', 'original', 'original'])
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: next(hashes))
    monkeypatch.setattr(campaign, '_snapshot', lambda *_: ('snapshot', 0, None))
    monkeypatch.setattr(campaign, '_ask', lambda config, *_: ('observer' if config.read_only else 'fixer',
        '' if config.read_only else 'CAMPAIGN_DECISION:\nstatus: no-change\nreason: partial candidate needs investigation'))
    assert campaign.run_campaign(cfg) == 0
    state = campaign._load_state(cfg.state_file)
    # The unverified partial edit was abandoned for cycle 1; the loop continued.
    assert state.completed_cycles == 1
    assert state.pending_fix is None
    assert state.primary_session_id == 'observer'
    monkeypatch.setattr(campaign, '_fresh_command', lambda cmd: cmd)
    monkeypatch.setattr(campaign, '_run',
                        lambda command, **_: subprocess.CompletedProcess(command, 0, 'fresh ok', ''))
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: 'original')
    assert campaign.run_campaign(dataclass_replace(cfg, max_cycles=2)) == 0
    assert campaign._load_state(cfg.state_file).completed_cycles == 2


def test_review_rejects_undeclared_new_code_before_commit(tmp_path, monkeypatch):
    import subprocess
    from dataclasses import replace as dataclass_replace
    from tools import campaign_fix_flow as fix_flow
    from tools.campaign_loop_log import fixes as fix_ledger
    cfg = dataclass_replace(_restart_config(tmp_path), dry_run=False)
    monkeypatch.setattr(campaign, '_dirty_paths',
                        lambda: {'tools/run_context.py', 'tools/hidden_change.py'})
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: 'same')
    review_prompts = []
    def ask(config, prompt, session, sequence, **_):
        review_prompts.append(prompt)
        if 'Primary review rejected' in prompt:
            # The fixer keeps hiding the undeclared change.
            return 'fixer', 'CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: tools/run_context.py\ntest: pytest passed\nprediction: sustained plastic\nreason: test'
        if 'REVIEW_DECISION' in prompt:
            return 'primary', 'REVIEW_DECISION:\nstatus: approved\nreason: ok'
        return 'fixer', 'CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: tools/run_context.py\ntest: pytest passed\nprediction: sustained plastic\nreason: test'
    monkeypatch.setattr(campaign, '_ask', ask)
    def run(command, **_):
        if 'pytest' in command:
            return subprocess.CompletedProcess(command, 0, 'passed', '')
        if command[:2] == ['git', 'ls-files']:
            return subprocess.CompletedProcess(command, 0, '', '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(campaign, '_run', run)
    (cfg.opencode_log_dir).mkdir(parents=True, exist_ok=True)
    (cfg.state_root / 'logs/campaign-loop').mkdir(parents=True, exist_ok=True)
    synthesis = cfg.state_root / 'logs/campaign-loop/synthesis-0001.md'
    synthesis.write_text('hypothesis: x')
    board = cfg.opencode_log_dir / 'observers/episode-1/board.md'
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text('board')
    state = campaign.State(campaign_id='c', active_cycle=1, active_episode_id='episode-1')
    outcome = fix_flow.run_fix_cycle(cfg, state, 1, 'episode-1', synthesis, board,
        {'status': 'unavailable', 'path': None, 'reason': 'none'}, 4)
    assert outcome == 'abandoned'
    assert not any(str(path).endswith('git') for path in [()])
    assert 'hidden_change' in (cfg.observations).read_text()
    assert fix_ledger(cfg.state_root)[list(fix_ledger(cfg.state_root))[-1]]['status'] == 'abandoned'


def test_laya_fixer_permissions_scope_edits_and_deny_lifecycle_and_git_writes(tmp_path, monkeypatch):
    import json
    import subprocess
    from dataclasses import replace
    worktree = tmp_path/'campaign-code'
    cfg = replace(_restart_config(tmp_path), dry_run=False, runtime='laya', campaign_worktree=worktree)
    seen = {}
    def run(command, **kwargs):
        seen['command'] = command
        return subprocess.CompletedProcess(command, 0, '{"sessionID":"scoped-fixer"}\n', '')
    monkeypatch.setattr(campaign, '_run', run)
    campaign._ask(cfg, 'one focused fix', None, 9)
    assert seen['command'][0] == 'env'
    policy = json.loads(seen['command'][1].split('=',1)[1])['permission']
    assert policy['*'] == 'deny'
    assert policy['edit']['*'] == 'deny' and policy['edit'][str(worktree.resolve())+'/**'] == 'allow'
    assert policy['bash']['*systemctl*'] == 'deny'
    assert policy['bash']['*git*commit*'] == 'deny'
    assert policy['task'] == 'deny'
