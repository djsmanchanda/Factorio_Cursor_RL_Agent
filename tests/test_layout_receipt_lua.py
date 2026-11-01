# Path: tests/test_layout_receipt_lua.py | Purpose: Exercise correlated layout receipt replay without a game server.
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LUA = shutil.which("lua")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")


def test_exact_duplicate_returns_cached_result_and_conflict_never_reexecutes():
    script = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; accessed=0; writes=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function()
          accessed=accessed+1; error('deliberate-world-failure') end}
        game={tick=55,print=function(value) last_print=value end}; defines={direction={}}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(value) report=value; return '{}' end,
          write_file=function(path) writes=writes+1; written_path=path end}
    ''' + (ROOT / "factorio_mod/layout_executor.lua").read_text() + '''
        id=string.rep('a',32); digest=string.rep('b',64)
        payload={request_id=id,request_digest=digest,authorization={approved_actions={'project_more_ghosts'}},
          build_plan={phases={{name='test',actions={{action_type='place_ghost',entity='lab',position={x=1,y=2}}}}}}}
        execute({parameter='exact-serialized-request'})
        assert(accessed==1 and writes==1 and report.ok==false)
        assert(report.request_id==id and report.request_digest==digest and report.tick==55)
        assert(report.receipt_version=='layout-submission-receipt-v1')
        assert(written_path=='factorio_mod/layout_reports/layout_'..id..'.json')
        game.tick=56; execute({parameter='exact-serialized-request'})
        assert(accessed==1 and writes==2 and report.tick==55)
        execute({parameter='different-serialized-request'})
        assert(accessed==1 and writes==2 and string.find(last_print, 'conflicts', 1, true))
        storage.layout_submission_receipts.entries[id].status='pending'
        execute({parameter='exact-serialized-request'})
        assert(accessed==1 and writes==2 and string.find(last_print, 'outcome unknown', 1, true))
    '''
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_duplicate_success_does_not_create_the_ghost_twice():
    script = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; created=0; writes=0
        local surface={name='training/test',find_entities_filtered=function() return {} end,
          create_entity=function() created=created+1; return {valid=true} end}
        local force={name='training-test'}
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() return surface end,
          get_or_create_planner_force=function() return force end,find_exact_entity=function() return nil end}
        game={tick=1,print=function(value) last_print=value end}; prototypes={entity={}}; defines={direction={}}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(value) report=value; return '{"placement_failures":{}}' end,
          write_file=function(path,text) writes=writes+1; written=text end}
    ''' + (ROOT / "factorio_mod/layout_executor.lua").read_text() + '''
        payload={request_id=string.rep('a',32),request_digest=string.rep('b',64),
          authorization={approved_actions={'project_more_ghosts'}},
          build_plan={surface='training/test',force='training-test',phases={{name='test',actions={{
            action_type='place_ghost',entity='lab',position={x=1.5,y=2.5}}}}}}}
        execute({parameter='serialized'})
        assert(report.ok==true and created==1 and report.succeeded_placements==1)
        assert(written=='{"placement_failures":[]}')
        execute({parameter='serialized'})
        assert(report.ok==true and created==1 and writes==2)
    '''
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_stream_replays_latest_rejects_old_and_handles_more_than_128_requests():
    script = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; accessed=0; writes=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function()
          accessed=accessed+1; error('deliberate-world-failure') end}
        game={tick=55,print=function(value) last_print=value end}; defines={direction={}}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(value) report=value; return '{}' end,
          write_file=function() writes=writes+1 end}
    ''' + (ROOT / "factorio_mod/layout_executor.lua").read_text() + '''
        stream=string.rep('c',16)
        payload={stream_id=stream,sequence=1,request_id=stream..string.format('%016x',1),
          request_digest=string.rep('b',64),authorization={approved_actions={'project_more_ghosts'}},
          build_plan={phases={{name='test',actions={{action_type='place_ghost',entity='lab',position={x=1,y=2}}}}}}}
        for i=1,130 do
          payload.sequence=i; payload.request_id=stream..string.format('%016x',i)
          execute({parameter='request-'..i})
          assert(report.sequence==i and report.stream_id==stream and report.receipt_version=='layout-submission-receipt-v2')
        end
        assert(accessed==130 and writes==130 and storage.layout_submission_streams.count==1)
        execute({parameter='request-130'})
        assert(accessed==130 and writes==131)
        execute({parameter='different'})
        assert(accessed==130 and string.find(last_print,'conflicts',1,true))
        payload.sequence=129; payload.request_id=stream..string.format('%016x',129)
        execute({parameter='request-129'})
        assert(accessed==130 and string.find(last_print,'stale',1,true))
        payload.sequence=132; payload.request_id=stream..string.format('%016x',132)
        execute({parameter='request-132'})
        assert(accessed==130 and string.find(last_print,'skipped',1,true))
        storage.layout_submission_streams.entries[stream].status='pending'
        payload.sequence=131; payload.request_id=stream..string.format('%016x',131)
        execute({parameter='request-131'})
        assert(accessed==130 and string.find(last_print,'pending',1,true))
    '''
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_stream_and_legacy_id_namespaces_cannot_overlap():
    script = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; accessed=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() accessed=accessed+1; error('stop') end}
        game={tick=1,print=function(value) last_print=value end}; defines={direction={}}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,table_to_json=function() return '{}' end,write_file=function() end}
    ''' + (ROOT / "factorio_mod/layout_executor.lua").read_text() + '''
        local stream=string.rep('c',16); local id=stream..string.format('%016x',1)
        storage.layout_submission_receipts={count=1,entries={[id]={payload='legacy'}}}
        payload={stream_id=stream,sequence=1,request_id=id,request_digest=string.rep('b',64),
          authorization={approved_actions={'project_more_ghosts'}},
          build_plan={phases={{name='test',actions={{action_type='place_ghost',entity='lab',position={x=1,y=2}}}}}}}
        execute({parameter='stream'})
        assert(accessed==0 and string.find(last_print,'legacy journal',1,true))
        storage.layout_submission_receipts={count=0,entries={}}
        execute({parameter='stream'})
        assert(accessed==1)
        payload.stream_id=nil; payload.sequence=nil
        execute({parameter='legacy'})
        assert(accessed==1 and string.find(last_print,'belongs to a stream',1,true))
    '''
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutation", [
    "payload.request_id='../unsafe'", "payload.request_digest=string.rep('A',64)",
    "payload.request_digest=nil", "payload.extra=true",
    "storage.layout_submission_receipts={count=128,entries={}}",
    "storage.layout_submission_receipts={count=-1,entries={}}",
    "storage.layout_submission_receipts=false",
    "storage.layout_submission_receipts={count=1,entries={[payload.request_id]=false}}",
])
def test_invalid_or_full_journal_rejects_before_world_access(mutation):
    script = f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + '''
        storage={}; accessed=0; writes=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() accessed=accessed+1 end}
        game={tick=1,print=function(value) last_print=value end}; defines={direction={}}
        commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,table_to_json=function() return '{}' end,
          write_file=function() writes=writes+1 end}
    ''' + (ROOT / "factorio_mod/layout_executor.lua").read_text() + f'''
        payload={{request_id=string.rep('a',32),request_digest=string.rep('b',64),
          authorization={{approved_actions={{'project_more_ghosts'}}}},
          build_plan={{phases={{{{name='test',actions={{{{action_type='place_ghost',entity='lab',position={{x=1,y=2}}}}}}}}}}}}}}
        {mutation}
        execute({{parameter='serialized'}})
        assert(accessed==0 and writes==0 and string.find(last_print, 'error', 1, true))
    '''
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


