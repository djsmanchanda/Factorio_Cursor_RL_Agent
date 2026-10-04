# Path: tests/test_laya_production_fixture.py | Purpose: Verify declared outages pause only their source and backpressure preserves independent source accounting.
from tests.test_laya_smelting_fixture import run_lua


def test_source_and_sink_interruptions_have_exact_tick_boundaries():
    run_lua('''
    storage={};local m=require('production_fixture');m.tick()
    local supplied,removed=0,0
    local line={insert_at_back=function() supplied=supplied+1;return true end,
      remove_item=function() removed=removed+1;return 1 end}
    local belt={get_transport_line=function() return line end}
    local force={get_item_production_statistics=function() return {get_input_count=function() return 0 end} end}
    local surface={find_entity=function() return belt end}
    game={tick=3599,surfaces={['training/mechanics-test']=surface},forces={['training-mechanics-test']=force}}
    local s={owner='factorio_training_lab.production_fixture',surface='training/mechanics-test',force='training-mechanics-test',
      started_tick=0,armed_tick=0,sources={},supplied={0},denied={0},delivered=0,credit={},
      spec={recipe='plastic-bar',interruption_ticks=2,interruption_source='coal',sink_pause_ticks=3,
      sources={{kind='belt',entity='transport-belt',position={0,0},item='coal',rate=120}},
      sinks={{kind='belt',entity='transport-belt',position={1,0},item='plastic-bar'}}}}
    storage.production_fixture=s
    m.tick();assert(supplied==2 and removed==2)
    game.tick=3600;m.tick();assert(supplied==2 and removed==2)
    game.tick=3601;m.tick();assert(supplied==2 and removed==2)
    game.tick=3602;m.tick();assert(supplied==4 and removed==2)
    game.tick=3603;m.tick();assert(supplied==6 and removed==4)
    assert(s.supplied[1]==6 and s.delivered==4)
    ''')


def test_ground_cleanup_orders_bots_only_at_the_declared_damaged_site():
    run_lua('''
    local requested,orders=0,0
    local force={name='training-mechanics-test'}
    local surface={find_entities_filtered=function(filter)
      assert(filter.type=='item-entity' and filter.area[1][1]==8 and filter.area[2][1]==12)
      return {{order_deconstruction=function(f) assert(f==force);orders=orders+1;return true end}}
    end}
    storage={production_fixture={owner='factorio_training_lab.production_fixture',surface='training/mechanics-test',force=force.name,
      spec={fault_action={position={x=10,y=20}}},perturbations={{tick=1}}}}
    game={tick=100,tick_paused=true,ticks_to_run=0,surfaces={['training/mechanics-test']=surface},forces={[force.name]=force}}
    local output,callback
    commands={add_command=function(name,help,fn) callback=fn end}
    helpers={json_to_table=function() return {op='clear-ground',report='production-fixture/test.json'} end,
      table_to_json=function(value) return value end,write_file=function(path,value) output=value end}
    rcon={print=function() end}
    require('production_fixture').register();callback({parameter='request'})
    assert(orders==1 and output.ok and output.value.extra_items_granted==0)
    assert(output.value.method=='construction-bot-deconstruction')
    ''')
