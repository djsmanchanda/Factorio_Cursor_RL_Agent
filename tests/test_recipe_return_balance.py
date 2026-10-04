# Path: tests/test_recipe_return_balance.py | Purpose: Check the committed recipe-return helper preserves quantities across chest and spill paths; this is not live delivery evidence.
from pathlib import Path
from tests.test_laya_smelting_fixture import run_lua


def test_recipe_switch_return_balances_legacy_quality_records_and_full_chests():
    source=(Path(__file__).resolve().parents[1]/'factorio_mod/layout_executor.lua').read_text()
    start=source.index('local function return_removed_items')
    end=source.index('local function configure_created_entity',start)
    helper=source[start:end]
    run_lua('''
    local function position_inside_box() return true end
    '''+helper+'''
    for _,with_chest in ipairs({true,false}) do
      local returned={}
      local function count(stack,n)
        local key=stack.name..':'..(stack.quality or 'normal')
        returned[key]=(returned[key] or 0)+n
      end
      local chest={insert=function(stack) local n=math.min(2,stack.count);count(stack,n);return n end}
      local surface={find_entities_filtered=function(filter)
        if filter.type=='inserter' then return {{drop_position={0,0},pickup_position={1,0}}} end
        return with_chest and {chest} or {}
      end,spill_item_stack=function(spec) assert(not spec.allow_belts);count(spec.stack,spec.stack.count) end}
      local entity={surface=surface,force='training',position={0,0},bounding_box={left_top={x=-1,y=-1},right_bottom={x=1,y=1}}}
      assert(return_removed_items(entity,{{name='iron-plate',count=11,quality='uncommon'},{name='electronic-circuit',count=7,quality='normal'}})==nil)
      assert(returned['iron-plate:uncommon']==11 and returned['electronic-circuit:normal']==7)
      returned={};assert(return_removed_items(entity,{['copper-cable']=9})==nil)
      assert(returned['copper-cable:normal']==9)
      surface.spill_item_stack=function() error('spill rejected') end
      assert(return_removed_items(entity,{{name='iron-plate',count=11}})=='recipe_switch_removed_items_lost')
    end
    ''')


def test_held_stack_conservation_for_belt_full_chest_and_failed_spill():
    source=(Path(__file__).resolve().parents[1]/'factorio_mod/layout_executor.lua').read_text()
    start=source.index('local function return_feeding_inserter_hands')
    end=source.index('local function return_removed_items',start)
    run_lua('local function position_inside_box() return true end\n'+source[start:end]+'''
    for _,chest_count in ipairs({-1,0,3,9}) do
      for _,spill_fails in ipairs({false,true}) do
        local returned=0
        local held={name='iron-plate',count=9,valid_for_read=true,quality={name='uncommon'}}
        held.clear=function() held.count=0;held.valid_for_read=false end
        local chest={insert=function(stack) assert(stack.quality=='uncommon');returned=returned+chest_count;return chest_count end}
        local inserter={drop_position={x=0,y=0},pickup_position={x=2,y=0},held_stack=held}
        local surface={find_entities_filtered=function(filter)
          if filter.type=='inserter' then return {inserter} end
          return chest_count<0 and {} or {chest}
        end,spill_item_stack=function(spec)
          assert(spec.stack.quality=='uncommon' and spec.position==inserter.pickup_position)
          if spill_fails then error('cannot spill') end
          returned=returned+spec.stack.count
        end}
        local entity={surface=surface,force='training',bounding_box={left_top={x=-1,y=-1},right_bottom={x=1,y=1}}}
        local error=return_feeding_inserter_hands(entity)
        assert(returned+held.count==9)
        if spill_fails and chest_count<9 then assert(error=='recipe_switch_held_stack_spill_failed')
        else assert(error==nil and not held.valid_for_read) end
      end
    end
    ''')
