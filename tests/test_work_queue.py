# Path: tests/test_work_queue.py | Purpose: Protect delivered accounting, continuation, priority, and isolation.
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.work_queue import WorkQueue
from orchestrator.execution_slice import execution_slice
from training.laya_ops import DeterministicSession


def queue(tmp_path, tick=0):
    return WorkQueue(tmp_path/'queue.json', world_id='private-episode', tick=tick)


def snapshot(tick=0, stock=None, owned=None):
    return {'tick': tick, 'stock': stock or {}, 'owned': owned or {}, 'claims': {}, 'layout': [4, 1]}


def test_31_assemblers_fulfil_29_without_running_or_refilling(tmp_path):
    q = queue(tmp_path)
    job = q.enqueue('produce', {'item': 'assembling-machine-1', 'stock_target': 29}, priority=50, tick=0)
    calls = []
    q.pulse(snapshot(stock={'assembling-machine-1': 31}), lambda *a: calls.append(a), can_produce=lambda _: True)
    assert job.state == 'complete' and calls == []
    q = queue(tmp_path, tick=100)
    assert q.jobs[job.job_id].state == 'complete'
    q.pulse(snapshot(100), lambda *a: calls.append(a), can_produce=lambda _: True)
    assert calls == []  # A finite bill is not a standing refill policy.


def test_four_installed_of_six_drills_only_requests_two(tmp_path):
    q = queue(tmp_path)
    drills = q.enqueue('produce', {'item': 'electric-mining-drill', 'stock_target': 6}, priority=100, tick=0)
    calls = []
    def execute(job, params):
        calls.append(params)
        return {'ok': True, 'operation': job.operation, 'params': params, 'code': 'producing'}
    q.pulse(snapshot(owned={drills.job_id: {'delivered': 4}}), execute, can_produce=lambda _: True)
    assert calls == [{'item': 'electric-mining-drill', 'stock_target': 2}]
    assert queue(tmp_path).jobs[drills.job_id].delivered == 4


def test_blocked_job_yields_to_ready_peer_and_prerequisite(tmp_path):
    q = queue(tmp_path)
    mine = q.enqueue('foundation', {'recipe': 'iron-plate'}, priority=100, tick=0)
    cable = q.enqueue('mall_slot', {'item': 'copper-cable'}, priority=50, tick=0)
    served = []
    def execute(job, params):
        served.append(job.item)
        if job is mine:
            return {'ok': False, 'code': 'material_shortage', 'details': {'required': {'electric-mining-drill': 6}}}
        return {'ok': True, 'code': 'submitted'}
    q.pulse(snapshot(), execute, can_produce=lambda _: True)
    assert served == ['iron-plate', 'electric-mining-drill', 'copper-cable']
    assert cable.state == 'complete'
    assert q.jobs[mine.dependencies[0]].priority >= mine.priority


def test_surplus_churn_does_not_retry_unchanged_missing_provider(tmp_path):
    q = queue(tmp_path)
    job = q.enqueue('mall_slot', {'item': 'inserter'}, priority=50, tick=0)
    q.record(job, {'ok': False, 'code': 'material_shortage', 'details': {'required': {'passive-provider-chest': 1}}},
             snapshot(), can_produce=lambda _: False)
    q.reconcile(snapshot(600, {'iron-plate': 5000}))
    assert q.ready(snapshot()) == []
    q.reconcile(snapshot(601, {'passive-provider-chest': 1}))
    assert q.ready(snapshot()) == [job]


def test_completed_prerequisite_is_not_dispatched_with_existing_stock(tmp_path):
    q = queue(tmp_path)
    parent = q.enqueue('mall_slot', {'item': 'inserter'}, priority=100, tick=0)
    calls = []
    def execute(job, params):
        calls.append(job.item)
        return {'ok': False, 'code': 'material_shortage',
                'details': {'required': {'assembling-machine-1': 1, 'passive-provider-chest': 1}}}
    q.pulse(snapshot(stock={'assembling-machine-1': 31}), execute, can_produce=lambda i: i=='assembling-machine-1')
    assert calls == ['inserter'] and parent.state == 'blocked'


def test_preemption_preserves_checkpoint_claims_and_resumes(tmp_path):
    q = queue(tmp_path)
    lower = q.enqueue('produce', {'item': 'assembling-machine-1', 'stock_target': 10}, priority=20, tick=0)
    lower.state, lower.reserved, lower.checkpoint = 'running', {'iron-plate': 12}, {'credit': 3}
    urgent = q.enqueue('produce', {'item': 'electric-mining-drill', 'stock_target': 2}, priority=100, tick=0)
    def execute(job, params):
        return {'ok': True, 'code': 'producing'}
    q.pulse(snapshot(), execute, can_produce=lambda _: True, preempt=lambda a,b: a is lower and b is urgent,
            max_jobs=1)
    assert lower.state == 'paused' and lower.reserved == {'iron-plate': 12} and lower.checkpoint == {'credit': 3}
    loaded = queue(tmp_path, tick=120)
    assert loaded.jobs[lower.job_id].state == 'paused'
    loaded.jobs[urgent.job_id].state = 'complete'
    calls = []
    loaded.pulse(snapshot(120), lambda j,p: (calls.append(j.job_id) or {'ok': True}), can_produce=lambda _: True)
    assert calls == [lower.job_id]


def test_fresh_clock_resets_retry_but_keeps_credit_and_world_isolation(tmp_path):
    q = queue(tmp_path, tick=9000)
    job = q.enqueue('produce', {'item': 'inserter', 'stock_target': 6}, priority=100, tick=9000)
    job.retry_tick, job.delivered = 9120, 4
    q.save()
    loaded = queue(tmp_path, tick=0)
    assert loaded.jobs[job.job_id].retry_tick == 0 and loaded.jobs[job.job_id].delivered == 4
    import pytest
    with pytest.raises(ValueError, match='world'):
        WorkQueue(q.path, world_id='production', tick=0)


def test_reorder_inherits_priority_and_cycle_is_visible(tmp_path):
    q = queue(tmp_path)
    goal = q.enqueue('produce', {'item': 'plastic-bar', 'stock_target': 30}, priority=50, tick=0)
    precursor = q.enqueue('produce', {'item': 'pipe', 'stock_target': 20}, priority=50, tick=0, parent=goal.job_id)
    assert q.enqueue('produce', goal.params, priority=50, tick=0, parent=precursor.job_id) is None
    assert precursor.reason.startswith('dependency_cycle:')
    q.promote(goal.job_id, tick=0)
    assert precursor.priority == goal.priority > 50


def test_spares_wait_until_urgent_work_is_clear(tmp_path):
    q = queue(tmp_path)
    q.enqueue('produce', {'item': 'inserter', 'stock_target': 2}, priority=5, tick=0, kind='spare')
    q.enqueue('produce', {'item': 'electric-mining-drill', 'stock_target': 6}, priority=100, tick=0)
    calls = []
    q.pulse(snapshot(), lambda j,p: (calls.append(j.kind) or {'ok': True}), can_produce=lambda _: True)
    assert calls == ['production']


