# Path: tests/test_campaign_codex.py | Purpose: Verify owning-thread handoffs, measured wall stalls and immutable bounded campaign clocks.
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from tools import campaign_codex as root
from tools import campaign_pingpong as pp
from tools import campaign_runtime as runtime
from tools import opencode_campaign_orchestrator as orch

pytestmark = pytest.mark.fast


def config(tmp_path):
    return orch._config(orch._parser().parse_args([
        '--runtime', 'laya', '--agent-topology', 'deepseek-codex',
        '--campaign-branch', pp.BRANCH, '--campaign-worktree', str(tmp_path),
        '--state-root', str(tmp_path / 'campaign'), '--game-port', '34989', '--rcon-port', '27989',
        '--fixer-model', 'gpt-6.1-sol', '--fixer-variant', 'high',
        '--observer-model', pp.DEEPSEEK, '--observer-variant', 'high']))


def test_native_root_route_never_invokes_haiku_or_bootstraps_another_agent(tmp_path, monkeypatch):
    c = config(tmp_path)
    identity = {'thread_id': 'saved-root', 'codex_session_id': 'native-root', 'model': 'gpt-6.1-sol', 'effort': 'high'}
    monkeypatch.setattr(root, 'root_identity', lambda _: identity)
    monkeypatch.setattr(pp, 'ask_haiku', lambda *args: pytest.fail('Haiku is superseded'))
    monkeypatch.setattr(root, 'ask_root', lambda *args: ('native-root', 'root packet'))
    assert orch._ensure_fixer(c, orch.State(active_cycle=1)) == 'native-root'
    assert orch._ensure_fixer(c, orch.State(active_cycle=2, fixer_session_id='native-root')) == 'native-root'
    assert orch._ask(c, 'DeepSeek plan', 'native-root', 1) == ('native-root', 'root packet')


def test_actual_controller_launch_clock_is_persisted_once_on_restart(tmp_path, monkeypatch):
    c = config(tmp_path)
    pp.atomic(c.state_root / 'campaign.json', {'deepseek_session_id': 'saved'})
    orch._save_state(c.state_file, orch.State(primary_session_id='saved'))
    clock = [1000]
    monkeypatch.setattr(root.time, 'time', lambda: clock[0])
    first = root.initialize_clock(c)
    clock[0] = 4000
    assert root.initialize_clock(c) == first
    assert datetime.fromisoformat(first['deadline_utc']).timestamp() == 22600
    assert datetime.fromisoformat(first['wrap_deadline_utc']).timestamp() == 26200
    assert orch._load_state(c.state_file).primary_session_id == 'saved'


def test_absolute_wrap_cannot_be_swallowed_by_model_retry(tmp_path, monkeypatch):
    c = config(tmp_path)
    pp.atomic(c.state_root / 'campaign.json', {'wrap_deadline_utc': datetime.fromtimestamp(25200, timezone.utc).isoformat()})
    monkeypatch.setattr(root.time, 'time', lambda: 21600)
    handlers, timers = [], []
    monkeypatch.setattr(root.signal, 'signal', lambda *args: handlers.append(args))
    monkeypatch.setattr(root.signal, 'setitimer', lambda *args: timers.append(args))
    root.prepare_wrap(c)
    assert timers[0][1] == 3300  # five-minute report reserve inside the one-hour overrun
    assert not issubclass(root.WrapDeadline, Exception)
    with pytest.raises(root.WrapDeadline):
        handlers[0][1]()


def test_root_episode_disables_old_tick_cutoff_without_duration_or_proxy_caps(tmp_path):
    c = config(tmp_path)
    command = runtime.LayaRuntime().run_command(c)
    assert command[command.index('--stall-observation-ticks') + 1] == '0'
    assert command[command.index('--research-goal') + 1] == 'mining-productivity-4'
    assert '--max-wall-seconds' not in command and '--max-game-ticks' not in command and '--until-accepted' not in command


def signature(crafted=10, delivered=10, research=.2):
    return {'useful': {'crafted': {'transport-belt': crafted}, 'delivered': {'transport-belt': delivered}},
            'live': {'crafted': {'transport-belt': crafted}, 'machines': 4, 'ghosts': 0,
                     'research': 'target', 'level': 3, 'research_progress': research}}


