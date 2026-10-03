# Path: tests/test_laya_mall.py | Purpose: Protect mall fixture budgets, manufactured credit, priority routing and checkpoint isolation.

import json

import pytest

from planners.laya_quad_cell import generate_quad_slot
from planners.plan_validation import validate_no_collisions
from training.laya_input_profile import input_profile, require_input_profile
from training.laya_mall import scenario, manufactured_stock, compile_candidates, decision_state, plate_priority
from training.laya_mall_supply import MALL_KIT, new_supply


def facts():
    sc=scenario(0,'test')
    recipes={
      'iron-gear-wheel':{'ingredients':[{'name':'iron-plate','amount':2}],'product_amount':1,'energy_ticks':30},
      'copper-cable':{'ingredients':[{'name':'copper-plate','amount':1}],'product_amount':2,'energy_ticks':30},
      'electronic-circuit':{'ingredients':[{'name':'iron-plate','amount':1},{'name':'copper-cable','amount':3}], 'product_amount':1,'energy_ticks':30},
      'transport-belt':{'ingredients':[{'name':'iron-plate','amount':1},{'name':'iron-gear-wheel','amount':1}], 'product_amount':2,'energy_ticks':30},
      'inserter':{'ingredients':[{'name':'iron-plate','amount':1}], 'product_amount':1,'energy_ticks':30},
      'electric-mining-drill':{'ingredients':[{'name':'iron-plate','amount':10}], 'product_amount':1,'energy_ticks':120},
      'splitter':{'ingredients':[{'name':'iron-plate','amount':5}], 'product_amount':1,'energy_ticks':60}}
    return {'version':'mall-world-v1','scenario':sc,'tick':100,'elapsed_ticks':0,'revision':0,
      'provider_stock':dict(MALL_KIT),'reserved':{},'wip':{},'production':dict.fromkeys(recipes,0),
      'consumption':{},'construction_committed':{},'customer_removed':{},'ledger':new_supply(100,{}),'recipes':recipes,
      'served':dict.fromkeys(('circuits','belts'),0),'queue':['circuits','belts','expansion'],
      'machines':[],'pending_ghosts':0,'robots':{'construction':50,'logistic':50}}


def test_seeded_fixture_reserves_all_slots_without_colliding_with_utility_scaffolding():
    for seed in range(8):
        sc=scenario(seed,'fixture-test')
        assert sc['kit']==MALL_KIT and sc['supply_rule']['initial_per_second']=={'iron-plate':1,'copper-plate':1}
        assert sc['robots']=={'construction':50,'logistic':50}
        plans=[('fixtures',{'phases':[{'actions':[{'action_type':'place_entity','entity':f['entity'],'position':f['position']} for f in sc['fixtures']]}]})]
        spec={'ingredients':[{'name':'iron-plate','amount':2}],'product_amount':1,'energy_ticks':30}
        for center in sc['centers']:
            for side in ('north','south','west','east'):
                plans.append((side,{'phases':[{'actions':generate_quad_slot('iron-gear-wheel',spec,(center['x'],center['y']),side)}]}))
        seen_chests=set()
        for _,plan in plans:
            distinct=[]
            for a in plan['phases'][0]['actions']:
                if a['entity'] in {'requester-chest','passive-provider-chest'}:
                    key=(a['entity'],a['position']['x'],a['position']['y'])
                    if key in seen_chests:continue
                    seen_chests.add(key)
                distinct.append(a)
            plan['phases'][0]['actions']=distinct
        validate_no_collisions(plans)
    assert scenario(0,'first')['scenario_hash']==scenario(0,'fork')['scenario_hash']
    assert scenario(0,'first')['scenario_hash']!=scenario(1,'other')['scenario_hash']


