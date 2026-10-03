# Path: tests/test_laya_smelting_fixture.py | Purpose: Check fixture isolation, cleanup ownership and counter semantics with native Lua mocks.
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
LUA=shutil.which('lua')
pytestmark=pytest.mark.skipif(not LUA,reason='native Lua unavailable')


def run_lua(code):
    result=subprocess.run([LUA,'-'],input=f"package.path='{ROOT}/factorio_training_lab/?.lua;'..package.path\n"+code,
                          text=True,capture_output=True)
    assert result.returncode==0,result.stderr


def test_owned_fixture_survives_lab_orphan_cleanup_but_foreign_surface_does_not():
    run_lua('''
    storage={smelting_fixture={owner='factorio_training_lab.smelting_fixture',surface='training/smelting-test',force='training-smelting-test'}}
    local owned={name='training/smelting-test',valid=true}
    local orphan={name='training/orphan',valid=true}
    local deleted={}
    log=function() end
    game={surfaces={['training/smelting-test']=owned,['training/orphan']=orphan},
          forces={neutral={name='neutral'},['training-smelting-test']={name='training-smelting-test',valid=true}},
          connected_players={},delete_surface=function(s) deleted[s.name]=true;return true end,
          merge_forces=function() error('owned force merged') end}
    local world=require('episode_world')
    world.cleanup_orphan_surfaces(10000,true)
    assert(not deleted['training/smelting-test'])
    assert(deleted['training/orphan'])
    ''')


def test_fixture_is_inert_without_setup_and_supplies_separately_from_sink():
    run_lua('''
    storage={};local m=require('smelting_fixture');m.tick()
    local supplied,removed=0,0
    local line={insert_at_back=function(item) assert(item.name=='iron-ore');supplied=supplied+item.count;return true end,
                remove_item=function(item) assert(item.name=='iron-plate');removed=removed+1;return 1 end}
    local belt={get_transport_line=function() return line end}
    game={surfaces={['training/smelting-test']={find_entity=function() return belt end}}}
    storage.smelting_fixture={surface='training/smelting-test',ore='iron-ore',recipe='iron-plate',
      rates={45,0,0,0},supplied={0,0,0,0},denied={0,0,0,0},delivered={0,0,0,0},credit={}}
    for i=1,8 do m.tick() end
    assert(supplied==6 and storage.smelting_fixture.supplied[1]==6)
    assert(storage.smelting_fixture.delivered[1]==16 and removed==64)
    ''')
