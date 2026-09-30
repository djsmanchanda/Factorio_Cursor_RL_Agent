# Path: tests/test_laya_repair.py | Purpose: Verify learned restoration choices retain survivors and refuse unknown or stale ownership.

from collections import Counter
import copy

import pytest

from tests.test_laya_toolkit import Bridge
from training.candidates.connected_workflow import connected_workflow_candidates
from training.laya_repair import restoration_candidates
from training.laya_toolkit import LayaToolkit, StaleLayaOption
from training.laya_feature_baseline import option_vector
from training.scenarios.connected_workflow import generate_connected_workflow_scenario


def damaged_cell():
    scenario = generate_connected_workflow_scenario(5)
    candidate = next(c for c in connected_workflow_candidates(scenario) if c['variant_id']=='iron.fast')
    phase = candidate['plan']['phases'][0]
    counts = dict(Counter(a['entity'] for a in phase['actions']))
    counts['fast-inserter'] -= 1
    bridge = Bridge(scenario)
    bridge.report['metrics'].update(built_entities=copy.deepcopy(counts), live_entities_by_phase={phase['name']:counts})
    toolkit = LayaToolkit(bridge, scenario, sleeper=lambda _:None)
    toolkit.bind_episode('repair-test')
    return toolkit, bridge, candidate


def test_destroyed_cell_can_be_restored_despite_historical_execution():
    toolkit, bridge, candidate = damaged_cell()
    options = toolkit.serve_goal_options(bridge.report, ['iron','construction'], {candidate['plan_hash']})
    ids = {o.option.option_id for o in options}
    assert 'repair.iron.fast' in ids and 'serve.iron' not in ids
    assert 'serve.construction' in ids and 'wait.1s' in ids
    selected = next(o for o in options if o.option.option_id=='repair.iron.fast')
    assert selected.candidate['features']['entity_count'] == 1
    assert selected.candidate['features']['material_cost'] < candidate['features']['material_cost']
    assert selected.handler().kind == 'execute'
    assert bridge.executions[0][2] == candidate['plan']


def test_restoration_rechecks_survivors_and_refuses_stale_bill():
    toolkit, bridge, candidate = damaged_cell()
    selected = next(o for o in toolkit.serve_goal_options(bridge.report, ['iron'], {candidate['plan_hash']})
                    if o.option.option_id=='repair.iron.fast')
    bridge.report['metrics']['live_entities_by_phase']['connected_workflow.iron']['fast-inserter'] += 1
    with pytest.raises(StaleLayaOption): selected.handler()
    assert bridge.executions == []


@pytest.mark.parametrize('change', ['unknown','unexecuted','unexpected','no_budget','negative','inactive'])
def test_no_restoration_is_inferred_without_current_authorized_evidence(change):
    toolkit, bridge, candidate = damaged_cell()
    known = {candidate['plan_hash']}
    budget = toolkit._remaining_budget(bridge.report)
    owned = bridge.report['metrics']['live_entities_by_phase']
    if change=='unknown': del bridge.report['metrics']['live_entities_by_phase']
    if change=='unexecuted': known=set()
    if change=='unexpected': owned['connected_workflow.iron']['assembling-machine-2']=1
    if change=='no_budget': budget['fast-inserter']=0
    if change=='negative': owned['connected_workflow.iron']['fast-inserter']=-1
    if change=='inactive': bridge.report['status']='failed'
    assert restoration_candidates([candidate],bridge.report,known,budget) == []


def test_foreign_scope_cannot_compile_a_restoration():
    toolkit, bridge, candidate = damaged_cell()
    bridge.report['surface']='nauvis'
    with pytest.raises(ValueError):
        toolkit.serve_goal_options(bridge.report,['iron'],{candidate['plan_hash']})


def test_restoration_features_preserve_saved_set_five_vectors():
    option = {'option_id':'repair.iron.fast','features':{'material_cost':12,'repair':1}}
    plain = copy.deepcopy(option)
    plain['features']['repair'] = 0
    assert (option_vector(option,{},5) == option_vector(plain,{},5)).all()
    assert (option_vector(option,{},6) != option_vector(plain,{},6)).any()
