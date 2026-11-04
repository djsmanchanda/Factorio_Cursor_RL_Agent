# Path: training/laya_restart_checkpoint.py | Purpose: Capture and verify disposable restart-conditioned checkpoints.
from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import copy
import subprocess
import uuid

from orchestrator.checkpoint_milestones import evaluate_milestone
from tools import milestone_checkpoint as checkpoints
from tools.checkpoint_catalog import atomic_json

KIND = 'laya-restart-segment-v2'
PREDICATE_SOURCE = Path(__file__).resolve().parents[1] / 'orchestrator/checkpoint_milestones.py'


def predicate_hash():
    return hashlib.sha256(PREDICATE_SOURCE.read_bytes()).hexdigest()


def mod_scope(manifest):
    return hashlib.sha256(json.dumps(manifest['mod_hashes'], sort_keys=True).encode()).hexdigest()


def _clock(adapter):
    try:
        value = json.loads(adapter.bridge.command('/sc rcon.print(helpers.table_to_json({tick=game.tick,paused=game.tick_paused}))'))
        if type(value['tick']) is not int or value['tick'] < 0 or value['paused'] is not True:
            raise ValueError('clock is not paused')
        return value['tick']
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Invalid paused clock receipt') from error


def _engine(adapter):
    # Offline fixtures must explicitly identify themselves; they cannot issue reload receipts.
    supplied = getattr(adapter, 'checkpoint_engine_receipt', None)
    if supplied is not None:
        value = supplied() if callable(supplied) else supplied
        if not isinstance(value, dict) or value.get('validated') is not True:
            raise ValueError('Invalid engine receipt')
        value = dict(value)
        value['source'] = 'fixture'
    else:
        pid = int((adapter.root / 'server/factorio.pid').read_text().strip())
        exe = Path(f'/proc/{pid}/exe')
        output = subprocess.check_output([str(exe), '--version'], text=True, timeout=10)
        match = re.search(r'Version:\s*([^\s]+).*?build\s+(\d+)', output, re.I | re.S)
        if not match:
            raise ValueError('Cannot determine engine version/build')
        value = {'version': match.group(1), 'build': int(match.group(2)),
                 'binary_sha256': checkpoints.sha256(exe), 'validated': True, 'source': 'proc'}
    if (not isinstance(value.get('version'), str) or not value['version'] or
            type(value.get('build')) is not int or not re.fullmatch(r'[0-9a-f]{64}', value.get('binary_sha256', ''))):
        raise ValueError('Incomplete engine identity')
    return value


def _boundary(summary, run_id):
    receipt = summary.get('_checkpoint_boundary')
    return (isinstance(receipt, dict) and receipt.get('pid') == os.getpid()
            and receipt.get('run_id') == run_id and receipt.get('operation_active') is False
            and type(receipt.get('decisions')) is int and receipt['decisions'] >= 0)


def _registered_bytes(server, registry):
    return {relative: checkpoints.sha256(path) for spec in registry
            for path, relative in checkpoints._safe_source(server, spec.path, required=spec.required, recursive=spec.recursive)}


@contextmanager
def paused_candidates(adapter, session, summary, run_id):
    """Refresh eligibility while paused and keep the same world through capture."""
    if not _boundary(summary, run_id):
        raise ValueError('Synchronous checkpoint boundary required')
    adapter.bridge.command('/sc game.tick_paused=true; game.ticks_to_run=0')
    try:
        tick = _clock(adapter)
        statuses = session.acceptance()
        if ('error' in statuses or not session._history or session._history[-1].tick != tick
                or _clock(adapter) != tick):
            raise ValueError('Fresh paused checkpoint telemetry unavailable')
        from training.laya_segment_evidence import current_predicates
        yield current_predicates(session)[0]
        if _clock(adapter) != tick:
            raise ValueError('Checkpoint clock moved while paused')
    finally:
        adapter.bridge.command('/sc game.tick_paused=false')


