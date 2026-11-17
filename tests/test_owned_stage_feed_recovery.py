# Path: tests/test_owned_stage_feed_recovery.py | Purpose: Keep selected forced-belt feed identity through interrupted construction and recovery.
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab, live_base, stage_recovery as recovery
from orchestrator.operation_invocations import Invocations, ReadbackRequired
from planners.local_layout_planner import LocalLayoutPlanner

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]
RECIPE = 'copper-cable'
MACHINES = [(-10.5, 26.5), (-7.5, 26.5)]
SOURCE = (20.5, 30.5)


def select(tmp_path, *, belt=True, tick=100, invocations=None, count=2,
           origin=(-12, 23), feed_style='chest', inserter_type='fast-inserter'):
    invocations = invocations or Invocations(tmp_path, 'episode')
    plan = LocalLayoutPlanner().generate_line_layout(RECIPE, count, *origin,
        feed_style=feed_style, terminal_collector=True, inserter_type=inserter_type)
    modes, feeds, _, _ = ab._conversion_feed_plan(object(), object(), 'nauvis', 'player',
        RECIPE, plan, {'copper-plate': SOURCE}, count, 'transport-belt', 'east', lambda _: None,
        allow_logistic_inputs=False, max_belt_route_tiles=None, force_belt_inputs=belt)
    plan.update(surface='nauvis', force='player')
    record = invocations.begin('production_service', {'item': 'productivity-module'})
    path = tmp_path / f'ack-{tick}.json'
    path.write_text(json.dumps({'ok': True, 'tick': tick, 'failed_placements': 0, 'recipe_failures': 0}))
    invocations.submit(record, lambda *_: path, {}, plan)
    record.update(finished=True, outcome={'code': 'material_shortage'})
    invocations.save(record)
    return invocations, record, feeds['copper-plate'], modes


@pytest.mark.parametrize('belt', [True, False])
def test_selected_override_survives_shortage_and_journal_reopen(tmp_path, monkeypatch, belt):
    inv, _, feed, modes = select(tmp_path, belt=belt)
    assert recovery._transport_mode(RECIPE, 'copper-plate', 2) == 'logistic'
    expected = 'steel-chest' if belt else 'requester-chest'
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: {'name': expected})
    calls = []
    monkeypatch.setattr(recovery, 'ensure_ingredient_transport', lambda *a, **kw: calls.append((a, kw)) or 12)
    monkeypatch.setattr(recovery, '_diagnose_machines', lambda *a, **kw: [])
    assert recovery.repair_existing_ingredient_transport(object(),
        SimpleNamespace(operation_invocations=Invocations(tmp_path, 'episode')),
        'nauvis', 'player', RECIPE, MACHINES, lambda _: SOURCE, lambda _: None)
    args, kwargs = calls[0]
    assert args[4:9] == (RECIPE, 'copper-plate', SOURCE, feed, 2)
    assert kwargs == {'reuse_existing': True, 'mode': modes['copper-plate']}
    assert len(calls) == 1 and len(list(inv.directory.glob('*.json'))) == 1


@pytest.mark.parametrize('belt,found', [(True, 'requester-chest'), (True, 'wooden-chest'),
                                       (False, 'steel-chest')])
def test_live_chest_cannot_choose_another_mode(tmp_path, monkeypatch, belt, found):
    inv, _, _, _ = select(tmp_path, belt=belt)
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: {'name': found})
    with pytest.raises(recovery.StuckError, match='refusing to guess a different endpoint'):
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, MACHINES,
            lambda _: pytest.fail('reject before any upstream mutation'), lambda _: None)


@pytest.mark.parametrize('kind', ['missing_ack', 'conflict', 'superseded', 'wrong_row', 'other_episode', 'wrong_scope'])
def test_unknown_conflicting_or_superseded_contract_never_dispatches(tmp_path, monkeypatch, kind):
    inv, record, _, _ = select(tmp_path)
    machines = MACHINES
    if kind == 'missing_ack':
        Path(record['submissions'][0]['receipt']).unlink()
        record['submissions'][0]['report'] = None
        inv.save(record)
    if kind == 'conflict':select(tmp_path, belt=False, tick=110, invocations=inv)
    if kind == 'superseded':
        replacement = inv.begin('produce', {'item': 'iron-gear-wheel'})
        plan = {'surface': 'nauvis', 'force': 'player', 'phases': [{'name': 'retarget', 'actions': [{
            'action_type': 'configure_entity', 'entity': 'assembling-machine-1',
            'position': dict(zip(('x', 'y'), MACHINES[0])), 'recipe': 'iron-gear-wheel'}]}]}
        ack = tmp_path / 'retarget.json'
        ack.write_text(json.dumps({'ok': True, 'tick': 120, 'failed_placements': 0, 'recipe_failures': 0}))
        inv.submit(replacement, lambda *_: ack, {}, plan)
    if kind == 'wrong_row':machines = [(x + 30, y) for x, y in MACHINES]
    if kind == 'other_episode':inv = Invocations(tmp_path, 'another-episode')
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: pytest.fail('no selected contract, no live fallback'))
    with pytest.raises(recovery.StuckError) as error:
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'other-surface' if kind == 'wrong_scope' else 'nauvis', 'player',
            RECIPE, machines, lambda _: SOURCE, lambda _: None)
    assert error.value.code == 'stage_feed_contract_unknown'


