"""Automate isolated deterministic Factorio runs with a per-run OpenCode observer.

The controller starts fresh episodes, records read-only two-minute evidence,
then asks that run's OpenCode session for one code-correlated focused change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
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


@dataclass
class State:
    completed_cycles: int = 0
    terminal_keys: list[str] = field(default_factory=list)
    active_cycle: int | None = None
    active_session_id: str | None = None


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


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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
    return [
        str(config.campaign_manager), "fresh", "--source-save", str(config.source_save),
        "--root", str(config.state_root), "--python", str(config.python), "--technology", config.technology,
    ]


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
    latest = contents.rfind(RUN_START)
    if latest < 0:
        return False, ""
    current = contents[latest:]
    return _RUN_END_LINE.search(current) is not None, current[-12000:]


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
    result = _run(_opencode_command(config, message, session_id), timeout=1800)
    output = result.stdout + result.stderr
    config.opencode_log_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(config.opencode_log_dir / f"opencode-{sequence:04d}.jsonl", output)
    if result.returncode:
        raise RuntimeError(f"OpenCode exited {result.returncode}; inspect opencode-{sequence:04d}.jsonl")
    return session_id or _session_id(output), output


def _initial_prompt(config: Config, cycle: int) -> str:
    return f"""You are the accountable investigator and implementer for fresh deterministic Factorio cycle {cycle}, using one session for this run only.
The parent controller already started the isolated run for `{config.technology}` and will send this same session a
durable checkpoint every {config.interval_seconds} seconds. Work in `{REPO_ROOT}`; preserve unrelated dirty files.
The sole live scope is `{config.state_root}` on `nauvis` / `player`.

Read AGENTS.md, docs/system_invariants.md, docs/factorio_operations.md, docs/deterministic/README.md,
docs/deterministic/planning.md, docs/deterministic/opencode_campaign_prompt.md, git status, and the prior run summaries in
`{config.observations}`. Read `docs/deterministic/run_context.md` for evidence scope. For every checkpoint, read only its new section and append a compact assessment below it.

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
When the cause and reusable fix are supported, implement, test and commit without asking the user to approve
routine repository work. An analysis-only handoff is not completion when a justified fix is available.
The parent process controls lifecycle and the next run; it is not a human approval gate. Reply briefly after setup."""


def _checkpoint_prompt(config: Config, cycle: int, checkpoint: int) -> str:
    return f"""Cycle {cycle}, checkpoint {checkpoint} is appended to `{config.observations}`. Read the new section and the referenced rolling context packet and append its assessment below it (at most 1200 characters): timestamped facts and deltas first, then explicitly labeled hypotheses. Keep the whole blocked
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
supported correction, run focused tests, address test failures, review the diff, and create one scoped commit.
Do not end with "want me to implement?", "parent-owned fix", or an analysis-only handoff when you have a supported
fix within repository scope. Report status: change only after implementation and verification. Stop or no-change
is appropriate only for a concrete unresolved evidence gap, failed verification, exhausted budget, mission
completion, or authority outside scope; explain the blocker and investigation already attempted. Never invent a
fix merely to continue. Respect the controller's existing repeated-failure and runtime guards. Record exact provenance in the journal entry (one entry
per episode): episode ID, commit, dirty-patch hash, save/configuration/profile, tests, and activated code. End exactly with:

CAMPAIGN_DECISION:
status: change|no-change|stop
files: comma-separated paths or none
test: command/result or not-run
reason: one sentence
"""


def _telemetry_prompt(config: Config, cycle: int) -> str:
    return f"""Cycle {cycle}'s final comparison declined a behavior change. A follow-up observability patch is allowed
only to resolve one specifically named evidence gap from that comparison: implement the minimal zero-behavior
telemetry that captures it, with a narrow regression test, and state what each possible reading would mean for the
competing explanations. Do not alter planning behavior, start/reset/redeploy Factorio, or touch unrelated files.
Never manufacture telemetry just to unlock another run: if no evidence gap names telemetry as its resolution, make
no change and say so. If implemented, verify and commit the telemetry patch yourself. Append the outcome to `{config.observations}`. End exactly with:

CAMPAIGN_DECISION:
status: change|no-change|stop
files: comma-separated paths or none
test: command/result or not-run
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


def _decision(output: str) -> str:
    # The session transcript also echoes the prompt template
    # ("status: change|no-change|stop"), so the first match is not the
    # verdict. Take the last match with a real status instead.
    best = "no-change"
    for match in DECISION.finditer(output):
        fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
        status = fields.get("status", "").strip().lower()
        if status in {"change", "no-change", "stop"}:
            best = status
    return best


