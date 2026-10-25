# Path: tests/test_fluid_machine_settings.py | Purpose: Verify native port mirroring and current fluid telemetry without changing reserved geometry.
import copy
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from core.fluid_systems import rotate_offset
from planners.fluid_layout_search import CARDINALS, MIRRORS, oriented_fluid_machine_row
from planners.fluid_machine_settings import complete_fluid_mirroring_settings
from planners.plan_validation import actions
from planners.resource_layouts import generate_pumpjack_source, verified_pumpjack_output_tile

pytestmark=pytest.mark.fast
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('direction',CARDINALS)
@pytest.mark.parametrize('mirror',MIRRORS)
def test_engine_port_frame_matches_rotated_and_reflected_refinery_pipes(direction,mirror):
    plan=oriented_fluid_machine_row('basic-oil-processing',4,(17,-9),direction=direction,mirror=mirror)
    pipes={(a['position']['x'],a['position']['y']) for a in actions(plan) if a.get('entity') in {'pipe','pipe-to-ground'}}
    for a in actions(plan):
        if a.get('entity')!='oil-refinery':continue
        for offset in [(1,3),(2,-3)]:  # Native non-mirrored north-facing ports.
            dx,dy=offset
            if a['mirroring']:dx=-dx
            dx,dy=rotate_offset((dx,dy),a['direction'])
            assert (a['position']['x']+dx,a['position']['y']+dy) in pipes


@pytest.mark.parametrize('direction',CARDINALS)
def test_pumpjack_setting_matches_the_reserved_source_stub(direction):
    site={'position':(18.5,20.5),'direction':direction}
    stub=verified_pumpjack_output_tile(site)
    site['output']=stub
    plan=generate_pumpjack_source([site],[stub])
    pump=next(a for a in actions(plan) if a.get('entity')=='pumpjack')
    assert pump['mirroring']
    dx,dy=rotate_offset((-1,-2),direction)
    assert stub==(math.floor(18.5+dx),math.floor(20.5+dy))


def test_old_reserved_fluid_plan_gets_missing_settings_without_moving_geometry():
    plan=oriented_fluid_machine_row('basic-oil-processing',4,(17,-9),direction='south',mirror='horizontal')
    for a in actions(plan):a.pop('mirroring',None)
    original=copy.deepcopy(plan)
    fixed=complete_fluid_mirroring_settings(plan)
    machines=[a for a in actions(fixed) if a.get('entity')=='oil-refinery']
    assert len(machines)==4 and all(a['mirroring'] for a in machines)
    assert plan==original
    for a in actions(fixed):a.pop('mirroring',None)
    assert fixed==original


@pytest.mark.parametrize('connected',[False,True])
def test_fluid_report_uses_current_storage_api_and_merged_input_prototypes(connected):
    lua=shutil.which('lua')
    if not lua:pytest.skip('Lua unavailable')
    source=(ROOT/'factorio_mod/live_execution.lua').read_text()
    excerpt=source[source.index('local function input_boxes'):source.index('local function fluid_machines')]
    script=excerpt+'''
local e={fluids_count=2}
setmetatable(e,{__index=function(_,k) if k=='fluidbox' then error('removed API') end end})
e.get_fluid_box_prototype=function(i) if i==1 then return {{production_type='input'},{production_type='input'}} end;return {production_type='output'} end
e.get_fluid=function(i) return {name='crude-oil',amount=40} end
'''
    script+=f'e.get_fluid_box_neighbours=function(i) return {"{{}}" if connected else "{}"} end\n'
    script+=f"local boxes=input_boxes(e);assert(#boxes==1 and boxes[1].index==1 and boxes[1].amount==40 and boxes[1].connected=={str(connected).lower()})\n"
    result=subprocess.run([lua,'-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
