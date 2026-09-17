# Path: tests/test_campaign_ownership.py
# Purpose: Worktree ownership classification and scoped-commit contracts.
import subprocess
from pathlib import Path

from tools import campaign_ownership as ownership


def test_generated_and_campaign_paths_never_block(tmp_path):
    dirty = {
        "saves/mod_playground.zip",
        "docs/deterministic/opencode_helper_runs/episode-1/findings.md",
        "runs/pytest-tmp/file",
        "logs/autonomous-run.log",
        "CURRENT_STATUS.md",
        "user_note.md",
        "fix.py",
    }
    categories = ownership.classify_dirty(dirty, fixer_files={"fix.py"}, pre_cycle_dirty={"user_note.md"})
    assert categories["generated"] == {"saves/mod_playground.zip",
                                       "docs/deterministic/opencode_helper_runs/episode-1/findings.md",
                                       "runs/pytest-tmp/file", "logs/autonomous-run.log"}
    assert categories["campaign"] == {"CURRENT_STATUS.md"}
    assert categories["fixer"] == {"fix.py"}
    assert categories["unrelated"] == {"user_note.md"}
    assert categories["unexpected"] == set()


def test_unsafe_declared_paths_are_rejected(tmp_path):
    (tmp_path / "fix.py").write_text("x=1")
    assert ownership.unsafe_declared(["user_note.md"], unrelated={"user_note.md"}, repo_root=tmp_path)
    assert ownership.unsafe_declared(["../escape.py"], unrelated=set(), repo_root=tmp_path)
    assert ownership.unsafe_declared(["/absolute/path.py"], unrelated=set(), repo_root=tmp_path)
    assert ownership.unsafe_declared(["fix.py"], unrelated={"user_note.md"}, repo_root=tmp_path) is None


def test_commit_paths_add_status_only_when_clean_before(tmp_path):
    assert ownership.commit_paths(fixer_files=["fix.py", "none"], status_clean_before=True) == \
        ["fix.py", "CURRENT_STATUS.md"]
    assert ownership.commit_paths(fixer_files=["fix.py"], status_clean_before=False) == ["fix.py"]
    assert ownership.commit_paths(fixer_files=[], status_clean_before=True) == ["CURRENT_STATUS.md"]
    assert ownership.commit_paths(fixer_files=[], status_clean_before=False) == []


def test_normalize_rejects_unsafe_paths():
    assert ownership.normalize(" orchestrator/run_cycle.py ") == "orchestrator/run_cycle.py"
    assert ownership.normalize("") is None
    assert ownership.normalize("../x") is None
    assert ownership.normalize("/abs/path") is None


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _git_out(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def test_real_git_targeted_commit_leaves_unrelated_work_untouched(tmp_path, monkeypatch):
    import types
    from tools import campaign_fix_flow as fix_flow
    from tools import opencode_campaign_orchestrator as orchestrator

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "fix.py").write_text("value = 1\n")
    (repo / "user_note.md").write_text("user work\n")
    _git(repo, "add", "fix.py", "user_note.md")
    _git(repo, "commit", "-m", "baseline")
    # Campaign-time changes: fixer code, campaign status, unrelated user edit,
    # and a generated runtime artifact.
    (repo / "fix.py").write_text("value = 2\n")
    (repo / "CURRENT_STATUS.md").write_text("status entry\n")
    (repo / "user_note.md").write_text("user work, edited\n")
    (repo / "new_helper_report.md").write_text("helper output\n")
    (repo / "saves").mkdir()
    (repo / "saves" / "mod_playground.zip").write_text("runtime artifact")

    def run(command, **_):
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
        return subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)

    monkeypatch.setattr(fix_flow, "_run_git", run)
    monkeypatch.setattr(fix_flow, "_dirty_paths", lambda: {
        "fix.py", "CURRENT_STATUS.md", "user_note.md",
        "new_helper_report.md", "saves/mod_playground.zip"})
    milestones = []
    monkeypatch.setattr(orchestrator, "_record_milestone", lambda fields, fix_id: milestones.append(fields))

    class State:
        active_cycle = 1
        pending_fix = None

    monkeypatch.setattr(orchestrator, "_save_state", lambda path, state: None)

    class Config:
        observations = repo / "notes.md"
        state_root = repo / "state"
        state_file = repo / "state" / "state.json"

    config = Config()
    state = State()
    outcome = fix_flow._commit(
        config, state, "FIX-0001", {"status": "change", "files": "fix.py", "prediction": "next run improves"},
        ["fix.py"], status_clean_before=True, sequence=1,
        verification_log=repo / "verification.log", pre_cycle={"user_note.md"})
    assert outcome == "committed"
    staged_files = _git_out(repo, "show", "--name-only", "--format=", "HEAD").split()
    # Only the declared fix and the campaign status were committed.
    assert staged_files == ["CURRENT_STATUS.md", "fix.py"]
    commit = _git_out(repo, "show", "--format=%H %s", "-s", "HEAD")
    assert "FIX-0001" in commit
    # Unrelated user work stays unstaged and intact; generated artifacts untouched.
    dirty_after = _git_out(repo, "status", "--porcelain")
    assert " M user_note.md" in dirty_after
    assert (repo / "user_note.md").read_text() == "user work, edited\n"
    assert (repo / "new_helper_report.md").is_file()
    assert "?? saves/" in dirty_after or "?? saves" in dirty_after
    assert milestones  # the campaign milestone was recorded for CURRENT_STATUS.md
