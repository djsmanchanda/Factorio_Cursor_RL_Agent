# Path: tests/test_laya_mission_contract.py | Purpose: Verify frozen runner capability and mission admission offline.
from pathlib import Path
import sys
import pytest
from training.laya_mission_contract import frozen_capability, mission_settings

pytestmark = pytest.mark.fast
def frozen_runner(tmp_path):
    code = tmp_path / 'code'
    (code / 'tools').mkdir(parents=True)
    (code / 'training').mkdir()
    (code / 'tools/run_laya_mixed.py').write_text('# fixture runner\n')
    (code / 'training/laya_mission_capabilities.py').write_text(
        "import json; print(json.dumps({'version': 1, 'runners': {'tools/run_laya_mixed.py': "
        "{'objective_modes': ['measurement_only', 'research_completion'], 'actions': 'deterministic', "
        "'operation_families': ['research_select', 'research_labs', 'scale_production']}}}))")
    return code


def test_frozen_manifest_admits_only_selected_runner(tmp_path):
    PM = frozen_runner(tmp_path)
    spec = {'objective_mode': 'research_completion', 'research_target': 'mining-productivity-4', 'arms': {'a': {}}}
    cap = frozen_capability(PM, 'tools/run_laya_mixed.py', sys.executable)
    effective = mission_settings(spec, 'a', capability=cap)
    assert {'research_select', 'research_labs', 'scale_production'} <= set(effective['operation_families'])
    for runner in ('tools/run_laya_clean.py', 'missing.py', '../pm/tools/run_laya_mixed.py'):
        with pytest.raises(ValueError):
            mission_settings(spec, 'a', capability=frozen_capability(PM, runner, sys.executable))


def test_explicit_and_legacy_missions():
    with pytest.raises(ValueError, match='research_target'):
        mission_settings({'objective_mode':'research_completion', 'arms':{'a':{}}}, 'a')
    spec = {'objective_mode':'measurement_only', 'arms':{'a':{}}}
    with pytest.raises(ValueError, match='unsupported capability'):
        mission_settings(spec, 'a', capability={})
    assert mission_settings(spec, 'a', capability={'objective_modes': ['measurement_only']})['objective_mode'] == 'measurement_only'
    legacy = {'research_target':'mining-productivity-4', 'arms':{'a':{}}}
    assert mission_settings(legacy, 'a')['objective_mode'] == 'legacy_unspecified'
    assert mission_settings({'arms':{'a':{}}}, 'a', new=True)['objective_mode'] == 'legacy_unspecified'


@pytest.mark.parametrize('mode', ['research_completion', 'measurement_only'])
def test_dispatch_forwards_arm_specific_mission_before_runner_call(tmp_path, monkeypatch, mode):
    from tests.test_laya_regression_scheduler import frozen_suite, dispatched_run_command
    from tools import laya_regression_scheduler as scheduler
    from tools.checkpoint_catalog import atomic_json
    PM = frozen_runner(tmp_path)
    root, registry = frozen_suite(tmp_path)
    spec = scheduler.read(root/'regression.json')
    spec['research_target'] = 'mining-productivity-4'
    spec['arms']['candidate'].update(runner='tools/run_laya_mixed.py', objective_mode=mode,
                                    research_target='mining-productivity-4' if mode == 'research_completion' else None)
    atomic_json(root/'regression.json', spec)
    freeze = scheduler.read(root/'freeze.json')
    cap = frozen_capability(PM, 'tools/run_laya_mixed.py', sys.executable)
    identity = next(a for a in freeze['arms'] if a['id'] == 'candidate')
    identity.update(runner='tools/run_laya_mixed.py', mission_capability=cap)
    freeze['spec_sha256'] = scheduler.sha(root/'regression.json')
    atomic_json(root/'freeze.json', freeze)
    monkeypatch.setattr(scheduler, 'frozen_capability', lambda code, runner, python: cap if runner == 'tools/run_laya_mixed.py' else {})
    run = dispatched_run_command(root, registry, monkeypatch)
    assert run[1].endswith('tools/run_laya_mixed.py')
    if mode == 'research_completion':
        assert run[run.index('--research-target')+1] == 'mining-productivity-4'
    else:
        assert '--research-target' not in run
    assert run[run.index('--objective-mode')+1] == mode
    job = scheduler.read(root/'job-status.json')['jobs'][0]
    assert job['effective_mission']['operation_families'] == cap['operation_families']


@pytest.mark.parametrize('document', ['[]', '{"version":1,"runners":null}', '{"version":2,"runners":{}}'])
def test_invalid_manifest_cannot_admit_an_explicit_mission(tmp_path, document):
    code = frozen_runner(tmp_path)
    (code/'training/laya_mission_capabilities.py').write_text('print(' + repr(document) + ')\n')
    capability = frozen_capability(code, 'tools/run_laya_mixed.py', sys.executable)
    with pytest.raises(ValueError, match='unsupported capability'):
        mission_settings({'objective_mode':'measurement_only', 'arms':{'a':{}}}, 'a', capability=capability)


def test_new_calibration_requires_mode_before_freezing(tmp_path):
    from tools import laya_regression_scheduler as scheduler
    with pytest.raises(ValueError, match='objective_mode required'):
        scheduler.enqueue(tmp_path, tmp_path/'registry',
                          {'name':'calibration', 'design':'mixed-start-v1', 'arms':{'a':{}}})
    assert not (tmp_path/'registry').exists()
