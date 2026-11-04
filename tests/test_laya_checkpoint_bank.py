# Path: tests/test_laya_checkpoint_bank.py | Purpose: Guard checkpoint boundary and clock receipts.
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from training import laya_restart_checkpoint as checkpoint
from tools import laya_checkpoint_bank as bank

pytestmark = pytest.mark.fast


def test_boundary_requires_current_synchronous_runner():
    receipt = {'pid': os.getpid(), 'run_id': 'r', 'operation_active': False, 'decisions': 3}
    assert checkpoint._boundary({'_checkpoint_boundary': receipt}, 'r')
    assert not checkpoint._boundary({}, 'r')
    for key, value in [('pid', -1), ('run_id', 'other'), ('operation_active', True), ('decisions', True)]:
        assert not checkpoint._boundary({'_checkpoint_boundary': {**receipt, key: value}}, 'r')


def test_clock_requires_paused_integer_tick():
    adapter = SimpleNamespace(bridge=SimpleNamespace(command=lambda _: json.dumps({'tick': 5, 'paused': True})))
    assert checkpoint._clock(adapter) == 5
    adapter.bridge.command = lambda _: json.dumps({'tick': 5, 'paused': False})
    with pytest.raises(ValueError):
        checkpoint._clock(adapter)


def test_missing_checkpoints_are_explicit(tmp_path):
    assert bank.discover(tmp_path) == {f'C{i}': [] for i in range(7)}


def test_changed_mods_need_an_explicit_directional_contract():
    meta = {'provenance':{'mod_hashes':{'mod':'source'}}}
    context = {'engine':{'version':'2.1.24'}}
    manifest = {'mod_hashes':{'mod':'candidate'}}
    with pytest.raises(ValueError,match='Unsupported mod migration'):
        checkpoint.migration_identity(meta,context,manifest)
    contract = {'kind':'laya-mod-migration-v1','source_mods':meta['provenance']['mod_hashes'],
                'target_mods':manifest['mod_hashes'],'engine':context['engine'],
                'sidecars':'identity-v1','description':'Lua storage migration on disposable reload; Python sidecar schema unchanged.'}
    assert len(checkpoint.migration_identity(meta,context,manifest,contract)) == 64
    with pytest.raises(ValueError):
        checkpoint.migration_identity(meta,context,{'mod_hashes':{'mod':'other'}},contract)


def test_changed_mod_reload_receipt_is_bound_to_contract_and_does_not_promote_source(tmp_path, monkeypatch):
    path = tmp_path/'bundle'
    path.mkdir()
    (path/'checkpoint.json').write_text('{}')
    engine = {'version':'2.1.24','source':'proc'}
    meta = {'checkpoint_id':'C4','capture_tick':42,'lineage_id':'origin',
            'world':{'sha256':'world'},'files':{'state/logs/laya-restart-context.json':'context'},
            'predicate':{'version':'c4-v1'},'provenance':{'mod_hashes':{'mod':'source'}}}
    context = {'engine':engine, 'source_root':'/old/source'}
    monkeypatch.setattr(bank, '_candidate', lambda *_: (path, meta, context))
    contract = {'kind':'laya-mod-migration-v1','source_mods':{'mod':'source'},
                'target_mods':{'mod':'candidate'},'engine':engine,'sidecars':'identity-v1',
                'description':'validated candidate migration'}
    digest = checkpoint.migration_identity(meta, context, {'mod_hashes':{'mod':'candidate'}}, contract)
    receipt = {'kind':'laya-disposable-reload-v1','status':'passed','generation':'gen',
               'checkpoint':'C4','manifest_sha256':bank.checkpoints.sha256(path/'checkpoint.json'),
               'world_sha256':'world','context_sha256':'context','loaded_tick':42,
               'origin_episode':'origin','engine':engine,'mod_hashes':{'mod':'candidate'},
               'migration_contract':contract,'migration_contract_sha256':digest,
               'ownership_reconstructed':True,'server_start_ticks':'123','server_pid':1,
               'reload_root':'/independent','predicate':{'status':'passed','checkpoint_id':'C4',
                                                         'predicate_version':'c4-v1'}}
    assert bank.checked_receipt(tmp_path, 'gen', receipt) == receipt
    invalid = {**receipt, 'migration_contract':{**contract, 'target_mods':{'mod':'other'}}}
    with pytest.raises(ValueError, match='Unsupported mod migration'):
        bank.checked_receipt(tmp_path, 'gen', invalid)
    directory = tmp_path/'validations/gen'
    directory.mkdir(parents=True)
    (directory/'receipt.json').write_text(json.dumps(receipt))
    assert bank.validated_receipts(tmp_path, 'gen') == []
    assert bank.validated_receipts(tmp_path, 'gen', mod_hashes={'mod':'candidate'}) == [receipt]


