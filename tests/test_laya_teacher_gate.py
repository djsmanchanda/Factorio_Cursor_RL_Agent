# Path: tests/test_laya_teacher_gate.py | Purpose: Reject collapsed training teachers without concealing environment failures.

import copy

from tools.laya_teacher_gate import assess
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
