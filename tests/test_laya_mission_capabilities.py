# Path: tests/test_laya_mission_capabilities.py | Purpose: Exercise mission capability, observed eligibility and research menu offline.
from training.laya_mission_capabilities import FAMILIES, MANIFEST, initial_eligibility
from training.laya_research import ResearchObjective, add_research_options
from orchestrator.operation_session import DeterministicSession
from tests.test_laya_research import pair
import pytest

pytestmark = pytest.mark.fast


def test_manifest_describes_executable_operations():
    session = object.__new__(DeterministicSession)
    session.client = session.bridge = session.reference_point = session.emit = None
    operations = session._operations()
    assert set(FAMILIES) <= operations.keys()
    assert MANIFEST['runners']['tools/run_laya_mixed.py']['operation_families'] == FAMILIES


def test_observed_target_eligibility_and_menu():
    observation, facts = pair(100)
    assert initial_eligibility(observation, 'mining-productivity-4', facts)['state'] == 'eligible'
    assert initial_eligibility({'available': False}, 'mining-productivity-4', facts)['state'] == 'unknown'
    inherited, _ = pair(100, complete=True)
    assert initial_eligibility(inherited, 'mining-productivity-4', facts)['state'] == 'inherited'
    observation['current'] = None
    observation['queue'] = []
    observation['labs']['total'] = 0
    observation['labs']['working'] = 0
    observation['selection_candidates'] = [{'technology':'mining-productivity-4', 'canonical_name':'mining-productivity-3'}]
    menu = add_research_options({'options': []}, {'targets': {'automation-science-pack': .25}, 'needs': []}, observation, facts, ResearchObjective())
    assert {'research_select', 'research_labs'} <= {option['family'] for option in menu['options']}


def test_unknown_or_inherited_initial_state_never_becomes_earned():
    from training.laya_mission_capabilities import observe_start_and_research
    for initial in ({'available': False}, pair(0, complete=True)[0]):
        obj = ResearchObjective()
        _, facts = pair(0)
        first, eligibility, _ = observe_start_and_research(obj, initial, facts, target='mining-productivity-4', service_ok=True, clean=True)
        assert first and eligibility['state'] in {'unknown', 'inherited'}
        observation, facts = pair(100, made=100, consumed=100, progress=.5, complete=True)
        _, again, evidence = observe_start_and_research(obj, observation, facts, target='mining-productivity-4', service_ok=True, clean=True)
        assert again == eligibility and not evidence['earned']
        assert evidence['reason'] == obj.brief()['reason']


@pytest.mark.parametrize('mutation', ['stale', 'scope'])
def test_start_eligibility_requires_fresh_scoped_evidence(mutation):
    observation, facts = pair(100)
    if mutation == 'stale':
        observation['tick'] = 99
    else:
        facts['force'] = 'other'
    assert initial_eligibility(observation, 'mining-productivity-4', facts)['state'] == 'unknown'


def test_eligible_observed_completion_and_eligibility_survive_restore():
    from training.laya_mission_capabilities import ELIGIBILITY_VERSION, observe_start_and_research
    objective = ResearchObjective()
    observation, facts = pair(0)
    observe_start_and_research(objective, observation, facts, target='mining-productivity-4', service_ok=True, clean=True)
    restored = ResearchObjective(state=objective.snapshot())
    observation, facts = pair(100, made=100, consumed=100, complete=True)
    first, eligibility, evidence = observe_start_and_research(restored, observation, facts, target='mining-productivity-4', service_ok=True, clean=True)
    assert not first and eligibility['state'] == 'eligible' and evidence['earned']
    assert evidence['eligibility_version'] == ELIGIBILITY_VERSION


@pytest.mark.parametrize('mode,target', [('research_completion', None), ('measurement_only', 'mining-productivity-4')])
def test_runner_rejects_contradictory_mission_before_opening_a_session(tmp_path, mode, target):
    import dataclasses
    from training.laya_clean_runner import Settings
    from training.laya_ops_runner import run
    with pytest.raises(ValueError):
        run(object(), object(), journal=tmp_path/'journal.jsonl', decisions=1, measure_ticks=600,
            seed=1, run_id='mission', objective_mode=mode,
            settings=dataclasses.replace(Settings(), research_target=target))
    assert not (tmp_path/'journal.jsonl').exists()
