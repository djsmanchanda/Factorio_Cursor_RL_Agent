# Path: tests/test_laya_frontier_plan.py | Purpose: Keep frontier plans bounded, isolated and attributable to one declared experimental factor.
import copy
import datetime as dt
import json
from pathlib import Path
import pytest
from tools.laya_frontier_plan import cohorts, prepare, validate, variants, CATALOG

ROOT = Path(__file__).resolve().parents[1]


def test_caps_and_dependencies_fail_closed():
    specs = cohorts()
    validate(specs)
    assert [len(s['variants']) * s['seed_count'] for s in specs] == [60, 60, 60, 12]
    for key, value in [('seed_count', 13), ('depends', ['missing']), ('ports', 39300)]:
        bad = copy.deepcopy(specs)
        bad[0][key] = value
        with pytest.raises(ValueError):
            validate(bad)


def test_each_candidate_changes_one_policy_parameter_or_one_rule_or_one_setting():
    catalog = json.loads((ROOT / CATALOG).read_text())
    choices = variants(catalog)
    for name, value in choices.items():
        factors = [v for k, v in value.items() if k in {'catalog_patch', 'settings', 'policy'}]
        assert len(factors) == (0 if name == 'control' else 1)
        if factors:
            assert len(factors[0]) == 1
    # Avoid the historical accidental stacked mall-slot/core-weight intervention.
    assert choices['batch-free']['catalog_patch']['mission.ops-one-batch']['prefer'][1]['weight'] == 3
    assert choices['foundation8']['catalog_patch']['mission.ops-foundation']['prefer'][1]['weight'] == .1


def test_fresh_manifest_pins_deadline_counts_and_separate_ports(tmp_path):
    source = tmp_path / 'source.zip'
    source.write_bytes(b'fixture')
    root = tmp_path / 'window'
    end = dt.datetime(2026, 10, 24, 3, 51, 47, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    result = prepare(root, code=ROOT, mods=tmp_path, source_save=source, python=Path('/python'),
                     revision='abc', hard_end=end)
    assert sum(c['planned_jobs'] for c in result['cohorts']) == 192
    assert dt.datetime.fromisoformat(result['until']) == end - dt.timedelta(minutes=5)
    for entry in result['cohorts']:
        cfg = json.loads((Path(entry['root']) / 'batch.json').read_text())
        assert len(cfg['arms']) * len(cfg['seeds']) <= 60
        assert all(a['extra_args'] == ['--research-target', 'mining-productivity-4'] for a in cfg['arms'])
        assert Path(cfg['host_budget']['registry']) == Path(entry['root']).parent
    with pytest.raises(ValueError, match='fresh root'):
        prepare(root, code=ROOT, mods=tmp_path, source_save=source, python=Path('/python'), revision='abc', hard_end=end)


def test_experimental_policy_capture_rejected_before_runtime(tmp_path, monkeypatch, capsys):
    from tools import run_laya_clean as cli
    monkeypatch.setattr(cli, 'owned_root', lambda path: path)
    variant = tmp_path / 'variant.json'
    variant.write_text(json.dumps(dict(version='1.0.0', variant_id='hot', policy={'temperature': 3.0})))
    with pytest.raises(SystemExit) as error:
        cli.main(['run', '--root', str(tmp_path / 'world'), '--variant', str(variant),
                  '--ops-checkpoint-dir', str(tmp_path / 'capture')])
    assert error.value.code == 2
    assert 'not supported by ops recovery identity' in capsys.readouterr().err
