# Path: tests/test_laya_matrix_campaign.py | Purpose: Protect fleet tail gates, frozen inputs, complete denominators and matched confirmation selection.
import json
from pathlib import Path

import pytest

from tools.laya_matrix_campaign import Campaign, gate, lines, summarize, verify

pytestmark = pytest.mark.fast


def world(ups=None, latency=1, arm='a'):
    return {'ups': ups if ups is not None else [120] * 100,
            'decisions': [latency] * 30, 'code_arm': arm}


def test_approved_fractions_and_both_latency_limits_are_required():
    assert gate({'one': world([120] * 95 + [105] * 5)}, {'a': 1})[0]
    assert not gate({'one': world([120] * 94 + [105] * 6)}, {'a': 1})[0]
    assert not gate({'one': world([120] * 97 + [80] * 3)}, {'a': 1})[0]
    assert not gate({'one': world(latency=2.01)}, {'a': 1})[0]
    assert not gate({'one': world(latency=5.01)}, {'a': 4})[0]
    assert not gate({'one': world([120] * 99)}, {'a': 1})[0]
    assert not gate({'one': world()}, {})[0]
    assert not gate({}, {'a': 1})[0]
    assert not gate({'fast': world(), 'slow': world([80] * 100)}, {'a': 1})[0]


def test_large_and_incomplete_json_rows_are_preserved(tmp_path):
    p = tmp_path / 'rows.jsonl'
    row = {'payload': 'x' * 100000}
    p.write_text(json.dumps(row) + '\n{"second":')
    rows, offset = lines(p)
    assert rows == [row]
    with p.open('a') as f:
        f.write('true}\n')
    assert lines(p, offset)[0] == [{'second': True}]


def test_acceptance_requires_original_predicate_packet_even_after_crash(tmp_path):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    packet = evidence / 'packet.json'
    packet.write_text(json.dumps({'results': {'C2': {'status': 'passed', 'evidence': {'checks': {'ready': True}}}},
                                 'current': {'C2': {'status': 'failed'}}}))
    rows = [{'event': 'decision'}, {'event': 'acceptance', 'statuses': {'C1': 'passed'}},
            {'event': 'acceptance', 'statuses': {'C2': 'passed'}, 'details_path': str(packet), 'wall_epoch': 10}]
    (evidence / 'clean-decisions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    result = summarize(tmp_path)
    assert result['decisions'] == 1
    assert result['accepted_verified'] == ['C2']
    assert result['current_acceptance'] == {'C2': 'failed'}
    assert result['first_acceptance_epoch'] == {'C2': 10}


def manifest(tmp_path):
    configs = [{'id': f'{a}-{e}', 'code_arm': a, 'epsilon': e, 'variant': 'unused'}
               for a in ('a', 'b', 'c', 'd') for e in (0, .05, .1, .15, .2, .25)]
    return {'root': str(tmp_path), 'deadline_epoch': 0, 'trial_deadline_epoch': 0, 'start_epoch': 0,
            'main_deadline_epoch': 0, 'jobs': [], 'configurations': configs,
            'arms': [{'id': a, 'code': str(tmp_path / a)} for a in ('a', 'b', 'c', 'd')],
            'experiment': 'test', 'reserve_seeds': [2]}


def test_deadline_keeps_every_planned_job_and_writes_final_report(tmp_path):
    m = manifest(tmp_path)
    m['jobs'] = [{'id': str(i), 'config': c['id'], 'cohort': 'core'} for i, c in enumerate(m['configurations'])]
    campaign = Campaign(m)
    campaign.run()
    rows = json.loads((tmp_path / 'job-status.json').read_text())
    assert len(rows) == 24 and all(j['status'].startswith('not_started') for j in rows)
    assert (tmp_path / 'report.md').exists()
    assert sum(v['planned'] for v in json.loads((tmp_path / 'progress.json').read_text())['configurations'].values()) == 24


def test_unmeasured_world_blocks_growth_and_two_complete_windows_are_required(tmp_path, monkeypatch):
    from tools import laya_matrix_campaign as module
    monkeypatch.setattr(module, 'memory_available', lambda: 10 * 1024**3)
    campaign = Campaign(manifest(tmp_path))
    campaign.m['arms'] = [{'id': 'a'}]
    campaign.baselines = {'a': 1}
    campaign.adjust({'one': world()}, False, 300)
    campaign.adjust({'one': world()}, False, 600)
    assert campaign.target == 4
    campaign.adjust({'one': world()}, True, 900)
    assert campaign.target == 4
    campaign.adjust({'one': world()}, True, 1200)
    assert campaign.target == 12
    campaign.adjust({'one': world([80] * 100)}, True, 1500)
    campaign.adjust({'one': world([80] * 100)}, True, 1800)
    assert campaign.target == 8


def test_reserves_are_admitted_as_complete_blocks_only_if_they_fit(tmp_path):
    m = manifest(tmp_path)
    m['main_deadline_epoch'] = 5000
    campaign = Campaign(m)
    campaign.target = 24
    campaign.reserves(0)
    assert len(campaign.jobs) == 24 and {j['seed'] for j in campaign.jobs} == {2}
    other = Campaign(m)
    other.target = 4
    other.reserves(4500)
    assert other.jobs == []


def test_confirmation_scores_only_complete_matched_seed_blocks(tmp_path):
    m = manifest(tmp_path)
    campaign = Campaign(m)
    campaign.jobs = [dict(campaign.make_job(c, 1, 'core'), status='completed', accepted_verified=[])
                     for c in m['configurations']]
    campaign.jobs[0]['accepted_verified'] = ['C1', 'C2']
    # An unmatched favorable run must not drive selection.
    campaign.jobs.append(dict(campaign.make_job(m['configurations'][-1], 2, 'core'),
                              status='completed', accepted_verified=['C1', 'C2']))
    campaign.select_confirmation()
    selected = json.loads((tmp_path / 'confirmation-selection.json').read_text())
    assert selected['matched_development_seeds'] == [1]
    assert {c['code_arm'] for c in selected['configs']} == {'a', 'b'}
    assert len([j for j in campaign.jobs if j['cohort'] == 'confirmation']) == 12


def test_changed_frozen_input_is_rejected(tmp_path):
    p = tmp_path / 'input'
    p.write_text('changed')
    with pytest.raises(ValueError, match='frozen file changed'):
        verify({'frozen_files': {str(p): '0' * 64}})


def test_repeated_setup_failure_stops_new_admissions_and_retains_jobs(tmp_path, monkeypatch):
    from tools import laya_matrix_campaign as module
    m = manifest(tmp_path)
    now = module.time.time()
    m.update(start_epoch=now, main_deadline_epoch=now + 3000,
             trial_deadline_epoch=now + 3300, deadline_epoch=now + 3600)
    campaign = Campaign(m)
    campaign.jobs = [campaign.make_job(c, 70001, 'calibration', 600) for c in m['configurations'][:12]]
    monkeypatch.setattr(campaign, 'run_job', lambda job, slot: job.update(status='prepare_failed'))
    monkeypatch.setattr(module, 'memory_available', lambda: 10 * 1024**3)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    campaign.run()
    assert campaign.stopping and (tmp_path / 'setup-failure.json').exists()
    saved = json.loads((tmp_path / 'job-status.json').read_text())
    assert len(saved) == 12
    assert sum(j['status'] == 'prepare_failed' for j in saved) <= 4
    assert any(j['status'] == 'not_started_interrupted' for j in saved)
