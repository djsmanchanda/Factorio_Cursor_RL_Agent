# Path: tests/test_extension_search_evidence.py | Purpose: Preserve bounded extension refusals through geometry, session and journal boundaries.
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder, refinery_state
from orchestrator.operation_invocations import Invocations
from orchestrator.operation_session import DeterministicSession
from planners.plan_validation import actions
from planners.smelter_block import generate_managed_refinery_plan
from training.laya_operation_outcome import normalize
from training.laya_ops_runner import compile_options, run

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


def refinery(origin):
    plan = generate_managed_refinery_plan('iron-plate', 30, origin_x=origin[0], origin_y=origin[1])
    furnaces = tuple(sorted((a['position']['x'], a['position']['y']) for a in actions(plan)
                            if a.get('entity') == 'electric-furnace'))
    return refinery_state.infer_refinery_state('iron-plate', furnaces)


@pytest.mark.parametrize('origin', [(0, 0), (137, -89)])
def test_real_extension_validator_reports_only_the_tested_candidate(monkeypatch, origin):
    state = refinery(origin)
    plan = builder.generate_managed_refinery_extension_plan(
        'iron-plate', 30, 36, origin_x=origin[0], origin_y=origin[1],
        current_variant=state.variant, target_variant=state.variant,
        vertical_mirror=state.vertical_mirror)
    tile = sorted(builder.planned_footprint_tiles(plan) - builder._planned_removal_tiles(plan))[0]
    owners = {tile: ('medium-electric-pole', tile[0] + .5, tile[1] + .5)}
    before = copy.deepcopy(owners)
    monkeypatch.setattr(builder, 'assert_refinery_removals_owned', lambda *a: None)
    monkeypatch.setattr(builder, 'live_refinery_placements', lambda *a: set())
    monkeypatch.setattr(builder.live_base, 'occupied_tile_owners', lambda *a: owners)
    monkeypatch.setattr(builder, 'assert_affordable', lambda *a: pytest.fail('collision reached material admission'))
    with pytest.raises(builder.StuckError) as caught:
        builder._assert_atomic_plate_expansion_affordable(
            SimpleNamespace(command=lambda *a: pytest.fail('unmocked survey')), 'nauvis', 'player',
            'iron-plate', SimpleNamespace(build_plan=None), state, 36, lambda *a: None)
    error = caught.value
    assert error.code == 'managed_extension_infrastructure_conflict'
    assert error.details['collision_tiles'] == [list(tile)]
    assert error.details['search_coverage'] == {
        'scope': 'standing_managed_refinery_delta', 'origin': list(origin), 'variant': state.variant,
        'vertical_mirror': state.vertical_mirror, 'current_furnaces': 30, 'target_furnaces': 36,
        'candidates_tested': 1}
    assert owners == before


COVERAGE = {'scope': 'standing_managed_refinery_delta', 'origin': [0., 0.], 'variant': 'standard',
            'vertical_mirror': False, 'current_furnaces': 30, 'target_furnaces': 36, 'candidates_tested': 1}
DETAILS = {'search_coverage': COVERAGE, 'first_validator_refusal': 'occupied_infrastructure',
           'collision_tiles': [[100, 50]]}
STARTED = [{'ok': True, 'operation': 'foundation', 'params': {'recipe': plate}, 'code': 'submitted'}
           for plate in ('iron-plate', 'copper-plate')]
BOARD = {'needs': [{'item': 'iron-plate', 'condition': 'unbuilt', 'goal_required_per_s': .5}]}