def persist_rebalanced_material(session):
    """Persist only the live ledger's unsaved allocation projection at a boundary."""
    import importlib
    from orchestrator.material_reservations import MaterialReservationLedger
    from training.laya_restart_state import _no_symlinks
    ab = importlib.import_module(type(session).__module__).ab
    live = ab._MATERIAL_RESERVATION_LEDGER
    expected = Path(session.script_output).parent / 'logs/deterministic-material-reservations' / f'{session.episode_id}.json'
    _no_symlinks(expected)
    disk = MaterialReservationLedger(session.script_output, episode_id=session.episode_id,
                                     surface='nauvis', force='player')
    if (live is None or live.path != disk.path or live.episode_id != disk.episode_id
            or live.surface != disk.surface or live.force != disk.force
            or live.revision != disk.revision or live.bootstrap_supply != disk.bootstrap_supply
            or set(live.projects) != set(disk.projects)):
        raise ValueError('Material ledger identity or revision differs from disk')
    for key, project in live.projects.items():
        saved = disk.projects[key]
        if project.state != saved.state and not ({project.state, saved.state} <= {'planned', 'ready', 'supply_wait'}):
            raise ValueError('Material ledger lifecycle differs from disk')
        if replace(project, state=saved.state, reserved=saved.reserved,
                   eta_seconds=saved.eta_seconds) != saved:
            raise ValueError('Material ledger differs beyond allocation projection')
    if live.projects != disk.projects:
        _no_symlinks(live.path)
        live._save()
        restored = MaterialReservationLedger(session.script_output, episode_id=session.episode_id,
                                             surface='nauvis', force='player')
        if restored.projects != live.projects or restored.revision != live.revision:
            raise ValueError('Material allocation did not persist consistently')


def ownership_defect(adapter, session):
    """Audit the supported fresh-C0 lineage and all durable planner locations.

    This supports a fresh-process restart, with the documented cache resets. It
    does not assert controller equivalence or accept arbitrary imported saves.
    """
    import importlib
    ab = importlib.import_module(type(session).__module__).ab
    from training.laya_restart_state import sidecar_registry, _read_sidecars
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.bootstrap_work import BootstrapWorkLedger
    server = adapter.root / 'server'
    lineage = adapter.manifest.get('checkpoint_lineage', {})
    if (lineage.get('origin') not in {'C0', 'segment'} or not lineage.get('source_sha256')
            or session.script_output.resolve() != (server / 'script-output').resolve()
            or tuple(session.reference_point) != (0., 0.)):
        return 'lineage_or_session_scope'
    registry = sidecar_registry(session.script_output, session.episode_id)
    _read_sidecars(registry)  # Includes symlink, bounds and JSON validation.
    live = ab._MATERIAL_RESERVATION_LEDGER
    disk = MaterialReservationLedger(session.script_output, episode_id=session.episode_id,
                                     surface='nauvis', force='player')
    if (live is None or live.path != disk.path or live.revision != disk.revision
            or live.projects != disk.projects or live.bootstrap_supply != disk.bootstrap_supply):
        return 'material_ledger_mismatch'
    work = BootstrapWorkLedger(session.script_output, episode_id=session.episode_id)
    active = ab._BOOTSTRAP_WORK_LEDGER
    if active is None or active.path != work.path or active.edges != work.edges or active.waits != work.waits:
        return 'bootstrap_work_ledger_mismatch'
    districts = ab._BOOTSTRAP_DISTRICT_LEDGER
    if districts is None or districts.episode_id != session.episode_id:
        return 'bootstrap_district_scope'
    districts.states()  # Reject malformed or foreign ownership records.
    from orchestrator.oil_transaction import OilTransactionStore
    OilTransactionStore(session.script_output, session.episode_id, 'nauvis', 'player').load()
    known = {p for role, path in registry.items()
             for p in (list(path.glob('*.json')) if role == 'bootstrap_district' else [path])}
    # Streaming server logs are deliberately excluded; unknown durable files fail closed.
    for path in (server / 'logs').rglob('*'):
        if not path.is_file():
            continue
        if path.is_symlink():
            return 'unknown_sidecar_symlink'
        if path.suffix in {'.json', '.jsonl', '.tmp'} and path not in known and path.name != 'laya-restart-context.json':
            return f'unknown_durable_sidecar:{path.relative_to(server)}'
    return None