def test_loan_output_is_not_counted_twice_as_stock_and_delivery(tmp_path):
    session = object.__new__(DeterministicSession)
    session.queue = queue(tmp_path)
    job = session.queue.enqueue('produce', {'item': 'electric-mining-drill', 'stock_target': 6}, priority=100, tick=0)
    loan = {'target_item': job.item, 'step_recipe': job.item, 'crafts_done': 4,
            'machine_position': [1, 1], 'step_baseline_finished': 0}
    facts = {'tick': 0, 'machines': [], 'poles': []}
    snap = session._queue_snapshot(facts, {'material_stock': {job.item: 4}, 'loans': [loan]})
    session.queue.reconcile(snap)
    assert job.delivered == 0 and job.state != 'complete'
    snap = session._queue_snapshot(facts, {'material_stock': {job.item: 0}, 'loans': [loan]})
    session.queue.reconcile(snap)
    assert job.delivered == 4 and job.state != 'complete'


def test_cooperative_wait_surveys_once_without_sleep(monkeypatch):
    from orchestrator import stage_services as services
    client = SimpleNamespace(command=lambda _: '4')
    monkeypatch.setattr(services.time, 'sleep', lambda _: (_ for _ in ()).throw(AssertionError('slept')))
    with execution_slice():
        assert services._wait_for_ghosts(client, 'private', 'player', ((0,0),(10,10))) == 4


def test_wait_choices_keep_executor_advancing_and_record_each_lane(tmp_path):
    import json
    from training.laya_guided_policy import GuidedPolicy
    from training.laya_ops_runner import run
    from tests.test_laya_ops_runner import FakeWorld, FakeSession
    class Session(FakeSession):
        episode_id = 'private-episode'
        def enable_queue(self, **kwargs):
            self.queue = WorkQueue(tmp_path/'queue.json', tick=1000, **kwargs)
            self.queue.enqueue('produce', {'item': 'electric-mining-drill', 'stock_target': 6}, priority=100, tick=1000)
            self.queue.enqueue('mall_slot', {'item': 'copper-cable'}, priority=50, tick=1000)
        def queue_menu(self, menu):
            return {**menu, 'options': [o for o in menu['options'] if o['family']=='wait']}
        def _queue_snapshot(self, facts, economics):
            return snapshot(facts['tick'])
        def execute_queue(self, facts, economics, reached):
            return self.queue.pulse(snapshot(facts['tick']), lambda j,p: self.run(j.operation, **p),
                                    can_produce=lambda _: True)
    world, session = FakeWorld(), Session()
    summary = run(world, GuidedPolicy(3), journal=tmp_path/'journal.jsonl', decisions=3,
                  measure_ticks=600, seed=3, run_id='queue-test', session=session)
    rows = [json.loads(line) for line in (tmp_path/'journal.jsonl').read_text().splitlines()]
    assert summary['failures'] == 0
    assert session.calls[:2] == [('produce', {'item': 'electric-mining-drill', 'stock_target': 6}),
                                  ('mall_slot', {'item': 'copper-cable'})]
    assert len([r for r in rows if r['event']=='executor']) >= 2
    assert all(r['choice']['option_id'].startswith('wait.') for r in rows if r['event']=='decision')
    assert 'work_queue' in summary


def test_recipe_handoff_uses_writable_disable_flag_and_restores_it():
    import subprocess
    import pytest
    from orchestrator.stage_services import _hold_recipe_changes, StuckError
    class LuaClient:
        progress, disabled = 0.5, False
        def command(self, text):
            lua = (
                f"e={{disabled_by_script={str(self.disabled).lower()},crafting_progress={self.progress}}};"
                "setmetatable(e,{__index=function(t,k) if k=='active' then return not t.disabled_by_script end end,"
                "__newindex=function(t,k,v) if k=='active' then error('LuaEntity::active is read only') else rawset(t,k,v) end end});"
                "function e.get_recipe() return {name='inserter'} end;"
                "game={surfaces={private={find_entities_filtered=function() return {e} end}}};"
                "rcon={print=function(v) print(v) end};helpers={};"
                "helpers.json_to_table=function(v) if string.find(v,'recipe') then "
                "return {{x=1,y=1,recipe='electric-mining-drill'}} else return {{x=1,y=1,disabled=false}} end end;"
                "helpers.table_to_json=function(t) if #t==0 then return '[]' else "
                "return '[{\"x\":1,\"y\":1,\"disabled\":false}]' end end;"
                + "(function() " + text[4:] + " end)();print('DISABLED='..tostring(e.disabled_by_script))"
            )
            result = subprocess.run(['lua','-'], input=lua, text=True, capture_output=True, check=True)
            lines = result.stdout.splitlines()
            self.disabled = lines[-1] == 'DISABLED=true'
            return lines[0] if len(lines)>1 else ''
    client = LuaClient()
    plan = {'phases': [{'actions': [{'action_type': 'configure_entity', 'entity': 'assembling-machine-1',
                                     'position': {'x':1,'y':1}, 'recipe':'electric-mining-drill'}]}]}
    with execution_slice():
        with pytest.raises(StuckError, match='completed craft'):
            with _hold_recipe_changes(client, 'private', 'player', plan):
                raise AssertionError('entered before boundary')
        assert client.disabled is False
        client.progress = 0
        with _hold_recipe_changes(client, 'private', 'player', plan):
            assert client.disabled is True
        assert client.disabled is False


def install_live_recipe_fixture(monkeypatch):
    import json
    from copy import deepcopy
    from planners.recipe_data import LINE_RECIPES, install_catalog_line_recipes
    root = Path(__file__).resolve().parents[1]
    original = deepcopy(LINE_RECIPES)
    install_catalog_line_recipes(json.loads((root/'tests/fixtures/player_recipe_catalog.json').read_text()))
    installed = deepcopy(LINE_RECIPES)
    LINE_RECIPES.clear()
    LINE_RECIPES.update(original)
    for item, spec in installed.items():
        monkeypatch.setitem(LINE_RECIPES, item, spec)


@pytest.mark.parametrize('capacity', [
    {'free_slots':4},
    {'free_slots':None,'mall_capacity':{'unlimited':True,'committed_slots':8}},
])
def test_backlog_automatically_queues_a_real_capacity_operation(tmp_path, monkeypatch, capacity):
    from orchestrator import autonomous_builder as ab
    from orchestrator import live_base
    install_live_recipe_fixture(monkeypatch)
    session = object.__new__(DeterministicSession)
    session.queue = queue(tmp_path)
    session.client, session.reference_point, session.emit = object(), (0,0), lambda _:None
    parent = session.queue.enqueue('foundation', {'recipe':'iron-plate'}, priority=100, tick=0)
    parent.retry_tick = 1000
    for item in ('electric-mining-drill', 'fast-inserter'):
        session.queue.enqueue('produce', {'item':item,'stock_target':6}, priority=100, tick=0, parent=parent.job_id)
    ledger = SimpleNamespace(projects={}, dependency_budget={}, allocatable_stock=lambda s: s)
    monkeypatch.setattr(ab, '_MATERIAL_RESERVATION_LEDGER', ledger)
    monkeypatch.setattr(ab, '_refresh_logistic_chest_budget', lambda *a,**k:None)
    monkeypatch.setattr(live_base, 'find_line', lambda *a: None)
    calls = []
    session.run = lambda op, **params: (calls.append((op,params)) or
                                      {'ok':False,'code':'queued_construction_wait','params':params,'operation':op})
    session._queue_preempt = lambda *a:False
    stock={item:100 for item in ('electronic-circuit','iron-gear-wheel','iron-plate','inserter')}
    session.execute_queue({'tick':0}, {'material_stock':stock,**capacity}, reached=['C0','C1'])
    assert ('mall_capacity', {'item':'electric-mining-drill','minimum_machines':1}) in calls