def blocked_session(tmp_path, monkeypatch):
    state = refinery((0, 0))
    extraction = SimpleNamespace(build_plan=None, drill_count=6, furnace_count=6,
        mining_productivity_bonus=0., ore_output=(10., 20.), smelter_flow_direction='east',
        system_drill_count_before=6, system_drill_target=12, ore='iron-ore', smelter_origin=(30., 30.))
    monkeypatch.setattr(builder, '_bootstrap_state', lambda *a: None)
    monkeypatch.setattr(builder, '_independent_mall_ready', lambda *a: False)
    monkeypatch.setattr(builder, '_electric_furnace_producer_started', lambda *a: True)
    monkeypatch.setattr(builder.live_base, 'available_items', lambda *a: {})
    monkeypatch.setattr(builder, 'retire_depleted_mines', lambda *a: None)
    monkeypatch.setattr(builder, 'plan_local_extraction', lambda *a, **k: extraction)
    monkeypatch.setattr(builder, '_cohesive_smelter_target', lambda *a: (state, 36))
    def refusal(*a, **k):
        # Classification uses the typed code, independent of human message text.
        raise builder.StuckError('a foreign pole blocks this candidate',
            code='managed_extension_infrastructure_conflict', details=copy.deepcopy(DETAILS))
    monkeypatch.setattr(builder, '_assert_atomic_plate_expansion_affordable', refusal)
    monkeypatch.setattr(builder, '_submit_mining_plan', lambda *a, **k: pytest.fail('mine grew after refusal'))
    session = DeterministicSession.__new__(DeterministicSession)
    session.invocations = Invocations(tmp_path, 'extension-evidence')
    session.client = object()
    session.bridge = SimpleNamespace(build_layout=lambda *a, **k: pytest.fail('refusal dispatched a build'))
    session._maintain_mall = lambda: None
    session.economics = lambda: None
    session._operations = lambda: {'extend': lambda recipe: builder.build_mining_stage(
        session.client, session.bridge, 'nauvis', 'player', recipe, (0., 0.), lambda *a: None, expand=True)}
    return session


def test_typed_conflict_preserves_deferral_and_reaches_actual_menu_and_journals(tmp_path, monkeypatch):
    session = blocked_session(tmp_path, monkeypatch)
    result = session.run('extend', recipe='iron-plate')
    assert result['code'] == 'production_prerequisite_deferred'
    assert normalize(result)['state'] == 'deferred'
    assert result['details'] == {**DETAILS, 'refusal_code': 'managed_extension_infrastructure_conflict'}
    result['tick'] = 100

    class World:
        root = None
        tick = 1000
        def query(self):
            return {'tick': self.tick, 'production': {}, 'consumption': {}, 'machines': [], 'networks': {},
                    'ports': [], 'resources': [], 'recipes': {}}
        def advance(self, ticks):
            self.tick += ticks

    class Policy:
        authority = 'test'
        def choose(self, state, menu):
            selected = next(o for o in menu['options'] if o['option_id'] == 'op.extend.iron-plate')
            return {**selected, 'probability': 1., 'rules': [], 'distribution': []}

    journal = tmp_path / 'decisions.jsonl'
    summary = run(World(), Policy(), journal=journal, decisions=2, measure_ticks=600, seed=1, run_id='bounded',
                  session=session, carried=STARTED + [result])
    decisions = [json.loads(row) for row in journal.read_text().splitlines()]
    selected = next(row for row in decisions if row.get('event') == 'decision')
    fact = selected['extension_search']
    assert fact['state'] == 'last_attempt_unrevalidated' and fact['tick'] == 100
    assert fact['search_coverage'] == COVERAGE
    records = [json.loads(row) for row in (tmp_path / 'decision-states.jsonl').read_text().splitlines()]
    option = next(o for o in records[0]['options'] if o['option_id'] == 'op.extend.iron-plate')
    assert option['extension_search'] == fact
    assert records[0]['outcome']['typed_outcome']['state'] == 'deferred'
    assert summary['decisions'] == 2


def test_later_success_or_unknown_clears_stale_menu_refusal():
    refusal = {'ok': False, 'operation': 'extend', 'params': {'recipe': 'iron-plate'},
               'code': 'production_prerequisite_deferred', 'tick': 100,
               'details': {**DETAILS, 'refusal_code': 'managed_extension_infrastructure_conflict'}}
    initial = compile_options(BOARD, STARTED + [refusal])
    fact = next(o for o in initial['options'] if o['option_id'] == 'op.extend.iron-plate')['extension_search']
    assert fact['search_coverage'] == COVERAGE
    fact['search_coverage']['origin'][0] = 999
    assert refusal['details']['search_coverage']['origin'][0] == 0
    for outcome in ({'ok': True, 'code': 'submitted'}, {'ok': False, 'code': 'survey_unavailable'}):
        latest = {**outcome, 'operation': 'extend', 'params': {'recipe': 'iron-plate'}}
        menu = compile_options(BOARD, STARTED + [refusal, latest])
        assert 'extension_search' not in next(o for o in menu['options'] if o['option_id'] == 'op.extend.iron-plate')
