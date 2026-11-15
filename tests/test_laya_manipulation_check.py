# Path: tests/test_laya_manipulation_check.py | Purpose: Protect the declared per-arm manipulation check: measurement, freeze validation, pausing and the absent-field default.
import json
from pathlib import Path
import subprocess

import pytest

from tools import laya_manipulation_check as check
from tools import laya_regression_scheduler as scheduler
from tools.checkpoint_catalog import atomic_json
from tools.laya_checkpoint_regression import report
from tests.test_laya_regression_scheduler import frozen_suite

pytestmark = pytest.mark.fast

CABLE = 'op.production_service.copper-cable'
OPS_SOURCE = {'training/laya_ops_fixture.py':
              'OPTION = {"option_id": "op.production_service.copper-cable", "family": "production_service"}\n'}


def record(chosen='op.wait', ok=False, offered=(), rules=(), stock=None):
    options = [{'option_id': o, 'family': o.split('.')[1]} for o in (*offered, chosen)]
    row = {'options': options, 'choice': {'option_id': chosen, 'rules': list(rules)}, 'outcome': {'ok': ok}}
    if stock is not None:
        row['state'] = {'observation_digest': {'saved_world': {'provider_stock': {'network': stock}}}}
    return row


def test_each_form_counts_only_its_exposure():
    rows = [record(CABLE, ok=False, rules=['mission.serve-bottleneck'], stock={'long-handed-inserter': 5}),
            record(CABLE, ok=True, stock={'long-handed-inserter': 30}),
            record('op.wait', offered=[CABLE]), {'evidence_error': 'malformed'}]
    measure = lambda **e: check.measure_records(rows, {'at_least': 1, **e})
    assert measure(form='offered', option_id=CABLE) == (3, 3)
    assert measure(form='chosen', family='production_service') == (2, 3)
    assert measure(form='succeeded', option_id=CABLE) == (1, 3)
    assert measure(form='rule_applied', rule='mission.serve-bottleneck') == (1, 3)
    assert measure(form='network_stock', item='long-handed-inserter') == (30, 3)


@pytest.mark.parametrize('declaration, message', [
    ({'exposure': {'form': 'built', 'entity': 'lab'}}, 'form must be one of'),
    ({'exposure': {'form': 'offered', 'option_id': CABLE, 'family': 'x'}}, 'exactly one'),
    ({'exposure': {'form': 'rule_applied', 'option_id': CABLE}}, "takes \\['rule'\\]"),
    ({'after_jobs': 3, 'exposure': {'form': 'offered', 'option_id': CABLE}}, 'exceeds the arm'),
    ({'after_jobs': 2, 'min_jobs': 3, 'exposure': {'form': 'offered', 'option_id': CABLE}}, 'min_jobs exceeds'),
    ({'after_jobs': 0, 'exposure': {'form': 'offered', 'option_id': CABLE}}, 'positive integer'),
    ({'surprise': 1, 'exposure': {'form': 'offered', 'option_id': CABLE}}, 'allows only'),
])
def test_bad_declaration_is_refused_before_anything_is_frozen(tmp_path, declaration, message):
    with pytest.raises(ValueError, match=message):
        frozen_suite(tmp_path, files=OPS_SOURCE,
                     spec_updates={'arms': {'candidate': {'revision': 'HEAD', 'manipulation_check': declaration},
                                            'reference': {'revision': 'HEAD'}}})
    assert not (tmp_path/'registry'/'regression-test').exists()


@pytest.mark.parametrize('exposure', [{'form': 'offered', 'family': 'mall_slot'},
                                      {'form': 'chosen', 'option_id': 'op.grow.copper-cable'},
                                      {'form': 'rule_applied', 'rule': 'mission.not-a-rule'}])
def test_names_absent_from_the_frozen_arm_are_a_freeze_error(tmp_path, exposure):
    with pytest.raises(ValueError, match='frozen arm'):
        frozen_suite(tmp_path, files=OPS_SOURCE,
                     spec_updates={'arms': {'candidate': {'revision': 'HEAD', 'manipulation_check': {'exposure': exposure, 'after_jobs': 2}},
                                            'reference': {'revision': 'HEAD'}}})
    freeze = scheduler.read(tmp_path/'registry/regression-test/freeze.json')
    assert freeze['state'] == 'incompatible'