def test_queue_schema_round_trip_and_lab_admission(tmp_path, monkeypatch):
    install_live_recipe_fixture(monkeypatch)
    import json
    from jsonschema import Draft7Validator
    q = queue(tmp_path)
    q.enqueue('produce', {'item':'fast-inserter','stock_target':6}, priority=80, tick=0)
    root = Path(__file__).resolve().parents[1]
    Draft7Validator(json.loads((root/'schemas/laya_work_queue.schema.json').read_text())).validate(json.loads(q.path.read_text()))
    session = object.__new__(DeterministicSession)
    session.reached_phases = ['C0','C1','C2']
    assert not session._queue_can_produce('lab')
    session.reached_phases += ['C4','C6']
    assert session._queue_can_produce('lab')


def test_new_consumer_cannot_reuse_old_delivery_credit(tmp_path):
    q = queue(tmp_path)
    mine = q.enqueue('foundation', {'recipe':'iron-plate'}, priority=100, tick=0)
    drills = q.enqueue('produce', {'item':'electric-mining-drill','stock_target':6}, priority=100, tick=0, parent=mine.job_id)
    drills.state, drills.delivered = 'complete', 6
    drills.checkpoint['lane_credits'] = {'old-lane':6}
    another = q.enqueue('foundation', {'recipe':'copper-plate'}, priority=100, tick=120)
    next_batch = q.enqueue('produce', drills.params, priority=100, tick=120, parent=another.job_id)
    assert next_batch.state == 'ready' and next_batch.delivered == 0
    assert next_batch.checkpoint['lane_floors'] == {'old-lane':6}


def test_deferred_high_priority_polls_do_not_starve_other_ready_work(tmp_path):
    q = queue(tmp_path)
    for item in ('inserter','transport-belt','electronic-circuit','electric-mining-drill'):
        j=q.enqueue('produce', {'item':item,'stock_target':6}, priority=100, tick=0)
        j.state, j.retry_tick = 'blocked', 0
        j.checkpoint['last_attempt_tick']=90
    gear=q.enqueue('mall_slot', {'item':'iron-gear-wheel'}, priority=50, tick=0)
    calls=[]
    q.pulse(snapshot(120), lambda j,p: (calls.append(j.item) or {'ok':False,'code':'production_prerequisite_deferred'}),
            can_produce=lambda _:True, max_jobs=1)
    assert calls==['iron-gear-wheel']
    gear.state='blocked'
    gear.checkpoint['last_attempt_tick']=0
    calls.clear()
    q.pulse(snapshot(240), lambda j,p: (calls.append(j.item) or {'ok':False,'code':'production_prerequisite_deferred'}),
            can_produce=lambda _:True, max_jobs=1)
    assert calls==['iron-gear-wheel']


def test_submitted_infrastructure_is_not_replanned_before_bots_finish(tmp_path):
    q=queue(tmp_path)
    job=q.enqueue('mall_slot', {'item':'iron-gear-wheel'}, priority=50,tick=0)
    q.record(job, {'ok':False,'code':'infrastructure_construction_wait',
                  'details':{'pending':[{'entity':'big-electric-pole','position':[14,2]}]}},
             snapshot(), can_produce=lambda _:True)
    calls=[]
    execute=lambda j,p:(calls.append(j.job_id) or {'ok':True,'code':'producing'})
    q.pulse(snapshot(600,owned={job.job_id:{'pending_ready':False}}),execute,can_produce=lambda _:True)
    assert calls==[]
    q.pulse(snapshot(601,owned={job.job_id:{'pending_ready':True}}),execute,can_produce=lambda _:True)
    assert calls==[job.job_id]


def test_mall_allocation_survives_power_yield_and_claims_ghosts_first(monkeypatch):
    import pytest
    from copy import deepcopy
    from orchestrator import mall_builder as mall
    from orchestrator.stage_services import StuckError
    client=SimpleNamespace(command=lambda _: '')
    checkpoint={}
    events,plans,saved=[],[],[]
    choose=[]
    def choose_slot(*a,**k):
        choose.append(True)
        return (9,0),'left'
    monkeypatch.setattr(mall,'_choose_slot',choose_slot)
    monkeypatch.setattr(mall,'_clear_stale_side_requests',lambda *a:None)
    monkeypatch.setattr(mall,'assert_mall_cell_footprint_free',lambda *a:None)
    monkeypatch.setattr(mall.live_base,'pole_network_id',lambda *a:None)
    monkeypatch.setattr(mall,'powered_reserved_substation_neighbor',lambda *a:None)
    def submit(c,b,s,plan,*a):
        events.append('mall_ghosts')
        plans.append(deepcopy(plan))
    def power(*a,**k):
        events.append('power')
        raise StuckError('pending power',code='infrastructure_construction_wait',state='constructing')
    monkeypatch.setattr(mall,'_submit',submit)
    monkeypatch.setattr(mall,'extend_power',power)
    for _ in range(2):
        with execution_slice(checkpoint=checkpoint,persist=lambda:saved.append(True)):
            with pytest.raises(StuckError,match='pending power'):
                mall.build_compact_mall_stage(client,object(),'private','player','iron-gear-wheel',{},(3,-1),
                                              lambda *a:None,lambda _:None)
    assert events==['mall_ghosts','power','mall_ghosts','power']
    assert plans[0]==plans[1] and choose==[True] and saved==[True]