def test_seeded_and_committed_items_cannot_be_spent_as_newly_manufactured_output():
    f=facts();f['production']['inserter']=4
    f['provider_stock']['inserter']=12
    # The four newly crafted items are WIP; the provider still holds seeded stock.
    assert manufactured_stock(f)['inserter']==0
    f['provider_stock']['inserter']=16
    assert manufactured_stock(f)['inserter']==4
    f['ledger']['spent_crafting']['inserter']=4
    f['provider_stock']['inserter']=12
    assert manufactured_stock(f)['inserter']==0
    f=facts();f['production']['inserter']=4
    f['construction_committed']['inserter']=12;f['provider_stock']['inserter']=4
    assert manufactured_stock(f)['inserter']==4
    f['construction_committed']['inserter']=14;f['provider_stock']['inserter']=2
    assert manufactured_stock(f)['inserter']==2
    f['customer_removed']['inserter']=2;f['provider_stock']['inserter']=0
    assert manufactured_stock(f)['inserter']==0


def test_queue_priority_controls_supply_bottleneck_without_choosing_a_build_order():
    f=facts()
    assert plate_priority(f)[0][0]=='copper-plate'
    f['queue']=['belts','circuits','expansion']
    f['scenario']['demands'][1]['release_ticks']=0
    assert plate_priority(f)[0][0]=='iron-plate'


def test_mall_profile_is_not_a_lab_or_saved_world_checkpoint_override():
    f=facts()
    candidates=[c for c in compile_candidates(f) if c['family']=='wait']
    state=decision_state(f,candidates,'mall-test',{'model_id':'test','model_hash':'sha256:'+'0'*64})
    assert input_profile(state)=='mall-world-v1'
    with pytest.raises(ValueError,match='not trained'):
        require_input_profile({'input_profiles':['workflow-health-v1']},state,allow_unrecorded=True)
    with pytest.raises(ValueError,match='no recorded mall'):
        require_input_profile({},state,allow_unrecorded=True)
    state['observation_digest']['saved_world']={'version':'saved-world-v1'}
    with pytest.raises(ValueError,match='disguised'):input_profile(state)


def test_cli_rejects_old_checkpoint_before_connecting(monkeypatch,tmp_path):
    from tools import run_laya_mall
    checkpoint=tmp_path/'old';checkpoint.mkdir()
    (checkpoint/'rl_agent_config.json').write_text(json.dumps({'input_profiles':['workflow-health-v1']}))
    monkeypatch.setattr(run_laya_mall,'MallWorld',lambda *a:pytest.fail('incompatible model connected'))
    with pytest.raises(ValueError,match='not trained'):
        run_laya_mall.main(['run','--root','data/laya-saved-world/no-connection-test','--checkpoint',str(checkpoint)])


def test_interrupted_rpc_blocks_clock_and_new_mutations_until_same_nonce_review(tmp_path):
    from training.laya_mall_world import MallWorld
    from training.laya_saved_world import SavedWorldError
    world=object.__new__(MallWorld);world.root=tmp_path
    (tmp_path/'rpc').mkdir()
    path=tmp_path/'rpc'/'lost.json'
    path.write_text(json.dumps({'status':'pending'}))
    with pytest.raises(SavedWorldError,match='needs review'):world.advance(60)
    world.require_clear_mutations(retry_nonce='lost')
    path.write_text(json.dumps({'status':'ambiguous'}))
    with pytest.raises(SavedWorldError,match='needs review'):world.require_clear_mutations()
    path.write_text(json.dumps({'status':'observed'}))
    world.require_clear_mutations()


def test_stale_or_forged_selection_cannot_reach_executor(monkeypatch,tmp_path):
    from training.laya_mall_world import MallWorld
    from training.laya_saved_world import SavedWorldError
    world=object.__new__(MallWorld);world.root=tmp_path
    f=facts();world.scenario=f["scenario"];world.query=lambda:f
    c=next(c for c in compile_candidates(f) if c['family']=='wait')
    c['at_tick']-=1
    with pytest.raises(SavedWorldError,match='stale'):world.execute(c['option_id'],[c])
    c['at_tick']=f['tick'];c['wait_ticks']=1
    with pytest.raises(SavedWorldError,match='not currently registered'):world.execute(c['option_id'],[c])


