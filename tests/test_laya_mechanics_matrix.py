# Path: tests/test_laya_mechanics_matrix.py | Purpose: Protect matched controls, intervention coverage, valid variants and earned checkpoint integrity.
from collections import defaultdict
from copy import deepcopy
import json
import re

import pytest

from tools.run_laya_mechanics_matrix import matrix,assign_checkpoints,ROOT
from training.laya_checkpoint import load_bundle,restore_context
from training.laya_component_lab import component_spec
from training.laya_experiment_context import apply_view,reservation_view
from training.laya_saved_world import file_hash
from training.laya_variants import load_variant


def test_matrix_has_controls_for_every_seed_and_configuration_and_valid_variants(tmp_path):
    variants,jobs=matrix(tmp_path)
    groups=defaultdict(list)
    for j in jobs:groups[(j['kind'],j['phase'],j['case'],j['seed'])].append(j)
    assert len({j['job_id'] for j in jobs})==len(jobs)
    assert all(re.fullmatch('[a-z0-9-]+',j['variant']) for j in jobs) # Fixture surface/force grammar.
    assert all(sum(j['variant']=='baseline' for j in arm)==1 for arm in groups.values())
    assert {j['category'].split('-')[0] for j in jobs}=={'checkpoint','transition','context','planning','transport','plastic','recovery'}
    ids={r['rule_id'] for r in json.loads((ROOT/'docs/rl/laya_mission_guidelines.json').read_text())['rules']}
    for name,v in variants.items():
        assert set(v.get('catalog_patch',{}))<=ids
        assert v.get('settings',{}).get('guideline_limit',15)<=15
        p=tmp_path/(name+'.json');p.write_text(json.dumps(v));assert load_variant(p)==v
    assert len(jobs)>500 and len(variants)>80
    assert all('export_checkpoint' in j for j in jobs[:8])
    assert all('export_checkpoint' not in j for j in jobs[8:])


def test_every_physical_intervention_compiles_funded_in_bounds_ghosts(tmp_path):
    _,jobs=matrix(tmp_path)
    cases={json.dumps(j['component'],sort_keys=True) for j in jobs if j['kind']=='component'}
    for raw in cases:
        s=component_spec(**json.loads(raw))
        for group in s['groups'].values():
            for a in group:
                assert a['action_type']=='place_ghost'
                assert -64<=a['position']['x']<128 and -64<=a['position']['y']<96
        if s['fault_action']:
            assert s['budget'][s['fault_action']['entity']]>=2
        assert all(p.get('item') not in {'copper-cable','electronic-circuit','plastic-bar'} for p in s['sources'])


def test_experiment_land_relaxation_keeps_original_plan_and_ore_constraints():
    p={'version':'laya-site-plan-v1','zones':[{'purpose':'mall'},{'purpose':'ore:iron-ore'},{'purpose':'corridor'}]}
    original=deepcopy(p)
    assert reservation_view(p,'mall-open')['zones']==p['zones'][1:]
    assert reservation_view(p,'corridor-open')['zones']==p['zones'][:2]
    assert reservation_view(p,'soft-zones')['zones']==[] and p==original
    with pytest.raises(ValueError):reservation_view(p,'unknown')


def test_context_ablation_and_history_do_not_mutate_validator_facts():
    facts={'networks':{},'recipes':{}}
    original=deepcopy(facts)
    state={'observation_digest':{'factory_status':{'bottleneck':['plate:short'],'needs':[{'item':'plate'}]}}}
    apply_view(state,facts,[],{'needs':[]},[],'bottleneck')
    assert state['observation_digest']['factory_status']=={'bottleneck':['plate:short']}
    history=[{'experiment_outcome':{'family':'assemble','product':'gear','produced':{}}}]
    apply_view(state,facts,[],{'needs':[]},history,'history')
    assert state['observation_digest']['action_outcomes']==[history[0]['experiment_outcome']]
    assert facts==original


def test_checkpoint_tampering_is_rejected_and_restore_preserves_only_attribution(tmp_path):
    bundle=tmp_path/'bundle';(bundle/'evidence').mkdir(parents=True)
    (bundle/'source.zip').write_bytes(b'earned save')
    (bundle/'evidence/transaction-test.json').write_text('{}')
    (bundle/'evidence/site-plan.json').write_text('{}')
    (bundle/'evidence/decision-states.jsonl').write_text('old episode')
    meta={'version':'laya-earned-checkpoint-v1','production_authority':False,'phases_reached':['C0','C1'],'extra_items_granted':0,
          'files':{str(p.relative_to(bundle)):file_hash(p) for p in bundle.rglob('*') if p.is_file()}}
    (bundle/'checkpoint.json').write_text(json.dumps(meta))
    new=tmp_path/'world';new.mkdir();restore_context(bundle,new)
    assert (new/'evidence/transaction-test.json').exists()
    assert not (new/'evidence/decision-states.jsonl').exists()
    (bundle/'source.zip').write_bytes(b'changed save')
    with pytest.raises(ValueError,match='hash/scope'):load_bundle(bundle)


def test_failed_seed_uses_a_distinct_earned_checkpoint_or_stays_blocked(tmp_path):
    for name in ['earned-a','earned-b']:
        p=tmp_path/name;p.mkdir();(p/'checkpoint.json').touch()
    rows=[{'export_checkpoint':str(tmp_path/n)} for n in ['missing','earned-a','earned-b']]
    rows += [{'case':n,'resume_bundle':str(tmp_path/'missing')} for n in ['first','second','third']]
    result=assign_checkpoints(rows)
    assert set(v for v in result.values() if v)=={str(tmp_path/'earned-a'),str(tmp_path/'earned-b')}
    assert result['third'] is None