def test_drain_finishes_gated_craft_without_another_input_and_survives_restart(tmp_path):
    import json, re, subprocess
    import pytest
    from orchestrator.stage_services import _hold_recipe_changes, StuckError
    class NativeLua:
        state={'progress':0.75,'finished':8,'input':7,'held':1,'chest':13,'feeding_disabled':False,
               'machine_disabled':False,'connected':True}
        def command(self,text):
            def literal(v):
                if isinstance(v,dict): return '{'+','.join('['+json.dumps(k)+']='+literal(x) for k,x in v.items())+'}'
                if isinstance(v,list): return '{'+','.join(literal(x) for x in v)+'}'
                return json.dumps(v)
            body=re.sub(r'helpers\.json_to_table\(("(?:\\.|[^"\\])*")\)',
                        lambda m:literal(json.loads(json.loads(m[1]))),text[4:])
            preamble='''
local state=STATE
local st={name='copper-plate',quality='normal',count=state.input}
local hs={name='copper-plate',quality='normal',count=state.held}
st.valid_for_read=st.count>0;hs.valid_for_read=hs.count>0
local cb={connect_to_logistic_network=state.connected}
local e={position={x=1,y=1},force='player',crafting_progress=state.progress,
 disabled_by_script=state.machine_disabled,products_finished=state.finished,
 bounding_box={left_top={x=-0.5,y=-0.5},right_bottom={x=2.5,y=2.5}}}
function e.get_recipe() return {name='copper-cable'} end
function e.get_control_behavior() return cb end
function e.get_inventory(index) assert(index==1);return {st} end
setmetatable(e,{__index=function(t,k) if k=='active' then return not t.disabled_by_script end end,
 __newindex=function(t,k,v) if k=='active' then error('active is read only') else rawset(t,k,v) end end})
local feeder={position={x=3,y=1},drop_position={x=1,y=1},pickup_position={x=4,y=1},
 disabled_by_script=state.feeding_disabled,held_stack=hs}
local dst={can_insert=function(_) return true end,insert=function(item) state.chest=state.chest+item.count;return item.count end}
local surface={find_entities_filtered=function(opts)
 if opts.type=='assembling-machine' then return {e} elseif opts.type=='inserter' then return {feeder} else return {dst} end end}
game={surfaces={private=surface}};defines={inventory={crafter_input=1}}
local function encode(v)
 if type(v)=='table' then local a={};if #v>0 then for _,x in ipairs(v) do a[#a+1]=encode(x) end;return '['..table.concat(a,',')..']' end
 for k,x in pairs(v) do a[#a+1]=string.format('%q',k)..':'..encode(x) end;return '{'..table.concat(a,',')..'}'
 elseif type(v)=='string' then return string.format('%q',v) else return tostring(v) end end
helpers={table_to_json=encode};rcon={print=function(v) print(v) end}
(function() BODY end)()
state.input=st.count;state.held=hs.count;state.feeding_disabled=feeder.disabled_by_script
state.machine_disabled=e.disabled_by_script;state.connected=cb.connect_to_logistic_network
print('STATE='..encode(state))
'''
            lua=preamble.replace('STATE',literal(self.state),1).replace('BODY',body,1)
            out=subprocess.run(['lua','-'],input=lua,text=True,capture_output=True,check=True).stdout.splitlines()
            self.state=json.loads(out[-1][6:])
            return out[0] if len(out)>1 else ''
    client=NativeLua()
    q=queue(tmp_path)
    plan={'phases':[{'actions':[{'action_type':'configure_entity','entity':'assembling-machine-1',
                                 'position':{'x':1,'y':1},'recipe':'electronic-circuit'}]}]}
    job=q.enqueue('produce',{'item':'electronic-circuit','stock_target':1},priority=100,tick=0)
    with execution_slice(checkpoint=job.checkpoint,runtime=q.runtime,persist=q.save):
        with pytest.raises(StuckError,match='completed craft'):
            with _hold_recipe_changes(client,'private','player',plan): pass
    assert client.state['input']==0 and client.state['held']==0 and client.state['chest']==21
    assert client.state['feeding_disabled'] and not client.state['connected']
    assert client.state['progress']==0.75 and client.state['finished']==8  # No manufactured progress.
    q=queue(tmp_path)
    job=next(iter(q.jobs.values()))
    assert q.runtime['craft_drains']['1.00:1.00']['feeders'][0]['disabled'] is False
    assert job.checkpoint['craft_handoffs']=={'1.00:1.00':'electronic-circuit'}
    # Engine completes the already-funded in-flight craft with no next input.
    client.state.update(progress=0,finished=9)
    with execution_slice(checkpoint=job.checkpoint,runtime=q.runtime,persist=q.save):
        from orchestrator.craft_boundary import recover_orphan_drains
        # A waiting new borrower has no loan tag yet. Recovery and ordinary
        # same-recipe maintenance must both leave its completed boundary held.
        recover_orphan_drains(client,'private','player',[],
                              pending_handoffs=job.checkpoint['craft_handoffs'])
        maintenance={'phases':[{'actions':[{**plan['phases'][0]['actions'][0], 'recipe':'copper-cable'}]}]}
        with _hold_recipe_changes(client,'private','player',maintenance): pass
        assert client.state['feeding_disabled'] and not client.state['connected']
        assert '1.00:1.00' in q.runtime['craft_drains']
        with _hold_recipe_changes(client,'private','player',plan):
            assert client.state['machine_disabled']
    assert not client.state['machine_disabled'] and not client.state['feeding_disabled']
    assert client.state['connected'] is True and q.runtime['craft_drains']=={}
    assert job.checkpoint['craft_handoffs']=={}


def test_released_borrower_cannot_leave_precursor_feeders_parked(tmp_path, monkeypatch):
    from orchestrator import craft_boundary as boundary
    q=queue(tmp_path)
    q.runtime={'craft_drains':{'1.00:1.00':{'x':1,'y':1,'recipe':'electronic-circuit'},
                               '2.00:2.00':{'x':2,'y':2,'recipe':'transport-belt'}}}
    calls=[]
    monkeypatch.setattr(boundary,'release_drains',lambda c,s,f,actors:calls.extend(actors))
    active=[SimpleNamespace(machine_position=(2,2),current_recipe='transport-belt')]
    with execution_slice(runtime=q.runtime,persist=q.save):
        boundary.recover_orphan_drains(object(),'private','player',active)
    assert calls==[{'x':1,'y':1,'recipe':'electronic-circuit'}]


def test_retry_prices_installed_cell_and_keeps_its_allocation(monkeypatch):
    from orchestrator import mall_builder as mall
    install_live_recipe_fixture(monkeypatch)
    origin = (32,32)
    state = {'mall_allocation:private:player:electronic-circuit': [list(origin),'right']}
    spec = mall.LINE_RECIPES['electronic-circuit']
    plan = mall.generate_paired_mall_layout('electronic-circuit','assembling-machine-1',
        spec['ingredients'],spec['amounts'],origin,'right',craft_time=spec['craft_time'],
        include_substation=False)
    records = [{'name':a['entity'],'position':tuple(a['position'].values()),'force':'player',
                'type':'assembling-machine' if a['entity'].startswith('assembling-machine') else 'container',
                'is_ghost':False,'deconstructed':False}
               for p in plan['phases'] for a in p['actions']]
    monkeypatch.setattr(mall.live_base,'area_entity_records',lambda *a:records)
    monkeypatch.setattr(mall,'_choose_slot',lambda *a,**k: (_ for _ in ()).throw(AssertionError('abandoned allocation')))
    with execution_slice(checkpoint=state):
        assert mall.preview_mall_allocation(object(),'private','electronic-circuit',(0,0)) == (origin,'right')
        bill = mall.compact_mall_project_bill('electronic-circuit',machine_name='assembling-machine-1',
            side='right',include_substation=False,client=object(),surface='private',allocation=(origin,'right'))
    assert bill == {'copper-cable':3,'iron-plate':1}
    # Unbuilt or foreign entities cannot pay the construction bill.
    records[0]['is_ghost'] = True
    bill = mall.compact_mall_project_bill('electronic-circuit',machine_name='assembling-machine-1',
        side='right',include_substation=False,client=object(),surface='private',allocation=(origin,'right'))
    assert bill['assembling-machine-1'] == 1


def test_committed_cell_continues_at_cap_without_borrowing(monkeypatch):
    from orchestrator import autonomous_builder as ab
    install_live_recipe_fixture(monkeypatch)
    state={'mall_allocation:private:player:transport-belt': [[32,32],'right']}
    monkeypatch.setattr(ab,'_independent_mall_ready',lambda *a:False)
    monkeypatch.setattr(ab,'_pipe_is_temporary_batch',lambda *a:False)
    monkeypatch.setattr(ab,'_recipe_depends_on_fast_transport_belt',lambda *a:False)
    monkeypatch.setattr(ab,'_transferable_or_available_stock',lambda *a:{})
    monkeypatch.setattr(ab,'_rationed_mall_completion_target',lambda *a:10)
    monkeypatch.setattr(ab,'_unfunded_ladder_ingredient',lambda *a:None)
    monkeypatch.setattr(ab,'active_bootstrap_loans',lambda *a:[])
    monkeypatch.setattr(ab,'_start_bootstrap_loan',lambda *a,**k: (_ for _ in ()).throw(AssertionError('borrowed')))
    monkeypatch.setattr(ab,'mall_slot_count',lambda *a:8)
    monkeypatch.setattr(ab,'_bootstrap_mall_slot_limit',lambda *a:8)
    monkeypatch.setattr(ab.live_base,'entity_at',lambda *a:{'name':'assembling-machine-1','force':'player'})
    monkeypatch.setattr(ab,'_ingredient_sources',lambda *a,**k:None)
    plan=SimpleNamespace(spec={'machine':'assembling-machine-1'},promote_to_line=False,existing=None,at_size=False)
    with execution_slice(checkpoint=state):
        assert not ab._rationed_mall_batch(object(),object(),'private','player','transport-belt',10,(0,0),lambda _:None)
        ab._build_assembled_stage(object(),object(),'private','player','transport-belt',(0,0),lambda _:None,
                                  plan,None,upgrade_bootstrap=False)


