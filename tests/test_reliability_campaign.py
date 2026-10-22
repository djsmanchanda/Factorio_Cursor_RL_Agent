# Path: tests/test_reliability_campaign.py
# Purpose: Exercise the automated acceptance gate and fix flow without launching Factorio or models.
import json
import subprocess
from pathlib import Path

from tools import opencode_campaign_orchestrator as campaign
from tools import campaign_fix_flow as fix_flow
from tools.campaign_loop_log import fixes as fix_ledger, loop_root


def review_config(root):
    return campaign.Config(
        state_root=root, source_save=root / 'source.zip', observations=root / 'notes.md',
        state_file=root / 'state.json', opencode_log_dir=root, technology='mining-productivity-4',
        interval_seconds=120, post_run_wait_seconds=0, max_cycles=1, max_runtime_seconds=60,
        model='test', variant='high', opencode_bin='opencode', python=Path('python'),
        campaign_manager=root / 'manager', dashboard_url='http://127.0.0.1',
        dry_run=False, resume_active_run=False,
    )


def test_three_fresh_successes_skip_fixer_and_certify(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    source = tmp_path / 'source.zip'
    source.write_bytes(b'baseline')
    cycles = []
    def run(command, **_):
        if 'fresh' in command:
            cycles.append(command)
            episode = f'episode-{len(cycles)}'
            manifest = dict(episode_id=episode, repository_revision='rev', source_save_sha256='save',
                deployed_factorio_mod_sha256='mod', deployed_factorio_training_lab_sha256='training',
                bootstrap_profile='reduced-v1', surface='nauvis', force='player')
            (root / 'episode').mkdir(parents=True, exist_ok=True)
            (root / 'episode/current.json').write_text(json.dumps(manifest))
            samples = [dict(tick=t, machines={'1': t // 60}, provider_id=1, provider_count=2,
                target='plastic-bar', surface='nauvis', force='player') for t in range(0, 7201, 600)]
            report = dict(schema_version=1, ok=True, result='passed', episode_id=episode, target='plastic-bar',
                surface='nauvis', force='player', provenance=manifest, acceptance_seconds=120, sample_seconds=10,
                start_tick=0, end_tick=7200, samples=samples)
            (root / 'logs/production-acceptance.json').write_text(json.dumps(report))
            (root / 'logs/autonomous-run.log').write_text('RUN START: ts=2026-09-11T00:00:00Z\n+120s RUN END\n')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(campaign, '_run', run)
    monkeypatch.setattr(campaign, '_code_hash', lambda: 'code')
    monkeypatch.setattr(campaign, '_snapshot', lambda *a: ('checkpoint', 0, None))
    monkeypatch.setattr(campaign, 'index_log', lambda *_: None)
    calls = []
    def ask(config, prompt, session, sequence):
        calls.append(prompt)
        assert 'has RUN END' not in prompt
        return 'session', ''
    monkeypatch.setattr(campaign, '_ask', ask)
    result = campaign.main(['--plastic-reliability', '--stop-after-acceptance', '--state-root', str(root),
        '--source-save', str(source), '--observations', str(tmp_path / 'notes.md'), '--max-cycles', '5'])
    assert result == 0 and len(cycles) == 3
    assert all('--produce' in command and 'plastic-bar' in command for command in cycles)
    state = json.loads((root / 'logs/reliability-state.json').read_text())
    assert state['phase'] == 'research' and len(state['baseline']['episodes']) == 3
    # One persistent primary bootstrap, one concise checkpoint turn per run,
    # and one concise run transition for every later run. No new session per run.
    assert len(calls) == 6
    assert 'persistent PRIMARY campaign agent' in calls[0]
    assert 'Checkpoint 1 for episode episode-1' in calls[1]
    assert 'Starting deterministic run 2/' in calls[2]
    assert 'Checkpoint 1 for episode episode-2' in calls[3]
    assert 'Starting deterministic run 3/' in calls[4]
    restored = campaign._load_state(root / 'logs/reliability-campaign-state.json')
    assert restored.primary_session_id == 'session'


def test_json_assistant_decision_ignores_tool_template():
    raw = '\n'.join([json.dumps({'type':'tool_use','part':{'text':'CAMPAIGN_DECISION:\nstatus: change\n'}}),
                     json.dumps({'type':'text','part':{'text':'CAMPAIGN_DECISION:\nstatus: stop\nreason: unresolved\n'}})])
    assert campaign._decision(raw) == 'stop'


def test_model_call_resumes_saved_session_after_interruption(tmp_path, monkeypatch):
    cfg = review_config(tmp_path)
    calls = []

    def ask(config, prompt, session, sequence, retry=0):
        calls.append((prompt, session, sequence, retry))
        if retry == 0:
            raise TimeoutError('provider busy')
        return 'saved-session', 'completed response'

    monkeypatch.setattr(campaign, '_ask', ask)
    session, output = campaign._ask_with_continue(cfg, 'inspect evidence', 'saved-session', 7)
    assert (session, output) == ('saved-session', 'completed response')
    assert calls[1][1] == 'saved-session' and calls[1][3] == 1
    assert calls[1][0] == 'continue'


def test_expired_persisted_deadline_cannot_start_fresh(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    state_file = root / 'logs/reliability-campaign-state.json'
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({'completed_cycles': 0, 'deadline_utc': '2000-01-01T00:00:00+00:00'}))
    monkeypatch.setattr(campaign, '_run', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('must not launch')))
    assert campaign.main(['--plastic-reliability', '--dry-run', '--state-root', str(root),
                          '--observations', str(tmp_path / 'notes.md')]) == 0
    assert json.loads(state_file.read_text())['deadline_utc'] == '2000-01-01T00:00:00+00:00'


CHANGE = ('CAMPAIGN_DECISION:\nstatus: change\nkind: behavior\nfiles: {files}\n'
          'test: pytest passed\nprediction: sustained plastic\nreason: fix\n')


def run_fix(tmp_path, monkeypatch, *, fixer_outputs, review_outputs, pytest_results,
            declared_files='fix.py', dirty=()):
    """Drive one full fix cycle with mocked model/git/pytest commands."""
    # The real milestone writer must be scoped to this disposable repository.
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'CURRENT_STATUS.md').write_text('test status\n')
    monkeypatch.setattr(campaign, 'REPO_ROOT', repo)
    root = tmp_path / 'runtime'
    root.mkdir(exist_ok=True)
    cfg = review_config(root)
    state = campaign.State(campaign_id='campaign-1', active_cycle=1, active_episode_id='episode-1',
                           pre_cycle_dirty=list(dirty))
    (root / 'logs/campaign-loop').mkdir(parents=True, exist_ok=True)
    synthesis = root / 'logs/campaign-loop/synthesis-0001.md'
    synthesis.write_text('hypothesis: root cause')
    board = root / 'agents/observers/episode-1/board.md'
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text('board')
    calls = []
    declared = declared_files.split(', ')

    def ask(config, prompt, session, sequence, retry=0):
        calls.append(prompt)
        if 'Primary review rejected' in prompt or 'Verification failed' in prompt:
            return 'fixer-session', fixer_outputs.pop(0) if fixer_outputs else ''
        if 'REVIEW_DECISION' in prompt:
            return 'primary-session', review_outputs.pop(0)
        if 'has ended' in prompt:
            return 'fixer-session', fixer_outputs.pop(0) if fixer_outputs else ''
        return 'observer-session', ''

    monkeypatch.setattr(campaign, '_ask', ask)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda *_: 'same')
    monkeypatch.setattr(campaign, '_dirty_paths', lambda: set(declared) | set(dirty))

    def run(command, **_):
        calls.append(' '.join(str(part) for part in command))
        if command[:2] == ['git', 'ls-files']:
            return subprocess.CompletedProcess(command, 0 if command[-1] in declared else 1, '', '')
        if 'pytest' in command:
            return subprocess.CompletedProcess(command, pytest_results.pop(0), 'tests ran', '')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(campaign, '_run', run)
    outcome = fix_flow.run_fix_cycle(cfg, state, 1, 'episode-1', synthesis, board,
                                     {'status': 'unavailable', 'path': None, 'reason': 'none'}, 5)
    return outcome, state, calls, root