def run_campaign(config: Config) -> int:
    if config.interval_seconds < 120:
        raise ValueError("--interval-seconds must be at least 120")
    if not config.dry_run and not config.source_save.is_file():
        raise FileNotFoundError(f"source save is missing: {config.source_save}")
    state = _load_state(config.state_file)
    started = time.monotonic()
    sequence = 1
    while config.max_cycles == 0 or state.completed_cycles < config.max_cycles:
        if config.max_runtime_seconds and time.monotonic() - started >= config.max_runtime_seconds:
            _append(config.observations, "## Campaign stop\n\nWall-clock limit reached; no new episode was started.\n")
            return 0
        cycle = state.completed_cycles + 1
        resuming = state.active_cycle == cycle and state.active_session_id is not None
        adopt_active_run = config.resume_active_run and not resuming
        if resuming:
            session_id = state.active_session_id
            _append(
                config.observations,
                f"## Cycle {cycle} — controller resumed ({_now()})\n\n"
                "The persisted OpenCode session will continue this same run; no fresh lifecycle was issued.\n",
            )
            offset = (config.state_root / "logs/autonomous-run.log").stat().st_size if (config.state_root / "logs/autonomous-run.log").exists() else 0
        elif adopt_active_run:
            complete, _ = _latest_run(config.state_root / "logs/autonomous-run.log")
            if complete:
                raise RuntimeError("--resume-active-run found a completed run; refuse to replace its final comparison")
            session_id, _ = _ask(config, _initial_prompt(config, cycle), None, sequence)
            sequence += 1
            if session_id is None:
                raise RuntimeError("OpenCode did not return a session ID; refusing to create an untracked observer session")
            state.active_cycle = cycle
            state.active_session_id = session_id
            _save_state(config.state_file, state)
            _append(
                config.observations,
                f"## Cycle {cycle} — observer attached ({_now()})\n\n"
                "The controller adopted the already-running isolated episode without a reset.\n",
            )
            offset = (config.state_root / "logs/autonomous-run.log").stat().st_size if (config.state_root / "logs/autonomous-run.log").exists() else 0
        else:
            fresh = subprocess.CompletedProcess(_fresh_command(config), 0, "dry run", "") if config.dry_run else _run(_fresh_command(config), timeout=900)
            if fresh.returncode:
                raise RuntimeError(f"fresh lifecycle failed:\n{fresh.stdout}\n{fresh.stderr}")
            _append(config.observations, f"## Cycle {cycle} — fresh lifecycle ({_now()})\n\n```text\n{(fresh.stdout + fresh.stderr).strip()}\n```\n")
            session_id, _ = _ask(config, _initial_prompt(config, cycle), None, sequence)
            sequence += 1
            if session_id is None:
                raise RuntimeError("OpenCode did not return a session ID; refusing to create an untracked observer session")
            state.active_cycle = cycle
            state.active_session_id = session_id
            _save_state(config.state_file, state)
            offset = 0
        checkpoint = 0
        while True:
            checkpoint += 1
            snapshot, offset = _snapshot(config, cycle, checkpoint, offset)
            _append(config.observations, snapshot)
            _, _ = _ask(config, _checkpoint_prompt(config, cycle, checkpoint), session_id, sequence)
            sequence += 1
            complete, run_text = _latest_run(config.state_root / "logs/autonomous-run.log")
            if complete or config.dry_run:
                break
            if config.max_runtime_seconds and time.monotonic() - started >= config.max_runtime_seconds:
                _append(config.observations, "## Campaign stop\n\nWall-clock limit reached during an active run.\n")
                return 0
            time.sleep(config.interval_seconds)
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
        before = _tree_fingerprint(config.observations)
        _, output = _ask(config, _completion_prompt(config, cycle), session_id, sequence)
        sequence += 1
        after = _tree_fingerprint(config.observations)
        state.completed_cycles += 1
        state.terminal_keys = (state.terminal_keys + [_terminal_key(run_text)])[-2:]
        state.active_cycle = None
        state.active_session_id = None
        _save_state(config.state_file, state)
        decision = _decision(output)
        if decision == "no-change" and before == after:
            _, output = _ask(config, _telemetry_prompt(config, cycle), session_id, sequence)
            sequence += 1
            after = _tree_fingerprint(config.observations)
            decision = _decision(output)
        if decision != "change" or before == after:
            _append(config.observations, "## Campaign stop\n\nObserver stopped or no explicit change verdict with a tree change was recorded.\n")
            return 0
        if len(state.terminal_keys) == 2 and state.terminal_keys[0] == state.terminal_keys[1]:
            _append(config.observations, "## Campaign stop\n\nThe same terminal failure occurred twice; a third retry is blocked.\n")
            return 0
    return 0


def _config(args: argparse.Namespace) -> Config:
    root = args.state_root.expanduser().resolve()
    return Config(
        state_root=root, source_save=args.source_save.expanduser().resolve(), observations=args.observations.resolve(),
        state_file=(args.state_file or root / "logs/opencode-campaign-state.json").resolve(),
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
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