def test_unbuilt_preview_cannot_block_a_funded_batch_behind_chest_supply(monkeypatch):
    from orchestrator import autonomous_builder as ab
    install_live_recipe_fixture(monkeypatch)
    state={'mall_allocation:private:player:transport-belt': [[32,32],'right']}
    monkeypatch.setattr(ab,'_independent_mall_ready',lambda *a:False)
    monkeypatch.setattr(ab,'_recipe_depends_on_fast_transport_belt',lambda *a:False)
    monkeypatch.setattr(ab,'_transferable_or_available_stock',lambda *a:{'iron-plate':100})
    monkeypatch.setattr(ab,'_rationed_mall_completion_target',lambda *a:10)
    monkeypatch.setattr(ab,'_unfunded_ladder_ingredient',lambda *a:None)
    monkeypatch.setattr(ab,'active_bootstrap_loans',lambda *a:[])
    monkeypatch.setattr(ab.live_base,'entity_at',lambda *a:None)
    monkeypatch.setattr(ab,'_bootstrap_loan_stock',lambda *a:({},{}))
    monkeypatch.setattr(ab,'bootstrap_external_shortages',lambda *a:[])
    monkeypatch.setattr(ab.live_base,'find_line',lambda *a,**k:None)
    monkeypatch.setattr(ab,'_bootstrap_demand_cell_affordable',lambda *a:(False,{'passive-provider-chest':1}))
    monkeypatch.setattr(ab,'_rationed_mall_spare_target',lambda *a:10)
    calls=[]
    monkeypatch.setattr(ab,'_start_bootstrap_loan',lambda *a,**k:calls.append((a[4],a[5])) or 'batch started')
    with execution_slice(checkpoint=state):
        assert ab._rationed_mall_batch(object(),object(),'private','player','transport-belt',10,(0,0),lambda _:None)
    assert calls==[('transport-belt',10)]
    assert state['mall_allocation:private:player:transport-belt']==[[32,32],'right']


def test_queue_can_make_refinery_equipment_from_stock_without_restarting_idle_steel(monkeypatch):
    from orchestrator import autonomous_builder as ab
    install_live_recipe_fixture(monkeypatch)
    monkeypatch.setattr(ab,'_chemical_capability_started',lambda c,s,f,item:item!='steel-plate')
    established=[]
    monkeypatch.setattr(ab,'ensure_produced',lambda *a,**k:established.append(a[4]))
    with execution_slice():
        assert ab._missing_chemical_ladder_predecessor(object(),'private','player','oil-refinery') is None
        ab._ensure_chemical_ladder_predecessor(object(),object(),'private','player','oil-refinery',(0,0),lambda _:None)
    assert established==[]
    with pytest.raises(ab.ProductionPrerequisiteDeferred):
        ab._ensure_chemical_ladder_predecessor(object(),object(),'private','player','oil-refinery',(0,0),lambda _:None)
    assert established==['steel-plate']


def test_idle_lanes_automatically_take_ready_items_and_missing_precursors(tmp_path, monkeypatch):
    from orchestrator import autonomous_builder as ab, mall_builder as mall
    from planners.recipe_data import MACHINE_CAPABILITIES
    monkeypatch.setitem(MACHINE_CAPABILITIES, 'assembling-machine-1',
                        {'ingredient_count':6,'categories':('crafting','pressing','electronics')})
    install_live_recipe_fixture(monkeypatch)
    s=object.__new__(DeterministicSession)
    s.queue=queue(tmp_path)
    s.client=SimpleNamespace(command=lambda _: '')
    s.reference_point,s.emit,s.reached_phases=(0,0),lambda _:None,['C0','C1']
    first=s.queue.enqueue('produce',{'item':'transport-belt','stock_target':12},priority=80,tick=0)
    second=s.queue.enqueue('produce',{'item':'electric-mining-drill','stock_target':6},priority=70,tick=0)
    idle=[((32,32),'right','assembling-machine-1'),((32,44),'left','assembling-machine-1')]
    monkeypatch.setattr(mall,'idle_mall_allocations',lambda *a: list(idle))
    monkeypatch.setattr(ab.live_base,'find_line',lambda *a:None)
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',SimpleNamespace(allocatable_stock=lambda s:dict(s)))
    s._queue_assign_idle_malls({'tick':100},{'material_stock':{'iron-plate':100,'iron-gear-wheel':50,'copper-cable':20}})
    assert first.checkpoint['mall_allocation:nauvis:player:transport-belt']==[[32,32],'right']
    circuits=next(j for j in s.queue.jobs.values() if j.item=='electronic-circuit')
    assert circuits.priority > second.priority
    assert circuits.checkpoint['mall_allocation:nauvis:player:electronic-circuit']==[[32,44],'left']
    assert 'mall_allocation:nauvis:player:electric-mining-drill' not in second.checkpoint
    # Existing ownership survives the next pass; a stranger can't seize it.
    circuits.params['stock_target'] = 40
    stranger=s.queue.enqueue('produce',{'item':'inserter','stock_target':5},priority=100,tick=100)
    s._queue_assign_idle_malls({'tick':120},{'material_stock':{'iron-plate':100,'iron-gear-wheel':50,'electronic-circuit':20}})
    assert not any(k.startswith('mall_allocation:') for k in stranger.checkpoint)


def test_mall_inputs_cannot_hoard_inserters_before_its_transfer_arms_are_built(monkeypatch):
    from copy import deepcopy
    from orchestrator import mall_builder as mall
    from orchestrator.stage_services import StuckError
    install_live_recipe_fixture(monkeypatch)
    monkeypatch.setattr(mall,'_choose_slot',lambda *a,**k:((32,32),'right'))
    monkeypatch.setattr(mall,'assert_mall_cell_footprint_free',lambda *a:None)
    monkeypatch.setattr(mall,'_clear_stale_side_requests',lambda *a:None)
    plans=[]
    monkeypatch.setattr(mall,'_submit',lambda c,b,s,p,*a:plans.append(deepcopy(p)))
    constructed=False
    def bring_up(*a,**k):
        if not constructed:
            raise StuckError('two regular arms await bots',code='infrastructure_construction_wait',state='constructing')
    state={}
    with execution_slice(checkpoint=state):
        with pytest.raises(StuckError,match='regular arms'):
            mall.build_compact_mall_stage(object(),object(),'private','player','long-handed-inserter',{},(0,0),
                                         bring_up,lambda _:None)
    assert not any(a.get('logistic_sections') for p in plans for phase in p['phases'] for a in phase['actions'])
    constructed=True
    with execution_slice(checkpoint=state):
        mall.build_compact_mall_stage(object(),object(),'private','player','long-handed-inserter',{},(0,0),
                                     bring_up,lambda _:None)
    configured=[a for p in plans for phase in p['phases'] for a in phase['actions'] if a.get('logistic_sections')]
    assert len(configured)==1
    assert configured[0]['action_type']=='configure_entity'
    assert any(r['name']=='inserter' for r in configured[0]['logistic_sections'][0]['requests'])


