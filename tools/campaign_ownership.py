# Path: tools/campaign_ownership.py
# Purpose: Classify dirty worktree paths so targeted commits never sweep user work.
"""Worktree ownership categories for the campaign supervisor.

The campaign must not park because the working tree is not globally clean.
Categories:
- `generated`: runtime/helper artifacts produced by the runner, helper or
  controller (save zips, helper findings, run logs, pytest scratch). Never
  staged, never blocking.
- `campaign`: campaign-owned records the controller itself edits
  (CURRENT_STATUS.md, the observations journal). Updated by the controller;
  staged only when they were clean before the controller touched them.
- `fixer`: source/test paths the current fix declared. Staged on commit.
- `unrelated`: pre-existing user changes that were already dirty when the
  cycle began. Preserved unstaged; never blocking.
- `unexpected`: new dirty paths in no other category. These need a fixer
  declaration (revision) before any commit; unresolved ambiguity blocks.
"""
from __future__ import annotations

from pathlib import Path

GENERATED_PREFIXES = (
    "saves/",
    "runs/",
    "logs/",
    "docs/deterministic/opencode_helper_runs/",
    "docs/rl/laya_helper_runs/",
)

CAMPAIGN_FILES = ("CURRENT_STATUS.md",)


def normalize(path: str) -> str | None:
    """Return a repo-relative posix path, or None when unsafe/outside repo."""
    candidate = Path(path.strip())
    if candidate.is_absolute() or ".." in candidate.parts or not path.strip():
        return None
    return candidate.as_posix()


def is_generated(path: str) -> bool:
    return path.startswith(GENERATED_PREFIXES)


def is_campaign_owned(path: str) -> bool:
    return path in CAMPAIGN_FILES


def classify_dirty(
    dirty: set[str], *, fixer_files: set[str], pre_cycle_dirty: set[str],
    campaign_files: set[str] | None = None,
) -> dict[str, set[str]]:
    owned = set(CAMPAIGN_FILES) | set(campaign_files or ())
    categories: dict[str, set[str]] = {
        "generated": set(), "campaign": set(), "fixer": set(),
        "unrelated": set(), "unexpected": set(),
    }
    for path in sorted(dirty):
        if is_generated(path):
            categories["generated"].add(path)
        elif is_campaign_owned(path) or path in (campaign_files or set()):
            categories["campaign"].add(path)
        elif path in fixer_files:
            categories["fixer"].add(path)
        elif path in pre_cycle_dirty:
            categories["unrelated"].add(path)
        else:
            categories["unexpected"].add(path)
    return categories


def unsafe_declared(declared: list[str], *, unrelated: set[str], repo_root: Path) -> str | None:
    """Reject declarations that touch unrelated pre-existing user work or escape the repo."""
    root = repo_root.resolve()
    for path in declared:
        if path in unrelated:
            return f"declared path is unrelated pre-existing user work: {path}"
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            return f"declared path escapes the repository: {path}"
    return None


def commit_paths(*, fixer_files: list[str], status_clean_before: bool) -> list[str]:
    """Paths to stage for the scoped commit (never unrelated/generated work)."""
    staged = [path for path in fixer_files if path and path != "none"]
    if status_clean_before and "CURRENT_STATUS.md" not in staged:
        staged.append("CURRENT_STATUS.md")
    return staged
