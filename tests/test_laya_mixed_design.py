# Path: tests/test_laya_mixed_design.py | Purpose: Verify paired allocation and current-evidence accounting.
import json

import pytest

from tools.laya_mixed_design import allocate_pairs, build_jobs
from tools.laya_checkpoint_regression import report

pytestmark = pytest.mark.fast


def test_allocation_and_invalid_small_totals():
    assert allocate_pairs() == dict(zip((f'C{i}' for i in range(7)), (24, 5, 5, 5, 5, 8, 8)))
    for total in (1, 2, 12):
        with pytest.raises(ValueError):
            allocate_pairs(total)
    assert sum(allocate_pairs(40, .35).values()) == 20


def test_one_hundred_eighty_job_allocation_covers_every_origin():
    pairs = allocate_pairs(180)
    assert pairs == {'C0': 36, 'C1': 8, 'C2': 8, 'C3': 7, 'C4': 7, 'C5': 12, 'C6': 12}
    assert sum(pairs.values()) * 2 == 180 and pairs['C0'] / 90 == .4


def test_excluded_origin_is_reallocated_explicitly_by_original_weights():
    pairs = allocate_pairs(200, excluded=['C1'])
    assert pairs == {'C0': 40, 'C1': 0, 'C2': 10, 'C3': 10, 'C4': 10, 'C5': 15, 'C6': 15}
    origins = {f'C{i}': {'generation_id': f'g{i}', 'validated': True, 'immutable': True} for i in range(7) if i != 1}
    plan = build_jobs(origins, total_runs=200, policy_seeds=range(100), engine='e', mods='m', budget={'ticks': 1},
                      excluded={'C1': 'strict C1 not attainable by the gated mall'})
    assert len(plan['jobs']) == 200 and plan['excluded_origins'] == {'C1': 'strict C1 not attainable by the gated mall'}
    assert not any(j['origin'] == 'C1' for j in plan['jobs'])
    with pytest.raises(ValueError):
        build_jobs(origins, total_runs=200, policy_seeds=range(100), engine='e', mods='m', budget={'ticks': 1})
    with pytest.raises(ValueError):
        allocate_pairs(200, excluded=['C0'])


def test_pairs_interleave_and_keep_unavailable_lanes():
    origins = {f'C{i}': {'generation_id': f'g{i}', 'validated': True, 'immutable': True} for i in range(7)}
    origins['C3'] = {'unavailable': True, 'reason': 'no validated source'}
    plan = build_jobs(origins, policy_seeds=range(60), engine='engine', mods='mods', budget={'ticks': 100})
    jobs = plan['jobs']
    assert len(jobs) == 120 and jobs[0]['origin'] == 'C6'
    assert plan['unavailable_origins'] == {'C3': 'no validated source'}
    for pair in range(60):
        a, b = jobs[2*pair:2*pair+2]
        assert (a['origin'], a['seed'], a['source'], a['engine'], a['mods'], a['budget']) == (b['origin'], b['seed'], b['source'], b['engine'], b['mods'], b['budget'])
        assert a['arm'] != b['arm']
    assert jobs[0]['arm'] != jobs[2]['arm']
    assert all(j['status'] == 'unavailable' and j['strict_target'] == 'C5' for j in jobs if j['origin'] == 'C3')


def test_report_requires_new_current_observations(tmp_path):
    jobs = [{'checkpoint': 'C1', 'arm': 'a', 'status': 'completed', 'loaded_tick': 100,
             'successor_passed': True, 'accepted_final': ['C2'],
             'strict_current': [{'tick': 100, 'statuses': {'C2': True}}, {'tick': 110, 'statuses': {'C3': True}}]},
            {'checkpoint': 'C1', 'arm': 'a', 'status': 'timeout', 'loaded_tick': 100,
             'strict_current_at_end': {'C2': True}}]
    (tmp_path/'job-status.json').write_text(json.dumps({'name': 'test', 'jobs': jobs}))
    (tmp_path/'regression.json').write_text('{}')
    group = report(tmp_path)['groups'][0]
    assert group['planned'] == 2
    assert group['strict_reach']['C2']['reached'] == 0
    assert group['strict_reach']['C3']['ticks_from_load'] == [10]
    assert group['held_at_end']['C2'] == {'true': 1, 'false': 0, 'unknown': 1}
    assert group['classifications']['right_censored_timeout'] == 1