def test_new_remaining_mine_shortage_does_not_reuse_its_completed_drill_bill(tmp_path):
    q=queue(tmp_path)
    mine=q.enqueue('foundation',{'recipe':'copper-plate'},priority=100,tick=0)
    drills=q.enqueue('produce',{'item':'electric-mining-drill','stock_target':1},priority=101,tick=0,parent=mine.job_id)
    drills.state,drills.delivered='complete',2
    q.record(mine,{'ok':False,'code':'material_shortage','details':{'required':{'electric-mining-drill':1}}},
             snapshot(),can_produce=lambda _:True)
    assert drills.state=='ready' and drills.delivered==2 and drills.target==3
    calls=[]
    q.pulse(snapshot(),lambda j,p:(calls.append(p) or {'ok':True,'code':'producing'}),can_produce=lambda _:True)
    assert calls==[{'item':'electric-mining-drill','stock_target':1}]


def test_installed_dependency_wakes_consumer_even_when_transferable_stock_stays_zero(tmp_path):
    q=queue(tmp_path)
    mine=q.enqueue('foundation',{'recipe':'iron-plate'},priority=100,tick=0)
    q.record(mine,{'ok':False,'code':'material_shortage','details':{'required':{'electric-mining-drill':6}}},
             snapshot(),can_produce=lambda _:True)
    drills=q.jobs[mine.dependencies[0]]
    q.reconcile(snapshot())
    installed=snapshot(120,owned={drills.job_id:{'delivered':6}})
    q.reconcile(installed)
    q.reconcile(installed)
    assert mine in q.ready(installed)
    assert drills.state=='complete'
    assert q.ready(installed)==queue(tmp_path,tick=120).ready(installed)


def test_toolkit_numeric_required_becomes_exact_precursor_batch(tmp_path):
    q=queue(tmp_path)
    urgent=q.enqueue('mall_slot',{'item':'fast-inserter'},priority=100,tick=0)
    q.record(urgent,{'ok':False,'code':'rotating_mall_prerequisite_handoff',
                    'details':{'prerequisite':'electronic-circuit','required':2}},snapshot(),can_produce=lambda _:True)
    child=q.jobs[urgent.dependencies[0]]
    assert child.target==2 and child.priority > urgent.priority


def test_existing_recipe_does_not_prevent_parallel_assignment_to_a_built_idle_lane(tmp_path,monkeypatch):
    from orchestrator import autonomous_builder as ab,mall_builder as mall
    from planners.recipe_data import MACHINE_CAPABILITIES
    install_live_recipe_fixture(monkeypatch)
    monkeypatch.setitem(MACHINE_CAPABILITIES,'assembling-machine-1',{'ingredient_count':6,'categories':('electronics',)})
    s=object.__new__(DeterministicSession)
    s.queue,s.client,s.reference_point,s.emit,s.reached_phases=queue(tmp_path),SimpleNamespace(command=lambda _: ''),(0,0),lambda _:None,['C0','C1']
    production=s.queue.enqueue('produce',{'item':'electronic-circuit','stock_target':18},priority=100,tick=0)
    monkeypatch.setattr(mall,'idle_mall_allocations',lambda *a:[((59,42),'south','assembling-machine-1')])
    monkeypatch.setattr(ab.live_base,'find_line',lambda *a:SimpleNamespace(machine_count=1))
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',SimpleNamespace(allocatable_stock=lambda stock:dict(stock)))
    s._queue_assign_idle_malls({'tick':120},{'material_stock':{'iron-plate':100,'copper-cable':60}})
    capacity=next(j for j in s.queue.jobs.values() if j.operation=='mall_capacity')
    assert capacity.params=={'item':'electronic-circuit','minimum_machines':2}
    assert capacity.checkpoint['mall_allocation:nauvis:player:electronic-circuit']==[[59,42],'south']
    assert capacity.priority > production.priority


def test_restart_restores_verified_plate_stages_without_replaying_old_decisions(tmp_path):
    from training.laya_ops_runner import compile_options
    q=queue(tmp_path)
    for plate in ('iron-plate','copper-plate'):
        j=q.enqueue('starter',{'recipe':plate},priority=100,tick=0)
        q.record(j,{'ok':True,'operation':'starter','params':j.params,'code':'producing'},snapshot(),can_produce=lambda _:True)
    unfinished=q.enqueue('foundation',{'recipe':'iron-plate'},priority=100,tick=0)
    unfinished.checkpoint['receipt']={'ok':True,'operation':'foundation','params':unfinished.params}
    q.save()
    restored=queue(tmp_path,tick=100)
    receipts=restored.planning_receipts()
    assert len(receipts)==2
    menu=compile_options({'needs':[{'item':'iron-plate','condition':'satisfied'}]},receipts,
                         queued_targets={'iron-plate':26},material_stock={'iron-plate':2},continuous_queue=True)
    ids={o['option_id'] for o in menu['options']}
    assert 'op.foundation.iron-plate' in ids
    assert 'op.starter.iron-plate' not in ids and 'op.extend.iron-plate' not in ids


def test_live_raw_input_bills_reach_options_without_resurrecting_finished_kit(tmp_path):
    q=queue(tmp_path)
    slot=q.enqueue('mall_slot',{'item':'splitter'},priority=100,tick=0)
    q.record(slot,{'ok':False,'code':'material_shortage','details':{'required':{'iron-plate':26}}},snapshot(),can_produce=lambda _:False)
    assert q.supply_targets({'transport-belt':20})=={'transport-belt':20,'iron-plate':26}
    q.record(slot,{'ok':True,'operation':'mall_slot','params':slot.params,'code':'producing'},snapshot(),can_produce=lambda _:False)
    assert q.supply_targets({'transport-belt':20})=={'transport-belt':20}


def test_funded_owned_cell_runs_with_seven_supports_even_while_aggregate_goal_is_eight(tmp_path):
    q=queue(tmp_path)
    cell=q.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    q.record(cell,{'ok':False,'code':'material_shortage','details':{'required':{'long-handed-inserter':8}}},
             snapshot(),can_produce=lambda _:True)
    supply=q.jobs[cell.dependencies[0]]
    funding=snapshot(120,stock={'long-handed-inserter':7},
                     owned={cell.job_id:{'funded':True,'reserved':{'long-handed-inserter':2}}})
    q.reconcile(funding)
    assert supply.state!='complete' and supply.target==8
    assert cell in q.ready(funding) and cell.reserved=={'long-handed-inserter':2}
    calls=[]
    def execute(job,params):
        calls.append(job.operation)
        return {'ok':True,'operation':job.operation,'params':params,'code':'producing'}
    q.pulse(funding,execute,can_produce=lambda _:True)
    assert 'mall_slot' in calls


