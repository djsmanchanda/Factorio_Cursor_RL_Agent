# Path: tests/test_laya_geometry_maintenance.py | Purpose: Require delivered replacement output and exact receipt ownership for bot recovery.
import json
from types import SimpleNamespace
import pytest

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
            if t['unit_number'] == 12: t['inventory_slots_used'] = 0
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


@pytest.mark.parametrize('slots', [5, 48, None])
def test_bulk_provider_waits_for_fewer_than_five_slots_across_restart(tmp_path, monkeypatch, slots):
    starter = record(tmp_path, 'starter', 'starter', [ghost('electric-furnace', 1.5, 1.5),
                     ghost('passive-provider-chest', 1.5, 4.5)], 10)
    line = record(tmp_path, 'line', 'line', [ghost('electric-furnace', 21.5, 1.5),
                  ghost('passive-provider-chest', 21.5, 4.5)], 20)
    state = {'slots': slots}
    def query(*, inspect):
        positions = [a['position'] for p in inspect['phases'] for a in p['actions']]
        targets = [dict(t) for t in starter + line if t['position'] in positions]
        for t in targets:
            if t['unit_number'] == 11: t['inventory_slots_used'] = state['slots']
            if t['unit_number'] == 20: t['products_finished'] = 2
            if t['unit_number'] == 21: t['inventory'] = {'copper-plate': 1}
        return {'targets': targets}
    adapter = SimpleNamespace(root=tmp_path, query=query, _check_instance=lambda: None,
                              bridge=object(), surface='nauvis', force='player')
    calls = []
    monkeypatch.setattr('tools.laya_geometry_maintenance.retire_entities_via_bots',
                        lambda *a, **kw: calls.append(a[4]))
    facts = {'tick': 100, 'machines': [], 'ports': [], 'poles': []}
    GeometryMaintenance()(adapter, facts)
    assert [a['entity'] for a in calls[0]['phases'][0]['actions']] == ['electric-furnace']
    assert len(GeometryMaintenance()(adapter, facts)['retired']) == 0
    assert len(calls) == 1  # A full or unmeasured chest remains available to logistics.
    state['slots'] = 4
    GeometryMaintenance()(adapter, facts)
    assert [a['entity'] for a in calls[1]['phases'][0]['actions']] == ['passive-provider-chest']
    GeometryMaintenance()(adapter, facts)
    assert len(calls) == 2


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


def test_pending_shared_steel_line_is_reported_before_offering_more_capacity(tmp_path):
    record(tmp_path, 'steel', 'smelt', [ghost('electric-furnace', 1.5, 1.5)], 10)
    path = tmp_path / 'evidence/transaction-steel.json'
    receipt = json.loads(path.read_text())
    receipt.update(status='construction_pending')
    receipt['candidate'].update(product='steel-plate', shared_feed=True)
    path.write_text(json.dumps(receipt))
    adapter = SimpleNamespace(root=tmp_path)
    result = GeometryMaintenance()(adapter, {'tick': 100, 'machines': [], 'ports': [], 'poles': []})
    assert result['constructing_products'] == ['steel-plate']


def test_growth_extends_collector_and_moves_collection_to_the_new_terminal():
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
    assert bill['passive-provider-chest'] == 1
    placed = [a for p in growth['plan']['phases'] for a in p['actions']]
    from planners.smelter_block import refinery_interfaces
    endpoint = refinery_interfaces(12, origin_x=30, origin_y=20, variant='basic').provider
    assert next(a['position'] for a in placed if a['entity'] == 'passive-provider-chest') == dict(zip(('x', 'y'), endpoint))
    assert min(a['position']['x'] for a in placed if a['entity'] == 'electric-mining-drill') < 0
    assert {r['action']['entity'] for r in growth['line_transition']['retire']} == {'fast-inserter', 'passive-provider-chest'}
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
    assert m._growth(rows, facts, set(), {'line.json', 'growth.json'}, pending={'line.json'}) == []
    [second] = m._growth(rows, facts, set(), {'line.json', 'growth.json'})
    assert second['target_furnaces'] == 18
    from planners.plan_validation import validate_build_plan
    validate_build_plan(second['plan'])


