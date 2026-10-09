# Path: tests/test_campaign_writable_root.py | Purpose: Keep campaign model edits in the explicit writable worktree instead of immutable supervisor code.
from pathlib import Path
from types import SimpleNamespace

from tools import opencode_campaign_orchestrator as campaign


def test_model_edit_directory_is_explicit_writable_campaign_tree(tmp_path):
    writable = tmp_path / 'campaign-code'
    config = SimpleNamespace(opencode_bin='opencode', model='test-model', variant='high', campaign_worktree=writable)
    command = campaign._opencode_command(config, 'fix one behavior', None)
    assert Path(command[command.index('--dir') + 1]) == writable


def test_module_entrypoint_uses_same_controller_for_runtime_leaves():
    import subprocess
    import sys
    script = """
import runpy, sys
sys.argv = ['opencode_campaign_orchestrator', '--help']
try:
    runpy.run_module('tools.opencode_campaign_orchestrator', run_name='__main__', alter_sys=True)
except SystemExit as exit:
    assert exit.code == 0
controller = sys.modules.get('tools.opencode_campaign_orchestrator')
assert controller is not None, 'runtime leaves would import a second unconfigured controller'
assert controller.__name__ == '__main__', 'entrypoint and runtime leaves must share globals'
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
