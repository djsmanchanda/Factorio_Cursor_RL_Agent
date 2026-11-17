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
    assert measure(form='offered', option_id=CABLE) == (3, 3, 3)
    assert measure(form='chosen', family='production_service') == (2, 3, 3)
    assert measure(form='succeeded', option_id=CABLE) == (1, 3, 3)
    assert measure(form='rule_applied', rule='mission.serve-bottleneck') == (1, 3, 3)
    # Only the two rows that carry a census are observations of stock.
    assert measure(form='network_stock', item='long-handed-inserter') == (30, 3, 2)


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


def suite(tmp_path, monkeypatch, declaration, journal, seeds=(1, 2, 3), checked='candidate'):
    """A frozen 2-arm suite whose fake runner writes ``journal(arm)`` as each job's decision records."""
    files = {**OPS_SOURCE, 'docs/rl/laya_ops_guidelines.json': json.dumps({'rules': [{'rule_id': 'mission.cable'}]})}
    arms = {'candidate': {'revision': 'HEAD'}, 'reference': {'revision': 'HEAD'}}
    if declaration is not None:
        arms[checked]['manipulation_check'] = declaration
    root, registry = frozen_suite(tmp_path, files=files, spec_updates={'arms': arms, 'seeds': list(seeds)})
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
    return root, runner


def run_suite(tmp_path, monkeypatch, declaration, journal):
    """Run a 2-arm, 3-seed suite to completion one dispatch at a time."""
    root, runner = suite(tmp_path, monkeypatch, declaration, journal)
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
    # Plan order is s1 candidate, s1 reference, s2 candidate, ...: K=2 candidate jobs finish by the third dispatch,
    # and that result publishes the verdict, so no dispatch is spent recording it.
    assert outcomes == ['completed'] * 5 + ['complete']
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


# --- R3: verdicts are published by the result that completes them, not by a later admission ---------------------

def campaign(root, runner, workers):
    # The production admission spacing is irrelevant to what these tests protect.
    original = scheduler._publish
    scheduler._publish = lambda r, mutate, **kw: original(r, lambda s: (s.update(last_admit=0), mutate(s))[1], **kw)
    try:
        return scheduler.run_campaign(root, workers=workers, executor=runner)
    finally:
        scheduler._publish = original


def whole_arm_declaration(form='succeeded'):
    return {'after_jobs': 3, 'exposure': {'form': form, 'option_id': CABLE}}


def assert_denominators_intact(root, planned=6):
    status = scheduler.read(root/'job-status.json')
    assert len(status['jobs']) == planned and all(job['status'] in scheduler.TERMINAL for job in status['jobs'])
    assert report(root)['planned'] == planned
    return status


def test_k_equal_to_the_whole_arm_publishes_a_verdict_at_campaign_end(tmp_path, monkeypatch):
    # Plan order is seed-major, so the reference arm owns the last job and nothing is admitted after it.
    root, runner = suite(tmp_path, monkeypatch, whole_arm_declaration(), lambda arm: [record(CABLE, ok=False)],
                         checked='reference')
    assert campaign(root, runner, workers=1) == 'complete'
    status = assert_denominators_intact(root)
    verdict = status['manipulation_checks']['reference']
    assert verdict['state'] == 'failed' and len(verdict['evaluated_jobs']) == 3
    assert report(root)['manipulation_check']['arms']['reference']['state'] == 'failed'


def test_full_concurrency_publishes_a_verdict_though_nothing_is_admitted_after_the_last_result(tmp_path, monkeypatch):
    import threading
    root, runner = suite(tmp_path, monkeypatch, whole_arm_declaration(), lambda arm: [record(CABLE, ok=True)])
    started = threading.Barrier(6, timeout=30)  # every job is admitted before any one finishes
    def gated(command, **kwargs):
        if command[2] == 'run':
            started.wait()
        return runner(command, **kwargs)
    assert campaign(root, gated, workers=6) == 'complete'
    status = assert_denominators_intact(root)
    assert status['manipulation_checks']['candidate']['state'] == 'passed'


def test_a_verdict_lost_with_the_coordinator_is_published_by_recovery_and_by_campaign_end(tmp_path, monkeypatch):
    root, runner = suite(tmp_path, monkeypatch, whole_arm_declaration(), lambda arm: [record(CABLE, ok=False)])
    assert campaign(root, runner, workers=1) == 'complete'
    lose = lambda status: status.pop('manipulation_checks')
    scheduler._publish(root, lose)                       # a restart after results, before any verdict was durable
    assert 'manipulation_checks' not in scheduler.read(root/'job-status.json')
    scheduler.recover(root)
    assert scheduler.read(root/'job-status.json')['manipulation_checks']['candidate']['state'] == 'failed'
    scheduler._publish(root, lose)
    assert campaign(root, runner, workers=1) == 'complete'
    assert_denominators_intact(root)
    assert scheduler.read(root/'job-status.json')['manipulation_checks']['candidate']['state'] == 'failed'


