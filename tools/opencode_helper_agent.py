"""Run one read-only OpenCode helper session alongside a deterministic episode."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from tools.run_context import build_context
from tools.run_log_format import is_run_end_line


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path.home() / ".local/share/factorio-rl/opencode_helper"
DEFAULT_REPORT_ROOT = REPO_ROOT / "docs/deterministic/opencode_helper_runs"
DEFAULT_MODEL = "opencode-go/muse-spark-1.3-contributor"


@dataclass(frozen=True)
class Config:
    run_id: str
    log_path: Path
    manifest_path: Path | None
    data_root: Path
    report_root: Path
    dashboard_url: str
    interval_seconds: int
    model: str
    variant: str
    opencode_bin: str


@dataclass
class State:
    session_id: str | None = None
    offset: int = 0
    checkpoints: int = 0
    completed: bool = False


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
        raise ValueError(f"helper state must be an object: {path}")
    return State(
        session_id=str(payload["session_id"]) if payload.get("session_id") else None,
        offset=int(payload.get("offset", 0)),
        checkpoints=int(payload.get("checkpoints", 0)),
        completed=bool(payload.get("completed", False)),
    )


def _save_state(path: Path, state: State) -> None:
    _atomic_write(path, json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")


def _run(command: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout, check=False)


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


def _new_log(path: Path, offset: int) -> tuple[bool, int]:
    """Scan new raw evidence for completion independently of context compaction."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(offset if 0 <= offset <= size else 0)
            contents = handle.read()
    except FileNotFoundError:
        return False, 0
    return any(is_run_end_line(line) for line in contents.splitlines()), size


def _context_path(config: Config) -> Path:
    return config.data_root / "runs" / config.run_id / "context.md"


def _refresh_context(config: Config) -> Path:
    """Keep one bounded evidence packet without embedding it in every prompt."""
    path = _context_path(config)
    _atomic_write(path, build_context(
        config.log_path, inventory_path=config.log_path.parent / "inventory-history.json",
    ))
    return path


def _manifest(path: Path | None) -> str:
    if path is None:
        return "Episode manifest unavailable."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return f"Episode manifest unavailable: {error}"
    if not isinstance(payload, dict):
        return "Episode manifest unavailable: invalid object."
    selected = {key: payload.get(key) for key in (
        "episode_id", "run_id", "lineage_id", "suite_id", "attempt_id",
        "repository_revision", "target_technology", "surface", "force", "started_at",
    )}
    return json.dumps(selected, sort_keys=True)


def _report_path(config: Config) -> Path:
    return config.report_root / config.run_id / "findings.md"


def _state_path(config: Config) -> Path:
    return config.data_root / "runs" / config.run_id / "state.json"


def _transcript_path(config: Config, checkpoint: int) -> Path:
    return config.data_root / "runs" / config.run_id / f"opencode-{checkpoint:04d}.jsonl"


def _attempt_transcript_path(config: Config, checkpoint: int, attempt: int) -> Path:
    suffix = "" if attempt == 0 else f"-retry-{attempt}"
    return config.data_root / "runs" / config.run_id / f"opencode-{checkpoint:04d}{suffix}.jsonl"


def _opencode_command(config: Config, message: str, session_id: str | None) -> list[str]:
    command = [
        config.opencode_bin, "run", "--dir", str(REPO_ROOT), "--format", "json",
        "--model", config.model, "--variant", config.variant, "--auto",
    ]
    if session_id:
        command += ["--session", session_id]
    else:
        command += ["--title", f"Factorio OpenCode Helper {config.run_id}"]
    return command + [message]


def _ask(config: Config, message: str, state: State) -> None:
    """Ask once, then resume the same helper session up to three times."""
    current_message = message
    for attempt in range(4):
        result = _run(_opencode_command(config, current_message, state.session_id), timeout=900)
        output = result.stdout + result.stderr
        transcript = _attempt_transcript_path(config, state.checkpoints, attempt)
        _atomic_write(transcript, output)
        state.session_id = state.session_id or _session_id(output)
        if result.returncode == 0:
            if state.session_id is None:
                raise RuntimeError("OpenCode did not return a session ID; refusing an untracked helper")
            return
        if attempt == 3:
            raise RuntimeError(
                f"OpenCode failed after three continue retries; inspect {transcript}"
            )
        if state.session_id is None:
            current_message = message
        else:
            current_message = (
                "Continue the same read-only helper run. The preceding request failed or timed out; "
                "do not restart observation or create another report. Resume from the existing findings "
                "document and process the newest supplied evidence. Never use a sleep command longer than 90 seconds."
            )


def _append_runner_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"OPENCODE HELPER: {message}\n")


def _initial_prompt(config: Config, report: Path) -> str:
    prior = _previous_report(config, report)
    return f"""You are the permanent read-only OpenCode Helper Agent for one isolated deterministic Factorio run.
Run ID: {config.run_id}. Work in {REPO_ROOT}. The runner will send this same session a new evidence checkpoint every {config.interval_seconds} seconds.

Write the complete run document at {report}. It is the only findings document for this run. At each checkpoint, append only changed milestones, blocked dependencies, or contradictions with timestamped evidence references and a falsifiable next signal. Do not repeat unchanged state. Separate observed facts from hypotheses; unknown is not zero, inventory change is not production rate, and total stock is not transferable supply. Read the rolling evidence packet when requested, its cited log lines, relevant repository code, the manifest, and GET {config.dashboard_url}; do not edit code, commit, deploy, restart, reset, send RCON, or alter the factory.

Continuously compare this run to the previous helper findings document when present: {prior}. Review the recent committed change history before forming a hypothesis:
```text
{_recent_commits()}
```
Do not issue a sleep command longer than 90 seconds.

Run identity: {_manifest(config.manifest_path)}

At RUN END you will receive the final evidence packet path and must wrap this document with a concise final diagnosis, comparison with the prior helper report if one exists, and a structured handoff for a later coding task. Do not implement any fix yourself."""


