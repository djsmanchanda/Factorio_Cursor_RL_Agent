"""Automate isolated deterministic Factorio runs with a per-run OpenCode observer.

The controller starts fresh episodes, records read-only two-minute evidence,
then asks that run's OpenCode session for one code-correlated focused change.
"""

from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import re
import subprocess
import sqlite3
import sys
import time
import shutil
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

from tools.run_context import build_context
from tools.run_history import index_log
from tools import reliability_state
from tools.run_log_format import is_run_end_line, parse_timed_run_log_line
DEFAULT_STATE_ROOT = Path.home() / ".local/share/factorio-rl/deterministic"
DEFAULT_SOURCE_SAVE = Path.home() / ".factorio/saves/mod_playground.zip"
DEFAULT_OBSERVATIONS = REPO_ROOT / "docs/deterministic/opencode_campaign_observations.md"
DEFAULT_MODEL = "opencode-go/muse-spark-1.3-contributor"
RUN_START = "RUN START:"
RUN_END = "RUN END"
# Real terminal lines look like `+2443s RUN END` at line start. The helper
# agent's start prompt quotes "RUN END" mid-line, so a substring test would
# declare every live run complete on its first checkpoint.
_RUN_END_LINE = re.compile(r"(?m)^\+\d+s RUN END$")
DECISION = re.compile(r"CAMPAIGN_DECISION:\s*(.*)", re.DOTALL)


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


@dataclass
class State:
    completed_cycles: int = 0
    terminal_keys: list[str] = field(default_factory=list)
    active_cycle: int | None = None
    active_session_id: str | None = None
    deadline_utc: str | None = None
    pending_fix: dict | None = None
    active_code_hash: str | None = None
    active_episode_id: str | None = None
    lifecycle_intent: dict | None = None


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
        active_episode_id=payload.get("active_episode_id"),
        lifecycle_intent=payload.get("lifecycle_intent"),
        deadline_utc=payload.get("deadline_utc"),
        pending_fix=payload.get("pending_fix"),
        active_code_hash=payload.get("active_code_hash"),
        completed_cycles=int(payload.get("completed_cycles", 0)),
        terminal_keys=[str(item) for item in payload.get("terminal_keys", []) if isinstance(item, str)],
        active_cycle=int(payload["active_cycle"]) if isinstance(payload.get("active_cycle"), int) else None,
        active_session_id=(
            str(payload["active_session_id"]) if isinstance(payload.get("active_session_id"), str)
            and payload["active_session_id"] else None
        ),
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
    return subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def _append(path: Path, text: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<!-- Path: docs/deterministic/opencode_campaign_observations.md | Purpose: Durable automated campaign evidence. -->\n\n"
            "# Automated deterministic campaign observations\n\n"
            "The controller appends raw evidence; each run's OpenCode session appends the code-correlated interpretation.\n",
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


def _terminal_key(run_text: str) -> str:
    lines = [line for line in run_text.splitlines() if "STUCK:" in line or "ERROR:" in line]
    value = lines[-1] if lines else "run-ended-without-terminal-detail"
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", value.lower())[:180]


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
    return (
        f"tick={report.get('tick', 'unknown')}; networks={len(networks) if isinstance(networks, list) else 0}; "
        f"disconnected_roboports={report.get('disconnected_roboports', 'unknown')}\n\nTop logistic items: {counts}"
    )


def _snapshot(config: Config, cycle: int, checkpoint: int, offset: int) -> tuple[str, int]:
    if config.dry_run:
        status_text = "Dry run: status probe skipped."
        log, next_offset = "Dry run: runner-log probe skipped.", offset
        inventory = "Dry run: Operations Console probe skipped."
    else:
        status = _run(_status_command(config), timeout=60)
        inventory = _inventory(config.dashboard_url)
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
        next_offset,
    )


def _session_ids(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"sessionID", "session_id"} and isinstance(child, str) and child:
                yield child
            yield from _session_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _session_ids(child)