def ownership_complete(adapter, session):
    return ownership_defect(adapter, session) is None


def _history(session):
    samples = session._history
    return [s for i, s in enumerate(samples) if i == 0 or s != samples[i - 1]]


def migration_identity(metadata, context, manifest, contract=None):
    source, target = metadata['provenance']['mod_hashes'], manifest['mod_hashes']
    if source == target:
        return None
    if (not isinstance(contract, dict) or contract.get('kind') != 'laya-mod-migration-v1'
            or contract.get('source_mods') != source or contract.get('target_mods') != target
            or contract.get('engine') != context['engine']
            or contract.get('sidecars') != 'identity-v1' or not contract.get('description')):
        raise ValueError('Unsupported mod migration; explicit source/target and sidecar contract required')
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def load(bundle):
    bundle = Path(bundle)
    from training.laya_restart_state import _no_symlinks
    _no_symlinks(bundle)
    metadata = checkpoints.verify(bundle)
    context = json.loads((bundle / 'state/logs/laya-restart-context.json').read_text())
    episode = json.loads((bundle / 'state/episode/current.json').read_text())
    source_predicate = predicate_hash()
    if context.get('predicate_sha256') != source_predicate:
        revision = metadata['provenance']['creator_commit']
        if not re.fullmatch(r'[0-9a-f]{40}',revision):
            raise ValueError('Checkpoint predicate source is unavailable')
        try:
            recorded = subprocess.check_output(['git','show',revision+':orchestrator/checkpoint_milestones.py'],
                cwd=PREDICATE_SOURCE.parents[1],stderr=subprocess.DEVNULL,timeout=10)
            source_predicate = hashlib.sha256(recorded).hexdigest()
        except (OSError,subprocess.SubprocessError) as error:
            raise ValueError('Checkpoint predicate source is unavailable') from error
    if (context.get('kind') != KIND or context.get('checkpoint') != metadata['checkpoint_id']
            or context.get('capture_tick') != metadata['capture_tick']
            or context.get('scope') != {'surface': 'nauvis', 'force': 'player'}
            or context.get('exact_continuation') is not False or context.get('fresh_policy_rng') is not True
            or context.get('predicate_sha256') != source_predicate
            or context.get('predicate') != metadata['predicate']['evidence']
            or context.get('run_id') != metadata['run_id']
            or context.get('episode_id') != metadata['lineage_id']
            or episode.get('episode_id') != metadata['lineage_id']
            or episode.get('repository_revision') != metadata['provenance']['creator_commit']
            or episode.get('mod_hashes') != metadata['provenance']['mod_hashes']
            or episode.get('production_authority') is not False
            or context.get('restart_state', {}).get('scope', {}).get('episode_id') != metadata['lineage_id']
            or context.get('restart_state', {}).get('scope', {}).get('mod_scope') != mod_scope(episode)):
        raise ValueError('Not a supported or consistently paired Laya restart checkpoint')
    return metadata, context


