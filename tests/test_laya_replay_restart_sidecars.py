# Path: tests/test_laya_replay_restart_sidecars.py | Purpose: PM replay restores the same restart ownership state as a cohort restart (review R1).

import base64
import copy
import hashlib
import json

import pytest

from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state
from orchestrator.loan_gate_retries import GateRetries
from orchestrator.operation_invocations import Invocations, ReadbackRequired
from tools import laya_pm_replay as replay

pytestmark = pytest.mark.fast

EPISODE = 'laya-C4-reference-s1'
KEY = 'a' * 64
PLAN = {'surface': 'nauvis', 'force': 'player', 'atomic': True,
        'phases': [{'name': 'p', 'actions': [{'action_type': 'place_ghost',
                    'entity': 'transport-belt', 'position': {'x': 10, 'y': 10}}]}]}
DISTRICT = 'logs/deterministic-bootstrap-districts/' + EPISODE + '/b5372b10c7f8f832.json'
RESERVATIONS = 'logs/deterministic-material-reservations/' + EPISODE + '.json'
PLAN_LOG = 'script-output/logs/deterministic-plan-reservations.jsonl'


def _fenced(script_output):
    try:
        Invocations(script_output, EPISODE).require_clear()
        return False
    except ReadbackRequired:
        return True


def _source_run(tmp_path):
    """A captured run: one interrupted dispatch, an exhausted gate budget, district and reservation ledgers."""
    server = tmp_path / 'source/server'
    output = server / 'script-output'
    invocations = Invocations(output, EPISODE)
    record = invocations.begin('starter', {})

    def interrupted(*_args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        invocations.submit(record, interrupted, {}, PLAN)
    retries = GateRetries(output, EPISODE)
    retries.start(KEY, 0, proposed=True)
    for tick in (1, 2, 3):
        retries.charge(KEY, tick)
    for relative, text in ((DISTRICT, '{"recipe": "copper-plate"}'), (RESERVATIONS, '{"reserved": {}}'),
                           (PLAN_LOG, '{}\n')):
        (server / relative).parent.mkdir(parents=True, exist_ok=True)
        (server / relative).write_text(text)
    return server


def _bundle(tmp_path, server, *, mutate=None):
    """Write a bundle the way capture does: embedded restart state plus copied state/ files."""
    baseline = FactorySnapshot()
    state = export_restart_state(server / 'script-output', EPISODE, 'mods', baseline, [baseline],
                                 CheckpointEvaluator())
    if mutate is not None:
        mutate(state)
    bundle = tmp_path / 'bundle'
    files = {}
    for relative in (DISTRICT, RESERVATIONS, PLAN_LOG):
        target = bundle / 'state' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((server / relative).read_bytes())
        files['state/' + relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    context = {'restart_state': state}
    path = bundle / replay.RESTART_CONTEXT
    path.write_text(json.dumps(context))
    files[replay.RESTART_CONTEXT] = hashlib.sha256(path.read_bytes()).hexdigest()
    return bundle, (lambda _bundle: ({'files': files}, copy.deepcopy(context)))


def test_replay_restore_keeps_the_dispatch_fence_and_the_spent_retry_budget(tmp_path):
    server = _source_run(tmp_path)
    assert _fenced(server / 'script-output')
    bundle, loader = _bundle(tmp_path, server)
    world = tmp_path / 'replay'
    restored = replay.restore_sidecars(bundle, world, loader=loader)
    output = world / 'server/script-output'
    # The session opens Invocations/GateRetries under restored['episode_id'] (DeterministicSession).
    assert restored['episode_id'] == EPISODE
    assert _fenced(output)
    retries = GateRetries(output, restored['episode_id'])
    retries.start(KEY, 0, proposed=True)  # an existing record is kept, not reset
    assert retries.state['records'][KEY]['attempts'] == 3
    assert retries.charge(KEY, 10) == 0  # exhausted stays exhausted
    for relative in (DISTRICT, RESERVATIONS, PLAN_LOG):
        assert (world / 'server' / relative).read_bytes() == (server / relative).read_bytes()
    assert not (world / 'server/logs/laya-restart-context.json').exists()


def test_replay_restore_installs_what_the_normal_importer_installs(tmp_path):
    server = _source_run(tmp_path)
    bundle, loader = _bundle(tmp_path, server)
    world = tmp_path / 'replay'
    replay.restore_sidecars(bundle, world, loader=loader)
    _metadata, context = loader(bundle)
    normal = tmp_path / 'normal/server/script-output'
    import_restart_state(context['restart_state'], normal, episode_id=EPISODE, mod_scope='mods')

    def tree(root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    embedded = tree(tmp_path / 'normal/server')
    replayed = tree(world / 'server')
    assert embedded and all(replayed[name] == data for name, data in embedded.items())


def test_legacy_bundle_gets_unknown_provenance_markers(tmp_path):
    server = _source_run(tmp_path)

    def legacy(state):
        state['version'] = 1
        del state['evidence_state']
        del state['sidecars']['operation_invocations']
        del state['sidecars']['loan_gate_retries']
    bundle, loader = _bundle(tmp_path, server, mutate=legacy)
    world = tmp_path / 'replay'
    replay.restore_sidecars(bundle, world, loader=loader)
    output = world / 'server/script-output'
    assert _fenced(output)
    retries = GateRetries(output, EPISODE)
    assert retries.state['provenance'] == 'unknown'
    retries.start(KEY, 0, proposed=True)
    assert retries.charge(KEY, 10) is None  # an unknown budget is never charged as fresh


def test_any_invalid_source_or_conflict_writes_nothing(tmp_path):
    server = _source_run(tmp_path)
    bundle, loader = _bundle(tmp_path, server)
    # A tampered auxiliary file is found before any embedded role is written.
    (bundle / 'state' / PLAN_LOG).write_text('{"tampered": 1}\n')
    world = tmp_path / 'replay'
    with pytest.raises(replay.ReplayError, match='manifest'):
        replay.restore_sidecars(bundle, world, loader=loader)
    assert not (world / 'server').exists() or not any(p.is_file() for p in (world / 'server').rglob('*'))
    (bundle / 'state' / PLAN_LOG).write_bytes((server / PLAN_LOG).read_bytes())
    # A destination holding another run's ledger conflicts; nothing else is written.
    other = world / 'server' / PLAN_LOG
    other.parent.mkdir(parents=True)
    other.write_text('{"other": "run"}\n')
    with pytest.raises(replay.ReplayError, match='conflicts'):
        replay.restore_sidecars(bundle, world, loader=loader)
    assert [p for p in (world / 'server').rglob('*') if p.is_file()] == [other]
    other.unlink()
    replay.restore_sidecars(bundle, world, loader=loader)
    # A second restore into a populated world is refused, like a cohort restart.
    with pytest.raises(replay.ReplayError, match='not empty'):
        replay.restore_sidecars(bundle, world, loader=loader)


def test_an_embedded_record_from_another_episode_is_refused(tmp_path):
    server = _source_run(tmp_path)

    def foreign(state):
        role = state['sidecars']['loan_gate_retries']
        name, = role
        data = json.loads(base64.b64decode(role[name]['base64']))
        data['scope']['episode_id'] = 'other'
        raw = json.dumps(data).encode()
        role[name] = {'sha256': hashlib.sha256(raw).hexdigest(),
                      'base64': base64.b64encode(raw).decode()}
    bundle, loader = _bundle(tmp_path, server, mutate=foreign)
    world = tmp_path / 'replay'
    with pytest.raises(replay.ReplayError):
        replay.restore_sidecars(bundle, world, loader=loader)
    assert not (world / 'server').exists() or not any(p.is_file() for p in (world / 'server').rglob('*'))