SHARED_STUB = '''
    storage={}; accessed=0; created=0; writes=0; tags_seen={}
    local surface={name='training/oil',find_entities_filtered=function(filter)
        if filter.name=='entity-ghost' and occupied_ghost then return {occupied_ghost} end
        return {} end,
      can_place_entity=function() return true end,
      create_entity=function(p) created=created+1; tags_seen[created]=p.tags
        return {valid=true,ghost_name=p.inner_name,name='entity-ghost'} end}
    local force={name='training-oil'}
    package.loaded.sandbox_shared={
      get_or_create_sandbox_surface=function() accessed=accessed+1; return surface end,
      get_or_create_planner_force=function() accessed=accessed+1; return force end,
      find_exact_entity=function(_, _, _, position) accessed=accessed+1
        if occupied_entity and position.x==occupied_entity.position.x and position.y==occupied_entity.position.y then
          return occupied_entity end
        return nil end}
    prototypes={entity={pipe={collision_box={{-0.29,-0.29},{0.29,0.29}}}}}
    defines={direction={north=0},build_check_type={manual=0}}
    game={tick=4,print=function(value) last_print=value end}
    commands={add_command=function(name, _, fn) if name=='build_layout_plan' then execute=fn end end}
    helpers={json_to_table=function() return payload end,
      table_to_json=function(value) report=value; return '{}' end,write_file=function() writes=writes+1 end}
'''
SHARED_PAYLOAD = '''
    tags={optimizer_episode='episode',optimizer_project='project',
      optimizer_transaction=string.rep('a',32),optimizer_plan_hash=string.rep('b',64)}
    local function ghost(x) return {action_type='place_ghost_no_reconcile',entity='pipe',position={x=x,y=0.5}} end
    payload={request_id=string.rep('a',32),request_digest=string.rep('b',64),
      optimizer_tag_protocol='optimizer-shared-tags-v1',
      authorization={approved_actions={'project_more_ghosts'}},
      build_plan={surface='training/oil',force='training-oil',atomic=true,optimizer_ownership=tags,
        phases={{name='one',actions={ghost(0.5),ghost(2.5)}},{name='two',actions={ghost(4.5)}}}}}
'''


