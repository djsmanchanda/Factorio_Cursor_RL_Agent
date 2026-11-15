# Path: tests/test_loan_gate_restart.py | Purpose: Verify real loan retry conservation across constructor and checkpoint boundaries.
import base64
from dataclasses import replace
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as b, operation_session as ops
from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state, sidecar_registry
from orchestrator.loan_gate_retries import GateRetries, identity
from tests.test_laya_loan_delivery import _loan

pytestmark = [pytest.mark.fast, pytest.mark.deterministic]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from orchestrator import material_reservations
    for name in ('_BOOTSTRAP_DISTRICT_LEDGER', '_MATERIAL_RESERVATION_LEDGER', '_BOOTSTRAP_WORK_LEDGER'):
        monkeypatch.setattr(b, name, None)
    monkeypatch.setattr(material_reservations, '_ACTIVE_LEDGER', None)
    loan = _loan(monkeypatch)
    step = SimpleNamespace(recipe='electronic-circuit', target_count=3)
    clock = {'tick': 10, 'crafts': 862}
    mutations = []
    monkeypatch.setattr(b, '_emit_loan_cell_telemetry', lambda *a, **k: None)
    monkeypatch.setattr(b, '_deliver_cell_ingredients', lambda *a, **k: False)
    monkeypatch.setattr(b, '_bootstrap_loan_products_finished', lambda *a: clock['crafts'])
    monkeypatch.setattr(b.live_base, 'loan_cell_item_count', lambda *a: 100)
    monkeypatch.setattr(b.live_base, 'mall_gate_survey', lambda *a: [{
        'position': loan.machine_position, 'recipe': step.recipe, 'circuit_enabled': False,
        'gate': {'signal': step.recipe, 'comparator': '<', 'constant': 1}}])

    def tick(*args):
        clock['tick'] += 1
        return clock['tick']

    monkeypatch.setattr(b.live_base, 'game_tick', tick)
    monkeypatch.setattr(ops, 'RconClient', lambda *a: object())
    for name in ('_restore_bootstrap_reservations', '_reconcile_bootstrap_work', '_reserve_plastic_output_chest'):
        monkeypatch.setattr(b, name, lambda *a, **k: None)

    def open_session(root):
        (root / 'server/script-output').mkdir(parents=True, exist_ok=True)
        (root / 'manifest.json').write_text('{"rcon_port": 12345}')
        (root / 'server/rcon-password').write_text('offline-test')
        catalog = root / 'catalog.json'
        catalog.write_text('{"recipes": [], "machines": [], "stack_sizes": {}}')
        monkeypatch.setattr(ops, 'GameBridge', lambda **k: SimpleNamespace(
            **k, export_recipe_catalog=lambda **kw: catalog))
        return ops.DeterministicSession(root, logical_episode_id='episode', emit=lambda *a: None)

    session = open_session(tmp_path / 'source')
    def submit(client, bridge, surface, plan, name, emit):
        # A charged repair must already be visible to a fresh storage reader.
        mutations.append(GateRetries(bridge.script_output, 'episode').state['records'][identity(loan, step)]['attempts'])
    monkeypatch.setattr(b, '_submit', submit)
    def advance(session, active=loan):
        return b._submit_bootstrap_loan(session.client, session.bridge, 'nauvis', 'player', active, lambda *a: None)
    def seed():
        # The actual new-loan configuration boundary initializes its known budget.
        advance(session, replace(loan, current_recipe='iron-gear-wheel', step_recipe=None,
                                 step_baseline_finished=None, proposed=True))
        assert mutations.pop() == 0
    def store(session=session):
        return GateRetries(session.script_output, 'episode')
    return SimpleNamespace(loan=loan, step=step, clock=clock, mutations=mutations, session=session,
                           open=open_session, advance=advance, seed=seed, store=store,
                           key=identity(loan, step))


def test_actual_constructor_and_transferred_checkpoint_preserve_three_charges(env, tmp_path):
    env.seed()
    env.advance(env.session)
    reopened = env.open(env.session.script_output.parent.parent)
    env.advance(reopened)
    sample = FactorySnapshot()
    bundle = export_restart_state(reopened.script_output, 'episode', 'mods', sample, [sample], CheckpointEvaluator())
    target = tmp_path / 'target/server/script-output'
    import_restart_state(bundle, target, episode_id='episode', mod_scope='mods')
    transferred = env.open(target.parent.parent)
    env.advance(transferred)
    with pytest.raises(b.StuckError, match='after 3 gate refreshes'):
        env.advance(env.open(target.parent.parent))
    assert env.mutations == [1, 2, 3]
    assert env.store(transferred).state['records'][env.key]['attempts'] == 3