def iron_rows():
    from pathlib import Path
    from training.laya_lines import line_units, mine_unit, haul, output_taps
    mine, head = mine_unit(0, 0, 3)
    original = mine + haul(head, 'east', 30, 20) + line_units('iron-plate', 1, 30, 20) + output_taps(1, 30, 20, taps=1, inserter='fast-inserter')
    parent = {'family': 'line', 'product': 'iron-plate', 'furnaces': 6, 'line_origin': [30, 20],
              'network': '1', 'network_anchor': {'x': 0, 'y': 0}, 'plan': {'phases': [{'actions': original}]}}
    return [(Path('line.json'), {'status': 'observed', 'candidate': parent, 'submission': {'ok': True}})]


def test_no_furnace_expansion_when_a_complete_drill_packet_collides():
    rows = iron_rows()
    facts = {'machines': [{'name': 'assembling-machine-1', 'position': {'x': -1.5, 'y': -1.5}}], 'ports': [], 'poles': []}
    assert GeometryMaintenance()._growth(rows, facts, set(), {'line.json'}) == []


@pytest.mark.parametrize('flow', ['east', 'west'])
def test_shared_collector_extension_preserves_the_haul_head_and_supplies_all_six_drills(flow):
    from planners.owned_line_extension import paired_collector_extension
    from training.laya_lines import mine_unit
    mine, _ = mine_unit(0, 0, 3, flow)
    drills = [a for a in mine if a['entity'] == 'electric-mining-drill']
    collector = [a for a in mine if a['entity'] == 'transport-belt']
    added = paired_collector_extension(drills, collector, 6)
    new_drills = [a for a in added if a['entity'] == 'electric-mining-drill']
    belts = [a for a in added + collector if a['entity'] == 'transport-belt']
    assert len(new_drills) == 6
    assert all(any(b['position'] == {'x': d['position']['x'], 'y': .5} and b['direction'] == flow for b in belts)
               for d in new_drills)
    expected = min if flow == 'east' else max
    assert expected(d['position']['x'] for d in new_drills) == (-7.5 if flow == 'east' else 16.5)
    assert (max if flow == 'east' else min)(b['position']['x'] for b in belts) == \
           (max if flow == 'east' else min)(b['position']['x'] for b in collector)


def test_preexisting_eight_drill_twelve_furnace_line_gets_four_drills_and_an_output_repair():
    from pathlib import Path
    from planners.smelter_block import generate_refinery_extension_plan
    from collections import Counter
    rows = iron_rows()
    delta = generate_refinery_extension_plan('iron-plate', 6, 12, origin_x=30, origin_y=20, current_variant='basic')
    added = [a for p in delta['phases'] for a in p['actions']]
    added += [ghost('electric-mining-drill', 10.5, y, d) for y, d in ((-1.5, 'south'), (2.5, 'north'))]
    broken = {**rows[0][1]['candidate'], 'growth_of': 'line.json', 'target_furnaces': 12,
              'plan': {'phases': [{'actions': added}]}}
    rows.append((Path('broken.json'), {'status': 'observed', 'candidate': broken, 'submission': {'ok': True}}))
    [repair] = GeometryMaintenance()._growth(rows, {'machines': [], 'poles': [], 'ports': []}, set(), {'line.json', 'broken.json'})
    bill = Counter(a['entity'] for p in repair['plan']['phases'] for a in p['actions'])
    assert bill['electric-mining-drill'] == 4 and bill['electric-furnace'] == 0 and bill['passive-provider-chest'] == 1
    assert repair['maintenance'] and repair['repair_of'] == 'line.json' and repair['target_furnaces'] == 12


@pytest.mark.parametrize('flow', ['south', 'west', 'north', 'east'])
def test_steel_growth_reuses_requester_and_stages_conflicting_output_tiles(flow):
    from pathlib import Path
    from planners.smelter_block import generate_requester_smelting_line
    parent = {'family': 'smelt', 'product': 'steel-plate', 'furnaces': 2, 'shared_feed': True,
              'network': '1', 'network_anchor': {'x': 0, 'y': 0},
              'plan': generate_requester_smelting_line('steel-plate', 2, origin_x=-7, origin_y=13, flow=flow)}
    parent['plan']['phases'][0]['actions'].append(ghost('medium-electric-pole', 50.5, 50.5))
    rows = [(Path('steel.json'), {'status': 'observed', 'candidate': parent, 'submission': {'ok': True}})]
    [growth] = GeometryMaintenance()._growth(rows, {'machines': [], 'poles': [], 'ports': []}, set(), {'steel.json'})
    assert growth['target_furnaces'] == 4
    additions = [a for p in growth['plan']['phases'] for a in p['actions']]
    cutover = growth['line_transition']
    assert 'requester-chest' not in [a['entity'] for a in additions + cutover['finish']]
    assert sum(a['entity'] == 'electric-furnace' for a in additions + cutover['finish']) == 2
    assert {r['action']['entity'] for r in cutover['retire']} == {'fast-inserter', 'passive-provider-chest', 'medium-electric-pole'}
    assert not any(r['action']['position']['x'] == 50.5 for r in cutover['retire'])
    assert all(a['action_type'] == 'place_ghost' for a in additions + cutover['finish'])