def test_nothing_is_written_when_no_verdict_is_due(tmp_path, monkeypatch):
    root, runner = suite(tmp_path, monkeypatch, None, lambda arm: [record(CABLE)])
    assert campaign(root, runner, workers=1) == 'complete'
    before = (root/'job-status.journal.jsonl').read_bytes()
    assert scheduler.finalize_checks(root, scheduler.read(root/'regression.json')) == []
    assert (root/'job-status.journal.jsonl').read_bytes() == before
    (tmp_path/'declared').mkdir()
    declared, runner = suite(tmp_path/'declared', monkeypatch, whole_arm_declaration(), lambda arm: [record(CABLE, ok=True)])
    assert campaign(declared, runner, workers=1) == 'complete'     # verdict already published
    before = (declared/'job-status.journal.jsonl').read_bytes()
    assert scheduler.finalize_checks(declared, scheduler.read(declared/'regression.json')) == []
    assert (declared/'job-status.journal.jsonl').read_bytes() == before


def test_an_arm_that_finishes_without_enough_usable_telemetry_is_inconclusive_not_failed(tmp_path, monkeypatch):
    declaration = {'after_jobs': 3, 'exposure': {'form': 'network_stock', 'item': 'lab'}}
    root, runner = suite(tmp_path, monkeypatch, declaration, lambda arm: [record(CABLE)])  # no census anywhere
    assert campaign(root, runner, workers=1) == 'complete'
    status = assert_denominators_intact(root)
    verdict = status['manipulation_checks']['candidate']
    assert verdict['state'] == 'inconclusive' and verdict['cancelled_jobs'] == []
    assert set(verdict['unmeasured_jobs'].values()) == {'exposure_telemetry_unavailable'}
    assert all(job['status'] == 'completed' for job in status['jobs'])


# --- R8: missing telemetry is unknown, not zero --------------------------------------------------------------

STOCK = {'form': 'network_stock', 'item': 'lab', 'at_least': 1}


def census_row(**network_path):
    row = {'choice': {'option_id': 'op.wait'}}
    if network_path:
        row['state'] = {'observation_digest': {'saved_world': {'provider_stock': network_path}}}
    return row


@pytest.mark.parametrize('rows, expected', [
    ([{'choice': {'option_id': 'op.wait'}}], (0, 1, 0)),                              # no state at all
    ([census_row(other=1)], (0, 1, 0)),                                               # no network key
    ([census_row(network=None), census_row(network=[1, 2]), census_row(network='x')], (0, 3, 0)),
    ([census_row(network={'lab': 'many'}), census_row(network={'lab': True}),
      census_row(network={'lab': float('nan')}), census_row(network={'lab': -3})], (0, 4, 0)),
    ([census_row(network={})], (0, 1, 1)),                                            # valid census, item absent: zero
    ([census_row(network={'lab': 0})], (0, 1, 1)),
    ([census_row(network={'lab': 4}), census_row(network=None), census_row(network={'lab': 2})], (4, 3, 2)),
])
def test_network_stock_distinguishes_unusable_census_from_observed_zero(rows, expected):
    assert check.measure_records(rows, STOCK) == expected


