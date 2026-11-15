# Path: tests/test_checkpoint_evidence_contract.py | Purpose: Exercise versioned retained-observation evidence at runner and sidecar boundaries.
from types import SimpleNamespace

import pytest

from orchestrator.checkpoint_milestones import FactorySnapshot
from orchestrator.checkpoint_evidence import project_evidence
from tools.laya_strict_sidecar import Measurement
from training.laya_segment_evidence import current_predicates

pytestmark = pytest.mark.fast


def sample(tick, craft, consumer=None):
    return FactorySnapshot.from_mapping({"tick": tick, "production": {"plastic-bar": {"craft_count": craft}},
        "providers": {"plastic-bar": {"delivered": craft}}, "ac_consumers": consumer})


def test_runner_and_sidecar_share_projection_with_flat_recovery_and_same_tick():
    samples = [sample(0, 1), sample(7200, 2), sample(7200, 2), sample(8000, 2), sample(9000, 3)]
    measurement = Measurement({})
    for item in samples:
        row = measurement.add(item)
        current, evidence = current_predicates(SimpleNamespace(_baseline=samples[0], _history=measurement.history))
        assert current == row['statuses']
    _, evidence = current_predicates(SimpleNamespace(_baseline=samples[0], _history=samples, _evidence_state=measurement.evidence_state))
    assert evidence['_contract'] == row['predicate_evidence']['_contract']
    intervals = evidence['_contract']['sampled_plastic']['intervals']
    assert [x['craft_delta'] for x in intervals] == [1, 0, 1]
    assert row['predicate_evidence']['C5']['predicate_version'] == 'c5-v1'
    assert evidence['_contract']['coverage']['complete_history'] is False


def test_consumer_identity_and_missing_observations_do_not_invent_events():
    def machine(unit, recipe, count):
        return [{'unit': unit, 'recipe': recipe, 'count': count}]
    samples = [sample(0, 0, machine(1, 'bulk-inserter', 50)),
               sample(1, 0, machine(1, 'productivity-module', 51)),
               sample(2, 0, machine(1, 'productivity-module', 52)),
               sample(3, 0, None), sample(4, 0, machine(1, 'productivity-module', 60)),
               sample(5, 0, machine(1, 'productivity-module', 1)),
               sample(6, 0, []), sample(7, 0, machine(2, 'bulk-inserter', 70)),
               sample(8, 0, machine(2, 'bulk-inserter', 71))]
    events = project_evidence(samples[0], samples)['consumer_events']
    assert [(e['unit'], e['kind'], e['count']-e['from_count']) for e in events] == [(1, 'productivity_module', 1), (2, 'generic_consumer', 1)]
    assert project_evidence(samples[0], samples)['inherited_start']['provenance'] == 'first_observation_not_earned'


def test_logistic_chests_and_invalid_counters_are_not_consumer_events():
    before = sample(0, 0, [{'unit': 1, 'recipe': 'requester-chest', 'count': 1},
                           {'unit': 2, 'recipe': 'productivity-module', 'count': -1}])
    after = sample(1, 0, [{'unit': 1, 'recipe': 'requester-chest', 'count': 2},
                          {'unit': 2, 'recipe': 'productivity-module', 'count': 1}])
    assert project_evidence(before, [before, after])['consumer_events'] == []


def test_synthetic_starter_retirement_keeps_start_separate_from_current():
    mall = [{'recipe': recipe, 'working': True} for recipe in ('iron-gear-wheel', 'copper-cable', 'electronic-circuit', 'transport-belt')]
    start = FactorySnapshot.from_mapping({'tick': 0, 'mall_assemblers': mall, 'starters': {k: {'present': True, 'healthy': True, 'producing': True} for k in ('iron', 'copper', 'stone')}})
    later = FactorySnapshot.from_mapping({'tick': 100, 'mall_assemblers': mall, 'starters': {k: {'present': False} for k in ('iron', 'copper', 'stone')}})
    result = project_evidence(start, [start, later])
    assert result['inherited_start']['predicates']['C1']['status'] == 'passed'
    assert result['present']['C1']['status'] == 'failed'
    assert not any(e['checkpoint'] == 'C1' for e in result['observed_events'])


