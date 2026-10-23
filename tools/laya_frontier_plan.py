# Path: tools/laya_frontier_plan.py | Purpose: Materialize bounded, one-factor Starship screening cohorts from an explicit immutable source.
"""Prepare manifests only; lifecycle remains owned by the recorded systemd units."""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from tools.laya_batch_defaults import DEFAULT_TRIAL_WALL_SECONDS

CATALOG = 'docs/rl/laya_ops_guidelines.json'


def variants(catalog: dict) -> dict[str, dict]:
    rules = {r['rule_id']: r for r in catalog['rules']}
    result = {}
    def add(name, description, **fields):
        result[name] = dict(version='1.0.0', variant_id='frontier-' + name,
                            description=description, catalog=CATALOG, **fields)
    def patch(name, description, rule, transform):
        preferences = copy.deepcopy(rules[rule].get('prefer', []))
        transform(preferences)
        add(name, description, catalog_patch={rule: {'prefer': preferences}})
    add('control', 'Exploratory reference: prior implementation at 9ac7937, not a new adoption.')
    add('epsilon45', 'Increase only uniform exploration mass from 0.15 to 0.45.', policy={'epsilon': .45})
    add('temperature3', 'Flatten the complete multiplicative prior with temperature 3.', policy={'temperature': 3.0})
    add('families', 'Split exploration mass equally by legal operation family.', policy={'exploration_mode': 'families'})
    add('novelty4', 'Multiply explicit untried-option prior by five; unknown history is neutral.', policy={'novelty_strength': 4.0})
    patch('mall-flat', 'Remove only mall-slot preferences (core x180 and other x6 become x1).',
          'mission.ops-mall-slots', lambda p: p.clear())
    patch('foundation8', 'Raise ready-starter foundation preference x1 to x8.',
          'mission.ops-foundation', lambda p: p[0].update(weight=8.0))
    patch('batch-free', 'Remove active-batch x0.05 soft suppression; keep the wait preference and all ownership guards.',
          'mission.ops-one-batch', lambda p: p[0].update(weight=1.0))
    patch('steel30', 'Raise steel-seed operation prior x5 to x30; funding and admission remain enforced.',
          'mission.ops-steel', lambda p: p[0].update(weight=30.0))
    patch('deepest16', 'Raise deepest-bottleneck prior x2 to x16, preserving other factors.',
          'mission.serve-bottleneck', lambda p: p[1].update(weight=16.0))
    patch('parallel-goals', 'Remove side-goal x0.2 suppression while retaining current-goal x3.',
          'mission.phase-goals-first', lambda p: p[1].update(weight=1.0))
    patch('retry-free', 'Remove unchanged-failure x0.05 soft penalty; factual legality and operation guards remain.',
          'mission.ops-no-blind-retry', lambda p: p.clear())
    patch('feedstock16', 'Raise measured feedstock growth prior x4 to x16.',
          'mission.ops-grow-feedstock', lambda p: p[0].update(weight=16.0))
    add('ramp125', 'Start science at 0.25; grow x1.25 after each identical earned sustain window.',
        settings={'research_growth_factor': 1.25})
    add('ramp4', 'Start science at 0.25; grow x4 after each identical earned sustain window.',
        settings={'research_growth_factor': 4.0})
    return result


def cohorts() -> list[dict]:
    return [
        dict(id='sampling', variants=['control', 'epsilon45', 'temperature3', 'families', 'novelty4'],
             seed_start=80600, seed_count=12, concurrency=18, wall=DEFAULT_TRIAL_WALL_SECONDS, ticks=540000, ports=39000, depends=[]),
        dict(id='capital', variants=['control', 'mall-flat', 'foundation8', 'batch-free', 'steel30'],
             seed_start=80700, seed_count=12, concurrency=18, wall=DEFAULT_TRIAL_WALL_SECONDS, ticks=540000, ports=39100, depends=['sampling']),
        dict(id='bottleneck', variants=['control', 'deepest16', 'parallel-goals', 'retry-free', 'feedstock16'],
             seed_start=80800, seed_count=12, concurrency=18, wall=DEFAULT_TRIAL_WALL_SECONDS, ticks=540000, ports=39200, depends=['capital']),
        dict(id='research', variants=['control', 'ramp125', 'ramp4'], seed_start=80900, seed_count=4,
             concurrency=6, wall=15000, ticks=1080000, ports=39300, depends=[]),
    ]


