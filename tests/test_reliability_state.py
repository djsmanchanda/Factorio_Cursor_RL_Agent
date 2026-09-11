# Path: tests/test_reliability_state.py
# Purpose: Certify only repeated sustained production on matching fresh candidates.
from copy import deepcopy

from tools.reliability_state import assess, record


def evidence(episode='e1'):
    manifest = dict(episode_id=episode, repository_revision='rev1', source_save_sha256='save1',
                    deployed_factorio_mod_sha256='mod1', deployed_factorio_training_lab_sha256='train1',
                    bootstrap_profile='reduced-v1', surface='nauvis', force='player')
    samples = [dict(tick=t, machines={'1': t // 60}, provider_id=2, provider_count=5,
                    target='plastic-bar', surface='nauvis', force='player') for t in range(0, 7201, 600)]
    report = dict(schema_version=1, ok=True, result='passed', episode_id=episode, target='plastic-bar',
                  surface='nauvis', force='player', provenance=deepcopy(manifest), acceptance_seconds=120,
                  sample_seconds=10, start_tick=0, end_tick=7200, samples=samples)
    return manifest, report


def test_three_same_candidate_fresh_runs_required(tmp_path):
    path = tmp_path / 'state.json'
    for number in range(1, 4):
        manifest, report = evidence(f'e{number}')
        state = record(path, manifest, report, seconds=120, required=3, code_hash='code')
        assert len(state['streak']) == number
        assert state['phase'] == ('research' if number == 3 else 'plastic')
    assert state['baseline']['episodes'] == ['e1', 'e2', 'e3']
    assert record(path, manifest, report, seconds=120, required=3, code_hash='code') == state


def test_failure_and_candidate_change_reset_streak(tmp_path):
    path = tmp_path / 'state.json'
    for number, code, passed, expected in [(1, 'a', True, 1), (2, 'a', False, 0), (3, 'a', True, 1), (4, 'b', True, 1)]:
        manifest, report = evidence(f'e{number}')
        report['ok'] = passed
        state = record(path, manifest, report, seconds=120, required=3, code_hash=code)
        assert len(state['streak']) == expected
        assert state['phase'] == 'plastic'


def test_stale_report_or_stock_only_cannot_pass():
    manifest, report = evidence()
    report['episode_id'] = 'other'
    assert not assess(report, manifest, seconds=120)[0]
    report['episode_id'] = manifest['episode_id']
    report['provenance']['repository_revision'] = 'old'
    assert not assess(report, manifest, seconds=120)[0]
    report['provenance'] = dict(manifest)
    for sample in report['samples']:
        sample['machines'] = {'1': 10}
    assert not assess(report, manifest, seconds=120)[0]