def _checkpoint_prompt(config: Config, report: Path, checkpoint: int, context: Path) -> str:
    return f"""Checkpoint {checkpoint} for run {config.run_id}. Read the current bounded evidence packet at {context} (it is replaced each checkpoint), then append only meaningful changes to {report}; remain read-only. Do not paste the packet or raw log into findings. Cite source lines for claims, distinguish stock from throughput and transferable supply, and state one falsifiable next signal. Follow cited source lines only when needed to resolve a specific uncertainty. Do not issue a sleep command longer than 90 seconds.

Manifest: {_manifest(config.manifest_path)}"""


def _completion_prompt(config: Config, report: Path, context: Path) -> str:
    previous = _previous_report(config, report)
    return f"""RUN END for {config.run_id}. Read the final bounded evidence packet at {context} and finish {report} now. Include terminal outcome, milestone timeline, blocked dependency, evidence references, hypotheses and competing explanations, and a concise structured coding handoff with a predicted measurable result. Compare to {previous} when it exists; distinguish improved factory behavior from useful diagnostic evidence. Do not paste raw logs or repeat checkpoint history. Remain read-only: no code edits, commits, lifecycle actions, RCON, or factory mutation."""


def _previous_report(config: Config, report: Path) -> Path | None:
    reports = sorted(path for path in config.report_root.glob("*/findings.md") if path != report)
    return reports[-1] if reports else None


def _recent_commits() -> str:
    result = _run(["git", "log", "--oneline", "--decorate", "-8"], timeout=60)
    return (result.stdout + result.stderr).strip() or "Commit history unavailable."


def run_helper(config: Config) -> int:
    if config.interval_seconds < 60:
        raise ValueError("--interval-seconds must be at least 60")
    state_path = _state_path(config)
    state = _load_state(state_path)
    if state.completed:
        return 0
    report = _report_path(config)
    report.parent.mkdir(parents=True, exist_ok=True)
    if not report.exists():
        try:
            report_label = report.relative_to(REPO_ROOT)
        except ValueError:
            report_label = report
        report.write_text(
            f"<!-- Path: {report_label} | Purpose: Read-only OpenCode Helper findings for {config.run_id}. -->\n\n"
            f"# OpenCode Helper findings — {config.run_id}\n\n"
            "The helper is read-only. Checkpoints and the final wrap-up are appended by one OpenCode session.\n",
            encoding="utf-8",
        )
    if state.offset == 0 and config.log_path.exists():
        contents = config.log_path.read_bytes()
        state.offset = max(0, contents.rfind(b"RUN START:"))
    if state.session_id is None:
        state.checkpoints += 1
        _ask(config, _initial_prompt(config, report), state)
        _save_state(state_path, state)
    while True:
        ended, state.offset = _new_log(config.log_path, state.offset)
        context = _refresh_context(config)
        state.checkpoints += 1
        if ended:
            _ask(config, _completion_prompt(config, report, context), state)
            state.completed = True
            _save_state(state_path, state)
            _append_runner_log(config.log_path, f"findings complete {report} (run directory {report.parent})")
            return 0
        _ask(config, _checkpoint_prompt(config, report, state.checkpoints, context), state)
        _save_state(state_path, state)
        time.sleep(config.interval_seconds)


def launch_helper(
    *, run_id: str, log_path: Path, manifest_path: Path | None,
    data_root: Path | None = None, report_root: Path | None = None,
    dashboard_url: str | None = None,
) -> str:
    """Launch the read-only OpenCode observer independently of the runner."""
    root = (data_root or DEFAULT_DATA_ROOT).expanduser()
    output = root / "runs" / run_id / "observer.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "tools.opencode_helper_agent", "--run-id", run_id,
        "--log-path", str(log_path), "--data-root", str(root),
        "--report-root", str((report_root or DEFAULT_REPORT_ROOT).resolve()),
    ]
    if dashboard_url:
        command += ["--dashboard-url", dashboard_url]
    if manifest_path is not None:
        command += ["--episode-manifest", str(manifest_path)]
    if os.environ.get("INVOCATION_ID"):
        unit = f"factorio-rl-opencode-helper-{os.getpid()}-{time.time_ns()}.service"
        subprocess.run([
            "systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
            f"--working-directory={REPO_ROOT}",
            f"--property=StandardOutput=append:{output}",
            f"--property=StandardError=append:{output}", *command,
        ], check=True)
        return f"unit={unit}"
    with output.open("ab") as handle:
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    return f"pid={process.pid}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--episode-manifest", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:9137/api/logistic-inventory")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--variant", default="xhigh")
    parser.add_argument("--opencode-bin", default=shutil.which("opencode") or "opencode")
    args = parser.parse_args(argv)
    config = Config(
        run_id=args.run_id, log_path=args.log_path.resolve(), manifest_path=args.episode_manifest.resolve() if args.episode_manifest else None,
        data_root=args.data_root.expanduser().resolve(), report_root=args.report_root.resolve(), dashboard_url=args.dashboard_url,
        interval_seconds=args.interval_seconds, model=args.model, variant=args.variant, opencode_bin=args.opencode_bin,
    )
    try:
        return run_helper(config)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"opencode helper: {error}", file=sys.stderr)
        try:
            _append_runner_log(config.log_path, f"stopped after OpenCode failure: {error}")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
