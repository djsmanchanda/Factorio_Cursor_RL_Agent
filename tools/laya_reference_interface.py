# Path: tools/laya_reference_interface.py | Purpose: Port only restart instrumentation into the proven reference policy.
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import subprocess

REFERENCE = '9d9b2796a3ecb5bc1faed213ea825a6614e8eaff'
FILES = ('training/laya_restart_state.py', 'training/laya_restart_checkpoint.py',
         'training/laya_segment_evidence.py', 'tools/milestone_checkpoint.py',
         'tools/checkpoint_catalog.py', 'tools/laya_checkpoint_bank.py')


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Reference interface anchor changed: {old[:100]}')
    return text.replace(old, new, 1)


def install(source, target):
    """No policy or mod adoption: reference tree plus enumerated interface edits."""
    source, target = Path(source), Path(target)
    revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
    if revision != REFERENCE:
        raise ValueError('Common interface port requires the proven reference revision')
    before = {}
    def write(name, value):
        path = target/name
        before[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value)
        compile(value, str(path), 'exec')
    for name in FILES:
        write(name, (source/name).read_text())
    # The bounded CLI exposes the common interface and existing guided defaults.
    write('tools/run_laya_mixed.py', (source/'tools/run_laya_clean.py').read_text())
    name = 'tools/run_laya_saved_world.py'
    text = (target/name).read_text()
    text = replace_once(text, 'import argparse\n', 'import argparse\nimport os\n')
    text = replace_once(text, '        "--root", str(root / "server"), "--fleet-mode",',
        '        "--runtime-root", os.environ["LAYA_CHECKPOINT_RUNTIME_ROOT"],\n        "--root", str(root / "server"), "--fleet-mode",')
    write(name,text)
    name = 'orchestrator/operation_session.py'
    text = (target/name).read_text()
    text = replace_once(text, 'logical_episode_id: str | None = None) -> None:',
        'logical_episode_id: str | None = None, origin_episode_id: str | None = None) -> None:')
    text = replace_once(text, 'self.episode_id = logical_episode_id or f"laya-{root.name}"',
        'self.episode_id = origin_episode_id or logical_episode_id or f"laya-{root.name}"')
    text = replace_once(text, '        self._history = (self._history + [snapshot])[-20:]',
        '        if not self._history or snapshot != self._history[-1]:\n            self._history = (self._history + [snapshot])[-20:]')
    module = (source/'training/laya_ops.py').read_text()
    cls = next(n for n in ast.parse(module).body if isinstance(n, ast.ClassDef))
    methods = [ast.get_source_segment(module,n) for n in cls.body
               if isinstance(n,ast.FunctionDef) and n.name in {'export_restart_state','restore_restart_acceptance'}]
    text = replace_once(text, '    def close(self) -> None:',
        '\n\n'.join('    '+m.replace('\n','\n    ') for m in methods)+'\n\n    def close(self) -> None:')
    write(name,text)
    name = 'training/laya_ops_runner.py'
    text = (target/name).read_text()
    text = replace_once(text, 'recovery_wall_seconds: float = 0) -> dict:',
        'recovery_wall_seconds: float = 0, initial_reached=(), strict_target=None, checkpoint_callback=None, loaded_tick=None) -> dict:')
    text = replace_once(text, '    reached: list[str] = []', '    reached: list[str] = list(initial_reached)')
    text = replace_once(text, '        if summary["decisions"] % 10 == 0:',
        '        if checkpoint_callback is not None or summary["decisions"] % 10 == 0:')
    text = replace_once(text, '    accept = getattr(session, "acceptance", None)', '''    from training.laya_segment_evidence import current_predicates, stop_classification
    import os
    summary['loaded_tick'] = loaded_tick
    summary['seed'] = seed
    accept = getattr(session, "acceptance", None)''')
    anchor = '                          "foundations": getattr(session, "foundation_observations", None)})'
    text = replace_once(text,anchor,anchor+'''
        tick = session._history[-1].tick if getattr(session, '_history', None) else None
        current, evidence = current_predicates(session) if 'error' not in statuses else ({}, {})
        row = {'event':'strict_current', 'run_id':run_id, 'at':event, 'tick':tick,
               'loaded_tick':summary['loaded_tick'], 'statuses':current, 'predicate_evidence':evidence,
               'decisions':summary['decisions'], 'elapsed_seconds':time.monotonic()-active_started}
        summary.setdefault('strict_current', []).append(row)
        summary['strict_current_at_end'] = current
        _append(journal,row)
        if checkpoint_callback is not None and 'error' not in statuses:
            summary['_checkpoint_boundary'] = {'pid':os.getpid(), 'run_id':run_id,
                                               'operation_active':False, 'decisions':summary['decisions']}
            try:
                checkpoint_callback(adapter,session,statuses,summary,results)
            finally:
                summary.pop('_checkpoint_boundary',None)''')
    text = replace_once(text,'facts["tick"] - samples[0][0] >= settings.max_game_ticks',
        'facts["tick"] - (loaded_tick if loaded_tick is not None else samples[0][0]) >= settings.max_game_ticks')
    text = replace_once(text,'        if should_stop():', '''        if strict_target and summary.get('strict_current_at_end', {}).get(strict_target) is True:
            summary['stop'] = 'strict_target_reached'
            break
        if should_stop():''')
    text = replace_once(text,'        if phase is None:\n            summary["stop"] = "mission_complete"', '''        if strict_target and summary.get('strict_current_at_end', {}).get(strict_target) is True:
            summary['stop'] = 'strict_target_reached'
            break
        if phase is None:
            summary["stop"] = "phase_ladder_complete"''')
    text = replace_once(text,'    check_acceptance("end")', '''    check_acceptance("end")
    summary['stop_classification'] = stop_classification(summary['stop'])''')
    write(name,text)
    # Record every changed byte; the base commit, guided policy, variants, mods
    # and deterministic operations are retained from the reference freeze.
    after = {name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in before}
    return {'kind':'reference-restart-interface-v1', 'reference':REFERENCE,
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip(),
            'before':before,'after':after,
            'policy_change':False, 'fresh_rng':True, 'proxy_phases':'carry', 'lifecycle_receipts':'carry'}
