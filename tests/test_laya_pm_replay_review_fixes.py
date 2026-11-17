# Path: tests/test_laya_pm_replay_review_fixes.py | Purpose: PM replay budget, funding targets, cumulative consumption and one acceptance verdict (review R2, R4-R7).

import io
import json

import pytest

from orchestrator.checkpoint_milestones import FactorySnapshot
from tools import laya_pm_replay as replay
from tools.laya_strict_sidecar import Measurement

pytestmark = pytest.mark.fast


def shortage(required, available=None, **extra):
    return {'code': 'material_shortage', 'ok': False,
            'details': {'required': required, 'available': available or {}, **extra}}


class Scripted:
    """A session whose produce answers come from ``bills`` (item -> outcome); ``on_run`` may move clocks."""

    def __init__(self, service, bills=None, on_run=None):
        self.service, self.bills, self.on_run, self.calls = service, bills or {}, on_run, []

    def economics(self):
        return {}

    def run(self, operation, **params):
        self.calls.append((operation, params))
        if self.on_run is not None:
            self.on_run(operation, params)
        if operation == 'production_service':
            return self.service
        return self.bills.get(params['item'], {'code': 'prerequisite_advanced'})


def produced(session):
    return [(params['item'], params['stock_target']) for operation, params in session.calls if operation == 'produce']


