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
    result = replay.drive(session, tmp_path / 'journal', max_steps=2, **kwargs)
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


def test_funding_recurses_into_the_shortage_bill_once(tmp_path):
    class Nested:
        def __init__(self):
            self.calls = []

        def run(self, operation, **kwargs):
            self.calls.append(kwargs["item"])
            if kwargs["item"] == 'long-handed-inserter':
                return {'code': 'material_shortage', 'ok': False,
                        'details': {'required': {'assembling-machine-1': 2}}}
            if kwargs['item'] == 'assembling-machine-1':
                return {'code': 'material_shortage', 'ok': False,
                        'details': {'required': {'iron-gear-wheel': 9}}}
            return {'code': 'produced', 'ok': True}

    session = Nested()
    journal = tmp_path / 'journal'
    with journal.open('w') as stream:
        funded = replay._fund_shortage(
            session, stream, 1,
            {'short': {'long-handed-inserter': 1}, 'required': {'long-handed-inserter': 1}})
    assert session.calls == ['long-handed-inserter', 'assembling-machine-1', 'iron-gear-wheel']
    assert [row['item'] for row in funded] == session.calls
    assert funded[-1]['outcome'] == 'produced'
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [row['event'] for row in rows] == ['pm_replay_funding'] * 3