def test_verified_change_commits_declared_files_and_status(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py')],
        review_outputs=['REVIEW_DECISION:\nstatus: approved\nreason: verified'],
        pytest_results=[0],
    )
    assert outcome == 'committed'
    staged = [line for line in calls if line.startswith('git add')]
    assert staged == ['git add -- fix.py CURRENT_STATUS.md']
    assert [line for line in calls if line.startswith('git commit')]
    record = [fix for fix in fix_ledger(root).values()][-1]
    assert record['status'] == 'committed' and record['files'] == ['fix.py']


def test_review_cannot_commit_if_verification_fails(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py')] * 3,
        review_outputs=['REVIEW_DECISION:\nstatus: approved\nreason: verified'] * 3,
        pytest_results=[1, 1, 1],
    )
    assert outcome == 'abandoned'
    assert not any(line.startswith('git add') for line in calls)
    assert not any(line.startswith('git commit') for line in calls)
    assert fix_ledger(root)[list(fix_ledger(root))[-1]]['status'] == 'abandoned'


def pytest_commands(calls):
    return [line for line in calls if line.startswith('python -m pytest')]


def test_declared_regression_files_run_without_cost_marker_filter(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='tests/test_reliability_campaign.py')],
        review_outputs=['REVIEW_DECISION:\nstatus: approved\nreason: verified'],
        pytest_results=[0, 0],
        declared_files='tests/test_reliability_campaign.py',
    )
    assert outcome == 'committed'
    gates = pytest_commands(calls)
    assert gates[0].endswith('-m not slow and not exhaustive')
    assert 'tests/test_reliability_campaign.py' in gates[1]


