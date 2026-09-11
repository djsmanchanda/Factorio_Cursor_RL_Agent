# Path: tests/test_production_acceptance.py
# Purpose: Guard sustained production acceptance against stock, timing, and identity false positives.
import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.production_acceptance import evaluate, monitor, read_sample


def sample(tick, crafts=1, **overrides):
    return dict(tick=tick, machines={'71': crafts}, provider_id=91, provider_count=10,
                surface='nauvis', force='player', target='plastic-bar', **overrides)


def test_requires_entire_window_of_new_crafts():
    rows = [sample(i * 600, i) for i in range(13)]
    assert not evaluate(rows[:-1], seconds=120)
    assert evaluate(rows, seconds=120)
    for row in rows:
        row['machines']['71'] = 10000
        row['provider_count'] = row['tick'] + 1000
    assert not evaluate(rows, seconds=120)


@pytest.mark.parametrize('change', ['stall', 'identity', 'provider', 'regression', 'gap'])
def test_resets_window_on_discontinuity(change):
    rows = [sample(i * 600, i) for i in range(13)]
    if change == 'stall':
        rows[6]['machines']['71'] = 5
    elif change == 'identity':
        rows[6]['machines'] = {'72': 6000}
    elif change == 'provider':
        rows[6]['provider_count'] = 0
    elif change == 'regression':
        rows[6]['machines']['71'] = 0
    else:
        rows[6]['tick'] = 8000
    assert not evaluate(rows, seconds=120)


def test_can_recover_with_complete_new_window():
    rows = [sample(i * 600, max(0, i - 3)) for i in range(16)]
    assert evaluate(rows, seconds=120)


def run_monitor(tmp_path, rows, *, timeout=600):
    values = iter(rows)
    now = [0]
    def sleep(seconds):
        now[0] += seconds
    return monitor(read=lambda: next(values), path=tmp_path / 'report.json',
                   episode_id='episode-test', provenance={'repository_revision': 'abc'},
                   target='plastic-bar', surface='nauvis', force='player',
                   seconds=120, timeout_seconds=timeout, clock=lambda: now[0], sleep=sleep)


def test_monitor_writes_scoped_game_tick_evidence(tmp_path):
    report = run_monitor(tmp_path, [sample(i * 600, i) for i in range(13)])
    assert report['ok'] and report['result'] == 'passed'
    assert report['end_tick'] - report['start_tick'] == 7200
    assert json.loads((tmp_path / 'report.json').read_text()) == report
    assert report['provenance']['repository_revision'] == 'abc'


def test_wrong_scope_fails_closed(tmp_path):
    row = sample(0)
    row['force'] = 'other'
    report = run_monitor(tmp_path, [row])
    assert report['result'] == 'production_acceptance_invalid'
    assert not report['ok']


def test_paused_simulation_cannot_pass_wall_time(tmp_path):
    report = run_monitor(tmp_path, [sample(0)] * 600)
    assert report['result'] == 'production_acceptance_timeout'
    assert len(report['samples']) == 1


def test_read_is_scoped_and_uses_exact_provider_and_finished_crafts():
    class Client:
        def command(self, command):
            self.command_text = command
            return json.dumps(sample(0))
    client = Client()
    read_sample(client, surface='nauvis', force='player', target='plastic-bar', output_position=(1, 2))
    assert 'force=f' in client.command_text
    assert 'e.products_finished' in client.command_text
    assert 'radius=0.1' in client.command_text
    assert 'p.get_item_count("plastic-bar")' in client.command_text


def test_campaign_production_dry_run(tmp_path):
    source = tmp_path / 'source.zip'
    source.write_text('fixture')
    result = subprocess.run(['bash', 'scripts/manage_linux_deterministic_campaign.sh', 'fresh',
                             '--source-save', str(source), '--root', str(tmp_path / 'isolated'),
                             '--produce', 'plastic-bar', '--acceptance-seconds', '120', '--dry-run'],
                            capture_output=True, text=True, check=True)
    assert '--technology produce:plastic-bar' in result.stdout
    assert '--produce plastic-bar --acceptance-seconds 120' in result.stdout
    assert '--episode-manifest' in result.stdout


def test_invalid_window_rejected_before_lifecycle(tmp_path):
    result = subprocess.run(['bash', 'scripts/manage_linux_deterministic_campaign.sh', 'fresh',
                             '--acceptance-seconds', '0', '--dry-run'], capture_output=True, text=True)
    assert result.returncode != 0
    assert not result.stdout