def journal(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# R2: one budget guard before every operation, including recursive funding.

def test_no_funding_starts_after_the_service_crossed_the_deadline(tmp_path):
    wall = [0]

    def on_run(operation, _params):
        if operation == 'production_service':
            wall[0] = 11
    session = Scripted(shortage({'a': 1, 'b': 1}), on_run=on_run)
    result = replay.drive(session, tmp_path / 'j.jsonl', max_steps=3, initial_tick=0, tick_reader=lambda: 1,
                          deadline=10, clock=lambda: wall[0], monotonic=lambda: 0, sleep=lambda _: None,
                          fund_shortages=True, stock_reader=lambda _s: {})
    assert produced(session) == []
    assert result['outcome'] == 'deadline_reached' and result['deadline_reached'] is True
    assert [row['cause'] for row in journal(tmp_path / 'j.jsonl') if row['event'] == 'pm_replay_budget_stop'] == [
        'deadline_reached']


def test_a_running_produce_finishes_but_no_child_or_sibling_starts(tmp_path):
    wall = [0]

    def on_run(operation, params):
        if params.get('item') == 'a':
            wall[0] = 11  # the deadline passes while 'a' runs
    session = Scripted(shortage({'a': 1, 'b': 1}), {'a': shortage({'x': 2})}, on_run=on_run)
    result = replay.drive(session, tmp_path / 'j.jsonl', max_steps=3, initial_tick=0, tick_reader=lambda: 1,
                          deadline=10, clock=lambda: wall[0], monotonic=lambda: 0, sleep=lambda _: None,
                          fund_shortages=True, stock_reader=lambda _s: {})
    assert produced(session) == [('a', 1)]
    assert len(result['steps']) == 1 and result['steps'][0]['funding'][0]['outcome'] == 'material_shortage'
    assert result['outcome'] == 'deadline_reached' and result['deadline_reached'] is True


@pytest.mark.parametrize('jump, cause', [(200_000, 'game_tick_budget_exhausted'), (-50, 'clock_not_advancing')])
def test_the_game_tick_guard_also_covers_funding(tmp_path, jump, cause):
    tick = [100]

    def on_run(operation, params):
        if params.get('item') == 'a':
            tick[0] += jump
    session = Scripted(shortage({'a': 1, 'b': 1}), on_run=on_run)
    result = replay.drive(session, tmp_path / 'j.jsonl', max_steps=3, initial_tick=99,
                          tick_reader=lambda: tick[0], max_game_ticks=108_000, monotonic=lambda: 0,
                          sleep=lambda _: None, fund_shortages=True, stock_reader=lambda _s: {})
    assert produced(session) == [('a', 1)]
    assert result['outcome'] == cause and result['deadline_reached'] is False


# R5: the funding target is the runner's failure-time network level, never the bill's quantity.

def test_target_is_stock_at_failure_plus_shortfall(tmp_path):
    reads = iter([{'x': 20}, {'x': 25}])  # before the service, then after it
    session = Scripted(shortage({'x': 10}, {'x': 8}))
    replay.drive(session, tmp_path / 'j.jsonl', max_steps=1, initial_tick=0, tick_reader=lambda: 1,
                 monotonic=lambda: 0, sleep=lambda _: None, fund_shortages=True,
                 stock_reader=lambda _s: next(reads, {'x': 99}))
    from training.laya_ops_runner import shortage_levels
    runner = shortage_levels([{'operation': 'production_service', **shortage({'x': 10}, {'x': 8}),
                               'stock_at': {'x': 20}}])
    assert runner == {'x': 22} and produced(session) == [('x', 22)]


def test_a_stock_at_the_outcome_carries_wins():
    bill = replay.pm_bill(shortage({'x': 10}, {'x': 8}, stock_at={'x': 20}), {'x': 0})
    assert bill['stock_at'] == {'x': 20} and bill['levels'] == {'x': 22}


def test_unknown_stock_stays_unknown_and_is_not_produced(tmp_path):
    session = Scripted(shortage({'x': 10}, {'x': 8}))
    result = replay.drive(session, tmp_path / 'j.jsonl', max_steps=1, initial_tick=0, tick_reader=lambda: 1,
                          monotonic=lambda: 0, sleep=lambda _: None, fund_shortages=True)
    assert produced(session) == []
    row, = result['steps'][0]['funding']
    assert row['outcome'] == 'stock_unknown' and row['stock_target'] is None
    assert result['steps'][0]['bill']['stock_at'] is None and result['steps'][0]['bill']['levels'] == {}


# R6: the highest target per item survives; an item is revisited only when its demand rises.

@pytest.mark.parametrize('needs, expected', [
    ({'a': 1, 'b': 10}, [('a', 1), ('x', 1), ('b', 1), ('x', 10)]),
    ({'a': 10, 'b': 1}, [('a', 1), ('x', 10), ('b', 1)]),
])
def test_a_diamond_keeps_the_larger_nested_requirement(needs, expected):
    class Session:
        def __init__(self):
            self.calls = []

        def run(self, operation, **params):
            self.calls.append((operation, params))
            need = needs.get(params['item'])
            return shortage({'x': need}) if need is not None else {'code': 'prerequisite_advanced'}
    session = Session()
    replay._fund_shortage(session, io.StringIO(), 1, replay.pm_bill(shortage({'a': 1, 'b': 1}), {}),
                          stock_reader=lambda _s: {})
    assert produced(session) == expected


def test_a_cycle_stops_when_the_demand_does_not_rise():
    session = Scripted(None, {'a': shortage({'b': 1}), 'b': shortage({'a': 1})})
    rows = replay._fund_shortage(session, io.StringIO(), 1, replay.pm_bill(shortage({'a': 1}), {}),
                                 stock_reader=lambda _s: {}, max_depth=10)
    assert produced(session) == [('a', 1), ('b', 1)]
    assert [row['depth'] for row in rows] == [1, 2]


def test_a_rising_cycle_is_bounded_by_depth():
    stock = {'a': 0, 'b': 0}

    def grow(_operation, params):
        stock[params['item']] += 5  # each attempt raises the next failure-time level
    session = Scripted(None, {'a': shortage({'b': 1}), 'b': shortage({'a': 1})}, on_run=grow)
    replay._fund_shortage(session, io.StringIO(), 1, replay.pm_bill(shortage({'a': 1}), {}),
                          stock_reader=lambda _s: dict(stock), max_depth=4)
    assert len(produced(session)) == 4


# R7: consumption is counted from cumulative crafts, not a rolling-window total.

def _measure(samples):
    """Strict sidecar report rows from ``(tick, ac_produced, consumers or None, failed_before)``."""
    measurement = Measurement({})
    for tick, produced_ac, consumers, failed in samples:
        if failed:
            measurement.failure()
        measurement.add(FactorySnapshot.from_mapping({
            'tick': tick, 'production': {'advanced-circuit': {'produced': produced_ac}},
            'ac_consumers': consumers}))
    return measurement.summary('running')


def _pm(count, unit=7, recipe='productivity-module'):
    return [{'unit': unit, 'recipe': recipe, 'count': count}]


def test_late_eligibility_and_window_rollover_still_count_rises(tmp_path):
    # The review's probe: PM crafts every sample; new ACs (C6's production leg) only after the window filled.
    report = _measure([(i * 3600, max(0, i - 19), _pm(i), False) for i in range(28)])
    path = tmp_path / 'strict.json'
    path.write_text(json.dumps(report))
    consumption = replay.sidecar_consumption(path)
    eligible = [row for row in consumption['samples'] if row['c6_passed']]
    assert len(eligible) == 8 and [row['crafts'] for row in eligible] == list(range(20, 28))
    # The window total is flat at 19 throughout, and the old detector saw one rise.
    assert {row['window_crafts'] for row in consumption['observations']} == {19}
    # Every eligible sample adds one craft over its predecessor, an hour apart: eight separated rises.
    assert consumption['sustained_rises'] == 8


def test_a_failed_poll_or_missing_telemetry_is_a_gap_not_a_rise(tmp_path):
    report = _measure([(0, 0, _pm(0), False), (3600, 1, _pm(1), False), (7200, 2, None, False),
                       (10800, 3, _pm(4), True), (14400, 4, _pm(4), False)])
    path = tmp_path / 'strict.json'
    path.write_text(json.dumps(report))
    samples = replay.sidecar_consumption(path)['samples']
    assert [(row['tick'], row['crafts']) for row in samples] == [(0, 0), (3600, 1), (10800, 4), (14400, 4)]
    assert replay.sustained_rises(samples) == 2


def test_inherited_counters_and_recipe_changes_add_nothing(tmp_path):
    # A mall machine re-recipe'd to PM brings 2294 lifetime crafts; a second machine appears mid-run.
    report = _measure([(0, 0, _pm(2294, recipe='iron-gear-wheel'), False),
                       (3600, 1, _pm(2294), False),
                       (7200, 2, _pm(2295) + _pm(50, unit=8), False),
                       (10800, 3, _pm(2295) + _pm(51, unit=8), False)])
    path = tmp_path / 'strict.json'
    path.write_text(json.dumps(report))
    samples = replay.sidecar_consumption(path)['samples']
    assert [row['crafts'] for row in samples] == [0, 0, 1, 2]


def test_generations_do_not_mix():
    samples = [{'tick': 0, 'crafts': 5, 'generation': 'old'}, {'tick': 4000, 'crafts': 1, 'generation': 'new'},
               {'tick': 8000, 'crafts': 6, 'generation': 'old'}]
    assert replay.sustained_rises(samples) == 3


# R4: drive, report and verify share one acceptance verdict.

def _world(tmp_path, *, rows, summary=None):
    world = tmp_path / 'world'
    evidence = world / 'evidence'
    evidence.mkdir(parents=True)
    (world / 'manifest.json').write_text(json.dumps({'production_authority': False}))
    (evidence / 'pm-placement.json').write_text(json.dumps({'placement_receipt': True, 'machines': 1}))
    (evidence / 'strict.json').write_text(json.dumps({'strict_current': rows}))
    if summary is not None:
        (evidence / 'pm-replay.jsonl').write_text(json.dumps({'event': 'pm_replay_summary', **summary}) + '\n')
    return world


def _row(tick, window, cumulative=None):
    row = {'tick': tick, 'statuses': {'C6': True}, 'predicate_evidence': {'C6': {
        'predicate_version': 'c6-v3', 'status': 'passed',
        'evidence': {'machine_consumption': {'productivity-module': window}}}}}
    if cumulative is not None:
        row['machine_crafts'] = {'generation': 'g', 'cumulative': {'productivity-module': cumulative}}
    return row


def test_the_report_cannot_contradict_a_failed_driver(tmp_path, capsys):
    world = _world(tmp_path, rows=[_row(3600, 1)],
                   summary={'pm_evidence_complete': False, 'outcome': 'game_tick_budget_exhausted'})
    report = replay.replay_report(world)
    assert report['pm_evidence_complete'] is False and report['acceptance']['reason'] == 'driver_not_complete'
    assert replay.main(['report', '--world', str(world)]) == 4
    assert replay.main(['verify', '--world', str(world)]) == 1


def test_a_driver_claim_the_measurement_does_not_support_is_unknown(tmp_path, capsys):
    world = _world(tmp_path, rows=[_row(3600, 1, 1)],
                   summary={'pm_evidence_complete': True, 'acceptance_version': replay.ACCEPTANCE_VERSION})
    report = replay.replay_report(world)
    assert report['pm_evidence_complete'] is None
    assert replay.main(['report', '--world', str(world)]) == 4


def test_sustained_cumulative_evidence_and_a_successful_driver_complete(tmp_path, capsys):
    rows = [_row(0, 1, 0), _row(3600, 2, 1), _row(7200, 3, 2)]
    world = _world(tmp_path, rows=rows,
                   summary={'pm_evidence_complete': True, 'acceptance_version': replay.ACCEPTANCE_VERSION})
    assert replay.replay_report(world)['pm_evidence_complete'] is True
    assert replay.main(['report', '--world', str(world)]) == 0
    assert replay.main(['verify', '--world', str(world)]) == 0