def run_suite(tmp_path, monkeypatch, declaration, journal):
    """Run a 2-arm, 3-seed suite to completion with a fake runner that writes ``journal(arm)``."""
    files = {**OPS_SOURCE, 'docs/rl/laya_ops_guidelines.json': json.dumps({'rules': [{'rule_id': 'mission.cable'}]})}
    arms = {'candidate': {'revision': 'HEAD'}, 'reference': {'revision': 'HEAD'}}
    if declaration is not None:
        arms['candidate']['manipulation_check'] = declaration
    root, registry = frozen_suite(tmp_path, files=files, spec_updates={'arms': arms, 'seeds': [1, 2, 3]})
    atomic_json(registry/'predecessor/job-status.json', {'jobs': [{'status': 'completed'}]})
    monkeypatch.setattr(scheduler, 'may_admit', lambda *a, **kw: True)
    monkeypatch.setattr(scheduler, 'ports_free', lambda *a: True)
    monkeypatch.setattr(scheduler, 'verify_engine', lambda *a: None)

    def runner(command, **kwargs):
        if command[2] == 'run':
            world = Path(command[command.index('--root')+1])
            (world/'evidence').mkdir(parents=True, exist_ok=True)
            rows = journal(world.parents[4].name)
            (world/'evidence/decision-states.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return subprocess.CompletedProcess(command, 0)
    outcomes = []
    for _ in range(12):
        scheduler._publish(root, lambda status: status.update(last_admit=0))
        outcomes.append(scheduler.run_one(root, executor=runner))
        if outcomes[-1] == 'complete':
            break
    return root, outcomes


def test_inert_treatment_is_paused_after_k_jobs_and_control_continues(tmp_path, monkeypatch):
    declaration = {'after_jobs': 2, 'exposure': {'form': 'succeeded', 'option_id': CABLE}}
    root, outcomes = run_suite(tmp_path, monkeypatch, declaration,
                               lambda arm: [record(CABLE, ok=False), record('op.wait')])
    # Plan order is s1 candidate, s1 reference, s2 candidate, ...: K=2 candidate jobs finish by the third dispatch.
    assert outcomes == ['completed'] * 3 + ['manipulation_check_recorded', 'completed', 'completed', 'complete']
    status = scheduler.read(root/'job-status.json')
    jobs = {job['id']: job for job in status['jobs']}
    assert jobs['C0-candidate-s3']['status'] == 'cancelled'
    assert jobs['C0-candidate-s3']['termination_cause'] == check.FAILED_CAUSE
    assert jobs['C0-reference-s3']['status'] == 'completed' and 'exposure' not in jobs['C0-reference-s3']
    verdict = status['manipulation_checks']['candidate']
    assert verdict['state'] == 'failed' and verdict['cancelled_jobs'] == ['C0-candidate-s3']
    assert verdict['evaluated_jobs'] == ['C0-candidate-s1', 'C0-candidate-s2']
    result = report(root)
    assert result['manipulation_check']['arms']['candidate']['state'] == 'failed'
    assert result['manipulation_check']['control_arms_unchecked'] == ['reference']
    assert result['planned'] == 6


def test_effective_treatment_passes_and_runs_every_job(tmp_path, monkeypatch):
    declaration = {'after_jobs': 2, 'exposure': {'form': 'rule_applied', 'rule': 'mission.cable'}}
    root, outcomes = run_suite(tmp_path, monkeypatch, declaration,
                               lambda arm: [record(CABLE, ok=True, rules=['mission.cable'])])
    assert outcomes.count('completed') == 6 and outcomes[-1] == 'complete'
    status = scheduler.read(root/'job-status.json')
    assert status['manipulation_checks']['candidate']['state'] == 'passed'
    assert all(job['status'] == 'completed' for job in status['jobs'])


def test_absent_declaration_keeps_todays_behaviour(tmp_path, monkeypatch):
    root, outcomes = run_suite(tmp_path, monkeypatch, None, lambda arm: [record(CABLE, ok=False)])
    assert outcomes == ['completed'] * 6 + ['complete']
    status = scheduler.read(root/'job-status.json')
    assert 'manipulation_checks' not in status
    assert not any('exposure' in job for job in status['jobs'])
    result = report(root)
    assert 'manipulation_check' not in result and result['exposure_evidence'] == 'unknown'
    # The pure evaluation is a no-op on undeclared specs.
    snapshot = json.dumps(status, sort_keys=True)
    assert check.evaluate(scheduler.read(root/'regression.json'), status, scheduler.TERMINAL) == []
    assert json.dumps(status, sort_keys=True) == snapshot


def test_jobs_without_decisions_do_not_count_toward_k_and_running_jobs_are_untouched():
    spec = {'arms': {'t': {'manipulation_check': {'after_jobs': 2, 'exposure': {'form': 'offered', 'family': 'x'}}}}}
    measured = lambda i, d: {'id': f't{i}', 'arm': 't', 'status': 'completed',
                             'exposure': {'decisions': d, 'shown': False, 'value': 0, 'measured_at': i}}
    status = {'jobs': [measured(1, 0), measured(2, 5), {'id': 't3', 'arm': 't', 'status': 'running'},
                       {'id': 't4', 'arm': 't', 'status': 'queued'}]}
    assert check.evaluate(spec, status, scheduler.TERMINAL) == []
    status['jobs'].append(measured(5, 3))
    assert check.evaluate(spec, status, scheduler.TERMINAL) == ['t']
    assert [job['status'] for job in status['jobs']] == ['completed', 'completed', 'running', 'cancelled', 'completed']
    assert status['manipulation_checks']['t']['evaluated_jobs'] == ['t2', 't5']


def test_measure_job_never_raises_on_a_malformed_journal(tmp_path):
    """measure_job runs in the scheduler's finally block; an exception there would skip terminal publication."""
    from tools import laya_manipulation_check as mc
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (evidence/'decision-states.jsonl').write_text('[1, 2]\n42\n"text"\n{"choice": 5, "options": "x"}\n')
    declaration = mc.validate({'after_jobs': 1, 'exposure': {'form': 'chosen', 'family': 'produce', 'at_least': 1}}, 1)
    record = mc.measure_job(tmp_path, declaration)
    assert record['form'] == 'chosen'
    assert record['shown'] in (None, False)
