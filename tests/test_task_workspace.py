# Path: tests/test_task_workspace.py | Purpose: Verify workspace safety with disposable Git repositories.
import json
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = [pytest.mark.ops, pytest.mark.integration]
HELPER = Path(__file__).resolve().parents[1] / "tools" / "task_workspace.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def cli(repo, *args):
    return subprocess.run([sys.executable, str(HELPER), *map(str, args)], cwd=repo, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "parent repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Workspace Test")
    git(root, "config", "user.email", "workspace@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "tracked.txt").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    return root


def create(repo, target, base="HEAD", machine="laptop-a", task="example"):
    return cli(repo, "create", "--base", base, "--machine", machine, "--task", task, "--path", target)


def test_explicit_base_and_dirty_parent_preserved(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "tracked.txt").write_text("new commit\n")
    git(repo, "commit", "-am", "advance parent")
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", ".")
    (repo / "tracked.txt").write_text("unstaged\n")
    (repo / "untracked file").write_text("local only\n")
    before = (git(repo, "status", "--porcelain=v1", "-z"), git(repo, "diff", "--cached"),
              git(repo, "diff"), git(repo, "rev-parse", "HEAD"), git(repo, "symbolic-ref", "HEAD"))
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text('#!/bin/sh\nprintf "hook ran" > hook-ran\n')
    hook.chmod(0o755)
    target = tmp_path / "task space; literal $name"
    result = create(repo, target, base)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["head"] == base
    assert git(target, "rev-parse", "HEAD").decode().strip() == base
    assert git(target, "branch", "--show-current").strip() == b"work/laptop-a/example"
    assert (target / "tracked.txt").read_text() == "base\n"
    assert not (target / "untracked file").exists()
    assert not (target / "hook-ran").exists()
    assert (repo / "untracked file").read_text() == "local only\n"
    assert before == (git(repo, "status", "--porcelain=v1", "-z"), git(repo, "diff", "--cached"),
                      git(repo, "diff"), git(repo, "rev-parse", "HEAD"), git(repo, "symbolic-ref", "HEAD"))


@pytest.mark.parametrize("collision", ["branch", "empty-directory", "file"])
def test_collision_fails_safely(repo, tmp_path, collision):
    target = tmp_path / "target"
    if collision == "branch":
        git(repo, "branch", "work/laptop-a/example")
    elif collision == "empty-directory":
        target.mkdir()
    else:
        target.write_text("preserve me")
    refs = git(repo, "show-ref")
    worktrees = git(repo, "worktree", "list", "--porcelain")
    result = create(repo, target)
    assert result.returncode != 0
    assert git(repo, "show-ref") == refs
    assert git(repo, "worktree", "list", "--porcelain") == worktrees
    if collision == "branch":
        assert not target.exists()
    elif collision == "file":
        assert target.read_text() == "preserve me"
    else:
        assert list(target.iterdir()) == []


@pytest.mark.parametrize("machine,task,base", [("../bad", "ok", "HEAD"), ("ok", "bad/task", "HEAD"),
                                               ("ok", "ok", "missing-revision")])
def test_invalid_input_leaves_no_worktree(repo, tmp_path, machine, task, base):
    target = tmp_path / "target"
    refs = git(repo, "show-ref")
    assert create(repo, target, base, machine, task).returncode != 0
    assert not target.exists()
    assert git(repo, "show-ref") == refs


def test_status_without_upstream_is_read_only(repo):
    git(repo, "mv", "tracked.txt", "renamed file.txt")
    (repo / "untracked\nfile").write_text("local")
    index = repo / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    result = cli(repo, "status")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["hostname"]
    assert report["branch"] == "main"
    assert report["head"] == git(repo, "rev-parse", "HEAD").decode().strip()
    assert report["dirty_paths"] == ["renamed file.txt", "tracked.txt", "untracked\nfile"]
    assert report["upstream"] is report["ahead"] is report["behind"] is None
    assert before == (index.read_bytes(), index.stat().st_mtime_ns)


def test_status_local_upstream_counts_and_detached_head(repo):
    git(repo, "branch", "shared-base")
    git(repo, "branch", "--set-upstream-to=shared-base")
    (repo / "tracked.txt").write_text("ahead\n")
    git(repo, "commit", "-am", "ahead")
    report = json.loads(cli(repo, "status").stdout)
    assert (report["upstream"], report["ahead"], report["behind"]) == ("refs/heads/shared-base", 1, 0)
    git(repo, "checkout", "--detach")
    report = json.loads(cli(repo, "status").stdout)
    assert report["branch"] is report["upstream"] is None
