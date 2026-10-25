# Path: tests/test_mall_cell_release_lua.py | Purpose: Prove finite-cell WIP recovery, ownership and retry conservation through Lua.
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LUA = shutil.which("lua")
pytestmark = [pytest.mark.lua, pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")]

FIXTURE = r'''
storage={}
local M=require('cell_release')
local groups=require('logistic_sections')
defines={inventory={chest=1},direction={north=0},entity_status={}}
local function stack(name,count,quality,capacity)
  local s={name=name,count=count,quality=quality or 'normal',valid_for_read=count>0}
  s.transfer_stack=function(other)
    if not other.valid_for_read then return false end
    if s.valid_for_read and (s.name~=other.name or s.quality~=other.quality) then return false end
    local moved=math.min(other.count,(capacity or 100)-s.count)
    s.name=other.name;s.quality=other.quality;s.count=s.count+moved;s.valid_for_read=s.count>0
    other.count=other.count-moved;other.valid_for_read=other.count>0
    return not other.valid_for_read
  end
  return s
end
local own={valid=true,group='mall-bootstrap:test',filters_count=1,
  slots={{value={name='electronic-circuit'},min=6}}}
local peer={valid=true,group='mall:gear:left',filters_count=1,multiplier=15,
  slots={{value={name='iron-plate'},min=2}}}
for _,section in ipairs({own,peer}) do
  section.get_slot=function(i) return section.slots[i] end
  section.clear_slot=function(i) section.slots[i]=nil end
end
local holder={sections={own,peer}}
local source={stack('iron-plate',15),stack('electronic-circuit',7,'rare')}
local target={stack('electronic-circuit',0,'rare',5)}
target.supports_bar=function() return true end
target.get_bar=function() return 2 end
local force={name='player'}
local requester={valid=true,name='requester-chest',unit_number=1,position={x=3,y=0},force=force,
  get_logistic_sections=function() return holder end,get_inventory=function() return source end}
local provider={valid=true,name='passive-provider-chest',unit_number=2,position={x=3,y=1},force=force,
  get_inventory=function() return target end}
local current='electric-mining-drill'
local stopped=0
local refund={}
local inserters={}
local machine={valid=true,unit_number=3,type='assembling-machine',name='assembling-machine-1',force=force,
  position={x=0,y=0},bounding_box={left_top={x=-1,y=-1},right_bottom={x=1,y=1}},
  get_recipe=function() return current and {name=current} end,
  set_recipe=function(name) stopped=stopped+1;current=name;local out=refund;refund={};return out end}
local surface={name='nauvis',create_entity=function() error('release must not place entities') end,
  find_entities_filtered=function(o)
    if o.type=='inserter' then return inserters end
    if o.force~=force then return {} end
    if o.name=='requester-chest' then return {requester} end
    if o.name=='passive-provider-chest' then return {provider} end
    if o.name=='assembling-machine-1' then return {machine} end
    return {}
  end}
machine.surface=surface
local spec={group=own.group,expected_recipe=current,requester=requester.position,provider=provider.position}

target.insert=function(value)
  local temporary=stack(value.name,value.count,value.quality)
  local initial=temporary.count
  for i=1,math.min(#target,target.get_bar()-1) do target[i].transfer_stack(temporary) end
  return initial-temporary.count
end
surface.spill_item_stack=function() error('cell recovery must never spill') end

'''


def run_lua(body):
    result = subprocess.run([LUA, "-"], input=f'package.path="{ROOT}/factorio_mod/?.lua;"..package.path\n'
                            + FIXTURE + body, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_release_preserves_companion_and_quality_across_a_full_provider_retry():
    run_lua('''
assert(M.apply(machine,spec)=='release_destination_full')
assert(not current and own.group==spec.group and own.get_slot(1)==nil)
assert(source[2].count==2 and target[1].count==5 and target[1].quality=='rare')
assert(source[1].count==15 and peer.multiplier==15 and peer.get_slot(1).min==2)
assert(M.verify(machine,spec)=='release_recovery_pending')
target[1].count=0;target[1].valid_for_read=false
assert(M.apply(machine,spec)==nil)
assert(target[1].count==2 and target[1].quality=='rare' and not source[2].valid_for_read)
assert(own.group==spec.group and peer.group=='mall:gear:left' and source[1].count==15)
spec.acknowledge=true
assert(M.apply(machine,spec)==nil and own.group=='')
assert(M.verify(machine,spec)==nil)
assert(M.apply(machine,spec)==nil) -- acknowledgement replay while still vacant
''')


def test_changed_recipe_or_missing_owner_cannot_release_another_job():
    run_lua('''
current='inserter'
assert(M.apply(machine,spec)=='release_recipe_changed')
assert(stopped==0 and source[2].count==7 and own.get_slot(1))
current='electric-mining-drill';own.group='another-job'
assert(M.apply(machine,spec)=='release_owner_missing')
assert(stopped==0 and source[2].count==7)
''')


def test_recovery_cannot_use_a_foreign_or_remote_container():
    run_lua('''
provider.position={x=100,y=1};spec.provider=provider.position
assert(M.apply(machine,spec)=='release_container_not_local')
assert(stopped==0 and source[2].count==7)
surface.find_entities_filtered=function(o) assert(o.force==force);return {} end
assert(M.apply(machine,spec)=='release_container_missing')
assert(stopped==0)
''')


def test_executor_applies_release_to_existing_entity_without_creation():
    source = (ROOT / "factorio_mod/layout_executor.lua").read_text()
    run_lua('''
target[2]=stack('electronic-circuit',0,'rare',100);target.get_bar=function() return 3 end
prototypes={entity={['assembling-machine-1']={type='assembling-machine'}}}
game={tick=1,surfaces={nauvis=surface},forces={player=force}}
commands={add_command=function(_name,_help,fn) execute=fn end}
helpers={json_to_table=function() return payload end,
 table_to_json=function(report) result=report;return '{}' end,write_file=function() end}
''' + source + '''
payload={authorization={approved_actions={'project_more_ghosts','place_core_infrastructure'}},
 build_plan={surface='nauvis',force='player',phases={{name='release',actions={{
  action_type='configure_entity',entity=machine.name,position=machine.position,release_cell=spec}}}}}}
execute({parameter='{}'})
assert(result.ok,result.error)
assert(not current and own.group==spec.group and not source[2].valid_for_read)
assert(source[1].count==15 and target[1].count+target[2].count==7)
assert(groups.has_settings({release_cell=spec}))
''')


def test_machine_refund_and_held_hand_remain_owned_until_destination_accepts_them():
    run_lua('''
refund={{name='electronic-circuit',count=7,quality='rare'}}
local hand=stack('electronic-circuit',3,'rare')
inserters={{drop_position={x=0,y=0},held_stack=hand}}
assert(M.apply(machine,spec)=='release_destination_full')
assert(storage.laya_cell_releases['3'].items[1].count==2)
assert(hand.count==3 and source[2].count==7 and target[1].count==5)
assert(not current and own.group==spec.group)
spec.acknowledge=true
assert(M.apply(machine,spec)=='release_recovery_pending')
spec.acknowledge=false
package.loaded['cell_release']=nil;M=require('cell_release') -- emulate a reload; durable ownership survives
target[2]=stack('electronic-circuit',0,'rare',100);target.get_bar=function() return 3 end
assert(M.apply(machine,spec)==nil)
assert(target[1].count+target[2].count==17 and not hand.valid_for_read and not source[2].valid_for_read)
assert(stopped==1 and storage.laya_cell_releases['3'].ready)
provider.unit_number=99
assert(M.apply(machine,spec)=='release_owner_changed')
provider.unit_number=2
spec.acknowledge=true
assert(M.apply(machine,spec)==nil and not storage.laya_cell_releases['3'])
''')


def test_restore_acknowledgement_requires_the_original_recipe():
    run_lua('''
target[2]=stack('electronic-circuit',0,'rare',100);target.get_bar=function() return 3 end
assert(M.apply(machine,spec)==nil and own.group==spec.group)
spec.acknowledge=true;spec.restore_recipe='copper-cable'
assert(M.apply(machine,spec)=='release_restore_not_verified' and own.group==spec.group)
current='copper-cable'
assert(M.apply(machine,spec)==nil and own.group=='')
assert(M.verify(machine,spec)==nil)
''')