def _session_id(output: str) -> str | None:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = next(_session_ids(event), None)
        if found:
            return found
    return None


def _opencode_command(config: Config, message: str, session_id: str | None) -> list[str]:
    command = [
        config.opencode_bin, "run", "--dir", str(REPO_ROOT), "--format", "json", "--model", config.model,
        "--variant", config.variant, "--auto",
    ]
    if session_id:
        command += ["--session", session_id]
    else:
        command += ["--title", "Automated Factorio deterministic campaign"]
    return command + [message]


def _ask(config: Config, message: str, session_id: str | None, sequence: int) -> tuple[str | None, str]:
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
    config.opencode_log_dir.mkdir(parents=True, exist_ok=True)
    transport = config.opencode_log_dir / f"opencode-{sequence:04d}.jsonl"
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


def _progress(config: Config, phase: str, state: State, **details) -> None:
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


def _initial_prompt(config: Config, cycle: int) -> str:
    return f"""You are the accountable investigator and implementer for fresh deterministic Factorio cycle {cycle}, using one session for this run only.
The parent controller already started the isolated run for `{config.technology}` and will send this same session a
durable checkpoint every {config.interval_seconds} seconds. Work in `{REPO_ROOT}`; preserve unrelated dirty files.
The sole live scope is `{config.state_root}` on `nauvis` / `player`.

Read AGENTS.md, docs/system_invariants.md, docs/factorio_operations.md, docs/deterministic/README.md,
docs/deterministic/planning.md, docs/deterministic/opencode_campaign_prompt.md, git status, and the prior run summaries in
`{config.observations}`. Read `docs/deterministic/run_context.md` for evidence scope. For every checkpoint, read its new section and return a compact assessment for the controller to append.

Separate observation from interpretation. Record timestamped facts, deltas, and evidence references (log offsets,
report ticks, inventory snapshots). Label every hypothesis as one; retain raw observations in source artifacts, not copied into the transcript;
summaries report only milestone transitions, newly blocked dependencies, production/delivery-rate changes, and
contradictions. Collapse repeated unchanged observations into one interval with a repetition count. Never convert
"unknown" into zero, and never infer adequate supply from aggregate stock: total, accessible, and already-allocated
stock are different claims. A blocked dependency is captured whole: recipe and required quantities, requester
contents, machine input/output inventories, assembler/inserter status, power, transferable stock, reservations,
ghost backlog, and craft/delivery deltas.

Compare against three references, not one: the preceding run, the best verified milestone run, and previous runs
with the same failure mechanism. A changed terminal item alone does not establish a different cause. Classify each
outcome separately: factory improvement, useful diagnostic evidence, regression, or inconclusive. Longer survival
and larger inventories do not establish factory improvement. Use read-only live observation and
GET `{config.dashboard_url}`; do not change code or execute server lifecycle/factory actions while the run is active.

At RUN END, compare all three references and own the investigation through implementation. Delegate bounded
observation, reproduction or review to subagents; verify their decisive claims against raw evidence and code.
When the cause and reusable fix are supported, implement and test without asking the user to approve
routine repository work, then return the diff for primary review. Do not commit: the primary orchestrator
reviews, integrates and commits. An analysis-only handoff is not completion when a justified fix is available.
The parent process controls lifecycle and the next run; it is not a human approval gate. Reply briefly after setup."""


def _checkpoint_prompt(config: Config, cycle: int, checkpoint: int) -> str:
    return f"""Cycle {cycle}, checkpoint {checkpoint} is appended to `{config.observations}`. Read the new section and the referenced rolling context packet and return its assessment for the controller (at most 1200 characters): timestamped facts and deltas first, then explicitly labeled hypotheses. Keep the whole blocked
dependency (recipe/quantities, requester and machine inventories, statuses, power, transferable vs allocated stock,
reservations, ghosts, craft/delivery deltas). If nothing changed, extend the unchanged interval with its repetition
count and name the next measurable signal rather than repeating old logs. The runner remains active: make no code,
lifecycle, deployment, reset, or factory changes."""


