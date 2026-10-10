# Path: tools/opencode_campaign_orchestrator.py
# Purpose: Durable autonomous campaign supervisor for isolated deterministic runs.
"""Persistent-session campaign supervisor.

Architecture:
- one persistent PRIMARY session controls the entire campaign (all checkpoints,
  run ends, syntheses, reviews and commits; it survives controller restarts);
- one persistent FIXER session implements one focused correction per episode;
- every fresh Factorio episode gets a NEW four-observer team;
- at RUN END, observer finalization starts immediately while the independent
  OpenCode helper receives at most `helper_wait_seconds` (default 120s) to
  finish; both run concurrently;
- durable state, the loop log, the fix/hypothesis ledgers and git are the
  campaign truth; conversations provide reasoning continuity only;
- recoverable worker/helper/git-state conditions never park the campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import re
import shutil
import os
import signal
import subprocess
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    import sys
    sys.path.insert(0, str(REPO_ROOT))

from tools.campaign_protocol import (
    assistant_text as _assistant_text, decision_fields as _decision_fields,
    marker_fields as _marker_fields, session_id as _session_id,
)
from tools.campaign_prompts import (
    fixer_bootstrap, primary_bootstrap, primary_checkpoint, primary_run_end, primary_run_transition,
)
from tools.campaign_loop_log import cycle_record
from tools.run_context import build_context
from tools.run_history import index_log
from tools import reliability_state
from tools.run_log_format import is_run_end_line, parse_timed_run_log_line

DEFAULT_STATE_ROOT = Path.home() / ".local/share/factorio-rl/deterministic"
DEFAULT_SOURCE_SAVE = Path.home() / ".factorio/saves/mod_playground.zip"
DEFAULT_OBSERVATIONS = REPO_ROOT / "docs/deterministic/opencode_campaign_observations.md"
DEFAULT_MODEL = "opencode-go/glm-5.3-flash"
RUN_START = "RUN START:"
RUN_END = "RUN END"
# Real terminal lines look like `+2443s RUN END` at line start. The helper
# agent's start prompt quotes "RUN END" mid-line, so a substring test would
# declare every live run complete on its first checkpoint.
_RUN_END_SECONDS = re.compile(r"(?m)^\+(?P<seconds>\d+)s RUN END$")
_HELPER_COMPLETE = re.compile(r"OPENCODE HELPER: findings complete (?P<path>\S+)")

# This is intentionally small and cross-component.  It protects the durable
# contracts most likely to regress together during a campaign fix; a changed
# test file is run separately during verification.  Keep fresh-start/live
# acceptance outside this offline gate.
MANDATORY_CAMPAIGN_TESTS = (
    "tests/test_episode_checkpoint.py",
    "tests/test_campaign_protocol.py",
    "tests/test_persistent_intermediates.py",
    "tests/test_construction_handoffs.py",
    "tests/test_mission_state.py",
)

# Fingerprint noise: campaign-owned/generated artifacts the controller itself
# edits during a cycle. They are never fixer evidence of a code change.
_FINGERPRINT_EXCLUDES = (
    "CURRENT_STATUS.md",
    "saves/",
    "runs/",
    "logs/",
    "docs/deterministic/opencode_helper_runs/",
    "docs/rl/laya_helper_runs/",
)


@dataclass(frozen=True)
class Config:
    state_root: Path
    source_save: Path
    observations: Path
    state_file: Path
    opencode_log_dir: Path
    technology: str
    interval_seconds: int
    post_run_wait_seconds: int
    max_cycles: int
    max_runtime_seconds: int
    model: str
    variant: str
    opencode_bin: str
    python: Path
    campaign_manager: Path
    dashboard_url: str
    dry_run: bool
    resume_active_run: bool
    plastic_reliability: bool = False
    acceptance_seconds: int = 120
    required_successes: int = 3
    stop_after_acceptance: bool = False
    checkpoint_failures: bool = False
    rcon_port: int = 27017
    game_port: int = 34199
    runtime_root: Path | None = None
    gui_mods: Path | None = None
    aspect_observers: bool = False
    progress_file: Path | None = None
    read_only: bool = False
    episode_id: str | None = None
    model_timeout_seconds: int = 1800
    review_model: str | None = None
    review_variant: str | None = None
    observer_model: str | None = None
    observer_variant: str | None = None
    fixer_model: str | None = None
    fixer_variant: str | None = None
    zen_observer_model: str | None = None
    helper_wait_seconds: int = 120
    max_fix_revisions: int = 2
    # Runtime seam: "deterministic" preserves existing behavior; "laya" drives a
    # natural Laya episode with a single read-only Muse helper.
    runtime: str = "deterministic"
    world_root: Path | None = None
    code_root: Path | None = None
    mods_source: Path | None = None
    run_speed: float = 2.0
    until_accepted: str = "C6"
    max_game_ticks: int = 1_440_000
    run_wall_seconds: int = 21_600
    seed: int = 0
    campaign_worktree: Path | None = None
    mod_builder: Path | None = None
    stamp_script: Path | None = None
    stamp_tag_base: int = 30000
    comparator_roots: tuple[Path, ...] = ()


@dataclass
class State:
    campaign_id: str | None = None
    completed_cycles: int = 0
    active_cycle: int | None = None
    active_episode_id: str | None = None
    deadline_utc: str | None = None
    phase: str | None = None
    primary_session_id: str | None = None
    fixer_session_id: str | None = None
    observer_episode: str | None = None
    last_checkpoint: int = 0
    last_checkpoint_episode: str | None = None
    last_seen_tick: int | None = None
    run_end: dict | None = None
    helper: dict | None = None
    synthesis: dict | None = None
    pre_cycle_dirty: list[str] = field(default_factory=list)
    pending_fix: dict | None = None
    active_code_hash: str | None = None
    lifecycle_intent: dict | None = None
    episode_runtime: dict | None = None
    # Durable observer-failure control: after terminal evidence fails, one
    # bounded retry is allowed at a revision; a still-failing same revision
    # records {"terminal": True} and no new episode may start at it.
    observer_failure: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _load_state(path: Path) -> State:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return State()
    if not isinstance(payload, dict):
        raise ValueError(f"campaign state must be an object: {path}")
    return State(
        campaign_id=payload.get("campaign_id"),
        active_episode_id=payload.get("active_episode_id"),
        lifecycle_intent=payload.get("lifecycle_intent"),
        episode_runtime=payload.get("episode_runtime"),
        deadline_utc=payload.get("deadline_utc"),
        phase=payload.get("phase"),
        primary_session_id=payload.get("primary_session_id"),
        fixer_session_id=payload.get("fixer_session_id"),
        observer_episode=payload.get("observer_episode"),
        run_end=payload.get("run_end"),
        helper=payload.get("helper"),
        synthesis=payload.get("synthesis"),
        pre_cycle_dirty=[str(p) for p in payload.get("pre_cycle_dirty", []) if isinstance(p, str)],
        pending_fix=payload.get("pending_fix"),
        active_code_hash=payload.get("active_code_hash"),
        observer_failure=payload.get("observer_failure"),
        completed_cycles=int(payload.get("completed_cycles", 0)),
        active_cycle=int(payload["active_cycle"]) if isinstance(payload.get("active_cycle"), int) else None,
        last_checkpoint=int(payload.get("last_checkpoint", 0)),
        last_checkpoint_episode=payload.get("last_checkpoint_episode"),
        last_seen_tick=int(payload["last_seen_tick"]) if isinstance(payload.get("last_seen_tick"), int) else None,
    )


def _save_state(path: Path, state: State) -> None:
    _atomic_write(path, json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")


_COMMAND_DEADLINE: float | None = None


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    if _COMMAND_DEADLINE is not None:
        remaining = _COMMAND_DEADLINE - time.time()
        if remaining <= 0:
            raise TimeoutError("campaign deadline exhausted before command")
        timeout = min(timeout, remaining)
    model_command = ("opencode" in Path(command[0]).name or
                     any(Path(token).name == "laya_helper_agent.py" for token in command[:3]) or
                     (command[0] == "env" and any("OPENCODE_CONFIG_CONTENT=" in token for token in command[:3])))
    if not model_command:
        return subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    # Model transports belong to this invocation. Bound their entire process
    # group so a timed-out Python helper cannot leave its OpenCode child alive.
    process = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _append(path: Path, text: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<!-- Path: docs/deterministic/opencode_campaign_observations.md | Purpose: Durable automated campaign evidence. -->\n\n"
            "# Automated deterministic campaign observations\n\n"
            "The controller appends raw evidence; each run's persistent primary session appends the code-correlated interpretation.\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + text.rstrip() + "\n")


def _fresh_command(config: Config) -> list[str]:
    command = [
        str(config.campaign_manager), "fresh", "--source-save", str(config.source_save),
        "--root", str(config.state_root), "--python", str(config.python), "--technology", config.technology, "--rcon-port", str(config.rcon_port),
    ]

    command += ["--game-port", str(config.game_port)]
    if config.episode_id:
        command += ["--episode-id", config.episode_id]
    if config.runtime_root is not None:
        command += ["--runtime-root", str(config.runtime_root)]
    if config.gui_mods is not None:
        command += ["--gui-mods", str(config.gui_mods)]
    if config.plastic_reliability and config.technology == "plastic-bar":
        command += ["--produce", "plastic-bar", "--acceptance-seconds", str(config.acceptance_seconds)]
    return command


def _status_command(config: Config) -> list[str]:
    return [
        str(config.campaign_manager), "status", "--root", str(config.state_root),
        "--python", str(config.python), "--technology", config.technology,
    ]


def _latest_run(path: Path) -> tuple[bool, str]:
    try:
        contents = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, ""
    current = _current_run_text(contents)
    return any(is_run_end_line(line) for line in current.splitlines()), current[-12000:]


def _current_run_text(contents: str) -> str:
    lines = contents.splitlines(keepends=True)
    starts = []
    for i, line in enumerate(lines):
        parsed = parse_timed_run_log_line(line.rstrip(), run_started_at=None)
        if line.startswith("RUN START:") or (parsed and parsed.message.startswith("RUN START:")):
            starts.append(i)
    return "".join(lines[starts[-1]:]) if starts else ""


def _run_end_seconds(run_text: str) -> int | None:
    matches = _RUN_END_SECONDS.findall(run_text)
    return int(matches[-1]) if matches else None


def _inventory(url: str) -> str:
    try:
        with urlopen(url, timeout=15) as response:  # noqa: S310 -- loopback endpoint is a CLI option.
            report = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
        return f"Unavailable: {error}"
    if not isinstance(report, dict) or not report.get("ok"):
        return f"Unavailable: {report.get('error', 'invalid report') if isinstance(report, dict) else 'invalid report'}"
    items = report.get("total_items", {})
    values = items.items() if isinstance(items, dict) else []
    top = sorted(
        ((str(name), count) for name, count in values if isinstance(count, (int, float)) and count > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )[:30]
    counts = ", ".join(f"{name}={count:g}" for name, count in top) or "no available logistic items"
    networks = report.get("networks", [])
    tick = report.get("tick")
    summary = f"tick={tick if tick is not None else 'unknown'}; networks={len(networks) if isinstance(networks, list) else 0}; "
    return (
        summary + f"disconnected_roboports={report.get('disconnected_roboports', 'unknown')}\n\nTop logistic items: {counts}"
    )


def _snapshot(config: Config, cycle: int, checkpoint: int, offset: int) -> tuple[str, int, int | None]:
    if config.dry_run:
        status_text = "Dry run: status probe skipped."
        log, next_offset = "Dry run: runner-log probe skipped.", offset
        inventory, tick = "Dry run: Operations Console probe skipped.", None
    else:
        status = _run(_status_command(config), timeout=60)
        inventory_text = _inventory(config.dashboard_url)
        tick_match = re.search(r"tick=(\d+)", inventory_text)
        tick = int(tick_match.group(1)) if tick_match else None
        inventory = inventory_text
        log_path = config.state_root / "logs/autonomous-run.log"
        packet_path = config.state_root / "logs/latest-context.md"
        packet = build_context(log_path, config.state_root / "logs/inventory-history.json")
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = packet_path.with_suffix(".tmp")
        temporary.write_text(packet, encoding="utf-8")
        temporary.replace(packet_path)
        log = f"Read current bounded context: {packet_path}. Raw evidence: {log_path}."
        next_offset = log_path.stat().st_size if log_path.exists() else 0
        status_text = (status.stdout + status.stderr).strip() or "No status output."
    return (
        f"## Cycle {cycle} — checkpoint {checkpoint} ({_now()})\n\n"
        f"### Controller status\n\n```text\n{status_text[-4000:]}\n```\n\n"
        f"### Run context\n\n```text\n{log}\n```\n\n"
        f"### Logistic inventory (`nauvis` / `player`)\n\n{inventory}\n\n"
        "### OpenCode assessment\n\nPending this session's review.\n",
        next_offset, tick,
    )


def _opencode_command(config: Config, message: str, session_id: str | None) -> list[str]:
    command = [
        config.opencode_bin, "run", "--dir", str(config.campaign_worktree or REPO_ROOT), "--format", "json", "--model", config.model,
        "--variant", config.variant, "--auto",
    ]
    if session_id:
        command += ["--session", session_id]
    else:
        command += ["--title", "Automated Factorio deterministic campaign"]
    return command + [message]


def _ask(config: Config, message: str, session_id: str | None, sequence: int,
         retry: int = 0) -> tuple[str | None, str]:
    if config.dry_run:
        return session_id or "dry-run-session", "CAMPAIGN_DECISION:\nstatus: dry-run\n"
    command = _opencode_command(config, message, session_id)
    if config.read_only:
        # Installed OpenCode SDK supports OPENCODE_CONFIG_CONTENT and permission
        # maps. Deny every tool except file readers; bash/MCP/task cannot mutate.
        permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow", "external_directory": "allow"}
        policy = {"permission": permissions, "agent": {"build": {"permission": permissions}}}
        command.remove("--auto")
        command[2:2] = ["--agent", "build"]
        command = ["env", "OPENCODE_CONFIG_CONTENT=" + json.dumps(policy), *command]
    elif config.runtime == "laya":
        # The fixer edits only the campaign branch. Lifecycle and Git writes
        # belong to this controller; MCP and further delegation remain denied.
        workspace = str((config.campaign_worktree or REPO_ROOT).resolve())
        shell = {"*": "allow"}
        for verb in ("add", "commit", "push", "pull", "fetch", "checkout", "reset", "stash", "worktree", "branch", "merge", "rebase", "cherry-pick", "clean", "config", "update-ref", "tag"):
            shell[f"*git*{verb}*"] = "deny"
        for pattern in ("*systemctl*", "*systemd-run*", "*kill *", "*pkill*", "*rcon_client*", "*run_laya_clean*", "*run_laya_saved_world*", "*manage_linux*", "*sync_linux_gui_mods*"):
            shell[pattern] = "deny"
        # Installed OpenCode requests edit permission with path.relative(project.worktree, file).
        # --dir pins that worktree; deny parent escapes and Git metadata after the local allow.
        edits = {"*": "deny", "**": "allow", "../**": "deny", "/**": "deny",
                 ".git": "deny", ".git/**": "deny"}
        permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow",
                       "external_directory": "allow", "edit": edits, "write": edits, "bash": shell, "task": "deny"}
        policy = {"permission": permissions, "agent": {"build": {"permission": permissions}}}
        command[2:2] = ["--agent", "build"]
        command = ["env", "OPENCODE_CONFIG_CONTENT=" + json.dumps(policy), *command]
    config.opencode_log_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if retry == 0 else f"-continue-{retry}"
    transport = config.opencode_log_dir / f"opencode-{sequence:04d}{suffix}.jsonl"
    try:
        result = _run(command, timeout=config.model_timeout_seconds)
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        _atomic_write(transport, decode(exc.stdout) + decode(exc.stderr))
        raise
    _atomic_write(transport, output)
    if result.returncode:
        raise RuntimeError(f"OpenCode exited {result.returncode}; inspect {transport}")
    return session_id or _session_id(output), output


def _ask_with_continue(config: Config, message: str, session_id: str | None,
                       sequence: int, *, require_response: bool = True,
                       retries: int | None = None) -> tuple[str | None, str]:
    """Recover a congested model call until it responds or the campaign ends."""
    active_session = session_id
    prompt = message
    attempt = 0
    while True:
        try:
            if attempt:
                returned_session, output = _ask(config, prompt, active_session, sequence, retry=attempt)
            else:
                returned_session, output = _ask(config, prompt, active_session, sequence)
            active_session = returned_session or active_session
            if require_response and not output.strip():
                raise ValueError("model returned no usable assistant response")
            return active_session, output
        except Exception as error:
            transport = config.opencode_log_dir / f"opencode-{sequence:04d}{'' if attempt == 0 else f'-continue-{attempt}'}.jsonl"
            if active_session is None and transport.exists():
                active_session = _session_id(transport.read_text(encoding="utf-8", errors="replace"))
            if retries is not None and attempt >= retries:
                raise RuntimeError(f"model did not recover after {retries} continue attempts") from error
            if _COMMAND_DEADLINE is not None and time.time() >= _COMMAND_DEADLINE:
                raise RuntimeError("model recovery stopped at the campaign deadline") from error
            attempt += 1
            # A recovered OpenCode session already contains the role brief and
            # all prior evidence. Repeating it wastes context and can restart
            # the session's reasoning; its next turn must be only a continue.
            prompt = "continue" if active_session else message
            time.sleep(min(5 * attempt, 60))


def _progress(config: Config, phase: str, state: State, **details) -> None:
    state.phase = phase
    if config.progress_file is not None:
        _atomic_write(config.progress_file, json.dumps({"phase": phase, "updated_at": _now(),
            "episode_id": state.active_episode_id, "cycle": state.active_cycle,
            "completed_cycles": state.completed_cycles, "deadline_utc": state.deadline_utc, **details}, indent=2) + "\n")


def _episode_id(config: Config) -> str | None:
    try:
        return json.loads((config.state_root / "episode/current.json").read_text()).get("episode_id")
    except (OSError, ValueError):
        return None


def _readonly(config: Config) -> Config:
    return replace(config, read_only=True, model_timeout_seconds=min(config.model_timeout_seconds, 240))


def _backend(config: Config):
    """Select the runtime backend; tolerant of lightweight config stand-ins."""
    from tools.campaign_runtime import backend_for
    return backend_for(config)


def _tree_fingerprint(observations: Path) -> str:
    """Fingerprint code/test changes while excluding campaign-owned artifacts."""
    command = ["git", "diff", "HEAD", "--binary", "--no-ext-diff", "--", "."]
    head_tree = _run(["git", "rev-parse", "HEAD^{tree}"], timeout=120).stdout
    try:
        relative_notes = observations.resolve().relative_to(REPO_ROOT)
    except ValueError:
        relative_notes = None
    excludes = [f":(exclude){item}" for item in _FINGERPRINT_EXCLUDES]
    if relative_notes is not None:
        excludes.append(f":(exclude){relative_notes.as_posix()}")
    command += excludes
    tracked = _run(command, timeout=120).stdout
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], timeout=120).stdout
    if relative_notes is not None:
        untracked = "\n".join(
            line for line in untracked.split("\0") if line and line != relative_notes.as_posix()
        )
    else:
        untracked = "\n".join(line for line in untracked.split("\0") if line)
    return hashlib.sha256((head_tree + tracked + "\n--untracked--\n" + untracked).encode("utf-8", errors="replace")).hexdigest()


def _decision(output: str) -> str:
    return _decision_fields(output).get("status", "no-change")


def _pin_source(config: Config) -> Path:
    content = config.source_save.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    target = config.state_root / "baseline-inputs" / (digest + ".zip")
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("pinned baseline input has been modified")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
    return target


def _code_hash(extra: tuple[str, ...] = ()) -> str:
    paths = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
                  "orchestrator", "planners", "core", "tools", "scripts", "factorio_mod", "schemas",
                  *extra], timeout=120).stdout.split("\0")
    digest = hashlib.sha256()
    for name in sorted(p for p in paths if p):
        path = REPO_ROOT / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"MISSING")
    return digest.hexdigest()


def _dirty_paths() -> set[str]:
    tracked = _run(["git", "diff", "HEAD", "--name-only", "-z"], timeout=120).stdout
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], timeout=120).stdout
    return {name for name in (tracked + untracked).split("\0") if name}


def _record_milestone(fields: dict, fix_id: str) -> str | None:
    """Append the accepted-fix milestone to CURRENT_STATUS.md (campaign-owned)."""
    path = REPO_ROOT / "CURRENT_STATUS.md"
    if not path.is_file():
        return None
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n- {_now()[:10]}: Campaign fix {fix_id} passed persistent-primary review and verification. "
            f"Kind: {fields.get('kind', 'behavior')}. Files: {fields.get('files', 'not declared')}. "
            f"Prediction: {fields.get('prediction', 'not provided')}. Reason: {fields.get('reason', 'verified campaign repair')}.\n")
    return "CURRENT_STATUS.md"


def _helper_finding(config: Config, episode: str) -> tuple[str | None, bool]:
    """Locate the OpenCode helper findings for one episode: (path, announced complete)."""
    from tools.opencode_helper_agent import DEFAULT_REPORT_ROOT
    try:
        run_log = (config.state_root / "logs/autonomous-run.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        run_log = ""
    for match in _HELPER_COMPLETE.finditer(_current_run_text(run_log)):
        path = Path(match.group("path"))
        if path.is_file():
            return str(path), True
    candidate = DEFAULT_REPORT_ROOT / episode / "findings.md"
    if candidate.is_file():
        return str(candidate), False
    return None, False


def _collect_helper(config: Config, episode: str, wait_seconds: int) -> dict:
    """Wait at most `wait_seconds` after RUN END for helper findings; never block."""
    if config.dry_run:
        return {"episode": episode, "status": "unavailable", "path": None, "reason": "dry run"}
    started = time.time()
    announced = False
    while True:
        path, announced = _backend(config).helper_finding(config, episode)
        if announced and path:
            return {"episode": episode, "status": "complete", "path": path,
                    "reason": "findings announced complete"}
        remaining = wait_seconds - (time.time() - started)
        if remaining <= 0:
            if path:
                return {"episode": episode, "status": "late", "path": path,
                        "reason": "findings not announced complete within the wait window"}
            return {"episode": episode, "status": "unavailable", "path": None,
                    "reason": f"no helper findings within {wait_seconds}s after RUN END"}
        time.sleep(min(5, max(1, remaining)))


def _late_helper(config: Config, state: State, episode: str) -> dict:
    """After the flow moved on, preserve helper findings as late evidence."""
    helper = state.helper or {}
    if helper.get("episode") != episode or helper.get("status") != "unavailable":
        return helper
    path, announced = _backend(config).helper_finding(config, episode)
    if path and announced:
        helper.update(status="late", path=path, reason="findings completed after the wait window")
    return helper


def _frozen_revision(config: Config, state: State) -> str:
    """Frozen code revision for the active episode; HEAD when no freeze exists.

    The same commit string is returned at failure time (freeze.json) and after a
    restart (HEAD) so an unchanged revision is recognised as unchanged.
    """
    experiment = config.episode_id or (state.episode_runtime or {}).get("experiment")
    if experiment:
        manifest_path = config.state_root / "episodes" / str(experiment) / "freeze.json"
        try:
            revision = json.loads(manifest_path.read_text(encoding="utf-8")).get("revision")
        except (OSError, ValueError):
            revision = None
        if revision:
            return str(revision)
    try:
        return _run(["git", "rev-parse", "HEAD"], timeout=120).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return "unknown"


def _retry_terminal_evidence(config: Config, episode: str, previous: dict) -> dict:
    """One bounded re-collection of terminal evidence for the SAME episode.

    The runtime observer is never re-invoked and no new episode is started; this
    only waits once more for a late report or completion marker. It returns a
    helper-shaped record so the caller can either continue or record a durable
    terminal failure.
    """
    backend = _backend(config)
    deadline = time.time() + max(0, config.helper_wait_seconds)
    while True:
        path, announced = backend.helper_finding(config, episode)
        if announced and path:
            return {"episode": episode, "status": "complete", "path": str(path),
                    "reason": "late observer evidence completed on the bounded retry"}
        remaining = deadline - time.time()
        if remaining <= 0:
            return {"episode": episode, "status": "failed", "path": str(path) if path else None,
                    "reason": "observer evidence still incomplete after one bounded retry "
                              f"(first: {previous.get('reason')})"}
        time.sleep(min(5, max(1, remaining)))


def _milestones(run_text: str) -> dict:
    milestones = {}
    patterns = {"iron_swap": "BOOTSTRAP SWAP: iron-plate", "copper_swap": "BOOTSTRAP SWAP: copper-plate",
                "am2": "CORE MALL READY: assembling-machine-2", "oil_packet": "OIL PACKET:",
                "plastic_output": "GOAL MET: plastic-bar"}
    for name, pattern in patterns.items():
        match = next((re.match(r"\+(\d+)s", line) for line in run_text.splitlines() if pattern in line), None)
        if match:
            milestones[name] = int(match.group(1))
    return milestones


def run_campaign(config: Config) -> int:
    global _COMMAND_DEADLINE, REPO_ROOT
    supervisor_root = REPO_ROOT
    if config.campaign_worktree is not None:
        REPO_ROOT = config.campaign_worktree.resolve()
    try:
        if config.dry_run:
            return _run_campaign(config)
        lock_path = config.state_root / "logs/campaign-controller.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("another campaign controller owns this runtime") from error
            return _run_campaign(config)
    finally:
        _COMMAND_DEADLINE = None
        REPO_ROOT = supervisor_root


def _ensure_primary(config: Config, state: State, cycle: int, sequence: int) -> tuple[str | None, bool]:
    """Bootstrap the persistent primary only when no usable session exists."""
    if state.primary_session_id:
        return state.primary_session_id, False
    session, _ = _ask_with_continue(_readonly(config), primary_bootstrap(config, cycle), None, sequence, require_response=False)
    if session is None:
        raise RuntimeError("OpenCode did not return a persistent primary session ID")
    state.primary_session_id = session
    _save_state(config.state_file, state)
    return session, True


def _ensure_fixer(config: Config, state: State, sequence: int = 0) -> str | None:
    if state.fixer_session_id:
        return state.fixer_session_id
    fixer_config = replace(config, model=config.fixer_model or config.model,
                           variant=config.fixer_variant or config.variant)
    session, _ = _ask_with_continue(fixer_config, fixer_bootstrap(config), None, sequence, require_response=False)
    if session is None:
        raise RuntimeError("OpenCode did not return a persistent fixer session ID")
    state.fixer_session_id = session
    _save_state(config.state_file, state)
    return session


def _primary_turn(config: Config, state: State, message: str, sequence: int) -> str:
    """Send one concise event message to the same persistent primary session."""
    session, output = _ask_with_continue(_readonly(config), message, state.primary_session_id, sequence, require_response=False)
    if session:
        state.primary_session_id = session
        _save_state(config.state_file, state)
    return output


def _terminal_evidence(config: Config, state: State, episode: str) -> tuple[dict, Path]:
    """Finalize observers concurrently with the bounded helper wait."""
    board = config.opencode_log_dir / "observers" / episode / "board.md"
    if state.helper and state.helper.get("episode") == episode:
        # RUN END evidence collection already ran once (controller restart or
        # repeated detection); do not duplicate the wait or finalization.
        return state.helper, board
    if _backend(config).name == "laya" and not config.dry_run:
        helper = _backend(config).observe(config, episode)
        state.helper = helper
        _save_state(config.state_file, state)
        return helper, Path(helper.get("path") or board)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="campaign-terminal") as pool:
        future = None
        if config.aspect_observers and not config.dry_run:
            from tools.campaign_observers import observe_team
            future = pool.submit(observe_team, config, episode, state.last_checkpoint + 1, True)
        helper = _collect_helper(config, episode, config.helper_wait_seconds)
        state.helper = helper
        _save_state(config.state_file, state)
        if future is None:
            return helper, board
        try:
            # observe_team only returns the board path; the helper result above
            # was collected while the observers finalized concurrently.
            future.result(timeout=config.helper_wait_seconds + 180)
        except Exception as error:  # observer finalization must never block synthesis
            _append(config.observations, f"Observer terminal finalization incomplete: {error}\n")
    return helper, board


def _record_hypothesis(config: Config, state: State, cycle: int, synthesis_text: str) -> str | None:
    fields = _marker_fields(synthesis_text, "SYNTHESIS")
    if not fields.get("hypothesis"):
        return None
    from tools import campaign_loop_log
    existing = {
        entry["hypothesis_id"]: entry
        for entry in campaign_loop_log.hypotheses(config.state_root).values()
        if entry.get("description") == fields["hypothesis"]
    }
    if existing:
        hypothesis_id, entry = next(iter(existing.items()))
        entry.setdefault("supporting_runs", [])
        if episode_key(state) not in entry["supporting_runs"]:
            campaign_loop_log.record_hypothesis(config.state_root, {
                "hypothesis_id": hypothesis_id, "supporting_runs": episode_key(state)})
    else:
        hypothesis_id = campaign_loop_log.next_hypothesis_id(config.state_root)
        campaign_loop_log.record_hypothesis(config.state_root, {
            "hypothesis_id": hypothesis_id,
            "description": fields["hypothesis"],
            "first_cycle": cycle,
            "confidence": fields.get("confidence", "unknown"),
            "supporting_evidence": fields.get("supporting", ""),
            "contradicting_evidence": fields.get("contradicting", ""),
            "competing": fields.get("competing", ""),
            "discriminating": fields.get("discriminating", ""),
            "status": "OPEN",
            "supporting_runs": episode_key(state),
            "contradicting_runs": [],
            "related_fixes": [],
        })
    return hypothesis_id


def episode_key(state: State) -> str:
    return f"{state.active_cycle or state.completed_cycles + 1}/{state.active_episode_id or 'unknown'}"


def _terminal_key(run_text: str) -> str:
    lines = [line for line in run_text.splitlines() if "STUCK:" in line or "ERROR:" in line]
    value = lines[-1] if lines else "run-ended-without-terminal-detail"
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", value.lower())[:180]


def _run_campaign(config: Config) -> int:
    global _COMMAND_DEADLINE
    if config.checkpoint_failures and not config.plastic_reliability:
        raise ValueError("--checkpoint-failures requires --plastic-reliability")
    if config.interval_seconds < 120:
        raise ValueError("--interval-seconds must be at least 120")
    if not config.dry_run and not config.source_save.is_file():
        raise FileNotFoundError(f"source save is missing: {config.source_save}")
    if config.plastic_reliability and (config.acceptance_seconds < 10 or config.acceptance_seconds > 600 or config.acceptance_seconds % 10 or config.required_successes < 3):
        raise ValueError("reliability requires >=3 successes and a 10–600 second window in 10-second increments")
    state = _load_state(config.state_file)
    if config.max_runtime_seconds and state.deadline_utc is None:
        state.deadline_utc = datetime.fromtimestamp(time.time() + config.max_runtime_seconds, timezone.utc).isoformat()
        _save_state(config.state_file, state)
    deadline = datetime.fromisoformat(state.deadline_utc).timestamp() if state.deadline_utc else float("inf")
    _COMMAND_DEADLINE = deadline
    if not state.campaign_id:
        state.campaign_id = "campaign-" + (state.deadline_utc or _now()).replace(":", "").replace("+", "Z")
        _save_state(config.state_file, state)
    reliability_path = config.state_root / "logs/reliability-state.json"
    base_config = config
    backend = _backend(config)
    if backend.name == "laya":
        if not config.dry_run and config.campaign_worktree is None:
            raise ValueError("Laya requires explicit --campaign-worktree for Git/model edits")
        if state.episode_runtime:
            config = backend.restore_config(config, state.episode_runtime)
    if backend.name == "laya" and not config.dry_run and (config.rcon_port == 27017 or config.game_port == 34199):
        raise ValueError("a Laya campaign needs private --rcon-port/--game-port, not the deterministic defaults")
    # A durable completed-cycle ledger entry wins over a state write lost
    # immediately after append/commit. Never issue a second fix for that cycle.
    if state.active_cycle and cycle_record(config.state_root, state.campaign_id, state.active_cycle):
        state.completed_cycles = max(state.completed_cycles, state.active_cycle)
        state.active_cycle = None
        state.active_episode_id = None
        state.lifecycle_intent = None
        state.episode_runtime = None
        state.pending_fix = None
        state.helper = None
        state.synthesis = None
        _save_state(config.state_file, state)
        config = base_config
    existing_sequences = [int(p.stem.split("-")[-1]) for p in config.opencode_log_dir.glob("opencode-*.jsonl") if p.stem.split("-")[-1].isdigit()]
    sequence = max(existing_sequences, default=0) + 1

    # Resume an interrupted fix cycle before anything touches a new episode.
    if state.pending_fix and not config.dry_run:
        pending = state.pending_fix
        if pending.get("episode") and state.active_episode_id \
                and pending["episode"] != state.active_episode_id:
            # The pending fix belongs to an episode that no longer exists.
            _append(config.observations,
                    f"Dropped stale pending fix {pending.get('fix_id')} for episode "
                    f"{pending.get('episode')}; active episode is {state.active_episode_id}.\n")
            state.pending_fix = None
            _save_state(config.state_file, state)
    if state.pending_fix and not config.dry_run:
        from tools.campaign_fix_flow import run_fix_cycle
        outcome = run_fix_cycle(config, state, state.active_cycle or state.completed_cycles + 1,
                                state.active_episode_id or "unknown",
                                Path(state.pending_fix.get("synthesis") or config.observations),
                                Path(state.pending_fix.get("board") or config.observations),
                                state.helper or dict(state.pending_fix.get("helper")
                                                     or {"status": "unavailable", "path": None,
                                                         "reason": "resumed"}),
                                sequence)
        _finish_cycle(config, state, outcome, {}, run_text="")
        sequence += 1

    while config.max_cycles == 0 or state.completed_cycles < config.max_cycles:
        if time.time() >= deadline and not (backend.name == "laya" and (state.active_cycle or state.lifecycle_intent)):
            return _stop_campaign(config, state, "DEADLINE_REACHED",
                                  reason="campaign deadline reached",
                                  journal="## Campaign stop\n\nWall-clock limit reached; no new episode was started.\n")
        if state.observer_failure and state.observer_failure.get("terminal") and not config.dry_run:
            revision = _frozen_revision(config, state)
            if state.observer_failure.get("revision") == revision:
                return _stop_campaign(config, state, "HARNESS_FAILURE",
                                      reason="deliberate harness failure; no new episode at an unchanged revision",
                                      journal="## Campaign stop\n\nA terminal observer failure was recorded at this "
                                              "revision; no new episode was started. Commit a change to resume.\n")
            _append(config.observations,
                    f"Clearing terminal observer failure: revision changed from "
                    f"{state.observer_failure.get('revision')} to {revision}.\n")
            state.observer_failure = None
            _save_state(config.state_file, state)
        if base_config.plastic_reliability:
            reliability = reliability_state.load(reliability_path)
            baseline = reliability.get("baseline")
            if reliability["phase"] == "research" and baseline:
                identity = baseline["identity"]
                revision = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
                source_hash = hashlib.sha256(base_config.source_save.read_bytes()).hexdigest() if base_config.source_save.is_file() else "missing"
                if (identity.get("code_hash") != _code_hash() or identity.get("repository_revision") != revision or identity.get("source_save_sha256") != source_hash
                        or identity.get("acceptance_seconds") != config.acceptance_seconds or len(baseline["episodes"]) < config.required_successes):
                    reliability.update(phase="plastic", streak=[])
                    reliability_state.atomic_json(reliability_path, reliability)
            if reliability["phase"] == "research" and base_config.stop_after_acceptance:
                return _stop_campaign(config, state, "SUCCESS", reason="plastic acceptance certified")
            config = replace(base_config, technology="plastic-bar" if reliability["phase"] == "plastic" else base_config.technology)
        if (config.plastic_reliability or backend.name == "laya") and not config.dry_run:
            config = replace(config, source_save=_pin_source(config))
        attempt_hash = _code_hash() if config.plastic_reliability else ""
        cycle = state.completed_cycles + 1
        # A saved active cycle is sufficient to resume even if the first model
        # call crashed before returning a session. Never infer reset permission
        # from the absence of a model session ID.
        resuming = state.active_cycle == cycle
        if state.lifecycle_intent:
            if backend.name == "laya":
                config = backend.restore_config(config, state.episode_runtime)
                # The durable unique root is identity; actual Laya run ids differ
                # from the campaign experiment id. fresh is idempotent by phase.
                _COMMAND_DEADLINE = None
                fresh = backend.fresh(config)
                _COMMAND_DEADLINE = None if backend.name == "laya" else deadline
                if fresh.returncode:
                    raise RuntimeError(f"resumed lifecycle failed: {fresh.stderr}")
                state.active_cycle = cycle
                state.active_episode_id = backend.episode_id(config)
                state.lifecycle_intent = None
                _save_state(config.state_file, state)
                resuming = True
            else:
                current_episode = backend.episode_id(config)
                run_log = backend.run_log_path(config)
                current_log = _current_run_text(run_log.read_text(encoding="utf-8", errors="replace")) if run_log.exists() else ""
                if (current_episode and current_episode == state.lifecycle_intent.get("expected_episode")
                        and current_episode in current_log):
                    state.active_cycle = cycle
                    state.active_episode_id = current_episode
                    state.lifecycle_intent = None
                    _save_state(config.state_file, state)
                    resuming = True
                else:
                    _progress(config, "UNRECOVERABLE_FAILURE", state,
                              reason="fresh lifecycle interrupted; inspect before retrying")
                    raise RuntimeError("fresh lifecycle intent is unresolved; refusing an automatic second reset")
        if resuming and config.plastic_reliability:
            attempt_hash = state.active_code_hash or "unknown-start-code"
        adopt_active_run = config.resume_active_run and not resuming
        if resuming:
            if state.active_episode_id and backend.episode_id(config) != state.active_episode_id and not config.dry_run:
                raise RuntimeError("active episode provenance changed; refuse to adopt another world")
            _append(config.observations, f"## Cycle {cycle} — resumed ({_now()}); no fresh lifecycle issued.\n")
        else:
            if adopt_active_run:
                complete, run_text = backend.latest_run(config)
                if complete or not run_text or not backend.episode_id(config):
                    raise RuntimeError("--resume-active-run requires an identified unfinished episode")
            else:
                config = replace(config, episode_id="episode-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
                if backend.name == "laya" and not config.dry_run:
                    config = backend.allocate_config(config, cycle)
                    state.episode_runtime = backend.config_identity(config)
                state.lifecycle_intent = {"previous_episode": backend.episode_id(config), "expected_episode": config.episode_id, "cycle": cycle}
                state.active_code_hash = attempt_hash
                _save_state(config.state_file, state)
                _progress(config, "STARTING_EPISODE", state)
                if backend.name == "laya":
                    _COMMAND_DEADLINE = None
                fresh = subprocess.CompletedProcess(backend.fresh_command(config), 0, "dry run", "") if config.dry_run else backend.fresh(config)
                _COMMAND_DEADLINE = None if backend.name == "laya" else deadline
                if fresh.returncode:
                    raise RuntimeError(f"fresh lifecycle failed:\n{fresh.stdout}\n{fresh.stderr}")
                _append(config.observations, f"## Cycle {cycle} — fresh lifecycle ({_now()})\n\n```text\n{(fresh.stdout + fresh.stderr).strip()}\n```\n")
            state.active_cycle = cycle
            state.active_episode_id = backend.episode_id(config) or (f"dry-run-{cycle}" if config.dry_run else None)
            state.active_code_hash = attempt_hash
            state.lifecycle_intent = None
            # A new episode always gets a new observer team and a clean checkpoint.
            state.observer_episode = state.active_episode_id
            state.last_checkpoint = 0
            state.last_checkpoint_episode = state.active_episode_id
            state.run_end = None
            state.helper = None
            state.synthesis = None
            state.pending_fix = None
            state.pre_cycle_dirty = sorted(_dirty_paths())
            _save_state(config.state_file, state)
        # The persistent primary is bootstrapped only once per campaign; a
        # fresh episode or a controller restart reuses the same session.
        bootstrapped = False
        if backend.name == "deterministic":
            _, bootstrapped = _ensure_primary(config, state, cycle, sequence)
        sequence += 1
        if not bootstrapped and backend.name == "deterministic":
            previous = cycle_record(config.state_root, state.campaign_id, cycle - 1)
            _primary_turn(config, state, primary_run_transition(
                config, cycle, state.active_episode_id, state.fixer_session_id,
                config.state_root / "logs/latest-context.md", config.state_root,
                Path(previous["synthesis_path"]) if previous and previous.get("synthesis_path") else None), sequence)
            sequence += 1
        _progress(config, "OBSERVING", state)
        checkpoint = max(state.last_checkpoint, 0)
        # A resumed episode may already have ended while the controller was
        # down; a freshly started episode must get one checkpoint turn before
        # its first end check so the new RUN START cannot race the old block.
        first_turn_done = resuming
        while True:
            complete, run_text = backend.latest_run(config)
            if (complete or config.dry_run) and first_turn_done:
                break
            if time.time() >= deadline and backend.name == "deterministic":
                return _stop_campaign(config, state, "DEADLINE_REACHED",
                                      reason="deadline reached during active episode",
                                      journal="## Campaign stop\n\nWall-clock limit reached during an active run.\n")
            checkpoint += 1
            snapshot, offset, tick = backend.snapshot(config, cycle, checkpoint, 0)
            _append(config.observations, snapshot)
            if tick is not None:
                state.last_seen_tick = max(state.last_seen_tick or 0, tick)
            if config.aspect_observers and state.active_episode_id and not config.dry_run:
                from tools.campaign_observers import observe_team
                board = observe_team(config, state.active_episode_id, checkpoint)
                _append(config.observations, f"Aspect evidence board: {board}\n")
            if backend.name == "deterministic":
                _, assessment = _ask_with_continue(_readonly(config), primary_checkpoint(cycle, state.active_episode_id, checkpoint), state.primary_session_id, sequence, require_response=False)
                if _assistant_text(assessment):
                    _append(config.observations, _assistant_text(assessment))
            sequence += 1
            first_turn_done = True
            state.last_checkpoint = checkpoint
            state.last_checkpoint_episode = state.active_episode_id
            _save_state(config.state_file, state)
            complete, run_text = backend.latest_run(config)
            if complete or config.dry_run:
                break
            time.sleep(config.interval_seconds if backend.name == "laya" else min(config.interval_seconds, max(0, deadline - time.time())))

        if not config.dry_run and backend.name == "deterministic":
            history_db = config.state_root / "logs/run-history.sqlite"
            history_sources = sorted((config.state_root / "logs/archive").glob("autonomous-run-*.log"))
            history_sources.append(config.state_root / "logs/autonomous-run.log")
            try:
                for source in history_sources:
                    if source.exists():
                        index_log(history_db, source)
            except (OSError, ValueError, sqlite3.Error) as error:
                _append(config.observations, f"History index unavailable: {error}\n")
        if config.plastic_reliability and config.technology == "plastic-bar" and not config.dry_run:
            manifest_path = config.state_root / "episode/current.json"
            manifest = json.loads(manifest_path.read_text())
            report_path = config.state_root / "logs/production-acceptance.json"
            try:
                report = json.loads(report_path.read_text())
            except (OSError, ValueError):
                report = {}
            if _code_hash() != attempt_hash:
                report = {"ok": False, "result": "code_changed_during_run"}
            archive = config.state_root / "logs/reliability-runs" / hashlib.sha256(manifest["episode_id"].encode()).hexdigest()[:20]
            archive.mkdir(parents=True, exist_ok=True)
            for evidence in (manifest_path, report_path, config.state_root / "logs/latest-context.md", config.state_root / "logs/autonomous-run.log"):
                if evidence.is_file() and not (archive / evidence.name).exists():
                    shutil.copy2(evidence, archive / evidence.name)
            full_log = (config.state_root / "logs/autonomous-run.log").read_text()
            full_log = _current_run_text(full_log)
            reliability = reliability_state.record(reliability_path, manifest, report,
                seconds=config.acceptance_seconds, required=config.required_successes, code_hash=attempt_hash,
                milestones=_milestones(full_log))
            if reliability["runs"][-1]["passed"]:
                _append(config.observations, f"Plastic acceptance: {len(reliability['streak'])}/{config.required_successes} same-candidate fresh runs passed.\n")
                if reliability["phase"] == "research" and config.stop_after_acceptance:
                    return _finish_cycle(config, state, "acceptance", {}, run_text=full_log,
                                         terminal_phase="SUCCESS",
                                         reason="plastic acceptance certified")
                _finish_cycle(config, state, "acceptance", {}, run_text=full_log)
                continue
        if config.plastic_reliability and config.technology != "plastic-bar" and not config.dry_run:
            mission = json.loads((config.state_root / "logs/deterministic-mission-state.json").read_text())
            manifest = json.loads((config.state_root / "episode/current.json").read_text())
            if mission.get("episode_id", mission.get("mission_id")) == manifest.get("episode_id") and mission.get("status") == "completed":
                _append(config.observations, "Research runner completed its contract; research queued is not proof of research completion.\n")
                return _finish_cycle(config, state, "acceptance", {}, run_text="",
                                     terminal_phase="SUCCESS",
                                     reason="research runner contract completed; research completion needs live proof")
        if config.plastic_reliability and config.checkpoint_failures and not config.dry_run:
            from tools.episode_checkpoint import capture
            from tools.rcon_client import RconClient
            client = RconClient("127.0.0.1", config.rcon_port, (config.state_root / "rcon-password").read_text().strip())
            try:
                bundle = capture(config.state_root, client=client)
                _append(config.observations, f"Failed-world checkpoint: {bundle}\n")
            finally:
                client.close()

        # ---- RUN END: explicit state transition -----------------------------
        state.run_end = state.run_end or {"episode": state.active_episode_id,
                                          "run_seconds": _run_end_seconds(run_text),
                                          "wall_time": _now()}
        _save_state(config.state_file, state)
        _progress(config, "WAITING_FOR_HELPER_AND_OBSERVERS", state)
        if backend.name == "laya":
            # Natural run and its terminal evidence can finish after the campaign
            # deadline. No new model fix or episode is allowed afterwards.
            _COMMAND_DEADLINE = None
        helper, board = _terminal_evidence(config, state, state.active_episode_id)
        if backend.name == "laya":
            backend.stop(config)
            _COMMAND_DEADLINE = deadline
            if time.time() >= deadline:
                _finish_cycle(config, state, "deadline-after-natural-run", {}, run_text=run_text)
                return _stop_campaign(config, state, "DEADLINE_REACHED", reason="natural episode and terminal evidence finished after deadline")
            if helper.get("status") != "complete":
                episode = state.active_episode_id
                revision = _frozen_revision(config, state)
                prior = state.observer_failure or {}
                retried = prior.get("revision") == revision and int(prior.get("attempts", 0)) >= 1
                if not retried:
                    # One bounded retry of terminal evidence for the SAME episode;
                    # never a fresh episode. Records the attempt before waiting so
                    # a crash cannot silently allow a second retry.
                    state.observer_failure = {"revision": revision, "episode": episode,
                                              "attempts": 1, "terminal": False}
                    _save_state(config.state_file, state)
                    _append(config.observations,
                            f"Observer evidence incomplete for {episode} ({helper.get('reason')}); "
                            f"one bounded terminal evidence retry at revision {revision}.\n")
                    retry_helper = _retry_terminal_evidence(config, episode, helper)
                    if retry_helper.get("status") == "complete":
                        helper = retry_helper
                        state.helper = retry_helper
                        state.observer_failure = None
                        _save_state(config.state_file, state)
                if helper.get("status") != "complete":
                    _finish_cycle(config, state, "observer-failed", {}, run_text=run_text)
                    state.observer_failure = {"revision": revision, "episode": episode,
                                              "attempts": int(prior.get("attempts", 0)) or 1,
                                              "terminal": True}
                    _save_state(config.state_file, state)
                    return _stop_campaign(config, state, "HARNESS_FAILURE",
                                          reason=f"terminal observer failure unchanged at revision {revision}: "
                                                 f"{helper.get('reason')}",
                                          journal="## Campaign stop\n\nTerminal observer failure; no new episode "
                                                  "was started at this revision.\n")
            _ensure_primary(config, state, cycle, sequence)
            sequence += 1
        sequence += 1
        _append(config.observations,
                f"RUN END recorded for episode {state.active_episode_id} at +{state.run_end.get('run_seconds')}s; "
                f"helper status: {helper.get('status')} ({helper.get('reason')}). Observer board: {board}\n")

        # ---- terminal synthesis by the persistent primary -------------------
        if state.synthesis and state.synthesis.get("episode") == state.active_episode_id \
                and Path(state.synthesis.get("path")).is_file():
            synthesis_path = Path(state.synthesis["path"])
        else:
            _progress(config, "SYNTHESIZING", state)
            synthesis_output = _primary_turn(config, state, primary_run_end(
                state.active_episode_id, board, helper.get("status"), helper.get("path"),
                config.state_root / "logs/latest-context.md", config.observations, config), sequence)
            sequence += 1
            synthesis_path = config.state_root / "logs/campaign-loop" / f"synthesis-{cycle:04d}.md"
            _atomic_write(synthesis_path, _assistant_text(synthesis_output) or synthesis_output)
            hypothesis_id = _record_hypothesis(config, state, cycle, _assistant_text(synthesis_output))
            state.synthesis = {"episode": state.active_episode_id, "path": str(synthesis_path),
                               "hypothesis_id": hypothesis_id}
            _save_state(config.state_file, state)

        # ---- fixing through commit, all revisions in the same sessions ------
        from tools.campaign_fix_flow import run_fix_cycle
        outcome = run_fix_cycle(config, state, cycle, state.active_episode_id, synthesis_path,
                                board, helper, sequence)
        state.helper = _late_helper(config, state, state.active_episode_id)
        _finish_cycle(config, state, outcome, {}, run_text=run_text)
    return _stop_campaign(config, state, "SUCCESS",
                          reason="configured cycle limit reached")


def _stop_campaign(config: Config, state: State, phase: str, *, reason: str,
                   journal: str | None = None) -> int:
    """Persist a terminal condition; only genuinely unrecoverable ends land here."""
    _progress(config, phase, state, reason=reason)
    _save_state(config.state_file, state)
    if journal:
        _append(config.observations, journal)
    return 0


def _finish_cycle(config: Config, state: State, outcome: str, fields: dict, *,
                  run_text: str = "", reason: str | None = None,
                  terminal_phase: str | None = None) -> int:
    """Append the durable loop record, reset per-episode workflow state, advance."""
    from tools import campaign_loop_log
    from tools.campaign_observers import observer_sessions
    cycle = state.active_cycle or state.completed_cycles + 1
    episode = state.active_episode_id or "unknown"
    observers = {}
    if state.observer_episode:
        observers = {role: session for role, session in observer_sessions(
            state.observer_episode, config.opencode_log_dir).items() if session}
    fix_record = {}
    for fix in campaign_loop_log.fixes(config.state_root).values():
        if fix.get("introduced_cycle") == cycle:
            fix_record = fix
    start_line = next((line for line in run_text.splitlines() if line.startswith("RUN START:")), "")
    record = {
        "campaign_id": state.campaign_id,
        "cycle_id": cycle,
        "episode_id": episode,
        "primary_session_id": state.primary_session_id,
        "fixer_session_id": state.fixer_session_id,
        "run_start": start_line.replace("RUN START: ts=", "").strip() or None,
        "run_end_seconds": (state.run_end or {}).get("run_seconds"),
        "observer_sessions": observers,
        "helper_status": (state.helper or {}).get("status"),
        "helper_path": (state.helper or {}).get("path"),
        "helper_reason": (state.helper or {}).get("reason"),
        "terminal_hypothesis": (state.synthesis or {}).get("hypothesis_id"),
        "synthesis_path": (state.synthesis or {}).get("path"),
        "fix_id": fix_record.get("fix_id"),
        "fix_status": fix_record.get("status", outcome),
        "files_changed": fix_record.get("files"),
        "tests": fix_record.get("verification"),
        "review_result": fix_record.get("review"),
        "commit": fix_record.get("commit"),
        "verification_result": fix_record.get("verification"),
        "outcome": outcome,
        "reason": reason,
        "completed_at": _now(),
    }
    if cycle_record(config.state_root, state.campaign_id, cycle) is None:
        campaign_loop_log.append_cycle(config.state_root, record)
    if terminal_phase is not None:
        return _stop_campaign(config, state, terminal_phase, reason=reason or outcome)
    state.completed_cycles += 1
    state.active_cycle = None
    state.active_episode_id = None
    state.observer_episode = None
    state.run_end = None
    state.helper = None
    state.synthesis = None
    state.pending_fix = None
    state.pre_cycle_dirty = []
    state.episode_runtime = None
    _save_state(config.state_file, state)
    return 0


def _config(args: argparse.Namespace) -> Config:
    from tools.campaign_runtime import (
        LAYA_MODEL, LAYA_OBSERVER_MODEL, LAYA_OBSERVER_VARIANT, LAYA_VARIANT,
    )
    root = args.state_root.expanduser().resolve()
    laya = args.runtime == "laya"
    world_root = args.world_root
    if world_root is None and laya:
        world_root = root / "laya-world"
    return Config(
        state_root=root, source_save=args.source_save.expanduser().resolve(), observations=args.observations.resolve(),
        state_file=(args.state_file or root / "logs" / ("reliability-campaign-state.json" if args.plastic_reliability else "opencode-campaign-state.json")).resolve(),
        opencode_log_dir=(args.opencode_log_dir or root / "logs/opencode-campaign").resolve(),
        technology=args.technology, interval_seconds=args.interval_seconds,
        post_run_wait_seconds=args.post_run_wait_seconds, max_cycles=args.max_cycles,
        max_runtime_seconds=args.max_runtime_hours * 3600, model=args.model or (LAYA_MODEL if laya else DEFAULT_MODEL),
        variant=args.variant or (LAYA_VARIANT if laya else "auto"),
        # Keep --python unresolved: the runner manager detects the venv via
        # `<python-dir>/../pyvenv.cfg`, and resolving `.venv/bin/python` to
        # the underlying uv interpreter breaks that detection, leaving the
        # systemd runner without its site-packages (ModuleNotFoundError).
        opencode_bin=args.opencode_bin, python=args.python, campaign_manager=args.campaign_manager.resolve(),
        dashboard_url=args.dashboard_url, dry_run=args.dry_run, resume_active_run=args.resume_active_run,
        plastic_reliability=args.plastic_reliability, acceptance_seconds=args.acceptance_seconds,
        required_successes=args.required_successes, stop_after_acceptance=args.stop_after_acceptance,
        checkpoint_failures=args.checkpoint_failures, rcon_port=args.rcon_port,
        game_port=args.game_port, runtime_root=args.runtime_root, gui_mods=args.gui_mods,
        # Laya uses exactly one read-only Muse writer, never the four-observer team.
        aspect_observers=False if laya else args.aspect_observers, progress_file=args.progress_file,
        review_model=args.review_model or (LAYA_MODEL if laya else None), review_variant=args.review_variant or "high",
        observer_model=args.observer_model or (LAYA_OBSERVER_MODEL if laya else "opencode-go/muse-spark-1.3-contributor"),
        observer_variant=args.observer_variant or (LAYA_OBSERVER_VARIANT if laya else "high"),
        fixer_model=args.fixer_model or (LAYA_MODEL if laya else None), fixer_variant=args.fixer_variant or "high",
        zen_observer_model=args.zen_observer_model,
        helper_wait_seconds=args.helper_wait_seconds,
        max_fix_revisions=args.max_fix_revisions,
        runtime=args.runtime, world_root=world_root, code_root=args.code_root,
        mods_source=args.mods_source, run_speed=args.speed, until_accepted=args.until_accepted,
        max_game_ticks=args.max_game_ticks, run_wall_seconds=args.run_wall_seconds, seed=args.seed,
        campaign_worktree=args.campaign_worktree.resolve() if args.campaign_worktree else None,
        mod_builder=args.mod_builder, stamp_script=args.stamp_script, stamp_tag_base=args.stamp_tag_base,
        comparator_roots=tuple(args.comparators or ()),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-failures", action="store_true", help="Save failed world and durable sidecars before fixing or resetting (reliability mode).")
    parser.add_argument("--rcon-port", type=int, default=27017)
    parser.add_argument("--game-port", type=int, default=34199)
    parser.add_argument("--runtime", choices=["deterministic", "laya"], default="deterministic",
                        help="laya drives a natural Laya episode with a single read-only Muse helper.")
    parser.add_argument("--world-root", type=Path, help="Laya episode root (default <state-root>/laya-world).")
    parser.add_argument("--code-root", type=Path, help="Immutable Laya code copy root (default: repository).")
    parser.add_argument("--mods-source", type=Path, help="Stamped mods copy to hash into freeze.json before prepare.")
    parser.add_argument("--speed", type=float, default=2.0, help="Declared live game speed for a Laya episode.")
    parser.add_argument("--until-accepted", default="C6", help="Laya checkpoint predicate that ends the run.")
    parser.add_argument("--max-game-ticks", type=int, default=1_440_000, help="Natural game-tick bound for a Laya episode.")
    parser.add_argument("--run-wall-seconds", type=int, default=21_600, help="Natural wall bound mirrored from the runner timeout.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--campaign-worktree", type=Path, help="Explicit writable Git/model/test tree, separate from frozen supervisor.")
    parser.add_argument("--mod-builder", type=Path, help="Build frozen current mod sources (--source factorio_mod --output mods).")
    parser.add_argument("--stamp-script", type=Path, help="Run-kit stamp script (--server-mods --gui-mods --tag).")
    parser.add_argument("--stamp-tag-base", type=int, default=30000)
    parser.add_argument("--comparators", type=Path, action="append", help="Prior run root passed explicitly to terminal Muse (repeatable).")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--gui-mods", type=Path)
    parser.add_argument("--aspect-observers", action="store_true")
    parser.add_argument("--progress-file", type=Path)
    parser.add_argument("--plastic-reliability", action="store_true", help="Require three sustained plastic fresh runs before research; review/test/commit fixes automatically.")
    parser.add_argument("--acceptance-seconds", type=int, default=120)
    parser.add_argument("--required-successes", type=int, default=3)
    parser.add_argument("--stop-after-acceptance", action="store_true")
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--source-save", type=Path, default=DEFAULT_SOURCE_SAVE)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--opencode-log-dir", type=Path)
    parser.add_argument("--technology", default="mining-productivity-4")
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument("--post-run-wait-seconds", type=int, default=120)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 stops only on a guard or completion.")
    parser.add_argument("--max-runtime-hours", type=int, default=8, help="0 disables this wall-clock guard.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--variant", default=None)
    parser.add_argument("--review-model")
    parser.add_argument("--review-variant", default=None)
    parser.add_argument("--observer-model", default=None)
    parser.add_argument("--observer-variant", default=None)
    parser.add_argument("--fixer-model")
    parser.add_argument("--fixer-variant", default=None)
    parser.add_argument("--zen-observer-model", default="opencode-zen/muse-spark-1.3")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--python", type=Path, default=REPO_ROOT / ".venv/bin/python")
    parser.add_argument("--campaign-manager", type=Path, default=REPO_ROOT / "scripts/manage_linux_deterministic_campaign.sh")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:9137/api/logistic-inventory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-active-run", action="store_true", help="Attach an observer to an already-running isolated episode without resetting it.")
    parser.add_argument("--helper-wait-seconds", type=int, default=120, help="Maximum seconds after RUN END to wait for OpenCode helper findings.")
    parser.add_argument("--max-fix-revisions", type=int, default=2, help="Bounded fixer revisions per episode before a candidate is abandoned.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_campaign(_config(args))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"opencode campaign orchestrator: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Runtime leaves import this canonical name; share configured ownership globals
    # when invoked with -m instead of creating a second controller module.
    sys.modules["tools.opencode_campaign_orchestrator"] = sys.modules[__name__]
    raise SystemExit(main())