def test_stock_oscillation_and_research_noop_do_not_reset_wall_stall():
    initial = root.progress_sample({}, signature(), now=100, launched_at=0)
    dropped = root.progress_sample(initial, signature(delivered=5), now=1000, launched_at=0)
    restored = root.progress_sample(dropped, signature(), now=1800, launched_at=0)
    assert restored['idle_wall_seconds'] == 1800
    # Serializing and reloading is the restart contract.
    resumed = json.loads(json.dumps(restored))
    assert root.progress_sample(resumed, signature(), now=1900, launched_at=0)['idle_wall_seconds'] == 1900


@pytest.mark.parametrize('changed', [signature(crafted=11), signature(research=.3)])
def test_demonstrated_crafting_or_research_progress_extends_observation(changed):
    first = root.progress_sample({}, signature(), now=100, launched_at=0)
    advanced = root.progress_sample(first, changed, now=1799, launched_at=0)
    assert advanced['last_progress_at'] == 1799
    assert root.progress_sample(advanced, changed, now=3500, launched_at=0)['idle_wall_seconds'] == 1701


def test_watchdog_stops_at_30_wall_minutes_even_when_planner_call_is_blocked(tmp_path, monkeypatch):
    c = config(tmp_path)
    active = replace(c, code_root=tmp_path / 'episode/code', world_root=tmp_path / 'episode/world')
    active.world_root.mkdir(parents=True)
    pp.atomic(active.code_root.parent / 'lifecycle.json', {'phase': 'running', 'run_id': 'owned-run', 'launched_at': 0})
    pp.atomic(active.code_root.parent / 'progress-watchdog.json', root.progress_sample({}, signature(), now=10, launched_at=0))
    orch._save_state(c.state_file, orch.State(episode_runtime={'experiment': 'owned'}))
    backend = SimpleNamespace(restore_config=lambda *_: active, latest_run=lambda *_: (False, 'active'))
    monkeypatch.setattr(runtime, 'backend_for', lambda _: backend)
    monkeypatch.setattr(root, 'live_progress', lambda *args: signature())
    stops = []
    monkeypatch.setattr(root, 'stop_owned_runner', lambda *args: stops.append(args[-1]))
    root.watchdog_once(c, now=1799)
    assert stops == []
    root.watchdog_once(c, now=1800)
    assert stops == ['stalled_wall_30m']


def test_watchdog_cannot_signal_production(tmp_path):
    c = config(tmp_path)
    pp.atomic(tmp_path / 'episode/lifecycle.json', {'pid': 1502810})
    with pytest.raises(RuntimeError, match='safe owned runner'):
        root.stop_owned_runner(c, tmp_path / 'episode', tmp_path / 'world', 'run', 'stalled_wall_30m')


def test_pending_handoff_resumes_delivery_and_waits_for_same_root_completion(tmp_path, monkeypatch):
    c = config(tmp_path)
    orch._save_state(c.state_file, orch.State(active_cycle=1, pending_fix={'phase': 'fixing'},
                                            primary_session_id='saved-deepseek', deadline_utc='2099-01-01T00:00:00+00:00'))
    identity = {'thread_id': 'saved-root', 'codex_session_id': 'native-root', 'model': 'gpt-6.1-sol', 'effort': 'high'}
    monkeypatch.setattr(root, 'root_identity', lambda _: identity)
    monkeypatch.setattr(root, 'require_edit_window', lambda *_: None)
    delivered = []
    def send(config, message_path, intent_path, **kwargs):
        delivered.append(message_path)
        packet_path = message_path.parent / 'completion-001.json'
        pp.atomic(packet_path, {**identity, 'cycle': 1, 'packet': 'CAMPAIGN_DECISION:\nstatus: no-change\n'})
        return {'messageId': 'actual-returned-message'}
    monkeypatch.setattr(root, 'send_root', send)
    sid, output = root.ask_root(c, 'DeepSeek plan', None)
    assert sid == 'native-root' and 'no-change' in output
    root.ask_root(c, 'continue', sid)
    assert delivered[0] == delivered[1]
    assert len(list(delivered[0].parent.glob('handoff-*.md'))) == 1
