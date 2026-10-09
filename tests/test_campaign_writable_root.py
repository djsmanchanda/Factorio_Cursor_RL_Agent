# Path: tests/test_campaign_writable_root.py | Purpose: Keep campaign model edits in the explicit writable worktree instead of immutable supervisor code.
from pathlib import Path
from types import SimpleNamespace

from tools import opencode_campaign_orchestrator as campaign


def test_model_edit_directory_is_explicit_writable_campaign_tree(tmp_path):
    writable = tmp_path / 'campaign-code'
    config = SimpleNamespace(opencode_bin='opencode', model='test-model', variant='high', campaign_worktree=writable)
    command = campaign._opencode_command(config, 'fix one behavior', None)
    assert Path(command[command.index('--dir') + 1]) == writable
