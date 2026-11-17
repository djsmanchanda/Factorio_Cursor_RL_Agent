# Path: tests/test_laya_pm_replay_probe.py | Purpose: PM replay shortage funding and the persistent strict sampler, with fake boundaries.

import json
from types import SimpleNamespace

import pytest

from orchestrator.checkpoint_milestones import _machine_consumption
from tools import laya_pm_replay as replay

pytestmark = pytest.mark.fast

SHORT = {'code': 'material_shortage', 'state': 'supply_wait', 'ok': False,
         'details': {'stage': 'conversion_advanced-circuit',
                     'required': {'assembling-machine-2': 1, 'steel-chest': 3, 'steel-plate': 5},
                     'available': {'assembling-machine-2': 0, 'steel-chest': 0, 'steel-plate': 112}}}


class Session:
    def __init__(self):
        self.calls = []
        self.ticks = iter(range(100, 10_000))

    def tick(self):
        return next(self.ticks)

    def run(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return SHORT if operation == 'production_service' else {'code': 'temporary_mall_batch', 'ok': False}

    def economics(self):
        return {'cells': []}


def drive(tmp_path, **kwargs):
    session = Session()
    result = replay.drive(session, tmp_path / 'journal', max_steps=2, stock_reader=lambda s: {}, **kwargs)
    rows = [json.loads(line) for line in (tmp_path / 'journal').read_text().splitlines()]
    return session, result, rows


def test_shortage_funds_each_short_item_at_its_required_level(tmp_path):
    session, result, rows = drive(tmp_path, fund_shortages=True)
    produce = [kwargs for operation, kwargs in session.calls if operation == 'produce']
    # Two steps, two short items each; steel-plate is stocked and never produced.
    assert produce == [{'item': 'assembling-machine-2', 'stock_target': 1},
                       {'item': 'steel-chest', 'stock_target': 3}] * 2
    funding = [row for row in rows if row['event'] == 'pm_replay_funding']
    assert [(row['step'], row['item'], row['outcome']) for row in funding][:2] == [
        (1, 'assembling-machine-2', 'temporary_mall_batch'), (1, 'steel-chest', 'temporary_mall_batch')]
    assert result['steps'][0]['funding'][1]['stock_target'] == 3


def test_without_the_flag_the_replay_only_retries_the_service(tmp_path):
    session, _, rows = drive(tmp_path)
    assert {operation for operation, _ in session.calls} == {'production_service'}
    assert not any(row['event'] == 'pm_replay_funding' for row in rows)


def test_one_sample_cannot_show_consumption_but_a_persistent_series_can():
    def sample(count):
        return SimpleNamespace(ac_consumers=[{'unit': 7, 'recipe': 'productivity-module', 'count': count}])
    assert _machine_consumption([sample(5)]) == {}
    assert _machine_consumption([sample(5), sample(6), sample(8)]) == {'productivity-module': 3}


def test_sampler_starts_once_without_stale_report_and_stops(tmp_path):
    report = tmp_path / 'evidence/strict.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'strict_current': [{'tick': 1}]}))
    launched = []

    class Process:
        def __init__(self, command, **kwargs):
            launched.append(command)
            self.state = None
        def poll(self):
            return self.state
        def terminate(self):
            self.state = -15
        def wait(self, timeout=None):
            return self.state

    sampler = replay.StrictSampler(tmp_path, report, interval=60, popen=Process)
    sampler.start()
    sampler.start()
    assert len(launched) == 1 and launched[0][-2:] == ['--interval', '60']
    assert not report.exists()
    assert sampler.probe()['pm_consumption_verified'] is False
    sampler.close()
    assert sampler.process.poll() == -15


def test_sampler_starts_after_live_and_before_the_first_attempt(tmp_path):
    events = []

    class Adapter:
        def tick(self):
            events.append('tick')
            return 100 + events.count('tick') - 1
        def go_live(self):
            events.append('live')
        def close(self):
            pass

    class OneSession(Session):
        def run(self, operation, **kwargs):
            events.append(operation)
            return {'code': 'temporary_mall_batch', 'ok': False}
        def close(self):
            pass

    replay.run_live_replay({'reload_verified': True, 'observed_tick': 100}, Adapter, OneSession,
                           tmp_path / 'journal', after_live=lambda: events.append('sampler'),
                           max_steps=1)
    assert events.index('live') < events.index('sampler') < events.index('production_service')


def test_rises_need_a_gap_and_an_increase():
    rows = [{'tick': 1000, 'crafts': 1}, {'tick': 2000, 'crafts': 2}, {'tick': 4700, 'crafts': 2},
            {'tick': 4800, 'crafts': 3}]
    assert replay.sustained_rises(rows) == 2
    assert replay.sustained_rises(rows[:3]) == 1


def test_placement_switches_to_observation_until_two_spaced_rises(tmp_path):
    reports = iter([[], [{'tick': 2000, 'crafts': 1}],
                    [{'tick': 2000, 'crafts': 1}, {'tick': 5700, 'crafts': 2}]])
    calls = []
    ticks = iter(range(100, 100_000, 50))

    class Placed(Session):
        def run(self, operation, **kwargs):
            calls.append(operation)
            return {'code': 'ok', 'ok': True}
        def economics(self):
            return {'cells': [{'recipe': 'productivity-module', 'machines': 1}]}

    now = [0.0]
    result = replay.drive(Placed(), tmp_path / 'journal', max_steps=50,
                          tick_reader=lambda: next(ticks),
                          consumption_probe=lambda: {'samples': next(reports, [])},
                          clock=lambda: now[0], monotonic=lambda: now[0],
                          sleep=lambda s: now.__setitem__(0, now[0] + s), deadline=1000,
                          observe_after_placement=True)
    assert calls == ['production_service']
    assert result['outcome'] == 'pm_evidence_complete' and result['pm_evidence_complete'] is True
    rows = [json.loads(line) for line in (tmp_path / 'journal').read_text().splitlines()]
    observation = next(row for row in rows if row['event'] == 'pm_replay_observation')
    assert observation['rises'] == 2 and (tmp_path / 'pm-placement.json').is_file()


def test_placement_without_rising_consumption_is_not_complete(tmp_path):
    ticks = iter(range(100, 1_000_000, 600))

    class Placed(Session):
        def run(self, operation, **kwargs):
            return {'code': 'ok', 'ok': True}
        def economics(self):
            return {'cells': [{'recipe': 'productivity-module', 'machines': 1}]}

    now = [0.0]
    result = replay.drive(Placed(), tmp_path / 'journal', max_steps=5, max_game_ticks=20_000,
                          tick_reader=lambda: next(ticks),
                          consumption_probe=lambda: {'samples': []},
                          clock=lambda: now[0], monotonic=lambda: now[0],
                          sleep=lambda s: now.__setitem__(0, now[0] + s), deadline=10_000,
                          observe_after_placement=True)
    assert result['outcome'] == 'game_tick_budget_exhausted'
    assert result['pm_placed'] is True and result['pm_evidence_complete'] is False


def shortage(stage, required, available=None):
    return {'code': 'material_shortage', 'ok': False, 'state': 'supply_wait',
            'message': f'{stage} needs material the base does not have',
            'details': {'stage': stage, 'required': required, 'available': available or {}}}


class Nested(Session):
    """The live o3 chain: the AC line needs a long-handed inserter, whose cell needs an assembling-machine-1."""

    answers = {
        'long-handed-inserter': shortage('paired_mall_long-handed-inserter', {'assembling-machine-1': 2}),
        'assembling-machine-1': shortage('paired_mall_assembling-machine-1',
                                         {'electronic-circuit': 3, 'long-handed-inserter': 1}),
        'electronic-circuit': shortage('paired_mall_electronic-circuit', {'copper-cable': 9}),
        'steel-chest': {'code': 'temporary_mall_batch', 'ok': False},
    }

    def run(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == 'production_service':
            return shortage('conversion_advanced-circuit', {'long-handed-inserter': 1, 'steel-chest': 3})
        return self.answers[kwargs['item']]


def funded(tmp_path, **kwargs):
    session = Nested()
    result = replay.drive(session, tmp_path / 'journal', max_steps=1, fund_shortages=True,
                          stock_reader=lambda s: {}, **kwargs)
    rows = [json.loads(line) for line in (tmp_path / 'journal').read_text().splitlines()]
    return session, result, [row for row in rows if row['event'] == 'pm_replay_funding']


def test_a_nested_shortage_funds_its_own_bill_depth_first(tmp_path):
    session, result, rows = funded(tmp_path)
    produce = [(kwargs['item'], kwargs['stock_target']) for operation, kwargs in session.calls if operation == 'produce']
    # long-handed-inserter -> assembling-machine-1 (at the nested bill's level 2) -> electronic-circuit; the
    # inserter named again by the machine's bill is not funded twice; depth 3 stops before copper-cable.
    assert produce == [('long-handed-inserter', 1), ('assembling-machine-1', 2), ('electronic-circuit', 3),
                       ('steel-chest', 3)]
    assert [(row['item'], row['depth'], row['parent']) for row in rows] == [
        ('long-handed-inserter', 1, None), ('assembling-machine-1', 2, 'long-handed-inserter'),
        ('electronic-circuit', 3, 'assembling-machine-1'), ('steel-chest', 1, None)]
    assert rows[0]['bill']['short'] == {'assembling-machine-1': 2}
    assert rows[2]['bill']['short'] == {'copper-cable': 9}  # recorded even where the depth bound stops funding
    assert 'bill' not in rows[3]
    assert result['steps'][0]['funding'] == [{k: v for k, v in row.items() if k != 'event'} for row in rows]


def test_depth_one_funds_only_the_service_bill(tmp_path):
    session, _, rows = funded(tmp_path, funding_depth=1)
    assert [row['item'] for row in rows] == ['long-handed-inserter', 'steel-chest']
    assert {row['depth'] for row in rows} == {1}


def test_each_step_funds_afresh(tmp_path):
    session = Nested()
    replay.drive(session, tmp_path / 'journal', max_steps=2, fund_shortages=True, stock_reader=lambda s: {})
    produce = [kwargs['item'] for operation, kwargs in session.calls if operation == 'produce']
    assert produce.count('assembling-machine-1') == 2  # once per step, not once per replay


def test_a_failed_nested_attempt_is_evidence_not_a_crash(tmp_path):
    class Broken(Nested):
        def run(self, operation, **kwargs):
            if kwargs.get('item') == 'assembling-machine-1':
                raise RuntimeError('rcon dropped')
            return super().run(operation, **kwargs)

    session = Broken()
    replay.drive(session, tmp_path / 'journal', max_steps=1, fund_shortages=True, stock_reader=lambda s: {})
    rows = [json.loads(line) for line in (tmp_path / 'journal').read_text().splitlines()]
    machine = next(row for row in rows if row.get('item') == 'assembling-machine-1')
    assert machine['outcome'] == 'funding_error' and 'bill' not in machine
    assert not any(row.get('item') == 'electronic-circuit' for row in rows)