def validate(specs):
    ids = set()
    ports = set()
    for spec in specs:
        count = spec['seed_count'] * len(spec['variants'])
        if not 1 <= count <= 60:
            raise ValueError('cohort must contain 1..60 TOTAL planned trials')
        if (spec['id'] in ids or spec['variants'][0] != 'control'
                or len(set(spec['variants'])) != len(spec['variants'])):
            raise ValueError('unique cohorts/arms and a matched control are required')
        if not set(spec['depends']) <= ids:
            raise ValueError('dependencies must refer to earlier cohorts')
        ids.add(spec['id'])
        endpoints = set(range(spec['ports'], spec['ports'] + spec['concurrency']))
        endpoints |= {p - 8000 for p in endpoints}
        if ports & endpoints:
            raise ValueError('cohort ports overlap')
        ports |= endpoints


def prepare(root: Path, *, code: Path, mods: Path, source_save: Path, python: Path,
            revision: str, hard_end: dt.datetime) -> dict:
    specs = cohorts()
    validate(specs)
    if hard_end.tzinfo is None:
        raise ValueError('deadline must be timezone-aware')
    if root.exists():
        raise ValueError('window requires a fresh root; preserve previous attempts')
    catalog = json.loads((code / CATALOG).read_text())
    choices = variants(catalog)
    root.mkdir(parents=True)
    registry = root / 'cohorts'
    registry.mkdir()
    variants_root = root / 'variants'
    variants_root.mkdir()
    for name, value in choices.items():
        (variants_root / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')
    # Leave five minutes for orderly owned-world cleanup before the hard cap.
    until = hard_end - dt.timedelta(minutes=5)
    manifest = dict(version='laya-frontier-window-v1', revision=revision, code=str(code),
                    source_save=str(source_save), source_sha256=hashlib.sha256(source_save.read_bytes()).hexdigest(),
                    mods=str(mods), registry=str(registry), hard_end=hard_end.isoformat(), until=until.isoformat(),
                    max_total_tests_per_cohort=60, max_worlds=24, control_is_experimental=True,
                    automatic_adoption=False, cohorts=[], confirmation=None)
    for spec in specs:
        directory = registry / spec['id']
        directory.mkdir()
        cfg = dict(name='frontier-20261023-' + spec['id'], until=until.isoformat(),
                   concurrency=spec['concurrency'], trial_wall_seconds=spec['wall'], max_game_ticks=spec['ticks'],
                   source_save=str(source_save), python=str(python),
                   game_port_base=spec['ports'], rcon_port_base=spec['ports'] - 8000,
                   seeds=list(range(spec['seed_start'], spec['seed_start'] + spec['seed_count'])),
                   host_budget=dict(registry=str(registry), stale_seconds=180, admit_interval_seconds=10,
                                    admit_min_available_gb=4.5),
                   depends_on=[str(registry / name) for name in spec['depends']],
                   ops_checkpoints=spec['id'] == 'research', arms=[])
        for name in spec['variants']:
            cfg['arms'].append(dict(id=name, code=str(code), mods=str(mods), revision=revision,
                variant=str(variants_root / (name + '.json')), change=choices[name]['description'],
                extra_args=['--research-target', 'mining-productivity-4']))
        (directory / 'batch.json').write_text(json.dumps(cfg, indent=2) + '\n')
        # This is the attribution contract; Batch separately records its immutable planned-job ledger.
        freeze = dict(revision=revision, source_sha256=manifest['source_sha256'],
                      batch_sha256=hashlib.sha256((directory / 'batch.json').read_bytes()).hexdigest(),
                      variant_sha256={name: hashlib.sha256((variants_root / (name + '.json')).read_bytes()).hexdigest()
                                      for name in spec['variants']}, uncommitted_diff=None,
                      comparison='each candidate vs control, not adjacent candidate arms',
                      primary_endpoint='research earned' if spec['id'] == 'research' else 'laya-c6-choice-output-v2',
                      planned_jobs=spec['seed_count'] * len(spec['variants']))
        (directory / 'freeze.json').write_text(json.dumps(freeze, indent=2) + '\n')
        manifest['cohorts'].append(dict(id=spec['id'], root=str(directory), planned_jobs=freeze['planned_jobs']))
    (root / 'window.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
