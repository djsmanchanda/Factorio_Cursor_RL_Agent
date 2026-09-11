# Path: tests/test_reliability_campaign.py
# Purpose: Exercise the automated acceptance gate without launching Factorio or models.
import json
import subprocess
from pathlib import Path

from tools import opencode_campaign_orchestrator as campaign


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
    monkeypatch.setattr(campaign, '_snapshot', lambda *a: ('checkpoint', 0))
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
    assert len(calls) == 6


def test_json_assistant_decision_ignores_tool_template():
    raw = '\n'.join([json.dumps({'type':'tool_use','part':{'text':'CAMPAIGN_DECISION:\nstatus: change\n'}}),
                     json.dumps({'type':'text','part':{'text':'CAMPAIGN_DECISION:\nstatus: stop\nreason: unresolved\n'}})])
    assert campaign._decision(raw) == 'stop'


def test_review_cannot_commit_if_verification_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(campaign, 'REPO_ROOT', tmp_path)
    (tmp_path / 'fix.py').write_text('value=1')
    commands = []
    def run(command, **_):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1 if 'pytest' in command else 0, '', '')
    monkeypatch.setattr(campaign, '_run', run)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda _: 'unchanged')
    monkeypatch.setattr(campaign, '_readonly', lambda config: config)
    monkeypatch.setattr(campaign, '_ask', lambda *_: ('reviewer', 'REVIEW_DECISION:\nstatus: approved\nreason: reviewed'))
    cfg = SimpleNamespace(observations=tmp_path / 'notes.md', state_root=tmp_path, python=Path('python'), opencode_log_dir=tmp_path)
    assert not campaign._review_and_commit(cfg, 'CAMPAIGN_DECISION:\nstatus: change\nfiles: fix.py', set(), 1)
    assert not any(command[:2] == ['git','add'] for command in commands)


def test_expired_persisted_deadline_cannot_start_fresh(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    state_file = root / 'logs/reliability-campaign-state.json'
    state_file.parent.mkdir(parents=True)
    state_file.write_text(json.dumps({'completed_cycles': 0, 'deadline_utc': '2000-01-01T00:00:00+00:00'}))
    monkeypatch.setattr(campaign, '_run', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('must not launch')))
    assert campaign.main(['--plastic-reliability', '--dry-run', '--state-root', str(root),
                          '--observations', str(tmp_path / 'notes.md')]) == 0
    assert json.loads(state_file.read_text())['deadline_utc'] == '2000-01-01T00:00:00+00:00'


def test_declared_regression_files_run_without_cost_marker_filter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    commands = []
    monkeypatch.setattr(campaign, '_readonly', lambda config: config)
    monkeypatch.setattr(campaign, '_tree_fingerprint', lambda _: 'unchanged')
    monkeypatch.setattr(campaign, '_ask', lambda *_: ('reviewer', 'REVIEW_DECISION:\nstatus: approved\nreason: reviewed'))
    def run(command, **_):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(campaign, '_run', run)
    cfg = SimpleNamespace(observations=tmp_path / 'notes.md', state_root=tmp_path, python=Path('python'), opencode_log_dir=tmp_path)
    assert campaign._review_and_commit(cfg, 'CAMPAIGN_DECISION:\nstatus: change\nfiles: tests/test_reliability_campaign.py', set(), 1)
    gates = [command for command in commands if 'pytest' in command]
    assert gates[0][-2:] == ['-m', 'not slow and not exhaustive']
    assert gates[1] == ['python', '-m', 'pytest', '-q', '--tb=short', 'tests/test_reliability_campaign.py']