def capture(adapter, session, checkpoint, summary, results, *, run_id, already_paused=False):
    server = adapter.root / 'server'
    manifest = adapter.manifest
    if (manifest.get('production_authority') is not False or manifest.get('runtime') != 'disposable_saved_world'
            or not manifest.get('repository_revision') or not manifest.get('mod_hashes')
            or not _boundary(summary, run_id)):
        raise ValueError('Disposable identity and synchronous runner boundary required')
    sync = getattr(adapter, 'checkpoint_sync_ownership', None)
    if sync is not None:
        sync(session)
    # Completeness is a source-specific assertion, not something the serializer can infer.
    completeness = getattr(adapter, 'checkpoint_ownership_complete', None)
    if not callable(completeness) or completeness(session) is not True:
        diagnose = getattr(adapter, 'checkpoint_ownership_defect', None)
        reason = diagnose(session) if callable(diagnose) else 'unavailable'
        raise ValueError(f'Ownership completeness must be established by the source: {reason}')
    engine = _engine(adapter)
    if not already_paused:
        adapter.bridge.command('/sc game.tick_paused=true; game.ticks_to_run=0')
    try:
        tick = _clock(adapter)
        statuses = session.acceptance()
        if 'error' in statuses or not session._history or session._baseline is None:
            raise ValueError('Strict telemetry unavailable at capture')
        current = session._history[-1]
        if current.tick != tick or _clock(adapter) != tick:
            raise ValueError('Predicate snapshot tick differs from paused clock')
        result = evaluate_milestone(checkpoint, current, baseline=session._baseline, history=_history(session))
        if not result.passed:
            raise ValueError('Checkpoint is not currently earned')
        evidence = result.as_mapping()
        restart = session.export_restart_state(mod_scope=mod_scope(manifest))
        identity = dict(manifest, episode_id=session.episode_id, run_id=run_id)
        episode = server / 'episode/current.json'
        if episode.exists():
            existing = json.loads(episode.read_text())
            if existing != identity:
                raise ValueError('Existing episode identity conflicts with capture')
        else:
            atomic_json(episode, identity)
        context = {'kind': KIND, 'checkpoint': checkpoint, 'capture_tick': tick,
                   'scope': {'surface': 'nauvis', 'force': 'player'}, 'exact_continuation': False,
                   'fresh_policy_rng': True, 'predicate_sha256': predicate_hash(), 'predicate': evidence,
                   'engine': engine, 'run_id': run_id, 'episode_id': session.episode_id,
                   'source_seed': summary.get('seed'), 'restart_state': restart,
                   'source_root': str(adapter.root.resolve()),
                   'lineage': manifest.get('checkpoint_lineage'),
                   'provisional': manifest.get('checkpoint_lineage', {}).get('origin') != 'C0',
                   'continuation_choices': {'proxy_phases': 'carry', 'lifecycle_receipts': 'carry',
                                            'policy_rng': 'fresh', 'planner_caches': 'fresh_process'},
                   'phases_reached': list(summary.get('phases_reached', [])),
                   'lifecycle': [row for row in results if row.get('ok') and row.get('operation') in
                       {'starter', 'foundation', 'extend', 'retire_starter', 'steel', 'reserve_steel_seed', 'mall_slot', 'grow'}]}
        atomic_json(server / 'logs/laya-restart-context.json', context)
        registry = (*checkpoints.default_sidecar_registry(), checkpoints.SidecarSpec('logs/laya-restart-context.json'))
        before = _registered_bytes(server, registry)
        def gate():
            return _boundary(summary, run_id) and _clock(adapter) == tick and completeness(session) is True
        bundle = checkpoints.capture(server, client=adapter.bridge, checkpoint_id=checkpoint,
            generation_id='laya-' + uuid.uuid4().hex, run_id=run_id, lineage_id=session.episode_id,
            capture_tick=tick, predicate_version=result.predicate_version, predicate_evidence=evidence,
            creator_commit=manifest['repository_revision'], mod_hashes=manifest['mod_hashes'],
            quiescence_check=gate, controller_handshake=gate, sidecar_registry=registry)
        copied = {key.removeprefix('state/'): value for key, value in checkpoints.verify(bundle)['files'].items() if key.startswith('state/')}
        if not gate() or before != _registered_bytes(server, registry) or before != copied:
            bundle.rename(bundle.with_name('.rejected-' + bundle.name))
            raise ValueError('Clock, boundary or registered sidecars changed during capture')
        load(bundle)
        return bundle
    finally:
        if not already_paused:
            adapter.bridge.command('/sc game.tick_paused=false')