def test_fixture_engine_cannot_masquerade_as_live_receipt():
    adapter = SimpleNamespace(checkpoint_engine_receipt={'validated':True,'version':'2.1.24',
        'build':87808,'binary_sha256':'a'*64,'source':'proc'})
    assert checkpoint._engine(adapter)['source'] == 'fixture'


def test_boundary_persists_only_unsaved_material_allocation(tmp_path, monkeypatch):
    from orchestrator.material_reservations import MaterialReservationLedger
    from orchestrator.bootstrap_work import BootstrapWorkLedger
    from orchestrator.bootstrap_district import BootstrapDistrictLedger
    server = tmp_path/'server'
    output = server/'script-output'
    output.mkdir(parents=True)
    class Session:
        script_output = output
        episode_id = 'episode'
        reference_point = (0., 0.)
    session = Session()
    live = MaterialReservationLedger(output, episode_id='episode', surface='nauvis', force='player')
    live.declare('p', {'iron-plate': 10}, {})
    live.allocatable_stock({'iron-plate': 10})  # Rebalances memory without writing the ledger.
    ab = SimpleNamespace(_MATERIAL_RESERVATION_LEDGER=live,
        _BOOTSTRAP_WORK_LEDGER=BootstrapWorkLedger(output, episode_id='episode'),
        _BOOTSTRAP_DISTRICT_LEDGER=BootstrapDistrictLedger(output, episode_id='episode',
            surface='nauvis', force='player', bootstrap_profile='reduced-v1'))
    monkeypatch.setattr(sys.modules[__name__], 'ab', ab, raising=False)
    adapter = SimpleNamespace(root=tmp_path, manifest={'checkpoint_lineage':
        {'origin':'C0', 'source_sha256':'a'*64}})
    assert not checkpoint.ownership_complete(adapter, session)
    checkpoint.persist_rebalanced_material(session)
    assert checkpoint.ownership_complete(adapter, session)
    live.projects['p'] = replace(live.projects['p'], required={'iron-plate': 11})
    with pytest.raises(ValueError, match='beyond allocation projection'):
        checkpoint.persist_rebalanced_material(session)
    assert MaterialReservationLedger(output, episode_id='episode', surface='nauvis',
                                     force='player').projects['p'].required == {'iron-plate': 10}


def test_candidate_eligibility_is_refreshed_and_held_paused(monkeypatch):
    from training import laya_segment_evidence
    calls = []
    def command(value):
        calls.append(value)
        return json.dumps({'tick':10, 'paused':True}) if 'paused=game.tick_paused' in value else ''
    session = SimpleNamespace(_history=[SimpleNamespace(tick=10)], acceptance=lambda: {'C1':'passed'})
    adapter = SimpleNamespace(bridge=SimpleNamespace(command=command))
    receipt = {'_checkpoint_boundary': {'pid':os.getpid(), 'run_id':'r',
               'operation_active':False, 'decisions':2}}
    monkeypatch.setattr(laya_segment_evidence, 'current_predicates', lambda _: ({'C1':False}, {}))
    with checkpoint.paused_candidates(adapter, session, receipt, 'r') as current:
        assert current['C1'] is False
        assert not any('tick_paused=false' in call for call in calls)
    assert 'tick_paused=false' in calls[-1]
