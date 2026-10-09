# Path: tests/test_laya_campaign_runner_budget.py | Purpose: Preserve frozen provenance and natural wall-stop/crash evidence without a live server.
import json
from types import SimpleNamespace

import pytest

from tools import run_laya_clean as cli
from training.laya_clean_runner import Settings
from training import laya_ops_runner


@pytest.fixture
def runner_harness(tmp_path, monkeypatch):
    code = tmp_path / 'episode/code'
    world = code / 'data/laya-saved-world/run'
    world.mkdir(parents=True)
    (world / 'manifest.json').write_text('{"game_port":34555}')
    (code.parent / 'freeze.json').write_text('{"revision":"frozen-revision"}')
    source = tmp_path / 'pinned-source.zip'
    source.write_bytes(b'pinned source')
    index = code / 'data/laya-saved-world/laya-runs.jsonl'
    messages = []
    clock = [0.0]
    class Adapter:
        observed_speed = 2
        speed = 2
        def __init__(self, *args):
            pass
        def go_live(self):
            pass
        def observe_speed(self):
            pass
        def tick(self):
            return 120
        def close(self):
            pass
    class Log:
        def __init__(self, *args):
            pass
        def start(self, **fields):
            messages.append(fields)
        def __call__(self, message):
            messages.append(message)
        def elapsed(self, *args):
            return 'ELAPSED: wall_seconds=10 game_seconds=0'
        def end(self):
            messages.append('RUN END')
    monkeypatch.setattr(cli, 'ROOT', code)
    monkeypatch.setattr(cli, 'RUN_INDEX', index)
    monkeypatch.setattr(cli, 'owned_root', lambda path: path)
    monkeypatch.setattr(cli, 'LiveWorld', Adapter)
    monkeypatch.setattr(cli, 'RunLog', Log)
    monkeypatch.setattr(cli, 'load_variant', lambda path: {'variant_id':'test'})
    monkeypatch.setattr(cli, 'apply_variant', lambda variant: (Settings(), {}, {}))
    monkeypatch.setattr(cli, 'variant_hash', lambda variant: 'hash')
    monkeypatch.setattr(cli, 'record_laya_run', lambda row: {'outcome':'test', 'highest_milestone':None})
    monkeypatch.setattr(cli.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(cli.signal, 'signal', lambda *args: None)
    argv = ['run', '--root', str(world), '--source-save', str(source), '--no-helper',
            '--actions', 'deterministic', '--policy', 'exploration', '--speed', '2', '--max-wall-seconds', '10']
    return SimpleNamespace(argv=argv, clock=clock, messages=messages, index=index)


def test_runner_wall_budget_finishes_current_operation_and_indexes_frozen_revision(runner_harness, monkeypatch):
    harness = runner_harness
    def run_ops(*args, should_stop, **kwargs):
        assert should_stop() is False
        harness.clock[0] = 10
        assert should_stop() is True
        return {'decisions':1, 'stop':'requested'}
    monkeypatch.setattr(laya_ops_runner, 'run', run_ops)
    assert cli.main(harness.argv) == 0
    row = json.loads(harness.index.read_text())
    assert row['commit'] == 'frozen-revision'
    assert row['summary']['stop'] == 'wall_budget'
    assert harness.messages[-1] == 'RUN END'
    assert any(isinstance(message, str) and message.startswith('WALL LIMIT:') for message in harness.messages)


def test_runner_crash_still_indexes_terminal_evidence_for_observer(runner_harness, monkeypatch):
    harness = runner_harness
    def fail(*args, **kwargs):
        raise RuntimeError('first hard operation failure')
    monkeypatch.setattr(laya_ops_runner, 'run', fail)
    assert cli.main(harness.argv) == 1
    row = json.loads(harness.index.read_text())
    assert row['summary']['stop'] == 'runner_error'
    assert 'first hard operation failure' in row['summary']['error']
    assert harness.messages[-1] == 'RUN END'
