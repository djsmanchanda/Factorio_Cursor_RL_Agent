# Path: tests/test_laya_mall_fleet.py | Purpose: Protect matched cohort splits, qualified hindsight data and independent student admission.

import json
from training.laya_mall import scenario,decision_state,compile_candidates
from tests.test_laya_mall import facts
from tools.run_laya_mall_fleet import scenario_split,select_seed,export_cases,student_gate


def test_physical_scenario_identity_controls_splits_not_worker_names():
    assert scenario_split(scenario(12,'a'))==scenario_split(scenario(12,'b'))
    for split in ['train','calibration','held_out']:
        assert scenario_split(scenario(select_seed(21000,split),'cohort'))==split


def test_only_observed_earned_growth_can_supply_hindsight_cases(tmp_path):
    f=facts();menu=[c for c in compile_candidates(f) if c['family']=='wait']
    state=decision_state(f,menu,'test',{'model_id':'population','model_hash':'sha256:'+'0'*64})
    (tmp_path/'episodes').mkdir()
    trace=tmp_path/'episodes/r0000-w00.jsonl'
    trace.write_text(json.dumps({'status':'observed','state':state,'candidates':menu,'selected':menu[0]['option_id']})+'\n')
    result={'worker':0,'round':0,'status':'observed','upgrades':0,'pending_ghosts':0,'pending_upgrades':0,'scenario_hash':f['scenario']['scenario_hash'],'split':'train'}
    assert export_cases(tmp_path,[result])==0
    result['upgrades']=1
    assert export_cases(tmp_path,[result])==1
    case=json.loads((tmp_path/'cases.jsonl').read_text());assert case['gold']['select_candidate']['label_source']=='outcome'
    assert case['label_provenance']['method']=='hindsight' and case['source']['scenario_seed']<=2147483647


def test_student_gate_requires_multiple_independent_calibration_scenarios(tmp_path):
    cases=[{'split':'train','source':{'scenario_hash':str(i%2)}} for i in range(100)]
    cases += [{'split':'calibration','source':{'scenario_hash':'cal1'}} for _ in range(40)]
    (tmp_path/'cases.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cases))
    assert not student_gate(tmp_path)['ready']
    cases[-1]['source']['scenario_hash']='cal2'
    (tmp_path/'cases.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in cases))
    assert student_gate(tmp_path)['ready']
