# Path: training/laya_mission_contract.py | Purpose: Validate missions against each frozen runner's capability manifest.
import json
import re
import subprocess
from pathlib import Path

REQUIRED = {'research_select', 'research_labs', 'scale_production'}


def mission_settings(spec, arm, *, new=False, capability=None):
    config = (spec.get('arms') or {}).get(arm) or {}
    mode = config.get('objective_mode', spec.get('objective_mode'))
    target = config.get('research_target', spec.get('research_target'))
    if mode is None:
        if new and (spec.get('design') == 'mixed-start-v1' or target is not None):
            raise ValueError('configuration: objective_mode required for calibration or research target')
        return {'objective_mode': 'legacy_unspecified', 'research_target': target, 'operation_families': None}
    if mode not in ('measurement_only', 'research_completion'):
        raise ValueError('configuration: invalid objective_mode')
    if mode == 'measurement_only' and target is not None:
        raise ValueError('configuration: measurement_only cannot activate a research target')
    if capability is not None and mode not in capability.get('objective_modes', []):
        raise ValueError('unsupported capability: frozen runner cannot enforce objective_mode')
    if mode == 'research_completion':
        if not isinstance(target, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', target):
            raise ValueError('configuration: research_completion requires a nonempty research_target')
        if capability is not None and (mode not in capability.get('objective_modes', []) or
                                       capability.get('actions') != 'deterministic' or
                                       not REQUIRED <= set(capability.get('operation_families', []))):
            raise ValueError('unsupported capability: frozen runner lacks research completion operations')
    return {'objective_mode': mode, 'research_target': target,
            'operation_families': capability.get('operation_families') if capability else None}


def frozen_capability(code, runner, python):
    code = Path(code).resolve()
    runner_path = (code / runner).resolve()
    try:
        runner_path.relative_to(code)
    except ValueError:
        raise ValueError('unsupported capability: runner outside frozen code') from None
    manifest = code / 'training/laya_mission_capabilities.py'
    if not runner_path.is_file() or not manifest.is_file():
        return {}
    try:
        result = subprocess.run([python, str(manifest), '--manifest'], cwd=code, capture_output=True,
                                text=True, timeout=10, check=True)
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('runners'), dict):
            return {}
        entry = data.get('runners', {}).get(runner)
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return {}
