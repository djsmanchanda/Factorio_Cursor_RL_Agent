# Path: tests/test_laya_factory_health.py | Purpose: Protect inventory ownership, observation freshness and shared train/live health rendering.

import copy

import pytest

from tests.test_laya_lab_workflow import BRANCHES, _report, _state
from training.laya_factory_health import STATES, ROLES, compact_factory_health
from training.laya_lab_workflow import observe_lab_workflow
from training.laya_branch_decisions import prepare_serve_goal
from training.laya_feature_baseline import option_vector
from training.laya_workflow_observation import WorkflowObservationError
from training.factorio_bridge import FactorioTrainingBridge


def health_report():
    report = _report()
    report['metrics']['factory_health'] = {
        'version': '1.0.0', 'surface': report['surface'], 'force': report['force'], 'tick': 220,
        'stock_scope': 'owned_inventory_and_held_items', 'resource_remaining': 300,
        'construction_remaining': {'electric-furnace': 1},
        'phases': {'iron': {'entities': 2, 'machines': 1,
                           'statuses': {state: int(state == 'starved') for state in STATES},
                           'power': {'consumers': 2, 'disconnected': 0, 'empty': 0},
                           'stock': {role: {'available': True, 'items': {}} for role in ROLES},
                           'machine_sample_ticks': 120, 'working_sample_ticks': 60}},
        'sources': {'ore': {'available': True, 'items': {'iron-ore': 1000}}},
    }
    report['metrics']['factory_health']['phases']['iron']['stock']['output']['items'] = {'iron-plate': 3}
    report['metrics']['item_consumption'] = {'available': True, 'items': {
        'iron-plate': {'available': True, 'window_rate_per_tick': .02, 'window_end_tick': 200}}}
    return report


def test_cell_wip_and_sources_do_not_become_accessible_stock():
    graph, supply = observe_lab_workflow(health_report(), BRANCHES)
    state, _, _ = prepare_serve_goal(_state(), graph, supply)
    digest = state['observation_digest']
    assert digest['item_supply']['iron-plate']['stock'] is None
    assert digest['item_supply']['iron-plate']['draw_rate'] == .02
    assert digest['factory_health']['stock'] == [[0, 'output', 'iron-plate', 3]]
    assert digest['factory_health']['source_stock'] == [['ore', 'iron-ore', 1000]]
    assert digest['factory_health']['phases'][0][-1] == .5
    assert supply['items']['iron-plate']['production']['capacity_rate'] is None


@pytest.mark.parametrize('mutation', ['stale', 'foreign', 'oversized', 'nonfinite', 'inconsistent'])
def test_invalid_health_fails_closed(mutation):
    report = health_report()
    h = report['metrics']['factory_health']
    if mutation == 'stale': h['tick'] -= 1
    if mutation == 'foreign': h['surface'] = 'training/another-episode'
    if mutation == 'oversized': h['phases'] = {str(i): copy.deepcopy(h['phases']['iron']) for i in range(17)}
    if mutation == 'nonfinite': h['resource_remaining'] = float('nan')
    if mutation == 'inconsistent': h['phases']['iron']['statuses']['working'] = 2
    with pytest.raises(WorkflowObservationError):
        observe_lab_workflow(report, BRANCHES)


def test_unavailable_stock_is_not_rendered_as_known_zero():
    report = health_report()
    h = report['metrics']['factory_health']
    h['phases']['iron']['stock']['output']['available'] = False
    compact = compact_factory_health(h)
    assert compact['unknown_stock'] == [[0, 'output']]
    assert compact['stock'] == []


def test_old_features_models_keep_their_vectors_and_new_models_distinguish_unknown_health():
    graph, supply = observe_lab_workflow(health_report(), BRANCHES)
    state, options, _ = prepare_serve_goal(_state(), graph, supply)
    absent = copy.deepcopy(state)
    del absent['observation_digest']['factory_health']
    assert (option_vector(options[0], state, 4) == option_vector(options[0], absent, 4)).all()
    assert (option_vector(options[0], state, 5) != option_vector(options[0], absent, 5)).any()


def test_future_flow_window_is_rejected():
    report = health_report()
    report['metrics']['item_consumption']['items']['iron-plate']['window_end_tick'] = 221
    with pytest.raises(WorkflowObservationError, match='tick provenance'):
        observe_lab_workflow(report, BRANCHES)


def test_bridge_accepts_the_new_strict_report_contract(tmp_path):
    bridge = FactorioTrainingBridge(tmp_path, host='127.0.0.1', port=28005, password='test', rcon=object())
    report = health_report()
    report.update(version='1.1.0', kind='observation', request_id='request-1', episode_id='ep-1', tick=220, scenario_id='health-test',
                  ok=True, objective={'item':'transport-belt','target_rate_per_tick':.01,'sustain_ticks':1800},
                  failure={'kind':'none','reason':''})
    schema = bridge._report_validator.schema['properties']['metrics']
    for key in schema['required']:
        kind = schema['properties'][key]['type']
        report['metrics'].setdefault(key, True if kind=='boolean' else {} if kind=='object' else 0)
    assert list(bridge._report_validator.iter_errors(report)) == []
    report['metrics']['factory_health']['phases']['iron']['stock']['input']['items']['iron-ore'] = -1
    assert list(bridge._report_validator.iter_errors(report))
