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


# --- C96d: a research target already complete in the save is refused before any decision ---
C96D_SAVE = {"source_save": "/cohort/laya-c96d-c0-science3-2x-20261112/inputs/source.zip",
             "source_sha256": "347eba65065014f82261330d84d5ca2fa3e3427cad3ab18dcfdf496dd9c442b0"}


def _prepared_root(tmp_path, researched):
    import json
    (tmp_path / 'manifest.json').write_text(json.dumps(C96D_SAVE))
    (tmp_path / 'initial-state.json').write_text(json.dumps({'tick': 238534, 'researched': researched}))
    return tmp_path


def test_completed_target_refusal_reads_only_the_exact_recorded_name():
    from training.laya_mission_capabilities import completed_target_refusal
    # Recorded C96d initial-state.json: `researched` lists researched prototypes only, all true.
    state = {'tick': 238534, 'researched': {'advanced-oil-processing': True, 'mining-productivity-2': True}}
    message = completed_target_refusal('advanced-oil-processing', state, C96D_SAVE)
    assert "'advanced-oil-processing'" in message and C96D_SAVE['source_save'] in message and '347eba650650' in message
    assert completed_target_refusal('night-vision-equipment', state, C96D_SAVE) is None
    # A ranged level has no entry of its own; it is never inferred from a neighbouring name.
    assert completed_target_refusal('mining-productivity-3', state, C96D_SAVE) is None
    assert completed_target_refusal('advanced-oil-processing', {'tick': 1}, C96D_SAVE) is None


def test_mixed_runner_refuses_completed_target_before_starting(tmp_path, monkeypatch, capsys):
    from tools import run_laya_mixed
    monkeypatch.setattr(run_laya_mixed, 'owned_root', lambda path: path)
    monkeypatch.setattr(run_laya_mixed, 'LiveWorld', lambda *_a, **_k: pytest.fail('world opened'))
    root = _prepared_root(tmp_path, {'advanced-oil-processing': True})
    status = run_laya_mixed.main(['run', '--root', str(root), '--actions', 'deterministic', '--no-helper',
                                  '--objective-mode', 'research_completion',
                                  '--research-target', 'advanced-oil-processing'])
    assert status == run_laya_mixed.RESEARCH_TARGET_COMPLETE_EXIT
    err = capsys.readouterr().err
    assert 'MISSION REFUSED' in err and 'advanced-oil-processing' in err and C96D_SAVE['source_save'] in err
    assert not (root / 'evidence').exists()


def test_ops_runner_refuses_inherited_target_at_first_observation(tmp_path, monkeypatch):
    """Ranged or unrecorded targets reach the runner; its first live observation refuses them."""
    import dataclasses
    import json
    from training.laya_clean_runner import Settings
    from training.laya_guided_policy import GuidedPolicy
    from training.laya_ops_runner import run
    from tests.test_laya_research import reports
    from tests.test_laya_ops_runner import FakeSession, FakeWorld

    class World(FakeWorld):
        root = _prepared_root(tmp_path, {})

        def query(self):
            return {**super().query(), 'surface': 'nauvis', 'force': 'player', 'mods': {'base': '2.1.24'}}

    world = World()
    monkeypatch.setattr('orchestrator.research_operations.collect_research_reports',
                        lambda *_a, **_k: reports(world.tick, complete=True))
    session = FakeSession()
    session.bridge = None
    journal = tmp_path / 'evidence' / 'journal.jsonl'
    summary = run(world, GuidedPolicy(1), journal=journal, decisions=5, measure_ticks=600, seed=1,
                  run_id='refuse', session=session, objective_mode='research_completion',
                  settings=dataclasses.replace(Settings(), research_target='mining-productivity-4'))
    assert summary['stop'] == 'research_target_already_complete' and summary['decisions'] == 0
    assert 'mining-productivity-4' in summary['refusal'] and C96D_SAVE['source_save'] in summary['refusal']
    assert session.calls == [] and not (journal.parent / 'decision-states.jsonl').exists()
    events = [json.loads(line)['event'] for line in journal.read_text().splitlines()]
    assert 'mission_refused' in events