def test_uncertain_dispatch_stays_fenced(tmp_path):
    inv, record, _, _ = select(tmp_path)
    pending = inv.begin('production_service', {})
    with pytest.raises(KeyboardInterrupt):
        inv.submit(pending, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()), {}, record['submissions'][0]['plan'])
    with pytest.raises(ReadbackRequired):
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, MACHINES, lambda _: SOURCE, lambda _: None)


def test_endpoint_is_rechecked_after_upstream_work(tmp_path, monkeypatch):
    inv, _, _, _ = select(tmp_path)
    live = {'name': 'steel-chest'}
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: live)
    monkeypatch.setattr(recovery, 'ensure_ingredient_transport', lambda *a, **kw: pytest.fail('changed endpoint'))
    def source(_):
        live['name'] = 'requester-chest'
        return SOURCE
    with pytest.raises(recovery.StuckError, match='expected steel-chest.*found requester-chest'):
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, MACHINES, source, lambda _: None)


def change_feed(tmp_path, inv, point, *, entity='requester-chest', action_type='configure_entity', **settings):
    record = inv.begin('production_service', {})
    plan = {'surface': 'nauvis', 'force': 'player', 'phases': [{'name': 'feed_update', 'actions': [{
        'action_type': action_type, 'entity': entity,
        'position': dict(zip(('x', 'y'), point)), **settings}]}]}
    ack = tmp_path / 'feed-update.json'
    ack.write_text(json.dumps({'ok': True, 'tick': 110, 'failed_placements': 0, 'recipe_failures': 0}))
    inv.submit(record, lambda *_: ack, {}, plan)


@pytest.mark.parametrize('count,style,tier', [(5, 'sideload', 'fast-inserter'),
                                           (3, 'chest', 'bulk-inserter')])
def test_unsupported_constructor_topology_cannot_choose_coincident_chest(tmp_path, monkeypatch, count, style, tier):
    inv, _, _, _ = select(tmp_path, count=count, origin=(0, 0), feed_style=style, inserter_type=tier)
    machines = [(1.5 + 3 * i, 3.5) for i in range(count)]
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: pytest.fail('reject unsupported topology before live work'))
    with pytest.raises(recovery.StuckError) as error:
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, machines,
            lambda _: pytest.fail('must not ensure source'), lambda _: None)
    assert error.value.code == 'stage_feed_contract_unknown'


@pytest.mark.parametrize('change', ['removed', 'direction'])
def test_feeder_topology_supersession_rejects_before_source_work(tmp_path, monkeypatch, change):
    inv, _, feed, _ = select(tmp_path)
    change_feed(tmp_path, inv, (feed[0], feed[1] + 1), entity='fast-inserter',
                action_type='remove_entity' if change == 'removed' else 'configure_entity',
                **({} if change == 'removed' else {'direction': 'south'}))
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: pytest.fail('topology no longer owned'))
    with pytest.raises(recovery.StuckError) as error:
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, MACHINES, lambda _: SOURCE, lambda _: None)
    assert error.value.code == 'stage_feed_contract_unknown'


@pytest.mark.parametrize('during_upstream', [False, True])
@pytest.mark.parametrize('request_field', ['logistic_request', 'logistic_requests', 'logistic_sections'])
def test_wrong_request_ingredient_rejected_before_transport(tmp_path, monkeypatch, during_upstream, request_field):
    inv, _, feed, _ = select(tmp_path, belt=False)
    def retarget():
        request = {'name': 'iron-plate', 'count': 200}
        setting = request if request_field == 'logistic_request' else [request]
        if request_field == 'logistic_sections':
            setting = [{'group': 'retarget', 'requests': [request]}]
        change_feed(tmp_path, inv, feed, **{request_field: setting})
    if not during_upstream:
        retarget()
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: {'name': 'requester-chest'})
    monkeypatch.setattr(recovery, 'ensure_ingredient_transport', lambda *a, **kw: pytest.fail('wrong requested ingredient'))
    def source(_):
        assert during_upstream, 'reject prior supersession before upstream work'
        retarget()
        return SOURCE
    with pytest.raises(recovery.StuckError) as error:
        recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
            'nauvis', 'player', RECIPE, MACHINES, source, lambda _: None)
    assert error.value.code == 'stage_feed_contract_unknown'


def test_compatible_request_count_and_cap_update_preserves_selected_feed(tmp_path, monkeypatch):
    inv, _, feed, _ = select(tmp_path, belt=False)
    change_feed(tmp_path, inv, feed, logistic_request={'name': 'copper-plate', 'count': 50},
                inventory_limit={'name': 'copper-plate', 'count': 150, 'minimum_stacks_by_group': {
                    'production': 1, 'logistics': 1, 'intermediate-products': 1}})
    monkeypatch.setattr(live_base, 'entity_at', lambda *_: {'name': 'requester-chest'})
    calls = []
    monkeypatch.setattr(recovery, 'ensure_ingredient_transport', lambda *a, **kw: calls.append((a, kw)) or 0)
    monkeypatch.setattr(recovery, '_diagnose_machines', lambda *a, **kw: [])
    assert recovery.repair_existing_ingredient_transport(object(), SimpleNamespace(operation_invocations=inv),
        'nauvis', 'player', RECIPE, MACHINES, lambda _: SOURCE, lambda _: None)
    assert calls[0][0][7] == feed and calls[0][1]['mode'] == 'logistic'
