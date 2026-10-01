# Path: tests/test_laya_teacher_gate.py | Purpose: Reject collapsed training teachers without concealing environment failures.

import copy

from tools.laya_teacher_gate import assess, assess_levels, main
from training.laya_failure import is_safe_lab_timeout


def row(status='completed',ticks=100):
    return {'status':status,'elapsed_ticks':ticks,'material_cost':0,'decisions':1,'safety_violations':0,
            'comparison_conditions':{'scenario_hash':'same','request_version':'factorio-decision-v2',
             'guideline_hash':'same','max_len':2048,'max_decisions':60,'max_no_progress':60,
             'stepped':True,'game_speed':32}}


def test_completion_loss_uses_verified_reference_even_if_failed_candidate_is_cheaper():
    code,report=assess({1:row()}, {1:row('timed_out')}, {1})
    assert code==3 and report['status']=='use_reference'


def test_slower_teacher_is_not_kept_just_because_it_completes():
    code,report=assess({1:row()}, {1:row(ticks=200)}, {1})
    assert code==3


def test_cheaper_completing_teacher_is_admitted():
    code,report=assess({1:row(ticks=200)}, {1:row()}, {1})
    assert code==0 and report['status']=='admitted'
    assert report['eligible_for_real_nauvis'] is False


def test_runtime_failures_and_all_failed_cohorts_block_instead_of_bootstrapping():
    assert assess({1:row()}, {1:row('failed')}, {1})[0]==2
    assert assess({1:row('timed_out')}, {1:row('timed_out')}, {1})[0]==2
    bad=row();bad['safety_violations']=1
    assert assess({1:row()}, {1:bad}, {1})[0]==2
    assert assess({1:row()}, {}, {1})[0]==2


def test_horizon_is_policy_failure_only_with_scoped_explicit_safety_health():
    report={'status':'timed_out','failure':{'kind':'timeout'},'surface':'training/teacher-1',
            'force':'training-teacher-1','metrics':{'forbidden_entities':0,'out_of_bounds_entities':0,
             'budget_overruns':0,'fixtures_valid':True,'power_connected':True}}
    assert is_safe_lab_timeout(report)
    for key in ('forbidden_entities','fixtures_valid','power_connected'):
        missing=copy.deepcopy(report);del missing['metrics'][key]
        assert not is_safe_lab_timeout(missing)
    report['surface']='nauvis'
    assert not is_safe_lab_timeout(report)


def test_global_teacher_rejects_earlier_level_completion_regression():
    baseline = {1: {1: row()}, 2: {1: row(ticks=200)}, 3: {1: row()}}
    candidate = {1: {1: row()}, 2: {1: row()}, 3: {1: row('timed_out')}}
    code, report = assess_levels(baseline, candidate, {1})
    assert code == 3
    assert report['levels']['2']['status'] == 'admitted'
    assert report['levels']['3']['candidate']['completion_losses'] == 1
    assert report['status'] == 'use_reference'


def test_global_teacher_requires_complete_matching_evidence_for_every_level():
    baseline = {1: {1: row()}, 3: {1: row()}}
    assert assess_levels(baseline, {1: {1: row()}}, {1})[0] == 2
    assert assess_levels(baseline, {1: {1: row('timed_out')}, 3: {}}, {1})[0] == 2
    assert assess_levels(baseline, baseline, {1})[0] == 0


def test_multi_level_cli_uses_every_supplied_pair(tmp_path, capsys):
    import json
    base = tmp_path / 'base.jsonl'
    good = tmp_path / 'good.jsonl'
    bad = tmp_path / 'bad.jsonl'
    for path, result in ((base, row(ticks=200)), (good, row()), (bad, row('timed_out'))):
        path.write_text(json.dumps({'seed': 1, **result}) + '\n')
    assert main(['--level-pair', '2', str(base), str(good), '--level-pair', '3', str(base), str(bad),
                 '--seed-start', '1', '--episodes', '1']) == 3
    assert json.loads(capsys.readouterr().out)['status'] == 'use_reference'