def _completion_prompt(config: Config, cycle: int) -> str:
    return f"""Cycle {cycle} has RUN END. Before anything else, inspect the preserved failed episode with read-only probes
where possible: the isolated save is still in place until the parent starts the next fresh run. Read its final run
block in `{config.state_root}/logs/latest-context.md`, follow decisive raw line references, wait up to {config.post_run_wait_seconds} seconds for helper output if it is still arriving, and compare this
completed run against three references in `{config.observations}`: the preceding run, the best verified milestone run,
and previous runs with the same failure mechanism. Use `python -m tools.run_history search --db {config.state_root}/logs/run-history.sqlite --query "<blocker or dependency>"` for matching historical evidence. Then classify the outcome: factory improvement, useful diagnostic
evidence, regression, or inconclusive. Survival time and inventory size alone decide nothing.

Only when the evidence supports a fix, implement exactly one small reusable fix, add/update its narrow regression
test, and run the focused test. Before editing, state: observed failure -> causal hypothesis -> supporting evidence
-> competing explanation -> smallest reusable fix -> predicted measurable result. If the evidence cannot distinguish
the explanations, obtain the missing read-only observation or local reproduction yourself, using subagents when
useful. Ask the user only for genuinely unavailable input or authority outside the campaign scope. A telemetry-only rerun is allowed
only when the missing evidence requires execution, and you must specify what each possible result would mean. Prefer
verifying the failure mechanism locally (a focused behavioral reproduction, then the predicted milestone under
matching starting conditions) over another full episode. Do not start/reset/redeploy Factorio; the parent owns the
next fresh run. Preserve unrelated dirty files. You own the code fix: verify subagent findings, implement the
supported correction, run focused tests, address test failures, and review the diff — then stop and report files
and results without committing. The primary orchestrator integrates and commits; your verdict stays `change` once
the tested edit exists in the worktree. Do not end with "want me to implement?", "parent-owned fix", or an analysis-only handoff when you have a supported
fix within repository scope. Report status: change only after implementation and verification. Stop or no-change
is appropriate only for a concrete unresolved evidence gap, failed verification, exhausted budget, mission
completion, or authority outside scope; explain the blocker and investigation already attempted. Never invent a
fix merely to continue. Respect the controller's existing repeated-failure and runtime guards. Record exact provenance in the journal entry (one entry
per episode): episode ID, commit, dirty-patch hash, save/configuration/profile, tests, and activated code. End exactly with:

CAMPAIGN_DECISION:
status: change|no-change|stop
files: comma-separated paths or none
test: command/result or not-run
prediction: falsifiable next production milestone and supporting local regression evidence
reason: one sentence
"""


def _telemetry_prompt(config: Config, cycle: int) -> str:
    return f"""Cycle {cycle}'s final comparison declined a behavior change. A follow-up observability patch is allowed
only to resolve one specifically named evidence gap from that comparison: implement the minimal zero-behavior
telemetry that captures it, with a narrow regression test, and state what each possible reading would mean for the
competing explanations. Do not alter planning behavior, start/reset/redeploy Factorio, or touch unrelated files.
Never manufacture telemetry just to unlock another run: if no evidence gap names telemetry as its resolution, make
no change and say so. If implemented, verify the telemetry patch and report it uncommitted for primary review. Append the outcome to `{config.observations}`. End exactly with:

CAMPAIGN_DECISION:
status: change|no-change|stop
files: comma-separated paths or none
test: command/result or not-run
prediction: falsifiable next production milestone and supporting local regression evidence
reason: one sentence
"""