def test_real_session_failure_restart_and_sidecar_equivalence(tmp_path, monkeypatch):
    import copy
    from orchestrator.operation_session import DeterministicSession
    from orchestrator.checkpoint_observer import ObservationUnsupported
    from orchestrator.checkpoint_restart_state import import_restart_state
    from orchestrator import checkpoint_observer
    source = tmp_path / 'source' / 'script-output'
    target = tmp_path / 'target' / 'script-output'
    session = DeterministicSession.__new__(DeterministicSession)
    session.client, session.bridge, session.reference_point = None, None, (0, 0)
    session.script_output, session.episode_id = source, 'episode'
    queue = []
    class Adapter:
        def __init__(self, *args, **kwargs): pass
        def observe(self):
            item = queue.pop(0)
            if isinstance(item, Exception): raise item
            return SimpleNamespace(snapshot=item)
    monkeypatch.setattr(checkpoint_observer, 'LiveCheckpointObservationAdapter', Adapter)
    measurement = Measurement({})
    def poll(item):
        queue.append(item)
        session.acceptance()
        if isinstance(item, Exception): measurement.failure()
        else: measurement.add(item)
        statuses, evidence = current_predicates(session)
        assert evidence['_contract'] == measurement.summary('running')['evidence']
        if isinstance(item, Exception): assert all(v is None for v in statuses.values())
    consumer = lambda n: [{'unit': 7, 'recipe': 'productivity-module', 'count': n}]
    a = sample(0, 1, consumer(1))
    poll(a)
    poll(a)
    poll(copy.deepcopy(a))
    poll(sample(7200, 2, consumer(2)))
    assert session.acceptance_fresh['C5'] == 'passed'
    poll(ObservationUnsupported('missing'))
    assert current_predicates(session)[1]['_contract']['present'] is None
    bundle = session.export_restart_state(mod_scope='mods')
    state = import_restart_state(bundle, target, episode_id='episode', mod_scope='mods')
    reopened = DeterministicSession.__new__(DeterministicSession)
    reopened.client, reopened.bridge, reopened.reference_point = None, None, (0, 0)
    reopened.script_output, reopened.episode_id = target, 'episode'
    reopened.restore_restart_acceptance(state)
    next_sample = sample(8000, 3, consumer(3))
    queue.extend([next_sample, next_sample])
    session.acceptance(); reopened.acceptance()
    assert session._evidence_state == reopened._evidence_state
    assert session._evidence_state['failed_polls'] == 1
    assert any(x['reason'] == 'gap' for x in session._evidence_state['boundaries'])
    assert next(iter(session._evidence_state['consumer_events'].values()))['kind'] == 'productivity_module'
    for tick in range(8001, 8023):
        queue.append(sample(tick, tick, consumer(tick)))
        session.acceptance()
    assert len(session._history) == 20
    assert 'C4' in session._evidence_state['events']


def test_legacy_and_corrupt_restart_preflight(tmp_path):
    import copy
    from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state
    from orchestrator.checkpoint_milestones import CheckpointEvaluator
    start = sample(0, 0)
    evaluator = CheckpointEvaluator(); evaluator.reached.add('C0')
    bundle = export_restart_state(tmp_path / 'source', 'episode', 'mods', start, [start], evaluator)
    legacy = copy.deepcopy(bundle); legacy['version'] = 1; del legacy['evidence_state']
    del legacy['sidecars']['operation_invocations']
    state = import_restart_state(legacy, tmp_path / 'legacy', episode_id='episode', mod_scope='mods')
    assert state.evaluator.reached == {'C0'} and state.evidence_state['origin'] == 'unknown'
    broken = copy.deepcopy(bundle); broken['evidence_state']['events']['C0'] = {'bad': True}
    target = tmp_path / 'bad'
    with pytest.raises(ValueError): import_restart_state(broken, target, episode_id='episode', mod_scope='mods')
    assert not target.exists()


def test_accumulator_consumer_boundaries_and_sampled_deltas():
    m = Measurement({})
    def row(unit, recipe, count): return [{'unit': unit, 'recipe': recipe, 'count': count}]
    for snapshot in [sample(0, 1, row(1, 'bulk-inserter', 1)),
                     sample(1, 2, row(1, 'bulk-inserter', 2)),
                     sample(2, 3, row(1, 'productivity-module', 50)),
                     sample(3, 4, row(1, 'productivity-module', 1)),
                     sample(4, 5, []), sample(5, 6, row(1, 'productivity-module', 7)),
                     sample(6, 7, None), sample(7, 8, row(1, 'productivity-module', 8))]:
        m.add(snapshot)
    state = m.evidence_state
    assert state['consumer_events']['1:0']['kind'] == 'generic_consumer'
    assert state['consumer_events']['1:0']['count'] == 2
    assert {'retarget', 'reset', 'removal', 'appearance', 'missing_consumer_telemetry'} <= {b['reason'] for b in state['boundaries']}
    assert state['consumers']['1']['generation'] >= 3


def test_later_pm_recipe_generation_retains_its_own_consumption_event():
    measurement = Measurement({})
    for tick, recipe, count in [(0, 'bulk-inserter', 1), (1, 'bulk-inserter', 2),
                                (2, 'productivity-module', 10), (3, 'productivity-module', 11)]:
        measurement.add(sample(tick, tick, [{'unit': 1, 'recipe': recipe, 'count': count}]))
    events = measurement.summary('stopped')['evidence']['consumer_events']
    assert [(e['kind'], e['generation']) for e in events] == [('generic_consumer', 0), ('productivity_module', 1)]


def test_clock_regression_breaks_consumer_generation_and_proxy_remains_explicit():
    measurement = Measurement({})
    for tick, count in [(100, 1), (90, 2), (110, 3)]:
        measurement.add(sample(tick, count, [{'unit': 1, 'recipe': 'productivity-module', 'count': count}]))
    assert any(x['reason'] == 'clock_discontinuity' for x in measurement.evidence_state['boundaries'])
    assert measurement.evidence_state['consumer_events']['1:1']['from_tick'] == 90
    session = SimpleNamespace(_baseline=measurement.baseline, _history=measurement.history)
    proxy = {'version': 'laya-runner-phase-v1', 'reached': ['C0']}
    assert current_predicates(session, proxy_phase=proxy)[1]['_contract']['proxy_phase'] == proxy