def test_undocumented_test_change_still_runs_mandatory_gate(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py')],
        review_outputs=['REVIEW_DECISION:\nstatus: approved\nreason: verified'],
        pytest_results=[0],
    )
    assert outcome == 'committed'
    gates = pytest_commands(calls)
    assert len(gates) == 1
    assert all(str(path) in gates[0] for path in
               [Path(__file__).resolve().parents[1] / name for name in campaign.MANDATORY_CAMPAIGN_TESTS])
    assert 'Verification log:' in (root / 'notes.md').read_text()


def test_rejected_review_revises_with_the_same_fixer_then_commits(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py'), CHANGE.format(files='fix.py')],
        review_outputs=['REVIEW_DECISION:\nstatus: rejected\nreason: missing coverage',
                        'REVIEW_DECISION:\nstatus: approved\nreason: verified'],
        pytest_results=[0],
    )
    assert outcome == 'committed'
    reviews = [prompt for prompt in calls if 'REVIEW_DECISION' in prompt]
    assert len(reviews) == 2
    revision = [prompt for prompt in calls if prompt.startswith('Primary review rejected FIX-')]
    assert revision and 'missing coverage' in revision[0]


def test_primary_rejection_abandons_after_bounded_revisions(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py')] * 3,
        review_outputs=['REVIEW_DECISION:\nstatus: rejected\nreason: no'] * 3,
        pytest_results=[],
    )
    assert outcome == 'abandoned'
    assert not any(line.startswith('git commit') for line in calls)


def test_unrelated_user_work_is_never_staged(tmp_path, monkeypatch):
    outcome, state, calls, root = run_fix(
        tmp_path, monkeypatch,
        fixer_outputs=[CHANGE.format(files='fix.py')],
        review_outputs=['REVIEW_DECISION:\nstatus: approved\nreason: verified'],
        pytest_results=[0],
        dirty=('user_note.md',),
    )
    assert outcome == 'committed'
    assert not any('user_note.md' in line for line in calls if line.startswith('git add'))