def _tree_fingerprint(observations: Path) -> str:
    """Fingerprint code/test changes while excluding the mandatory campaign journal."""
    command = ["git", "diff", "HEAD", "--binary", "--no-ext-diff", "--", "."]
    head_tree = _run(["git", "rev-parse", "HEAD^{tree}"], timeout=120).stdout
    try:
        relative_notes = observations.resolve().relative_to(REPO_ROOT)
    except ValueError:
        relative_notes = None
    if relative_notes is not None:
        command.append(f":(exclude){relative_notes.as_posix()}")
    tracked = _run(command, timeout=120).stdout
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], timeout=120).stdout
    if relative_notes is not None:
        untracked = "\n".join(
            line for line in untracked.splitlines() if line != relative_notes.as_posix()
        )
    return hashlib.sha256((head_tree + tracked + "\n--untracked--\n" + untracked).encode("utf-8", errors="replace")).hexdigest()


def _decision_fields(output: str, marker: str = "CAMPAIGN_DECISION") -> dict[str, str]:
    # JSON transport escapes newlines; parse only assistant text events, not
    # tool output or echoed prompts, before reading the final decision block.
    texts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "text":
            part = event.get("part", {})
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    text = "\n".join(texts) if texts else output
    result = {}
    for chunk in text.split(marker + ":")[1:]:
        fields = dict(line.split(":", 1) for line in chunk.splitlines() if ":" in line)
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        if fields.get("status") in {"change", "no-change", "stop", "approved", "rejected"}:
            result = fields
    return result


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


def _code_hash() -> str:
    paths = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "orchestrator", "planners", "core", "tools", "scripts", "factorio_mod", "schemas"], timeout=120).stdout.split("\0")
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