def stock_world(tmp_path, name, rows):
    world = tmp_path/name
    (world/'evidence').mkdir(parents=True)
    (world/'evidence/decision-states.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return world


def measured_jobs(tmp_path, census_by_job):
    """Exposure records for jobs with the given decision rows, through the real measurement entry point."""
    declaration = check.validate({'after_jobs': 2, 'exposure': STOCK}, 3)
    jobs = []
    for index, rows in enumerate(census_by_job, 1):
        exposure = check.measure_job(stock_world(tmp_path, f'w{index}', rows), declaration)
        exposure['measured_at'] = index
        jobs.append({'id': f'j{index}', 'arm': 't', 'status': 'completed', 'exposure': exposure})
    spec = {'arms': {'t': {'manipulation_check': {'after_jobs': 2, 'exposure': STOCK}}}}
    return spec, jobs


def test_missing_and_malformed_census_record_null_with_a_cause_and_do_not_cancel_the_arm(tmp_path):
    spec, jobs = measured_jobs(tmp_path, [[census_row()], [census_row(network='garbage')]])
    assert [(j['exposure']['value'], j['exposure']['shown'], j['exposure']['cause']) for j in jobs] == [
        (None, None, 'exposure_telemetry_unavailable')] * 2
    assert all(j['exposure']['decisions'] == 1 and j['exposure']['observations'] == 0 for j in jobs)
    status = {'jobs': [*jobs, {'id': 'j3', 'arm': 't', 'status': 'queued'}]}
    assert check.evaluate(spec, status, scheduler.TERMINAL) == []
    assert status['jobs'][2]['status'] == 'queued'
    view = check.summarize(spec, status, scheduler.TERMINAL)['arms']['t']
    assert view['state'] == 'pending' and view['measured_jobs'] == 0
    assert view['unmeasured_jobs'] == {'j1': 'exposure_telemetry_unavailable', 'j2': 'exposure_telemetry_unavailable'}


def test_a_job_with_no_decision_records_has_its_own_cause(tmp_path):
    _, [job] = measured_jobs(tmp_path, [[]])
    assert (job['exposure']['value'], job['exposure']['shown'], job['exposure']['cause']) == (None, None, 'no_decision_records')


def test_a_valid_empty_census_is_observed_zero_and_can_fail_the_arm(tmp_path):
    spec, jobs = measured_jobs(tmp_path, [[census_row(network={})], [census_row(network={'iron-plate': 9})]])
    assert [(j['exposure']['value'], j['exposure']['shown']) for j in jobs] == [(0, False)] * 2
    status = {'jobs': [*jobs, {'id': 'j3', 'arm': 't', 'status': 'queued'}]}
    assert check.evaluate(spec, status, scheduler.TERMINAL) == ['t']
    assert status['manipulation_checks']['t']['state'] == 'failed' and status['jobs'][2]['status'] == 'cancelled'


def test_unusable_census_is_skipped_when_counting_toward_k(tmp_path):
    spec, jobs = measured_jobs(tmp_path, [[census_row()], [census_row(network={'lab': 3})], [census_row(network={'lab': 1})]])
    status = {'jobs': jobs}
    assert check.evaluate(spec, status, scheduler.TERMINAL) == ['t']
    verdict = status['manipulation_checks']['t']
    assert verdict['evaluated_jobs'] == ['j2', 'j3'] and verdict['state'] == 'passed'


def test_exposure_records_from_before_observations_existed_still_count_by_decisions():
    spec = {'arms': {'t': {'manipulation_check': {'after_jobs': 1, 'exposure': STOCK}}}}
    status = {'jobs': [{'id': 'old', 'arm': 't', 'status': 'completed',
                        'exposure': {'decisions': 2, 'value': 5, 'shown': True, 'measured_at': 1}}]}
    assert check.evaluate(spec, status, scheduler.TERMINAL) == ['t']
    assert status['manipulation_checks']['t']['state'] == 'passed'


# --- offline verdicts for a finished cohort ------------------------------------------------------------------

def tree_state(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
            for path in sorted(root.rglob('*')) if path.is_file()}


def finished_cohort(tmp_path, monkeypatch):
    root, runner = suite(tmp_path, monkeypatch, whole_arm_declaration(), lambda arm: [record(CABLE, ok=True)])
    assert campaign(root, runner, workers=1) == 'complete'
    return root


def test_offline_verdicts_are_computed_from_the_worlds_and_write_nothing(tmp_path, monkeypatch):
    root = finished_cohort(tmp_path, monkeypatch)
    # Stored rows claim no exposure (as rows written under the old rule could); the worlds say otherwise.
    def stale(status):
        status.pop('manipulation_checks')
        for job in status['jobs']:
            if job['arm'] == 'candidate':
                job['exposure'] = {**job['exposure'], 'value': 0, 'shown': False}
    scheduler._publish(root, stale)
    before = tree_state(root)
    stored = check.offline_verdicts(root, scheduler.TERMINAL, remeasure=False)
    fresh = check.offline_verdicts(root, scheduler.TERMINAL)
    assert stored['verdicts']['candidate']['state'] == 'failed'
    assert fresh['verdicts']['candidate']['state'] == 'passed' and fresh['published_verdicts'] == {}
    assert fresh['verdicts']['candidate']['evaluated_jobs'] == [f'C0-candidate-s{i}' for i in (1, 2, 3)]
    assert tree_state(root) == before


def test_offline_cli_prints_json_and_refuses_to_write_inside_the_cohort(tmp_path, monkeypatch, capsys):
    root = finished_cohort(tmp_path, monkeypatch)
    before = tree_state(root)
    assert check.main(['offline', str(root), '--output', str(tmp_path/'verdicts.json')]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['verdicts']['candidate']['state'] == 'passed'
    assert json.loads((tmp_path/'verdicts.json').read_text()) == printed
    with pytest.raises(SystemExit):
        check.main(['offline', str(root), '--output', str(root/'verdicts.json')])
    assert tree_state(root) == before