def _run_shared(statements):
    script = (f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n' + SHARED_STUB
              + (ROOT / "factorio_mod/layout_executor.lua").read_text() + SHARED_PAYLOAD + statements)
    result = subprocess.run([LUA, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_shared_tags_reach_every_ghost_across_phases():
    _run_shared('''
        execute({parameter='serialized'})
        assert(report.ok==true and report.succeeded_placements==3 and created==3 and accessed>=2)
        for i=1,3 do
          local seen=tags_seen[i]
          assert(seen.optimizer_project=='project' and seen.optimizer_plan_hash==string.rep('b',64))
        end
    ''')


def test_correlated_per_action_tags_without_protocol_still_apply():
    _run_shared('''
        payload.optimizer_tag_protocol=nil; payload.build_plan.optimizer_ownership=nil
        payload.build_plan.phases={{name='one',actions={
          {action_type='place_ghost_no_reconcile',entity='pipe',position={x=0.5,y=0.5},optimizer_ownership=tags},
          {action_type='place_ghost',entity='pipe',position={x=2.5,y=0.5}}}}}
        execute({parameter='serialized'})
        assert(report.ok==true and created==2 and tags_seen[1].optimizer_episode=='episode' and tags_seen[2]==nil)
    ''')


@pytest.mark.parametrize("form", ["shared", "per_action"])
@pytest.mark.parametrize("occupant", ["ghost", "entity"])
def test_tagged_optimizer_plan_rejects_preexisting_target_before_any_placement(form, occupant):
    per_action = "" if form == "shared" else '''
        payload.optimizer_tag_protocol=nil; payload.build_plan.optimizer_ownership=nil
        for _,phase in ipairs(payload.build_plan.phases) do
          for _,action in ipairs(phase.actions) do action.optimizer_ownership=tags end
        end
    '''
    _run_shared(per_action + f'''
        occupied_{occupant}={{valid=true,name={'entity-ghost'!r},ghost_name='pipe',position={{x=2.5,y=0.5}}}}
        execute({{parameter='serialized'}})
        assert(report.ok==false and report.error=='optimizer_tagged_position_already_occupied')
        assert(report.placement_failures[1].position.x==2.5 and created==0)
    ''')


def test_correlated_replay_returns_original_receipt_after_ghost_exists():
    _run_shared('''
        execute({parameter='serialized'})
        assert(report.ok==true and created==3)
        occupied_ghost={valid=true,name='entity-ghost',ghost_name='pipe',position={x=0.5,y=0.5}}
        execute({parameter='serialized'})
        assert(report.ok==true and created==3)
    ''')


@pytest.mark.parametrize("mutation", [
    # Envelope protocol and correlation.
    "payload.optimizer_tag_protocol=nil",
    "payload.optimizer_tag_protocol='optimizer-shared-tags-v2'",
    "payload.optimizer_tag_protocol=true",
    "payload.request_id=nil; payload.request_digest=nil",
    "payload.request_id=nil; payload.request_digest=nil; payload.optimizer_tag_protocol=nil",
    "payload.build_plan.optimizer_ownership=nil",
    "payload.extra=true",
    # Plan scope.
    "payload.build_plan.atomic=false", "payload.build_plan.atomic=nil", "payload.build_plan.atomic=1",
    "payload.build_plan.surface=nil", "payload.build_plan.surface=''", "payload.build_plan.force=7",
    "payload.build_plan.phases={}", "payload.build_plan.phases[2].actions={}",
    "payload.build_plan.phases={one={}}", "payload.build_plan.phases[1].actions={first=1}",
    "payload.build_plan.phases='phases'",
    # Mixed or non-ghost actions.
    "payload.build_plan.phases[1].actions[1].optimizer_ownership=tags",
    "payload.build_plan.phases[1].actions[1].research_lab_ownership={research_lab_episode='e',"
    "research_lab_packet='p',research_lab_transaction=string.rep('a',32)}",
    "payload.build_plan.phases[1].actions[1].action_type='place_ghost'",
    "payload.build_plan.phases[1].actions[1].action_type='configure_entity'",
    "payload.build_plan.phases[1].actions[1].action_type='remove_entity'",
    "payload.build_plan.phases[2].actions[1]='ghost'",
    # Malformed tags.
    "payload.build_plan.optimizer_ownership=false", "payload.build_plan.optimizer_ownership='tags'",
    "tags.extra='x'", "tags.optimizer_plan_hash=string.rep('B',64)",
    "tags.optimizer_transaction=string.rep('a',31)..'\\n'", "tags.optimizer_episode=5",
    "tags.optimizer_project=''", "tags.optimizer_project=nil",
])
def test_invalid_shared_tag_request_never_reaches_the_world(mutation):
    _run_shared(f'''
        {mutation}
        execute({{parameter='serialized'}})
        assert(accessed==0 and created==0)
        assert(report==nil or report.ok==false)
        assert(report~=nil or string.find(last_print, 'error', 1, true))
    ''')