def restore(bundle, root, *, migration_contract=None):
    from training.laya_restart_state import import_restart_state
    metadata, context = load(bundle)
    if context['predicate_sha256'] != predicate_hash():
        raise ValueError('Restoring code has a different strict predicate source')
    manifest = json.loads((Path(root) / 'manifest.json').read_text())
    if manifest.get('production_authority') is not False or manifest.get('runtime') != 'disposable_saved_world':
        raise ValueError('Checkpoint reload must be disposable')
    migration = migration_identity(metadata, context, manifest, migration_contract)
    if manifest['source_sha256'] != metadata['world']['sha256']:
        raise ValueError('Prepared world does not derive from this checkpoint')
    state = copy.deepcopy(context['restart_state'])
    state['scope']['mod_scope'] = mod_scope(manifest)
    restored = import_restart_state(state, Path(root) / 'server/script-output',
        episode_id=state['scope']['episode_id'], mod_scope=mod_scope(manifest))
    # Embedded roles above are only part of the durable state. Restore every
    # registered auxiliary file before a session can reconcile its ledgers.
    for relative in metadata['files']:
        if not relative.startswith('state/') or relative == 'state/logs/laya-restart-context.json':
            continue
        target = Path(root) / 'server' / relative.removeprefix('state/')
        from training.laya_restart_state import _no_symlinks
        _no_symlinks(target)
        source = Path(bundle) / relative
        if target.exists():
            if checkpoints.sha256(target) != metadata['files'][relative]:
                raise ValueError('Destination durable sidecar conflicts with source')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
    context = {**context, 'migration_contract_sha256': migration}
    return restored, context


def validate_restored(adapter, session, bundle, *, migration_contract=None):
    """Validate a live disposable reload; never upgrades fixture evidence to runtime evidence."""
    metadata, context = load(bundle)
    engine = _engine(adapter)
    if engine.get('source') != 'proc':
        raise ValueError('A live process engine receipt is required for reload validation')
    migration = migration_identity(metadata, context, adapter.manifest, migration_contract)
    if engine != context['engine']:
        raise ValueError('Restored engine identity differs')
    if adapter.manifest.get('production_authority') is not False:
        raise ValueError('Reload must be disposable')
    if _clock(adapter) != metadata['capture_tick']:
        raise ValueError('Loaded tick differs from capture')
    state = session.export_restart_state(mod_scope=mod_scope(adapter.manifest))
    state['scope']['mod_scope'] = context['restart_state']['scope']['mod_scope']
    if state != context['restart_state']:
        raise ValueError('Restored baseline, history or ownership differs')
    statuses = session.acceptance()
    if 'error' in statuses or not session._history or session._history[-1].tick != metadata['capture_tick']:
        raise ValueError('Fresh strict telemetry unavailable')
    result = evaluate_milestone(metadata['checkpoint_id'], session._history[-1],
                                baseline=session._baseline, history=_history(session))
    if not result.passed or _clock(adapter) != metadata['capture_tick']:
        raise ValueError('Restored checkpoint predicate or clock failed')
    if not ownership_complete(adapter, session):
        raise ValueError('Restored ownership audit failed')
    for relative, digest in metadata['files'].items():
        if relative.startswith('state/') and relative != 'state/logs/laya-restart-context.json':
            if checkpoints.sha256(adapter.root / 'server' / relative.removeprefix('state/')) != digest:
                raise ValueError('Restored auxiliary state differs')
    if adapter.root.resolve() == Path(context['source_root']).resolve():
        raise ValueError('Reload must use an independent disposable root')
    pid = int((adapter.root / 'server/factorio.pid').read_text())
    return {'kind': 'laya-disposable-reload-v1', 'status': 'passed',
            'reload_root': str(adapter.root.resolve()), 'server_pid': pid,
            'server_start_ticks': Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19],
            'validator_pid': os.getpid(), 'migration_contract_sha256': migration,
            'migration_contract': copy.deepcopy(migration_contract) if migration is not None else None,
            'ownership_reconstructed': True,
            'generation': metadata['generation_id'], 'checkpoint': metadata['checkpoint_id'],
            'manifest_sha256': checkpoints.sha256(Path(bundle) / 'checkpoint.json'),
            'world_sha256': metadata['world']['sha256'],
            'context_sha256': metadata['files']['state/logs/laya-restart-context.json'],
            'loaded_tick': metadata['capture_tick'], 'origin_episode': metadata['lineage_id'],
            'engine': engine, 'mod_hashes': adapter.manifest['mod_hashes'],
            'predicate': result.as_mapping()}