def _review_and_commit(config: Config, output: str, protected: set[str], sequence: int) -> bool:
    fields = _decision_fields(output)
    paths = [p.strip() for p in fields.get("files", "").split(",") if p.strip()]
    if not paths or any(p == "none" or p in protected or p.startswith("-") or
                        Path(p).is_absolute() or ".." in Path(p).parts or
                        not (REPO_ROOT / p).resolve().is_relative_to(REPO_ROOT.resolve()) for p in paths):
        _append(config.observations, "Fix requires review: missing/unsafe file list or pre-existing dirty file was touched.\n")
        return False
    permitted = set(paths) | protected
    if config.observations.resolve().is_relative_to(REPO_ROOT.resolve()):
        permitted.add(config.observations.resolve().relative_to(REPO_ROOT.resolve()).as_posix())
    undeclared = _dirty_paths() - permitted
    if undeclared:
        _append(config.observations, "Fix requires review: undeclared new changes: " + ", ".join(sorted(undeclared)) + "\n")
        return False
    if _run(["git", "diff", "--cached", "--name-only"], timeout=120).stdout.strip():
        _append(config.observations, "Fix requires review: index already contains staged work.\n")
        return False
    digest = _tree_fingerprint(config.observations)
    evidence = config.opencode_log_dir / f"review-{sequence:04d}.md"
    diff = _run(["git", "diff", "HEAD", "--", *paths], timeout=120).stdout
    additions = []
    for path in paths:
        if _run(["git", "ls-files", "--error-unmatch", "--", path], timeout=120).returncode:
            additions.append(f"\nNew file: {path}\n" + (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace"))
    _atomic_write(evidence, "# Candidate evidence\n\n" + output + "\n\n## Diff\n\n" + diff + "\n".join(additions))
    _, review = _ask(_readonly(config), f"""Independently review this candidate fix. Read AGENTS.md, the current run packet at
{config.state_root}/logs/latest-context.md and the changed files: {', '.join(paths)}.
Read the parent-captured candidate diff, new files, prediction and test claim at {evidence}.
Read-only review: do not edit, commit or operate Factorio. Check causal evidence, invariants,
regression coverage and the predicted plastic milestone. Verify the prediction is cause-specific and backed by the regression; a repeated terminal permits another episode only with this evidence. Do not approve merely because tests pass.
Finish exactly with REVIEW_DECISION:
status: approved|rejected
reason: one sentence""", None, sequence)
    if _decision_fields(review, "REVIEW_DECISION").get("status") != "approved" or _tree_fingerprint(config.observations) != digest:
        return False
    pytest = [str(config.python), "-m", "pytest", "-q", "--tb=short"]
    # Do not spend every repair cycle on exhaustive unrelated training sweeps.
    # Changed regression files run unfiltered, including any slow-marked case.
    changed_tests = [p for p in paths if Path(p).parts[0] == "tests"
                     and Path(p).name.startswith("test_") and Path(p).suffix == ".py"]
    commands = [pytest + ["-m", "not slow and not exhaustive"]]
    if changed_tests:
        commands.append(pytest + changed_tests)
    verification_log = config.opencode_log_dir / f"verification-{sequence:04d}.log"
    _atomic_write(verification_log, "")
    for command in commands:
        validation = _run(command, timeout=900)
        with verification_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(command) + "\n" + validation.stdout + validation.stderr)
        if validation.returncode:
            return False
    if _run(["git", "diff", "--check"], timeout=120).returncode:
        return False
    # Primary controller owns the scoped commit; fixer and reviewer remain separate.
    if _run(["git", "add", "--", *paths], timeout=120).returncode:
        return False
    committed = _run(["git", "commit", "-m", "fix(deterministic): verified campaign repair"], timeout=120)
    if committed.returncode:
        return False
    revision = _run(["git", "rev-parse", "HEAD"], timeout=120).stdout.strip()
    _append(config.observations, f"Verified commit: {revision or 'inspect git log'}; files: {', '.join(paths)}. "
            f"Prediction: {fields.get('prediction', 'not provided')}. Independent review and pytest passed.\n")
    return True


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
    global _COMMAND_DEADLINE
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
    reliability_path = config.state_root / "logs/reliability-state.json"
    base_config = config
    existing_sequences = [int(p.stem.split("-")[-1]) for p in config.opencode_log_dir.glob("opencode-*.jsonl") if p.stem.split("-")[-1].isdigit()]
    sequence = max(existing_sequences, default=0) + 1
    if state.pending_fix and state.pending_fix.get("phase", "review") == "review" and config.plastic_reliability:
        pending = state.pending_fix
        if time.time() >= deadline or not _review_and_commit(config, pending["output"], set(pending["protected"]), sequence):
            _progress(config, "parked", state, reason="pending review could not complete")
            return 0
        state.pending_fix = None
        _save_state(config.state_file, state)
        sequence += 1
    while config.max_cycles == 0 or state.completed_cycles < config.max_cycles:
        if time.time() >= deadline:
            _progress(config, "deadline", state, reason="campaign deadline reached")
            _append(config.observations, "## Campaign stop\n\nWall-clock limit reached; no new episode was started.\n")
            return 0
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
                _progress(config, "complete", state, reason="plastic acceptance certified")
                return 0
            config = replace(base_config, technology="plastic-bar" if reliability["phase"] == "plastic" else base_config.technology)
        if config.plastic_reliability and not config.dry_run:
            config = replace(config, source_save=_pin_source(config))
        attempt_hash = _code_hash() if config.plastic_reliability else ""
        cycle = state.completed_cycles + 1
        # A saved active cycle is sufficient to resume even if the first model
        # call crashed before returning a session. Never infer reset permission
        # from the absence of a model session ID.
        resuming = state.active_cycle == cycle
        if state.lifecycle_intent:
            current_episode = _episode_id(config)
            run_log = config.state_root / "logs/autonomous-run.log"
            current_log = _current_run_text(run_log.read_text(encoding="utf-8", errors="replace")) if run_log.exists() else ""
            if (current_episode and current_episode == state.lifecycle_intent.get("expected_episode")
                    and current_episode in current_log):
                state.active_cycle = cycle
                state.active_episode_id = current_episode
                state.lifecycle_intent = None
                _save_state(config.state_file, state)
                resuming = True
            else:
                _progress(config, "blocked", state, reason="fresh lifecycle interrupted; inspect before retrying")
                raise RuntimeError("fresh lifecycle intent is unresolved; refusing an automatic second reset")
        if resuming and config.plastic_reliability:
            attempt_hash = state.active_code_hash or "unknown-start-code"
        adopt_active_run = config.resume_active_run and not resuming
        if resuming:
            if state.active_episode_id and _episode_id(config) != state.active_episode_id and not config.dry_run:
                raise RuntimeError("active episode provenance changed; refuse to adopt another world")
            session_id = state.active_session_id
            _append(config.observations, f"## Cycle {cycle} — resumed ({_now()}); no fresh lifecycle issued.\n")
            offset = 0
        else:
            if adopt_active_run:
                complete, run_text = _latest_run(config.state_root / "logs/autonomous-run.log")
                if complete or not run_text or not _episode_id(config):
                    raise RuntimeError("--resume-active-run requires an identified unfinished episode")
            else:
                config = replace(config, episode_id="episode-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
                state.lifecycle_intent = {"previous_episode": _episode_id(config), "expected_episode": config.episode_id, "cycle": cycle}
                state.active_code_hash = attempt_hash
                _save_state(config.state_file, state)
                _progress(config, "starting", state)
                fresh = subprocess.CompletedProcess(_fresh_command(config), 0, "dry run", "") if config.dry_run else _run(_fresh_command(config), timeout=900)
                if fresh.returncode:
                    raise RuntimeError(f"fresh lifecycle failed:\n{fresh.stdout}\n{fresh.stderr}")
                _append(config.observations, f"## Cycle {cycle} — fresh lifecycle ({_now()})\n\n```text\n{(fresh.stdout + fresh.stderr).strip()}\n```\n")
            state.active_cycle = cycle
            state.active_episode_id = _episode_id(config) or (f"dry-run-{cycle}" if config.dry_run else None)
            state.active_code_hash = attempt_hash
            state.lifecycle_intent = None
            state.active_session_id = None
            _save_state(config.state_file, state)
            session_id = None
            offset = 0
        _progress(config, "observing", state)
        if session_id is None:
            try:
                session_id, _ = _ask(_readonly(config), _initial_prompt(config, cycle) + "\nRead-only: return findings; do not edit files. The controller owns journal writes.", None, sequence)
            except Exception:
                transport = config.opencode_log_dir / f"opencode-{sequence:04d}.jsonl"
                if transport.exists():
                    state.active_session_id = _session_id(transport.read_text())
                    _save_state(config.state_file, state)
                raise
            sequence += 1
            if session_id is None:
                raise RuntimeError("OpenCode did not return a session ID; active episode retained")
            state.active_session_id = session_id
            _save_state(config.state_file, state)
        checkpoint = 0
        while True:
            checkpoint += 1
            snapshot, offset = _snapshot(config, cycle, checkpoint, offset)
            _append(config.observations, snapshot)
            if config.aspect_observers and state.active_episode_id and not config.dry_run:
                from tools.campaign_observers import observe_team
                board = observe_team(config, state.active_episode_id, checkpoint)
                _append(config.observations, f"Aspect evidence board: {board}\n")
            _, assessment = _ask(_readonly(config), _checkpoint_prompt(config, cycle, checkpoint) + "\nReturn findings only; do not edit the journal.", session_id, sequence)
            from tools.campaign_observers import _assistant_text
            if _assistant_text(assessment):
                _append(config.observations, _assistant_text(assessment))
            sequence += 1
            complete, run_text = _latest_run(config.state_root / "logs/autonomous-run.log")
            if complete or config.dry_run:
                break
            if time.time() >= deadline:
                _progress(config, "deadline", state, reason="deadline reached during active episode")
                _append(config.observations, "## Campaign stop\n\nWall-clock limit reached during an active run.\n")
                return 0
            time.sleep(min(config.interval_seconds, max(0, deadline - time.time())))
        if not config.dry_run:
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
                state.completed_cycles += 1
                state.active_cycle = state.active_session_id = state.active_episode_id = None
                state.terminal_keys = []
                _save_state(config.state_file, state)
                _append(config.observations, f"Plastic acceptance: {len(reliability['streak'])}/{config.required_successes} same-candidate fresh runs passed.\n")
                if reliability["phase"] == "research" and config.stop_after_acceptance:
                    _progress(config, "complete", state, reason="plastic acceptance certified")
                    return 0
                continue
        if config.plastic_reliability and config.technology != "plastic-bar" and not config.dry_run:
            mission = json.loads((config.state_root / "logs/deterministic-mission-state.json").read_text())
            manifest = json.loads((config.state_root / "episode/current.json").read_text())
            if mission.get("episode_id", mission.get("mission_id")) == manifest.get("episode_id") and mission.get("status") == "completed":
                state.completed_cycles += 1
                state.active_cycle = state.active_session_id = state.active_episode_id = None
                _save_state(config.state_file, state)
                _progress(config, "complete", state, reason="research runner contract completed; research completion needs live proof")
                _append(config.observations, "Research runner completed its contract; research queued is not proof of research completion.\n")
                return 0
        if config.plastic_reliability and config.checkpoint_failures and not config.dry_run:
            from tools.episode_checkpoint import capture
            from tools.rcon_client import RconClient
            client = RconClient("127.0.0.1", config.rcon_port, (config.state_root / "rcon-password").read_text().strip())
            try:
                bundle = capture(config.state_root, client=client)
                _append(config.observations, f"Failed-world checkpoint: {bundle}\n")
            finally:
                client.close()
        _progress(config, "fixing", state)
        board_context = ""
        if config.aspect_observers and state.active_episode_id and not config.dry_run:
            from tools.campaign_observers import observe_team
            board = observe_team(config, state.active_episode_id, checkpoint + 1, terminal=True)
            board = observe_team(config, state.active_episode_id, checkpoint + 2, terminal=True)
            board_context = f"\nConvene over all four role findings in {board}; verify their raw evidence and resolve disagreements before fixing."
        if not state.pending_fix:
            state.pending_fix = {"phase": "fixing", "protected": sorted(_dirty_paths()) if config.plastic_reliability else [],
                                 "before": _tree_fingerprint(config.observations)}
            _save_state(config.state_file, state)
        protected = set(state.pending_fix["protected"])
        before = state.pending_fix["before"]
        try:
            fixer_session, output = _ask(config, _completion_prompt(config, cycle) + board_context +
                         "\nContinue any partial candidate from the previous interrupted attempt; preserve pre-existing dirty files: " + ", ".join(sorted(protected)), state.pending_fix.get("session_id"), sequence)
            state.pending_fix["session_id"] = fixer_session
            _save_state(config.state_file, state)
        except Exception:
            transport = config.opencode_log_dir / f"opencode-{sequence:04d}.jsonl"
            if transport.exists() and not state.pending_fix.get("session_id"):
                state.pending_fix["session_id"] = _session_id(transport.read_text())
                _save_state(config.state_file, state)
            raise
        sequence += 1
        after = _tree_fingerprint(config.observations)
        decision = _decision(output)
        if not _decision_fields(output).get("status") and not config.dry_run:
            _, output = _ask(config, "Your response omitted CAMPAIGN_DECISION. Continue this same investigation and any partial edit; do not operate lifecycle. Return the required status/files/test/prediction/reason fields after verifying the candidate, or identify a concrete blocker.", state.pending_fix.get("session_id"), sequence)
            sequence += 1
            after = _tree_fingerprint(config.observations)
            decision = _decision(output)
        if decision == "no-change" and before == after:
            _, output = _ask(config, _telemetry_prompt(config, cycle), state.pending_fix.get("session_id"), sequence)
            sequence += 1
            after = _tree_fingerprint(config.observations)
            decision = _decision(output)
        if decision != "change" or before == after:
            # Resume must return to this investigation, especially when a model
            # leaves a partial diff without a valid verdict. It cannot authorize
            # running that unverified candidate or resetting its failed world.
            _save_state(config.state_file, state)
            _progress(config, "parked", state, reason="no verified change verdict; resume continues this investigation")
            _append(config.observations, "## Campaign parked\n\nNo verified change verdict. Episode and pending investigation retained; no next reset authorized.\n")
            return 0
        state.completed_cycles += 1
        state.terminal_keys = (state.terminal_keys + [_terminal_key(run_text)])[-2:]
        state.active_cycle = state.active_session_id = state.active_episode_id = None
        state.pending_fix = ({"phase": "review", "output": output, "protected": sorted(protected)}
                             if decision == "change" and before != after and config.plastic_reliability else None)
        _save_state(config.state_file, state)
        if config.plastic_reliability and not config.dry_run:
            _progress(config, "reviewing", state)
            state.pending_fix = {"phase": "review", "output": output, "protected": sorted(protected)}
            _save_state(config.state_file, state)
            if not _review_and_commit(config, output, protected, sequence):
                _progress(config, "parked", state, reason="review, verification or commit failed")
                _append(config.observations, "Campaign parked: independent review, verification or scoped commit did not pass.\n")
                return 0
            sequence += 1
            state.pending_fix = None
            _save_state(config.state_file, state)
        repeated = len(state.terminal_keys) == 2 and state.terminal_keys[0] == state.terminal_keys[1]
        fields = _decision_fields(output)
        justified_retry = (config.plastic_reliability and not config.dry_run and
                           bool(fields.get("prediction", "").strip()) and fields.get("test", "not-run") != "not-run")
        if repeated and not justified_retry:
            _progress(config, "blocked", state, reason="repeated terminal without reviewed distinguishing prediction")
            _append(config.observations, "## Campaign stop\n\nThe same terminal failure occurred twice; a third retry is blocked.\n")
            return 0
    _progress(config, "complete", state, reason="configured cycle limit reached")
    return 0


def _config(args: argparse.Namespace) -> Config:
    root = args.state_root.expanduser().resolve()
    return Config(
        state_root=root, source_save=args.source_save.expanduser().resolve(), observations=args.observations.resolve(),
        state_file=(args.state_file or root / "logs" / ("reliability-campaign-state.json" if args.plastic_reliability else "opencode-campaign-state.json")).resolve(),
        opencode_log_dir=(args.opencode_log_dir or root / "logs/opencode-campaign").resolve(),
        technology=args.technology, interval_seconds=args.interval_seconds,
        post_run_wait_seconds=args.post_run_wait_seconds, max_cycles=args.max_cycles,
        max_runtime_seconds=args.max_runtime_hours * 3600, model=args.model, variant=args.variant,
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
        aspect_observers=args.aspect_observers, progress_file=args.progress_file,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-failures", action="store_true", help="Save failed world and durable sidecars before fixing or resetting (reliability mode).")
    parser.add_argument("--rcon-port", type=int, default=27017)
    parser.add_argument("--game-port", type=int, default=34199)
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
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--variant", default="xhigh")
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--python", type=Path, default=REPO_ROOT / ".venv/bin/python")
    parser.add_argument("--campaign-manager", type=Path, default=REPO_ROOT / "scripts/manage_linux_deterministic_campaign.sh")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:9137/api/logistic-inventory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-active-run", action="store_true", help="Attach an observer to an already-running isolated episode without resetting it.")
    args = parser.parse_args(argv)
    try:
        return run_campaign(_config(args))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"opencode campaign orchestrator: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
