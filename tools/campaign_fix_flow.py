# Path: tools/campaign_fix_flow.py
# Purpose: Persistent-fixer invocation, primary review, verification and scoped commit.
"""One fix cycle: SYNTHESIZING output -> fixer -> primary review -> verification -> commit.

The fixer and primary are persistent campaign sessions (ids held by durable
state); revisions return to the same sessions. Recoverable outcomes — review
rejection, failing verification, dirty campaign-owned or unrelated user files
— loop through bounded revisions or proceed without a commit instead of
parking the campaign. Only genuine, unresolved worktree ambiguity abandons a
fix, and even then the reason is recorded and the campaign continues.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from tools import campaign_loop_log as loop_log
from tools.campaign_observers import _ask as _flow_ask
from tools.campaign_ownership import (
    classify_dirty, commit_paths, is_campaign_owned, is_generated, normalize, unsafe_declared,
)
from tools.campaign_protocol import decision_fields
from tools.campaign_prompts import (
    fixer_episode_invocation, fixer_revision, fixer_verification_failure, primary_review,
)

MAX_FIX_REVISIONS = 2


def _journal(config, text: str) -> None:
    from tools.opencode_campaign_orchestrator import _append
    _append(config.observations, text)


def _fingerprint(config) -> str:
    from tools.opencode_campaign_orchestrator import _tree_fingerprint
    return _tree_fingerprint(config.observations)


def _dirty_paths() -> set[str]:
    from tools.opencode_campaign_orchestrator import _dirty_paths as dirty
    return dirty()


def _run_git(command: list[str], *, timeout: int = 120):
    from tools.opencode_campaign_orchestrator import _run
    return _run(command, timeout=timeout)


def _ask(config, message: str, session_id: str | None, sequence: int, *, readonly: bool = False):
    if readonly:
        from tools.opencode_campaign_orchestrator import _readonly
        config = _readonly(config)
    return _flow_ask(config, message, session_id, sequence, retries=None)


def mandatory_tests() -> tuple[str, ...]:
    from tools.opencode_campaign_orchestrator import MANDATORY_CAMPAIGN_TESTS
    return MANDATORY_CAMPAIGN_TESTS


def _backend(config):
    from tools.campaign_runtime import backend_for
    return backend_for(config)


def git_evidence_file(state_root: Path, cycle: int) -> Path:
    path = loop_log.loop_root(state_root) / f"git-{cycle:04d}.md"
    status = _run_git(["git", "status", "--short"], timeout=60)
    diff = _run_git(["git", "diff", "--stat", "HEAD"], timeout=60)
    log = _run_git(["git", "log", "--oneline", "-8"], timeout=60)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "## git status\n\n```text\n" + (status.stdout + status.stderr).strip() +
        "\n```\n\n## diff stat\n\n```text\n" + (diff.stdout + diff.stderr).strip() +
        "\n```\n\n## recent commits\n\n```text\n" + (log.stdout + log.stderr).strip() + "\n```\n",
        encoding="utf-8",
    )
    return path


def _verification_commands(config, declared: list[str]) -> list[list[str]]:
    return _backend(config).verification_commands(config, declared)


def _verification_passed(config, command: list[str], result) -> bool:
    return _backend(config).verification_passed(config, command, result)


def _parse_declared(fields: dict) -> tuple[list[str], str | None]:
    declared = []
    for token in (fields.get("files") or "").split(","):
        path = normalize(token)
        if token.strip() and path is None:
            return [], f"unsafe declared path: {token.strip()}"
        if path and path != "none":
            declared.append(path)
    return declared, None


def _repo_path(config, path: str):
    from tools.opencode_campaign_orchestrator import REPO_ROOT
    candidate = REPO_ROOT / path
    return candidate if candidate.is_relative_to(REPO_ROOT) else None


def _ensure_fixer(config, state):
    from tools.opencode_campaign_orchestrator import _ensure_fixer as ensure
    return ensure(config, state)


def _abandon(config, state, fix_id: str, declared: list[str], pre_cycle: set[str], reason: str) -> str:
    """Restore the pre-fix worktree for clean paths; quarantine the rest."""
    from tools.opencode_campaign_orchestrator import REPO_ROOT
    if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
        from tools.campaign_pingpong import require_edit_window
        require_edit_window(config, state)
    quarantine = loop_log.loop_root(config.state_root) / "quarantine" / fix_id
    quarantine.mkdir(parents=True, exist_ok=True)
    restored, quarantined = [], []
    for path in declared:
        full = REPO_ROOT / path
        if not full.is_relative_to(REPO_ROOT) or not full.is_file():
            continue
        tracked = _run_git(["git", "ls-files", "--error-unmatch", "--", path], timeout=60).returncode == 0
        if path in pre_cycle:
            # Existing user work remains exactly where it was. Record a copy
            # for diagnosis, never move or reset the original.
            target = quarantine / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, target)
            continue
        if tracked:
            target = quarantine / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, target)
            if _run_git(["git", "checkout", "HEAD", "--", path], timeout=120).returncode == 0:
                restored.append(path)
                continue
        target = quarantine / path
        target.parent.mkdir(parents=True, exist_ok=True)
        full.replace(target)
        quarantined.append(path)
    loop_log.record_fix(config.state_root, {
        "fix_id": fix_id, "status": "abandoned", "reason": reason,
        "reverted": restored, "quarantined": quarantined,
    })
    _journal(config, f"Fix {fix_id} abandoned after bounded revisions: {reason}. "
                     f"Reverted: {restored or 'none'}; quarantined: {quarantined or 'none'}.")
    state.pending_fix = None
    from tools.opencode_campaign_orchestrator import _save_state
    _save_state(config.state_file, state)
    return "abandoned"


def run_fix_cycle(config, state, cycle: int, episode: str, synthesis_path: Path,
                  board: Path, helper: dict, sequence: int) -> str:
    """Drive fixing -> review -> verification -> commit. Returns the outcome."""
    from tools.opencode_campaign_orchestrator import REPO_ROOT, _progress, _save_state

    dry_run = config.dry_run
    if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
        from tools.campaign_pingpong import require_edit_window
        require_edit_window(config, state)
    pending = state.pending_fix or {}
    fix_id = pending.get("fix_id") or loop_log.next_fix_id(config.state_root)
    pre_cycle = set(pending.get("pre_cycle_dirty") or state.pre_cycle_dirty or ())
    status_clean_before = pending.get("status_clean_before", "CURRENT_STATUS.md" not in _dirty_paths())
    before = pending.get("before")
    attempts = int(pending.get("attempts", 0))
    declared: list[str] = list(pending.get("declared") or [])
    fields: dict = dict(pending.get("fields") or {})
    git_evidence = git_evidence_file(config.state_root, cycle)
    previous = loop_log.previous_fix(config.state_root, cycle)
    previous_reference = (
        f"{previous['fix_id']} (commit {previous.get('commit', 'unknown')}, target "
        f"{previous.get('target', 'undeclared')})" if previous else "none"
    )

    def persist(**update):
        merged = {**state.pending_fix, **update} if state.pending_fix else dict(update)
        state.pending_fix = {**merged, "fix_id": fix_id, "phase": merged.get("phase", "fixing")}
        _save_state(config.state_file, state)

    def ask_fixer(message: str) -> dict:
        nonlocal sequence
        session, output = _ask(config, message, state.fixer_session_id, sequence)
        if session:
            state.fixer_session_id = session
        if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
            from tools.campaign_pingpong import relay_completion
            relay_completion(config, state, fix_id, output)
        parsed = decision_fields(output)
        if not parsed.get("status") and not dry_run:
            sequence += 1
            session, output = _ask(
                config,
                "Your response omitted CAMPAIGN_DECISION. Continue this same investigation and any partial "
                "edit; do not operate lifecycle. Return the required status/kind/files/test/prediction/reason "
                "fields after verifying the candidate, or identify a concrete blocker.",
                state.fixer_session_id, sequence)
            if session:
                state.fixer_session_id = session
            if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
                relay_completion(config, state, fix_id, output)
            parsed = decision_fields(output)
        if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"} and not parsed.get("status"):
            raise RuntimeError("Haiku omitted completion decision after bounded correction")
        return parsed

    if pending.get("phase") == "committing":
        return _commit(config, state, fix_id, fields, declared, status_clean_before, sequence,
                       Path(pending.get("verification_log") or config.observations), pre_cycle)

    # ---- FIXING ------------------------------------------------------------
    if not state.pending_fix:
        # Persist the pending record before the first fixer turn so an
        # interrupted fixer invocation retains the original dirty baseline.
        state.pending_fix = {"fix_id": fix_id, "phase": "fixing", "episode": episode,
                             "synthesis": str(synthesis_path), "board": str(board),
                             "helper": dict(helper),
                             "before": _fingerprint(config), "pre_cycle_dirty": sorted(pre_cycle),
                             "status_clean_before": status_clean_before}
        _save_state(config.state_file, state)
        before = state.pending_fix["before"]
    if pending.get("phase") in (None, "fixing"):
        message = ("continue" if pending else fixer_episode_invocation(
            episode, synthesis_path, board, helper.get("path"),
            config.state_root / "logs/latest-context.md", git_evidence, previous_reference,
            config.state_root))
        _journal(config, f"Fixer invoked for episode {episode} ({fix_id}).")
        _progress(config, "FIXING", state, fix_id=fix_id)
        _ensure_fixer(config, state)
        fields = ask_fixer(message)
        declared, unsafe = _parse_declared(fields)
        if unsafe and not dry_run:
            fields = ask_fixer(fixer_revision(fix_id, unsafe))
            declared, unsafe = _parse_declared(fields)
        persist(phase="review", fields=fields, declared=declared, before=before,
                pre_cycle_dirty=sorted(pre_cycle), status_clean_before=status_clean_before,
                attempts=attempts)
    decision = fields.get("status") or ("dry-run" if dry_run else "no-change")
    if decision in {"no-change", "stop"} or dry_run:
        after = _fingerprint(config)
        loop_log.record_fix(config.state_root, {
            "fix_id": fix_id, "introduced_cycle": cycle, "status": decision,
            "kind": fields.get("kind", "no-change"), "prediction": fields.get("prediction", ""),
            "reason": fields.get("reason", ""),
        })
        _journal(config, f"Fixer verdict {decision} for episode {episode}: {fields.get('reason', '')}")
        if decision == "no-change" and before not in (None, after) and not dry_run:
            return _abandon(config, state, fix_id, declared, pre_cycle,
                            "no-change verdict with uncommitted edits")
        state.pending_fix = None
        _save_state(config.state_file, state)
        return decision

    # ---- PRIMARY_REVIEW / VERIFYING / COMMITTING ---------------------------
    evidence_path = config.opencode_log_dir / f"review-{sequence:04d}.md"
    verification_log = config.opencode_log_dir / f"verification-{sequence:04d}.log"
    while True:
        _progress(config, "PRIMARY_REVIEW", state, fix_id=fix_id)
        diff = _run_git(["git", "diff", "HEAD", "--", *declared], timeout=120).stdout
        additions = []
        for path in declared:
            tracked = _run_git(["git", "ls-files", "--error-unmatch", "--", path], timeout=120).returncode == 0
            candidate = _repo_path(config, path)
            if not tracked and candidate is not None and candidate.is_file():
                additions.append("\nNew file: " + path + "\n"
                                 + candidate.read_text(encoding="utf-8", errors="replace"))
        evidence_path.write_text(
            f"# Candidate evidence ({fix_id})\n\n"
            + json.dumps(fields, indent=2, sort_keys=True)
            + f"\n\n## Declared files\n{', '.join(declared)}\n\n## Diff\n\n{diff}\n" + "\n".join(additions),
            encoding="utf-8")
        _, review = _ask(config, primary_review(fix_id, evidence_path, declared),
                         state.primary_session_id, sequence, readonly=True)
        verdict = decision_fields(review, "REVIEW_DECISION")
        if verdict.get("status") == "approved":
            loop_log.record_fix(config.state_root, {"fix_id": fix_id, "review": "approved",
                                                    "review_reason": verdict.get("reason", "")})
            break
        attempts += 1
        loop_log.record_fix(config.state_root, {"fix_id": fix_id, "review": "rejected",
                                                "review_reason": verdict.get("reason", "")})
        if attempts > MAX_FIX_REVISIONS:
            return _abandon(config, state, fix_id, declared, pre_cycle,
                            f"primary review rejected {attempts - 1} revisions")
        _journal(config, f"Primary rejected {fix_id}: {verdict.get('reason', '')}. Revision "
                         f"{attempts}/{MAX_FIX_REVISIONS} requested from the same fixer session.")
        _progress(config, "FIXING", state, fix_id=fix_id, revision=attempts)
        fields = ask_fixer(fixer_revision(fix_id, verdict.get("reason", "")))
        new_declared, unsafe = _parse_declared(fields)
        if unsafe:
            return _abandon(config, state, fix_id, declared, pre_cycle, unsafe)
        declared = new_declared or declared
        persist(phase="review", fields=fields, declared=declared, attempts=attempts)

    while True:
        if _backend(config).name == "laya":
            proof_log = config.opencode_log_dir / f"regression-{fix_id}.log"
            proof_log.parent.mkdir(parents=True, exist_ok=True)
            if not _backend(config).regression_proof(config, declared, fields, proof_log):
                return _abandon(config, state, fix_id, declared, pre_cycle,
                                f"focused fail-before/pass-after proof failed: {proof_log}")
            loop_log.record_fix(config.state_root, {"fix_id": fix_id, "regression_log": str(proof_log)})

        _progress(config, "VERIFYING", state, fix_id=fix_id)
        verification_log.parent.mkdir(parents=True, exist_ok=True)
        verification_log.write_text("", encoding="utf-8")
        failed = False
        for command in _verification_commands(config, declared):
            try:
                validation = _run_git(command, timeout=900)
            except (subprocess.TimeoutExpired, TimeoutError) as error:
                with verification_log.open("a", encoding="utf-8") as handle:
                    handle.write(f"TIMEOUT: {error}\n")
                failed = True
                break
            with verification_log.open("a", encoding="utf-8") as handle:
                handle.write(f"$ {' '.join(command)}\nreturncode: {validation.returncode}\n")
                handle.write(validation.stdout + validation.stderr)
            if not _verification_passed(config, command, validation):
                failed = True
                break
        if not failed:
            loop_log.record_fix(config.state_root, {"fix_id": fix_id, "verification": "passed",
                                                    "verification_log": str(verification_log)})
            break
        attempts += 1
        loop_log.record_fix(config.state_root, {"fix_id": fix_id, "verification": "failed",
                                                "verification_log": str(verification_log)})
        if attempts > MAX_FIX_REVISIONS:
            return _abandon(config, state, fix_id, declared, pre_cycle,
                            "verification failed after bounded revisions")
        _journal(config, f"Verification failed for {fix_id}; revision {attempts}/{MAX_FIX_REVISIONS} "
                         "requested from the same fixer session.")
        _progress(config, "FIXING", state, fix_id=fix_id, revision=attempts)
        fields = ask_fixer(fixer_verification_failure(fix_id, verification_log, declared))
        new_declared, unsafe = _parse_declared(fields)
        if unsafe:
            return _abandon(config, state, fix_id, declared, pre_cycle, unsafe)
        declared = new_declared or declared
        persist(phase="review", fields=fields, declared=declared, attempts=attempts)
        if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
            return run_fix_cycle(config, state, cycle, episode, synthesis_path, board, helper, sequence + 1)

    # ---- COMMITTING --------------------------------------------------------
    _progress(config, "COMMITTING", state, fix_id=fix_id)
    while True:
        categories = classify_dirty(_dirty_paths(), fixer_files=set(declared), pre_cycle_dirty=pre_cycle)
        # classify() puts declared paths in "fixer" first, so check the declared
        # set against pre-cycle user work explicitly.
        user_overlap = {p for p in pre_cycle
                        if p in declared and not is_generated(p) and not is_campaign_owned(p)}
        conflict = unsafe_declared(declared, unrelated=user_overlap, repo_root=REPO_ROOT)
        if not categories["unexpected"] and not conflict:
            break
        if _backend(config).name == "laya":
            return _abandon(config, state, fix_id, declared, pre_cycle,
                            f"unsafe worktree changes: {conflict or sorted(categories['unexpected'])}")
        attempts += 1
        loop_log.record_fix(config.state_root, {"fix_id": fix_id,
                                                "unexpected": sorted(categories["unexpected"]),
                                                "conflict": conflict})
        if attempts > MAX_FIX_REVISIONS:
            return _abandon(config, state, fix_id, declared, pre_cycle,
                            f"unsafe worktree changes: {conflict or 'undeclared new changes'}")
        listing = ", ".join(sorted(categories["unexpected"])) or conflict
        _journal(config, f"Worktree ownership problem ({listing}); asking the same fixer session to "
                         f"declare or revert ({attempts}/{MAX_FIX_REVISIONS}).")
        fields = ask_fixer(fixer_revision(fix_id, f"worktree ownership problem: {listing}. "
                                                "Declare every file you changed, or revert changes "
                                                "that are not part of this fix."))
        new_declared, unsafe = _parse_declared(fields)
        if unsafe:
            return _abandon(config, state, fix_id, declared, pre_cycle, unsafe)
        declared = new_declared or declared
        persist(phase="committing", fields=fields, declared=declared, attempts=attempts)
    persist(phase="committing", fields=fields, declared=declared, attempts=attempts,
            verification_log=str(verification_log))
    return _commit(config, state, fix_id, fields, declared, status_clean_before, sequence,
                   verification_log, pre_cycle)


def _commit(config, state, fix_id: str, fields: dict, declared: list[str], status_clean_before: bool,
            sequence: int, verification_log: Path, pre_cycle: set[str]) -> str:
    from tools.opencode_campaign_orchestrator import REPO_ROOT, _record_milestone, _save_state
    if getattr(config, "agent_topology", "legacy") in {"deepseek-haiku", "deepseek-codex"}:
        from tools.campaign_pingpong import require_edit_window
        require_edit_window(config, state)
    # Git commit may have succeeded immediately before supervisor interruption.
    # Its unique fix id and clean declared paths make completion repeatable.
    if state.pending_fix and state.pending_fix.get("phase") == "committing":
        latest = _run_git(["git", "log", "-1", "--format=%H %s"], timeout=120).stdout.strip()
        clean = _run_git(["git", "diff", "HEAD", "--", *declared], timeout=120).stdout.strip() == ""
        if f"({fix_id})" in latest and clean:
            revision = latest.split(" ", 1)[0]
            loop_log.record_fix(config.state_root, {"fix_id": fix_id, "status": "committed", "commit": revision,
                                                    "files": declared, "introduced_cycle": state.active_cycle})
            state.pending_fix = None
            _save_state(config.state_file, state)
            return "committed"
    conflict = unsafe_declared(declared, unrelated={p for p in pre_cycle}, repo_root=REPO_ROOT)
    if conflict:
        return _abandon(config, state, fix_id, declared, pre_cycle, conflict)
    staged = commit_paths(fixer_files=declared, status_clean_before=status_clean_before)
    if not staged:
        return _abandon(config, state, fix_id, declared, pre_cycle, "no files declared for commit")
    if _run_git(["git", "diff", "--check"], timeout=120).returncode:
        return _abandon(config, state, fix_id, declared, pre_cycle,
                        "whitespace errors in the candidate diff")
    _record_milestone(fields, fix_id)
    if _run_git(["git", "add", "--", *staged], timeout=120).returncode:
        return _abandon(config, state, fix_id, declared, pre_cycle, "staging the declared files failed")
    committed = _run_git(["git", "commit", "-m", _backend(config).commit_message(fix_id)],
                         timeout=120)
    if committed.returncode:
        return _abandon(config, state, fix_id, declared, pre_cycle,
                        "git commit failed; inspect repository state")
    revision = _run_git(["git", "rev-parse", "HEAD"], timeout=120).stdout.strip()
    loop_log.record_fix(config.state_root, {
        "fix_id": fix_id, "status": "committed", "commit": revision,
        "files": declared, "introduced_cycle": state.active_cycle, "target": ", ".join(declared),
    })
    _journal(config, f"Verified commit {revision or 'inspect git log'} ({fix_id}); "
                     f"files: {', '.join(staged)}. Verification log: {verification_log}.")
    state.pending_fix = None
    _save_state(config.state_file, state)
    return "committed"