def test_funding_must_belong_to_the_job_and_does_not_reopen_geometry_failure(tmp_path):
    q=queue(tmp_path)
    j=q.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    q.record(j,{'ok':False,'code':'material_shortage','details':{'required':{'long-handed-inserter':8}}},
             snapshot(),can_produce=lambda _:True)
    foreign=snapshot(120,stock={'long-handed-inserter':7},owned={'foreign':{'funded':True}})
    q.reconcile(foreign)
    assert j not in q.ready(foreign)
    j.reason='geometry:mall_slot_unbuildable'
    j.blocked_signature=q.signature(j,foreign)
    owned=snapshot(120,stock={'long-handed-inserter':7},owned={j.job_id:{'funded':True}})
    q.reconcile(owned)
    assert j not in q.ready(owned)
    j.reason='material_shortage'
    j.checkpoint['pending_entities']=[{'entity':'inserter','position':[1,2]}]
    q.reconcile(owned)
    assert j not in q.ready(owned)


def test_snapshot_uses_exact_ready_reservation_owner_for_dispatch(tmp_path):
    s=object.__new__(DeterministicSession)
    s.queue=queue(tmp_path)
    j=s.queue.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    j.checkpoint['receipt']={'details':{'stage':'paired_mall_inserter'}}
    data={'material_stock':{'long-handed-inserter':7},
          'projects':{'paired_mall_inserter':{'state':'ready','reserved':{'long-handed-inserter':2}}}}
    proof=s._queue_snapshot({'tick':120},data)['owned'][j.job_id]
    assert proof['funded'] is True and proof['reserved']=={'long-handed-inserter':2}


def test_legacy_vertical_receipt_uses_its_canonical_kit_not_a_foreign_ready_owner(tmp_path):
    s=object.__new__(DeterministicSession)
    s.queue=queue(tmp_path)
    j=s.queue.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    j.checkpoint['receipt']={'details':{'stage':'seed_vertical_mall_inserter_north'}}
    data={'projects':{'seed_vertical_mall_inserter_north':{'state':'supply_wait','reserved':{}},
                      'paired_mall_inserter':{'state':'ready','reserved':{'long-handed-inserter':2}}}}
    assert s._queue_snapshot({'tick':0},data)['owned'][j.job_id]['funded'] is True
    data['projects'].pop('paired_mall_inserter')
    data['projects']['paired_mall_splitter']={'state':'ready','reserved':{'long-handed-inserter':2}}
    assert s._queue_snapshot({'tick':0},data)['owned'][j.job_id]['funded'] is False


def test_conflicting_saved_previews_replan_only_unbuilt_later_claim(tmp_path, monkeypatch):
    from orchestrator import mall_builder as mall
    from training.laya_ops import ab
    s=object.__new__(DeterministicSession)
    s.queue=queue(tmp_path);s.reference_point=(0,0);s.client=SimpleNamespace(command=lambda _:None)
    first=s.queue.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    later=s.queue.enqueue('mall_slot',{'item':'splitter'},priority=100,tick=0)
    first.checkpoint['mall_allocation:nauvis:player:inserter']=[[35,31],'north']
    later.checkpoint['mall_allocation:nauvis:player:splitter']=[[35,31],'north']
    monkeypatch.setattr(mall,'idle_mall_allocations',lambda *a,**kw:[])
    monkeypatch.setattr(mall,'mall_allocation_is_committed',lambda *a:True)
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',SimpleNamespace(allocatable_stock=lambda stock:stock))
    s._queue_assign_idle_malls({'tick':0},{'material_stock':{}})
    assert 'mall_allocation:nauvis:player:splitter' in later.checkpoint
    monkeypatch.setattr(mall,'mall_allocation_is_committed',lambda *a:False)
    s._queue_assign_idle_malls({'tick':0},{'material_stock':{}})
    assert 'mall_allocation:nauvis:player:inserter' in first.checkpoint
    assert 'mall_allocation:nauvis:player:splitter' not in later.checkpoint
    assert s.queue.runtime['mall_allocations']['nauvis:player:35:31:north']=='inserter'


def test_completed_owned_construction_resumes_without_refilling_consumed_kits(tmp_path):
    q=queue(tmp_path)
    cell=q.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    q.record(cell,{'ok':False,'code':'material_shortage','details':{'required':{'long-handed-inserter':8}}},
             snapshot(),can_produce=lambda _:True)
    supply=q.jobs[cell.dependencies[0]]
    q.record(cell,{'ok':False,'code':'infrastructure_construction_wait',
                  'details':{'owner':'paired_mall_inserter','pending':[{'entity':'long-handed-inserter','position':[1,2]}]}},
             snapshot(),can_produce=lambda _:True)
    pending=snapshot(120,owned={cell.job_id:{'pending_ready':False}})
    q.reconcile(pending)
    assert cell not in q.ready(pending)
    built=snapshot(240,owned={cell.job_id:{'pending_ready':True}})
    q.reconcile(built)
    assert supply.state!='complete' and supply.target==8
    assert cell in q.ready(built) and cell.checkpoint['resume_construction'] is True
    restored=queue(tmp_path,tick=240)
    assert restored.jobs[cell.job_id] in restored.ready(snapshot(240))
    q.record(cell,{'ok':False,'code':'material_shortage','details':{'required':{'electronic-circuit':1}}},
             snapshot(240),can_produce=lambda _:True)
    assert cell not in q.ready(snapshot(240))  # Resumption still honors a new measured shortage.


def test_resume_recovers_completed_pending_entities_from_own_receipt(tmp_path, monkeypatch):
    from training.laya_ops import live_base
    s=object.__new__(DeterministicSession)
    s.client=object();s.queue=queue(tmp_path)
    cell=s.queue.enqueue('mall_slot',{'item':'inserter'},priority=100,tick=0)
    cell.checkpoint['receipt']={'code':'infrastructure_construction_wait',
                              'details':{'owner':'paired_mall_inserter',
                                         'pending':[{'entity':'long-handed-inserter','position':[1,2]}]}}
    monkeypatch.setattr(live_base,'entity_names_at',lambda *a:{(1,2):'long-handed-inserter'})
    proof=s._queue_snapshot({'tick':240},{'projects':{'paired_mall_inserter':{'state':'constructing','reserved':{}}}})
    assert proof['owned'][cell.job_id]['pending_ready'] is True
    assert proof['owned'][cell.job_id]['funded'] is False
    s.queue.reconcile(proof)
    assert cell.checkpoint['resume_construction'] is True


def test_configured_producer_dispatches_despite_obsolete_cell_kit_dependencies(tmp_path):
    q=queue(tmp_path)
    job=q.enqueue('produce',{'item':'inserter','stock_target':20},priority=100,tick=0)
    q.record(job,{'ok':False,'code':'material_shortage','details':{'required':{'long-handed-inserter':8}}},
             snapshot(),can_produce=lambda _:True)
    supply=q.jobs[job.dependencies[0]]
    observed=snapshot(120,owned={job.job_id:{'producer_present':True}})
    q.reconcile(observed)
    assert supply.state!='complete' and job in q.ready(observed)
    job.reason='dependency_cycle:old_cell_kit'
    assert job in q.ready(observed)
    # Unknown or an unconfigured machine cannot remove the kit dependency.
    assert job not in q.ready(snapshot(120))


def test_only_configured_physical_assemblers_prove_producer_presence(tmp_path):
    s=object.__new__(DeterministicSession);s.queue=queue(tmp_path)
    job=s.queue.enqueue('produce',{'item':'inserter','stock_target':20},priority=100,tick=0)
    for machine in ({'name':'assembling-machine-1'}, {'name':'entity-ghost','recipe':'inserter'},
                    {'name':'assembling-machine-1','recipe':'splitter'}):
        assert s._queue_snapshot({'tick':0,'machines':[machine]}, {})['owned'][job.job_id]['producer_present'] is False
    assert s._queue_snapshot({'tick':0,'machines':[{'name':'assembling-machine-1','recipe':'inserter'}]}, {})['owned'][job.job_id]['producer_present'] is True


