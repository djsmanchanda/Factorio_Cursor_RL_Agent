# Path: tests/test_laya_geometry_maintenance.py | Purpose: Require delivered replacement output and exact receipt ownership for bot recovery.
import json
from types import SimpleNamespace

from tools.laya_geometry_maintenance import GeometryMaintenance
from training.laya_saved_world import ghost


def record(root, name, family, actions, start_unit):
    candidate = {'family': family, 'product': 'copper-plate', 'network': '1', 'network_anchor': {'x': 0, 'y': 0},
                 'plan': {'phases': [{'actions': actions}]}}
    targets = [{**a, 'state': 'built', 'unit_number': i + start_unit} for i, a in enumerate(actions)]
    raw = {'status': 'observed', 'candidate': candidate,
           'submission': {'ok': True, 'already_present_placements': 0}, 'construction': {'targets': targets}}
    path = root / 'evidence' / f'transaction-{name}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(raw))
    return targets


def test_starter_recovery_requires_craft_delivery_and_current_unit_identity(tmp_path, monkeypatch):
    starter = record(tmp_path, 'starter', 'starter', [ghost('electric-furnace', 1.5, 1.5),
                     ghost('electric-mining-drill', 1.5, -1.5), ghost('passive-provider-chest', 1.5, 4.5),
                     ghost('medium-electric-pole', 4.5, 4.5)], 10)
    line = record(tmp_path, 'line', 'line', [ghost('electric-furnace', 21.5, 1.5),
                  ghost('passive-provider-chest', 21.5, 4.5)], 20)
    state = {'crafts': 0, 'delivered': 0, 'changed': False}
    def inspect(*, inspect):
        positions = {(a['position']['x'], a['position']['y']) for p in inspect['phases'] for a in p['actions']}
        targets = [dict(t) for t in starter + line if (t['position']['x'], t['position']['y']) in positions]
        for t in targets:
            if t['unit_number'] == 20: t['products_finished'] = state['crafts']
            if t['unit_number'] == 21: t['inventory'] = {'copper-plate': state['delivered']}
            if state['changed'] and t['unit_number'] == 10: t['unit_number'] = 999
        return {'targets': targets}
    adapter = SimpleNamespace(root=tmp_path, query=inspect, _check_instance=lambda: None,
                              bridge=object(), surface='nauvis', force='player')
    calls = []
    monkeypatch.setattr('tools.laya_geometry_maintenance.retire_entities_via_bots',
                        lambda *a, **kw: calls.append((a[4], kw)))
    maintenance = GeometryMaintenance()
    facts = {'tick': 100, 'machines': [], 'ports': [], 'poles': []}
    maintenance(adapter, facts)
    state['crafts'] = 2
    maintenance(adapter, facts)
    assert calls == []
    state.update(delivered=1, changed=True)
    maintenance(adapter, facts)
    assert calls == []  # A rebuilt same-name furnace is not the recorded owner.
    state['changed'] = False
    result = maintenance(adapter, facts)
    assert len(calls) == 1 and calls[0][1] == {'wait': False}
    actions = calls[0][0]['phases'][0]['actions']
    assert {a['entity'] for a in actions} == {'electric-furnace', 'electric-mining-drill', 'passive-provider-chest'}
    assert len(result['retired']) == 3
    GeometryMaintenance()(adapter, facts)
    assert len(calls) == 1  # Durable ownership ledger survives restart.


def test_an_exhausted_owned_drill_is_recovered_without_shared_belts(tmp_path, monkeypatch):
    line = record(tmp_path, 'line', 'line', [ghost('electric-mining-drill', 1.5, -1.5),
                  ghost('electric-mining-drill', 1.5, 2.5), ghost('transport-belt', 1.5, .5, 'east')], 20)
    adapter = SimpleNamespace(root=tmp_path, _check_instance=lambda: None, bridge=object(), surface='nauvis', force='player')
    adapter.query = lambda *, inspect: {'targets': [t for t in line if any(t['position'] == a['position']
                                        for p in inspect['phases'] for a in p['actions'])]}
    facts = {'tick': 100, 'machines': [{'name': 'electric-mining-drill', 'position': line[0]['position'],
             'unit_number': 20, 'resource_remaining': 0}], 'ports': [], 'poles': []}
    calls = []
    monkeypatch.setattr('tools.laya_geometry_maintenance.retire_entities_via_bots',
                        lambda *a, **kw: calls.append(a[4]))
    GeometryMaintenance()(adapter, facts)
    assert [a['entity'] for a in calls[0]['phases'][0]['actions']] == ['electric-mining-drill']


def test_growth_reuses_collector_haul_and_provider_without_placing_a_second_line():
    from pathlib import Path
    from collections import Counter
    from planners.plan_validation import validate_build_plan
    from training.laya_lines import line_units, mine_unit, haul, output_taps
    mine, head = mine_unit(0, 0, 3)
    original = (mine + haul(head, 'east', 30, 20) + line_units('copper-plate', 1, 30, 20)
                + output_taps(1, 30, 20, taps=1, inserter='fast-inserter'))
    parent = {'family': 'line', 'product': 'copper-plate', 'furnaces': 6, 'line_origin': [30, 20],
              'network': '1', 'network_anchor': {'x': 0, 'y': 0}, 'plan': {'phases': [{'actions': original}]}}
    rows = [(Path('line.json'), {'status': 'observed', 'candidate': parent, 'submission': {'ok': True}})]
    [growth] = GeometryMaintenance()._growth(rows, {'machines': [], 'poles': [], 'ports': []}, set(), {'line.json'})
    validate_build_plan(growth['plan'])
    bill = Counter(a['entity'] for p in growth['plan']['phases'] for a in p['actions'])
    assert bill['electric-furnace'] == 6 and bill['electric-mining-drill'] == 6
    assert bill['passive-provider-chest'] == 0
    assert growth['growth_of'] == 'line.json' and growth['reuses_infrastructure']
    assert GeometryMaintenance()._growth(rows, {'machines': [], 'poles': [], 'ports': []}, set(), set()) == []


def test_followup_growth_reconciles_previous_extension_before_next_packet():
    from pathlib import Path
    from training.laya_lines import line_units, mine_unit, haul, output_taps
    mine, head = mine_unit(0, 0, 3)
    original = mine + haul(head, 'east', 30, 20) + line_units('copper-plate', 1, 30, 20) + output_taps(1, 30, 20, taps=1, inserter='fast-inserter')
    c = {'family': 'line', 'product': 'copper-plate', 'furnaces': 6, 'line_origin': [30, 20],
         'network': '1', 'network_anchor': {'x': 0, 'y': 0}, 'plan': {'phases': [{'actions': original}]}}
    rows = [(Path('line.json'), {'status': 'observed', 'candidate': c, 'submission': {'ok': True}})]
    facts = {'machines': [], 'poles': [], 'ports': []}
    m = GeometryMaintenance()
    [first] = m._growth(rows, facts, set(), {'line.json'})
    followup = {'status': 'construction_pending', 'candidate': first, 'submission': {'ok': True}}
    rows.append((Path('growth.json'), followup))
    assert m._growth(rows, facts, set(), {'line.json'}) == []
    followup['status'] = 'observed'
    [second] = m._growth(rows, facts, set(), {'line.json'})
    assert second['target_furnaces'] == 18
    from planners.plan_validation import validate_build_plan
    validate_build_plan(second['plan'])
