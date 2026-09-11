# Path: orchestrator/production_acceptance.py
# Purpose: Verify sustained scoped production with game-tick craft evidence, never stock deltas.
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable


def read_sample(client, *, surface: str, force: str, target: str, output_position) -> dict:
    # JSON quoted strings are valid Lua literals for these controlled identifiers.
    s, f, item = (json.dumps(value) for value in (surface, force, target))
    x, y = map(float, output_position)
    if not all(math.isfinite(v) for v in (x, y)):
        raise ValueError('invalid production output position')
    lua = (
        f'local s=game.surfaces[{s}];local f=game.forces[{f}];'
        'if not s or not f then error("acceptance scope missing") end;'
        f'local out={{tick=game.tick,surface=s.name,force=f.name,target={item},machines={{}}}};'
        f'local p=s.find_entities_filtered{{position={{{x},{y}}},radius=0.1,force=f,type="logistic-container"}}[1];'
        f'if p and (p.name=="passive-provider-chest" or p.name=="active-provider-chest") then out.provider_id=p.unit_number;out.provider_count=p.get_item_count({item}) end;'
        f'for _,e in pairs(s.find_entities_filtered{{position={{{x},{y}}},radius=24,force=f,type={{"assembling-machine","furnace"}}}}) do '
        'local r=e.get_recipe();'
        f'if r and r.name=={item} then out.machines[tostring(e.unit_number)]=e.products_finished end end;'
        'rcon.print(helpers.table_to_json(out))'
    )
    return json.loads(client.command('/sc ' + lua).strip())


def evaluate(samples: list[dict], *, seconds: int, sample_seconds: int = 10) -> bool:
    """A fixed producer cohort must advance in each sampled interval of the window."""
    start = None
    previous = None
    for sample in samples:
        if not sample.get('machines') or sample.get('provider_count', 0) <= 0:
            start = previous = None
            continue
        if previous is not None:
            delta = sample['tick'] - previous['tick']
            same = (sample['machines'].keys() == previous['machines'].keys()
                    and sample.get('provider_id') == previous.get('provider_id'))
            advancing = same and all(sample['machines'][key] >= value for key, value in previous['machines'].items()) and sum(sample['machines'].values()) > sum(previous['machines'].values())
            if not (sample_seconds * 60 <= delta <= sample_seconds * 120 and advancing):
                start = sample['tick']
        else:
            start = sample['tick']
        previous = sample
        if sample['tick'] - start >= seconds * 60:
            return True
    return False


def monitor(*, read: Callable[[], dict], path: Path, episode_id: str | None,
            provenance: dict, target: str, surface: str, force: str,
            seconds: int = 120, timeout_seconds: int = 600,
            clock=time.monotonic, sleep=time.sleep) -> dict:
    if seconds <= 0 or seconds % 10 or timeout_seconds < seconds:
        raise ValueError('acceptance window must be positive, divisible by 10, and within timeout')
    report = dict(schema_version=1, ok=False, result='running', episode_id=episode_id,
                  target=target, surface=surface, force=force, provenance=provenance,
                  acceptance_seconds=seconds, sample_seconds=10, start_tick=None, end_tick=None, samples=[])
    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(path)
    save()
    deadline = clock() + timeout_seconds
    try:
        while clock() < deadline:
            sample = read()
            if any(sample.get(k) != report[k] for k in ('surface', 'force', 'target')):
                raise ValueError('acceptance scope mismatch')
            tick = sample['tick']
            if report['start_tick'] is None:
                report['start_tick'] = tick
            if report['end_tick'] is not None and tick < report['end_tick']:
                raise ValueError('simulation tick regressed')
            if report['end_tick'] is None or tick - report['end_tick'] >= 600:
                report['samples'].append(sample)
                report['end_tick'] = tick
                if evaluate(report['samples'], seconds=seconds):
                    report.update(ok=True, result='passed')
                    break
                save()
            if tick - report['start_tick'] >= timeout_seconds * 60:
                break
            sleep(1)
        if not report['ok']:
            report['result'] = 'production_acceptance_timeout'
    except Exception as error:
        report.update(result='production_acceptance_invalid', error=f'{type(error).__name__}: {error}')
    save()
    return report