def test_cutover_waits_for_stocked_provider_and_observed_bot_removal_across_restart(tmp_path, monkeypatch):
    from pathlib import Path
    from planners.smelter_block import generate_requester_smelting_line
    parent = {'family': 'smelt', 'product': 'steel-plate', 'furnaces': 2, 'shared_feed': True,
              'network': '1', 'network_anchor': {'x': 0, 'y': 0}, 'plan': generate_requester_smelting_line('steel-plate', 2)}
    record(tmp_path, 'steel', 'smelt', parent['plan']['phases'][0]['actions'], 100)
    parent_path = tmp_path / 'evidence/transaction-steel.json'
    raw = json.loads(parent_path.read_text()); raw['candidate'] = parent; parent_path.write_text(json.dumps(raw))
    [growth] = GeometryMaintenance()._growth([(parent_path, raw)], {'machines': [], 'poles': [], 'ports': []}, set(), {parent_path.name})
    record(tmp_path, 'growth', 'line', growth['plan']['phases'][0]['actions'], 200)
    growth_path = tmp_path / 'evidence/transaction-growth.json'
    grown = json.loads(growth_path.read_text()); grown['candidate'] = growth; growth_path.write_text(json.dumps(grown))
    state = {'slots': 5, 'gone': set(), 'changed': False}
    from training.laya_saved_world import target_key
    all_targets = raw['construction']['targets'] + grown['construction']['targets']
    def query(*, inspect):
        wanted = {target_key(a) for p in inspect['phases'] for a in p['actions']}
        result = []
        for t in all_targets:
            if target_key(t) in wanted:
                t = dict(t)
                if t['entity'] == 'passive-provider-chest': t['inventory_slots_used'] = state['slots']
                if target_key(t) in state['gone']: t['state'] = 'missing'
                if state['changed'] and t['entity'] == 'fast-inserter': t['unit_number'] = 99999
                result.append(t)
        return {'targets': result}
    adapter = SimpleNamespace(root=tmp_path, query=query, _check_instance=lambda: None,
                              bridge=object(), surface='nauvis', force='player')
    ordered = []
    monkeypatch.setattr('tools.laya_geometry_maintenance.retire_entities_via_bots', lambda *args, **kw: ordered.extend(args[4]['phases'][0]['actions']))
    facts = {'tick': 100, 'machines': [], 'poles': [], 'ports': []}
    state['changed'] = True
    GeometryMaintenance()(adapter, facts)
    assert not ordered  # Rebuilt occupant cannot be retired under the old receipt.
    state['changed'] = False
    result = GeometryMaintenance()(adapter, facts)
    assert {a['entity'] for a in ordered} == {'fast-inserter', 'medium-electric-pole'}
    assert not result['candidates'] and result['constructing_products'] == ['steel-plate']
    state['gone'].update(target_key(a) for a in ordered)
    state['slots'] = 4
    result = GeometryMaintenance()(adapter, facts)
    assert ordered[-1]['entity'] == 'passive-provider-chest' and not result['candidates']
    result = GeometryMaintenance()(adapter, facts)
    assert not result['candidates']  # An order is not proof that the chest has been removed.
    state['gone'].update(target_key(a) for a in ordered)
    result = GeometryMaintenance()(adapter, facts)
    [finish] = result['candidates']
    assert finish['cutover_of'] == growth_path.name and finish['maintenance']
    assert all(a['action_type'] == 'place_ghost' for p in finish['plan']['phases'] for a in p['actions'])
    targets = record(tmp_path, 'finish', 'line', finish['plan']['phases'][0]['actions'], 300)
    finish_path = tmp_path / 'evidence/transaction-finish.json'
    raw_finish = json.loads(finish_path.read_text()); raw_finish['candidate'] = finish
    finish_path.write_text(json.dumps(raw_finish))
    all_targets.extend(targets)
    result = GeometryMaintenance()(adapter, facts)
    assert growth_path.name in json.loads((tmp_path / 'evidence/geometry-maintenance.json').read_text())['transitions_done']
    assert not result['constructing_products']
