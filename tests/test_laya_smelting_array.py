# Path: tests/test_laya_smelting_array.py | Purpose: Protect serial shape, physical fanout/merge legality and engine-counter acceptance.
from collections import Counter
from copy import deepcopy
import pytest
from training.laya_lines import line_units
from training.laya_smelting_array import *


def test_array_is_16_unmodified_18_furnace_lines_with_no_chest_path():
    plans=[checked_plan('training/smelting-test','training-smelting-test',f'h{i}',headers(i)) for i in range(4)]
    plans += [checked_plan('training/smelting-test','training-smelting-test',f'l{i}',serial_line('iron-plate',i)) for i in range(16)]
    validate_union(plans)
    counts=bill(plans)
    assert counts['electric-furnace']==288 and counts['express-splitter']==24
    assert not any('chest' in n for n in counts)
    assert serial_line('iron-plate',0)==line_units('iron-plate',3,0,0)


def test_tier_swap_keeps_positions_directions_and_priorities():
    basic=serial_line('copper-plate',0); express=serial_line('copper-plate',0,belt_tier='express',fast_arms=True)
    for a,b in zip(basic,express):
        assert {k:v for k,v in a.items() if k!='entity'}=={k:v for k,v in b.items() if k!='entity'}
    assert Counter(a['entity'] for a in express)['electric-furnace']==18


def test_footprint_check_catches_collision_between_headers_and_lines():
    p=checked_plan('training/smelting-test','training-smelting-test','a',serial_line('iron-plate',0))
    with pytest.raises(ValueError,match='collision'):validate_union([p,p])
    with pytest.raises(ValueError,match='identity'):checked_plan('nauvis','player','a',serial_line('iron-plate',0))


def counters(tick):
    return dict(tick=tick,produced=0,consumed=0,supplied=[0]*4,delivered=[0]*4,denied=[0]*4,
                line_finished=[0]*16,ghosts=0,path_chests=0)


def test_acceptance_needs_engine_and_belt_counters_over_two_minutes():
    before=counters(0);after=counters(7200)
    after.update(produced=21600,consumed=21600,supplied=[5400]*4,delivered=[5400]*4,line_finished=[1350]*16)
    assert measurement(before,after,16)['accepted']
    for key in ['produced','consumed']:
        bad=deepcopy(after);bad[key]=0;assert not measurement(before,bad,16)['accepted']
    bad=deepcopy(after);bad['delivered']=[0]*4;assert not measurement(before,bad,16)['accepted']
    bad=deepcopy(after);bad['denied'][0]=1;assert not measurement(before,bad,16)['accepted']
    bad=deepcopy(after);bad['path_chests']=1;assert not measurement(before,bad,16)['accepted']
    with pytest.raises(ValueError,match='two game minutes'):measurement(before,counters(7100),16)


def test_mechanics_state_has_lab_scope_and_does_not_mutate_shared_scope():
    from tools.run_laya_smelting_test import state
    from training.laya_saved_world import SCOPE
    from orchestrator.laya_state import validate_state
    original=deepcopy(SCOPE)
    f=counters(100);f.update(surface='training/smelting-test',force='training-smelting-test',
        recipe='iron-plate',ore='iron-ore',stock={},pending_bill={},ports=[])
    p=checked_plan(f['surface'],f['force'],'h',headers(0))
    option=dict(option_id='header-0',family='line',summary='Build headers',plan=p,bill=bill([p]))
    s=state(f,option,'test');validate_state(s)
    assert s['run']['surface']==f['surface'] and s['options'][0]['scope']['force']==f['force']
    assert SCOPE==original