def test_rejected_bootstrap_cycle_marker_yields_after_real_dependencies_finish(tmp_path):
    q=queue(tmp_path)
    job=q.enqueue('produce',{'item':'assembling-machine-1','stock_target':6},priority=100,tick=0)
    support=q.enqueue('produce',{'item':'long-handed-inserter','stock_target':12},priority=101,tick=0,parent=job.job_id)
    job.reason='dependency_cycle:'+job.job_id
    assert job not in q.ready(snapshot())
    support.state='complete'
    assert job in q.ready(snapshot())  # Revalidates/borrows a seed; no material is granted.
    q.record(job,{'ok':False,'code':'material_shortage','details':{'required':{'assembling-machine-1':1}}},
             snapshot(),can_produce=lambda _:True)
    assert job not in q.ready(snapshot())  # An unchanged self-seed shortage still waits.


def test_exact_funded_seed_breaks_cycle_but_an_unfunded_empty_dependency_set_does_not(tmp_path):
    q=queue(tmp_path)
    job=q.enqueue('produce',{'item':'assembling-machine-1','stock_target':6},priority=100,tick=0)
    job.reason='dependency_cycle:'+job.job_id
    assert job not in q.ready(snapshot())
    assert job in q.ready(snapshot(owned={job.job_id:{'funded':True}}))


def test_world_clock_rollback_discards_old_chemical_admission(tmp_path):
    q=queue(tmp_path,tick=100)
    q.runtime.update(production_baseline={'plastic-bar':4000},product_admission=['C4','C6'])
    q.save()
    restored=queue(tmp_path,tick=0)
    assert 'production_baseline' not in restored.runtime and 'product_admission' not in restored.runtime


def test_funded_batch_runs_while_new_cell_waits_for_chests_and_preserves_claims(tmp_path,monkeypatch):
    from orchestrator import autonomous_builder as ab
    from orchestrator.material_reservations import MaterialReservationLedger
    install_live_recipe_fixture(monkeypatch)
    s=object.__new__(DeterministicSession);s.queue=queue(tmp_path)
    s.client=object();s.reached_phases=()
    item='iron-stick';spec=ab.LINE_RECIPES[item]
    ledger=MaterialReservationLedger(tmp_path/'reservations',episode_id='batch',surface='nauvis',force='player')
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',ledger)
    iron=spec['ingredients'][0]
    stock={iron:20}
    ledger.declare('other-project',{iron:19},stock,hold_until_producing=True)
    ledger.mark_constructing('other-project',stock)
    batch=s.queue.enqueue('produce',{'item':item,'stock_target':4},priority=100,tick=0)
    s.queue.record(batch,{'ok':False,'code':'material_shortage','details':{'required':{'passive-provider-chest':1}}},
                   snapshot(),can_produce=lambda _:True)
    chests=s.queue.jobs[batch.dependencies[0]]
    s.queue.record(chests,{'ok':False,'code':'material_shortage','details':{'required':{'advanced-circuit':1}}},
                   snapshot(),can_produce=lambda _:False)
    facts={'tick':0,'machines':[]}
    unfunded=s._queue_snapshot(facts,{'material_stock':stock})
    s.queue.reconcile(unfunded)
    assert not unfunded['owned'][batch.job_id]['batch_funded'] and batch not in s.queue.ready(unfunded)
    # Surplus funds the actual two crafts while the unrelated project keeps19.
    stock[iron]=21
    funded=s._queue_snapshot(facts,{'material_stock':stock})
    s.queue.reconcile(funded)
    assert funded['owned'][batch.job_id]['batch_funded'] and batch in s.queue.ready(funded)
    assert chests.state!='complete' and ledger.projects['other-project'].reserved[iron]==19
    calls=[]
    s.queue.pulse(funded,lambda job,params:calls.append(job.item) or {'ok':True},
                  can_produce=lambda _:True,max_jobs=1)
    assert calls==[item]  # No new chest or assembler is prerequisite to rotation.


def test_batch_precursor_bill_scales_with_remaining_output_and_credits(tmp_path,monkeypatch):
    from orchestrator import autonomous_builder as ab
    install_live_recipe_fixture(monkeypatch)
    s=object.__new__(DeterministicSession);s.queue=queue(tmp_path);s.reached_phases=()
    s.queue.tick=10
    pole=s.queue.enqueue('produce',{'item':'medium-electric-pole','stock_target':12},priority=100,tick=10)
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',SimpleNamespace(allocatable_stock=lambda stock:stock))
    facts={'tick':10,'machines':[]}
    econ={'material_stock':{'medium-electric-pole':9,'iron-stick':6,'steel-plate':100,'copper-cable':100}}
    s._queue_supply_batch_inputs(facts,econ)
    sticks=s.queue.jobs[s.queue.identity('produce',{'item':'iron-stick'})]
    assert sticks.target==12 and sticks.priority>pole.priority and sticks.parents==[pole.job_id]
    # Existing production already owns its inputs; forecasting must not reclaim
    # another requester's WIP or add a fresh supply demand while it crafts.
    facts['machines']=[{'name':'assembling-machine-1','recipe':'medium-electric-pole'}]
    s.queue.jobs.pop(sticks.job_id);pole.dependencies=[]
    s._queue_supply_batch_inputs(facts,econ)
    assert len(s.queue.jobs)==1
    facts['machines']=[];pole.delivered=2
    s._queue_supply_batch_inputs(facts,econ)
    assert len(s.queue.jobs)==1  # Only one remaining pole: six sticks already fund it.


def test_batch_readiness_can_use_its_own_reservation_and_forecasts_foreign_hold(tmp_path,monkeypatch):
    from orchestrator import autonomous_builder as ab
    from orchestrator.material_reservations import MaterialReservationLedger
    install_live_recipe_fixture(monkeypatch)
    s=object.__new__(DeterministicSession);s.queue=queue(tmp_path);s.reached_phases=()
    s.client=object()
    ledger=MaterialReservationLedger(tmp_path/'reservations',episode_id='claims',surface='nauvis',force='player')
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',ledger)
    stock={'medium-electric-pole':9,'iron-stick':12,'steel-plate':100,'copper-cable':100}
    ledger.declare('paired_mall_medium-electric-pole',{'iron-stick':4},stock,hold_until_producing=True)
    ledger.mark_constructing('paired_mall_medium-electric-pole',stock)
    pole=s.queue.enqueue('produce',{'item':'medium-electric-pole','stock_target':12},priority=100,tick=0)
    facts={'tick':0,'machines':[]};econ={'material_stock':stock}
    assert s._queue_snapshot(facts,econ)['owned'][pole.job_id]['batch_funded']
    s._queue_supply_batch_inputs(facts,econ)
    assert len(s.queue.jobs)==1  # Its own four sticks must not be made again.
    ledger.declare('foreign-project',{'iron-stick':4},stock,hold_until_producing=True)
    ledger.mark_constructing('foreign-project',stock)
    assert not s._queue_snapshot(facts,econ)['owned'][pole.job_id]['batch_funded']
    s._queue_supply_batch_inputs(facts,econ)
    sticks=s.queue.jobs[s.queue.identity('produce',{'item':'iron-stick'})]
    assert sticks.target==16 and ledger.projects['foreign-project'].reserved['iron-stick']==4