def test_machine_controls_are_actor_options_and_lateness_survives_completion():
    from copy import deepcopy
    from training.laya_mall import outcome
    f=facts();f['machines']=[{'position':{'x':-7.5,'y':-4.5},'recipe':'iron-gear-wheel','status':'working','active':True}]
    controls=[c for c in compile_candidates(f) if c['family']=='toggle']
    assert len(controls)==1 and controls[0]['active'] is False
    f['machines'][0]['active']=False
    assert next(c for c in compile_candidates(f) if c['family']=='toggle')['active'] is True
    after=deepcopy(f);after['tick']+=60;after['elapsed_ticks']=20000
    after['served']={d['id']:d['count'] for d in after['scenario']['demands']}
    after['completed_at']={'circuits':12500,'belts':18100}
    after['ledger']['upgrades']=1
    measured=outcome(f,after)
    assert measured['completed'] and measured['late_ticks']==600
    state=decision_state(f,controls,'mall-control',{'model_id':'test','model_hash':'sha256:'+'0'*64})
    assert state['observation_digest']['mall']['machines'][0]['side']=='west'
    assert state['options'][0]['risk_tier']=='reversible_mutation'


def test_two_decision_cli_run_preserves_typed_history_and_presented_evidence(monkeypatch,tmp_path,capsys):
    from copy import deepcopy
    from tools import run_laya_mall
    from training import laya_runtime,laya_runner
    from training.laya_mall import outcome
    checkpoint=tmp_path/'checkpoint';checkpoint.mkdir()
    (checkpoint/'rl_agent_config.json').write_text(json.dumps({'input_profiles':['mall-world-v1']}))
    seen=[]
    class Predictor:
        def __init__(self,*a):pass
        def predict(self,state,questions):
            seen.append(deepcopy(state))
            return {'answers':{'select_candidate':{'choice':state['options'][0]['option_id'],'confidence':1}}}
    class World:
        code_hashes={'fixture':'test'}
        def __init__(self,*a):self.f=facts();self.scenario=self.f["scenario"]
        def query(self):return deepcopy(self.f)
        def reconcile(self):pass
        def close(self):pass
        def options(self,f,*a,**k):return [c for c in compile_candidates(f) if c['family']=='wait'],{}
        def execute(self,selected,presented,*a):
            before=self.query();self.f['tick']+=presented[0]['wait_ticks'];self.f['elapsed_ticks']+=presented[0]['wait_ticks']
            return {'status':'observed','measurement':outcome(before,self.query())}
    root=tmp_path/'world';root.mkdir()
    monkeypatch.setattr(run_laya_mall,'owned_root',lambda p:p)
    monkeypatch.setattr(run_laya_mall,'MallWorld',World)
    monkeypatch.setattr(laya_runtime,'LayaModelPredictor',Predictor)
    monkeypatch.setattr(laya_runner,'checkpoint_model_ref',lambda p:{'model_id':'test','model_hash':'sha256:'+'0'*64})
    assert run_laya_mall.main(['run','--root',str(root),'--checkpoint',str(checkpoint),'--decisions','2'])==0
    assert len(seen)==2 and seen[1]['recent_history'][0]['outcome']=='completed'
    records=[json.loads(line) for line in (root/'decisions.jsonl').read_text().splitlines()]
    assert len(records)==4
    for record in records:
        assert record['candidates'] and record['decision']['selected_option']['option_id']==record['selected']
        assert run_laya_mall.file_hash(root/record['observation'])==record['observation_sha256']
    capsys.readouterr()


def test_consumption_action_cannot_mutate_before_measurement_budget_check(tmp_path):
    from training.laya_mall_world import MallWorld
    from training.laya_saved_world import SavedWorldError
    f=facts();f['elapsed_ticks']=f['scenario']['max_episode_ticks']-59
    world=object.__new__(MallWorld);world.root=tmp_path;world.scenario=f['scenario'];world.query=lambda:f
    world.rpc=lambda *a,**k:pytest.fail('action mutated despite exhausted measurement budget')
    c=next(c for c in compile_candidates(f) if c['family']=='priority')
    assert not any(c['family']=='wait' for c in compile_candidates(f))
    with pytest.raises(SavedWorldError,match='remaining episode ticks'):world.execute(c['option_id'],[c])