@pytest.mark.parametrize('ticks', [(11, 12), (11, 11), (12, 10)])
def test_restart_does_not_charge_same_or_regressing_tick(env, monkeypatch, ticks):
    env.seed()
    env.advance(env.session)  # First charge at tick 12.
    samples = iter(ticks)
    monkeypatch.setattr(b.live_base, 'game_tick', lambda *a: next(samples))
    assert 'waits for' in env.advance(env.open(env.session.script_output.parent.parent))
    assert env.mutations == [1]


def test_unknown_and_regressing_crafts_cannot_replenish_exhausted_budget(env, monkeypatch):
    env.seed()
    for _ in range(3):
        env.advance(env.session)
    for before, after in [(None, None), (861, 862), (862, 861)]:
        samples = iter((before, after))
        monkeypatch.setattr(b, '_bootstrap_loan_products_finished',
                            (lambda *a: None) if before is None else (lambda *a: next(samples)))
        assert 'waits for' in env.advance(env.session)
        assert env.store().state['records'][env.key]['attempts'] == 3
    # Progress since the saved observation, with advancing time, earns a reset.
    monkeypatch.setattr(b, '_bootstrap_loan_products_finished', lambda *a: 863)
    env.advance(env.session)
    assert env.mutations == [1, 2, 3, 1]


@pytest.mark.parametrize('stage', ['file', 'directory'])
def test_failed_durable_charge_never_dispatches_and_reopen_is_conservative(env, monkeypatch, stage):
    env.seed()
    real_sync = os.fsync
    calls = 0
    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == (1 if stage == 'file' else 2):
            raise OSError('injected flush failure')
        real_sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fail)
        with pytest.raises(OSError, match='injected flush failure'):
            env.advance(env.session)
    assert env.mutations == []
    env.advance(env.session)
    assert env.mutations == ([1] if stage == 'file' else [2])


def test_missing_historical_budget_waits_until_observed_progress(env):
    assert 'waits for' in env.advance(env.session)
    assert env.mutations == []
    env.clock['crafts'] = 863
    env.advance(env.session)
    assert env.mutations == [1]


@pytest.mark.parametrize('version', [1, 2, 3])
def test_legacy_transfer_preserves_unknown_retry_provenance(env, tmp_path, version):
    sample = FactorySnapshot()
    bundle = export_restart_state(env.session.script_output, 'episode', 'mods', sample, [sample], CheckpointEvaluator())
    bundle['version'] = version
    del bundle['sidecars']['loan_gate_retries']
    if version < 3:
        del bundle['sidecars']['operation_invocations']
    if version == 1:
        del bundle['evidence_state']
    target = tmp_path / 'legacy/server/script-output'
    import_restart_state(bundle, target, episode_id='episode', mod_scope='mods')
    store = GateRetries(target, 'episode')
    assert store.state['provenance'] == 'unknown'
    store.start(env.key, 862, proposed=True)
    assert not store.observe(env.key, 862, 862, 1, 2)
    assert store.charge(env.key, 2) is None
    upgraded = export_restart_state(target, 'episode', 'mods', sample, [sample], CheckpointEvaluator())
    final = tmp_path / 'upgraded/script-output'
    import_restart_state(upgraded, final, episode_id='episode', mod_scope='mods')
    assert GateRetries(final, 'episode').state == store.state


@pytest.mark.parametrize('corruption', ['foreign', 'duplicate', 'nan', 'attempts', 'version'])
def test_bad_retry_state_is_rejected_before_any_import_write(env, tmp_path, corruption):
    env.seed()
    sample = FactorySnapshot()
    bundle = export_restart_state(env.session.script_output, 'episode', 'mods', sample, [sample], CheckpointEvaluator())
    state = env.store().state
    if corruption == 'foreign': state['scope']['episode_id'] = 'other'
    if corruption == 'attempts': state['records'][env.key]['attempts'] = True
    if corruption == 'version': state['version'] = True
    data = json.dumps(state).encode()
    if corruption == 'duplicate': data = data[:-1] + b', "version": 1}'
    if corruption == 'nan': data = data[:-1] + b', "extra": NaN}'
    path = sidecar_registry(env.session.script_output, 'episode')['loan_gate_retries']
    bundle['sidecars']['loan_gate_retries'][path.name] = {
        'base64': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest()}
    target = tmp_path / 'bad/server/script-output'
    with pytest.raises(ValueError):
        import_restart_state(bundle, target, episode_id='episode', mod_scope='mods')
    assert not target.parent.exists()


def test_other_cell_and_episode_cannot_use_a_known_budget(env):
    env.seed()
    other = replace(env.loan, requester_position=(139.5, 32.5))
    # Same recipe/target at another cell has no recorded craft progress.
    assert not env.store().observe(identity(other, env.step), 862, 862, 1, 2)
    assert GateRetries(env.session.script_output, 'foreign').charge(env.key, 20) is None
    assert env.mutations == []
