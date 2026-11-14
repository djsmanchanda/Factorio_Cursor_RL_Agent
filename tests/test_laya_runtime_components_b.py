# Path: tests/test_laya_runtime_components_b.py | Purpose: Exercise Track B startup provenance and exclusive publication.
import json
import contextlib
from pathlib import Path

import pytest

from training import laya_runtime_components as components
from tools import run_laya_clean as cli

pytestmark = pytest.mark.fast


def test_exclusive_publication_and_legacy(tmp_path, monkeypatch):
    path = tmp_path / 'components.json'
    value = {'identity': 'one'}
    assert components.install_manifest(path, value)['state'] == 'known'
    assert components.install_manifest(path, value)['state'] == 'known'
    original = path.read_bytes()
    with pytest.raises(components.ManifestMismatch):
        components.install_manifest(path, {'identity': 'two'})
    assert path.read_bytes() == original
    assert components.install_manifest(tmp_path / 'legacy', value, legacy=True)['state'] == 'unknown'
    assert not (tmp_path / 'legacy').exists()

    def fail(_fd):
        raise OSError('flush failed')
    monkeypatch.setattr(components.os, 'fsync', fail)
    with pytest.raises(OSError):
        components.install_manifest(tmp_path / 'failed', value)
    assert not (tmp_path / 'failed').exists()


def test_competing_writer_preserves_complete_winner(tmp_path, monkeypatch):
    path = tmp_path / 'components.json'
    real_link = components.os.link
    def race(src, dst):
        Path(dst).write_bytes(components.manifest_bytes({'winner': True}))
        return real_link(src, dst)
    monkeypatch.setattr(components.os, 'link', race)
    with pytest.raises(components.ManifestMismatch):
        components.install_manifest(path, {'winner': False})
    assert json.loads(path.read_text()) == {'winner': True}


def test_nested_copy_does_not_claim_parent_git(tmp_path, monkeypatch):
    monkeypatch.setattr(components, 'ROOT', Path(__file__).resolve().parents[1] / 'data')
    assert components._git('rev-parse', 'HEAD') is None


def test_cli_rejects_changed_variant_before_adapter(tmp_path, monkeypatch):
    root = tmp_path / 'episode'
    (root / 'evidence').mkdir(parents=True)
    monkeypatch.setattr(cli, 'owned_root', lambda _: root)
    monkeypatch.setattr(cli, 'file_hash', lambda _: 'hash')
    class StopAtAdapter(Exception): pass
    monkeypatch.setattr(cli, 'LiveWorld', lambda *a: (_ for _ in ()).throw(StopAtAdapter()))
    monkeypatch.setattr(cli, 'runner_pid_record', lambda _: __import__('contextlib').nullcontext())
    args = ['run', '--root', str(root), '--seed', '1']
    with pytest.raises(StopAtAdapter):
        cli.main(args)
    journal = root / 'evidence/clean-decisions.jsonl'
    record = json.loads(journal.read_text())
    assert record['runtime_components']['state'] == 'known'
    before = (root / 'evidence/runtime-components.json').read_bytes()
    manifest = json.loads(before)
    import jsonschema
    jsonschema.validate(manifest, json.loads((cli.ROOT / 'schemas/laya_runtime_components.schema.json').read_text()))
    assert manifest['contracts']['decision_record'] == cli.DECISION_RECORD_VERSION
    assert manifest['contracts']['operation_outcome_source'] == cli.OUTCOME_SOURCE_VERSION
    assert manifest['contracts']['strict_predicate_source']['evaluation'] == 'not_attested_by_clean_cli'
    with pytest.raises(StopAtAdapter):
        cli.main(args)
    with pytest.raises(components.ManifestMismatch):
        cli.main(args + ['--seed', '2'])
    variant = tmp_path / 'variant.json'
    variant.write_text(json.dumps({'version': '1.0.0', 'variant_id': 'different', 'policy': {'epsilon': 0.2}}))
    for changes in (['--variant', str(variant)], ['--actions', 'deterministic'],
                    ['--policy', 'exploration'], ['--max-game-ticks', '1200'], ['--stepped']):
        with pytest.raises(components.ManifestMismatch):
            cli.main(args + changes)
    assert (root / 'evidence/runtime-components.json').read_bytes() == before
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len({r['run_id'] for r in records}) == 2


@pytest.mark.parametrize('component', ['planner', 'lua', 'catalog', 'source'])
def test_frozen_inputs_cannot_reopen_another_identity(tmp_path, monkeypatch, component):
    root = tmp_path / 'code'
    files = {'planner': root / 'planners/build.py', 'lua': root / 'factorio_mod/layout_executor.lua',
             'catalog': root / 'docs/rl/catalog.json', 'source': root / 'docs/strategy.md'}
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('initial')
    files['catalog'].write_text(json.dumps({'rules': [{'source': 'docs/strategy.md'}]}))
    monkeypatch.setattr(components, 'ROOT', root)
    mission = {'catalogs': {'policy': 'docs/rl/catalog.json', 'guidance': 'docs/rl/catalog.json'}}
    first = components.build_manifest(entrypoint='test', mission=mission)
    assert first['source']['commit'] is None
    path = tmp_path / 'episode/components.json'
    components.install_manifest(path, first)
    # A comment is semantically harmless but still changes the exact source identity.
    files[component].write_text(files[component].read_text() + '\n ')
    with pytest.raises(components.ManifestMismatch):
        components.install_manifest(path, components.build_manifest(entrypoint='test', mission=mission))
    assert path.read_bytes() == components.manifest_bytes(first)


def test_actual_cli_output_binds_successful_invocation(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'episode'
    (root / 'evidence').mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps({'game_port': 1}))
    monkeypatch.setattr(cli, 'owned_root', lambda _: root)
    monkeypatch.setattr(cli, 'file_hash', lambda _: 'hash')
    monkeypatch.setattr(cli, 'runner_pid_record', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(cli, 'RUN_INDEX', tmp_path / 'index.jsonl')
    monkeypatch.setattr(cli, 'record_laya_run', lambda _: {'outcome': 'test', 'highest_milestone': None})
    class Log:
        def __init__(self, *args): pass
        def start(self, **kwargs): pass
        def __call__(self, message): pass
        def end(self): pass
    monkeypatch.setattr(cli, 'RunLog', Log)
    class World:
        def __init__(self, *args):
            assert (root / 'evidence/runtime-components.json').is_file()
        def go_live(self): pass
        def close(self): pass
    monkeypatch.setattr(cli, 'LiveWorld', World)
    def run(adapter, policy, **kwargs):
        with kwargs['journal'].open('a') as output:
            output.write(json.dumps({'run_id': kwargs['run_id'], 'index': 0, 'result': 'test'}) + '\n')
        return {'decisions': 1, 'stop': 'test', 'phases_reached': []}
    monkeypatch.setattr(cli, 'run', run)
    assert cli.main(['run', '--root', str(root), '--seed', '7']) == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    records = [json.loads(line) for line in (root / 'evidence/clean-decisions.jsonl').read_text().splitlines()]
    assert records[0]['run_id'] == records[1]['run_id'] == summary['run_id']
    assert records[0]['runtime_components'] == summary['runtime_components']
    artifact = Path(summary['runtime_components']['path'])
    assert summary['runtime_components']['sha256'] == components._digest(artifact.read_bytes())
    index = json.loads((tmp_path / 'index.jsonl').read_text())
    assert index['summary']['runtime_components'] == summary['runtime_components']
