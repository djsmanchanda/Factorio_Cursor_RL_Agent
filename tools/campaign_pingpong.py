# Path: tools/campaign_pingpong.py | Purpose: Exact DeepSeek/Claude transports and audit for a resumable isolated Laya campaign.
"""The existing campaign orchestrator owns all lifecycle, verification and Git writes.

This adapter changes only the model handoffs and records the requested campaign
protocol. DeepSeek remains read-only; Claude gets a fresh real session per fix.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

from tools import campaign_protocol as protocol

DEEPSEEK = "opencode-go/deepseek-v4.1-flash"
HAIKU = "claude-haiku-5-5"
BRANCH = "t3/laya-deepseek-haiku-6h"


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def atomic(path: Path, value) -> None:
    from tools.opencode_campaign_orchestrator import _atomic_write
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def event(config, kind: str, **details) -> None:
    path = config.state_root / "agents/handoffs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "kind": kind, **details}) + "\n")


def require_edit_window(config, state) -> None:
    from tools import opencode_campaign_orchestrator as orch
    if time.time() >= datetime.fromisoformat(state.deadline_utc).timestamp():
        raise TimeoutError("original deadline reached: candidate remains unverified; observation only")
    branch = orch._run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != config.campaign_branch:
        raise RuntimeError(f"campaign branch changed: {branch}")
    # A model may never stage files; an existing user index is also protected.
    if orch._run(["git", "diff", "--cached", "--name-only"]).stdout.strip():
        raise RuntimeError("nonempty index outside harness commit phase")


def validate_campaign(config, state) -> None:
    codex = getattr(config, "agent_topology", "legacy") == "deepseek-codex"
    fixer_model, fixer_effort = ("gpt-6.1-sol", "high") if codex else (HAIKU, "xhigh")
    if (config.runtime != "laya" or config.model != DEEPSEEK or config.variant != "high"
            or config.fixer_model != fixer_model or config.fixer_variant != fixer_effort
            or config.observer_model != DEEPSEEK or config.observer_variant != "high"
            or config.campaign_branch != BRANCH or config.run_speed != 2
            or config.technology != "mining-productivity-4"
            or any((config.until_accepted, config.max_game_ticks, config.run_wall_seconds))):
        raise ValueError("exact DeepSeek/Haiku campaign protocol configuration is required")
    if not state.primary_session_id:
        raise RuntimeError("real saved Hi-bootstrap session required")
    if not state.deadline_utc:
        raise RuntimeError("persist launch+six-hour deadline before starting controller")
    original = read(config.state_root / "campaign.json")
    if state.deadline_utc != original.get("deadline_utc"):
        raise RuntimeError("original absolute deadline changed")
    if state.primary_session_id != original.get("deepseek_session_id"):
        raise RuntimeError("saved DeepSeek session drift")
    if codex:
        from tools.campaign_codex import root_identity
        root_identity(config)
        launch = datetime.fromisoformat(original["launch_utc"]).timestamp()
        if datetime.fromisoformat(state.deadline_utc).timestamp() != launch + 21600 or datetime.fromisoformat(original["wrap_deadline_utc"]).timestamp() != launch + 25200:
            raise RuntimeError("six-hour deadline or one-hour overrun drift")
    from tools import opencode_campaign_orchestrator as orch
    branch = orch._run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != config.campaign_branch:
        raise RuntimeError("wrong campaign branch")


def haiku_command(config, message: str, session: str | None, settings: Path) -> list[str]:
    command = [config.claude_bin, "-p", message, "--model", HAIKU, "--effort", "xhigh",
               "--output-format", "stream-json", "--verbose", "--no-chrome", "--strict-mcp-config",
               "--setting-sources", "", "--settings", str(settings), "--permission-mode", "dontAsk",
               "--tools", "Bash,Read,Glob,Grep,Edit,Write", "--disallowedTools", "Agent,Task,WebFetch,WebSearch"]
    if session:
        command += ["--resume", session]
    return command


def ask_haiku(config, message: str, session: str | None) -> tuple[str, str]:
    from tools import opencode_campaign_orchestrator as orch
    state = orch._load_state(config.state_file)
    require_edit_window(config, state)
    if state.fixer_cycle != state.active_cycle:
        raise RuntimeError("fresh fix session cycle was not allocated")
    cycle_dir = config.state_root / "agents" / f"haiku-cycle-{state.active_cycle:04d}"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    attempt = len(list(cycle_dir.glob("turn-*.jsonl"))) + 1
    transport = cycle_dir / f"turn-{attempt:03d}.jsonl"
    intent = cycle_dir / "active.json"
    if read(intent).get("status") == "running":
        prior_pid = read(intent).get("pid")
        if prior_pid and process_alive(prior_pid):
            raise RuntimeError("existing Haiku transport still active; do not duplicate")
        if not session:
            raise RuntimeError("interrupted Haiku has no returned session; preserve candidate and diagnose")
    worktree = config.campaign_worktree
    settings = cycle_dir / "settings.json"
    allowed = [f"Edit(/{worktree}/**)", f"Write(/{worktree}/**)", "Read", "Glob", "Grep"]
    allowed += [f"Bash({config.python} -m pytest *)", "Bash(rg *)", "Bash(cat *)", "Bash(sed *)",
                "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)",
                "Bash(git ls-files*)", "Bash(git rev-parse*)"]
    atomic(settings, {"permissions": {"allow": allowed,
        "deny": [f"Edit(/{worktree}/.git/**)", f"Write(/{worktree}/.git/**)",
                 f"Edit(/{config.state_root}/**)", f"Write(/{config.state_root}/**)",
                 "Bash(git add*)", "Bash(git commit*)", "Bash(git checkout*)", "Bash(git switch*)",
                 "Bash(git push*)", "Bash(git merge*)", "Bash(git reset*)", "Bash(systemctl *)",
                 "Bash(systemd-run *)", "Bash(*rcon*)", "Bash(*manage_linux*)", "Bash(*run_laya*)"]}})
    handoff = f"""You are the fresh Claude Haiku5.5/xhigh IMPLEMENTER for cycle {state.active_cycle}.
Saved DeepSeek session ID: {state.primary_session_id}. All observations/plans/completion packets address it.
Campaign branch: {config.campaign_branch}; worktree: {worktree}; evidence: {config.state_root}.
Follow the saved DeepSeek findings/plan, do not become campaign planner. Read AGENTS.md and isolation docs.
This same branch holds every fix. NEVER create/switch branches, stage/commit/push/merge/adopt or operate
Factorio/lifecycle/RCON. Do not access Chrome, spawn other models, weaken tests or expand allowlists.
Preserve unrelated files. Edits only source/tests/docs for the handoff. No editing frozen data/episodes.
Every behavior change needs a declared focused regression test failing before and passing after.
Run {config.python} -m pytest tests -m fast -k "not test_runner_records_controller" -q --tb=short.
Only independently confirmed baseline failure is the fixed import-firewall node; preserve its content.
Original deadline {state.deadline_utc}; stop editing by it. Harness owns proof/gate/review/scoped commit/freeze.
Completion MUST return changes, tests, predictions, unresolved issues, verification status and this block:
CAMPAIGN_DECISION:
status: change|no-change|stop
kind: behavior|instrumentation|no-change
files: comma-separated repository-relative paths (all changed paths)
test: actual commands/results
regression: tests/test_file.py::test_name (comma-separated if multiple behaviors)
prediction: falsifiable next-run effect
unresolved: issues or none
verification: actual status
reason: one sentence
Your completion packet is sent verbatim by the harness to the SAVED DeepSeek session {state.primary_session_id}.
The harness adds the resulting commit and next run identity later. Do not commit or update CURRENT_STATUS yourself.

{message}"""
    (cycle_dir / f"handoff-{attempt:03d}.md").write_text(handoff)
    command = haiku_command(config, handoff, session, settings)
    atomic(cycle_dir / f"command-{attempt:03d}.json", command)
    atomic(intent, {"status": "running", "session_id": session, "transport": str(transport)})
    remaining = datetime.fromisoformat(state.deadline_utc).timestamp() - time.time()
    end = time.monotonic() + min(config.model_timeout_seconds, remaining)
    process = subprocess.Popen(command, cwd=worktree, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True, bufsize=1)
    atomic(intent, {"status": "running", "pid": process.pid, "session_id": session,
                    "transport": str(transport)})
    result, actual_session = None, session
    with transport.open("w") as output:
        try:
            while True:
                if time.monotonic() >= end:
                    raise TimeoutError("Haiku transport timeout; preserve unverified candidate")
                ready, _, _ = select.select([process.stdout], [], [], min(1, max(.01, end-time.monotonic())))
                if not ready:
                    continue
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    continue
                output.write(line); output.flush()
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                sid = row.get("session_id")
                if sid:
                    if actual_session and actual_session != sid:
                        raise RuntimeError("Claude session continuity violation within cycle")
                    if actual_session is None:
                        history = read(config.state_root / "agents/haiku-sessions.json")
                        if sid in history.values():
                            raise RuntimeError("Claude reused a previous cycle session")
                        history[str(state.active_cycle)] = sid
                        atomic(config.state_root / "agents/haiku-sessions.json", history)
                        actual_session = sid
                        state.fixer_session_id = sid
                        orch._save_state(config.state_file, state)
                        event(config, "haiku-session", cycle=state.active_cycle, session_id=sid,
                              deepseek_session_id=state.primary_session_id, model=HAIKU, effort="xhigh")
                    atomic(intent, {"status": "running", "pid": process.pid, "session_id": sid,
                                    "transport": str(transport)})
                if row.get("type") == "system" and row.get("subtype") == "init" and row.get("model") != HAIKU:
                    raise RuntimeError("Claude model substitution")
                if row.get("type") == "result":
                    result = row
            code = process.wait()
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            atomic(intent, {"status": "interrupted", "session_id": actual_session, "transport": str(transport)})
            raise
    if code or not result or result.get("is_error") or not actual_session:
        raise RuntimeError(f"Haiku incomplete/failed; inspect {transport}")
    usage = result.get("modelUsage") or {}
    if set(usage) != {HAIKU} or usage[HAIKU].get("provider") != "firstParty":
        raise RuntimeError("Claude transport used an unexpected model/provider")
    # The installed Claude transcript records actual effort; metadata labels do not suffice.
    project = "-" + str(worktree.resolve()).strip("/").replace("/", "-").replace("_", "-")
    native = Path.home() / ".claude/projects" / project / f"{actual_session}.jsonl"
    rows = [json.loads(line) for line in native.read_text().splitlines()]
    assistant = [r for r in rows if r.get("type") == "assistant"]
    if not assistant or any(r.get("effort") != "xhigh" or r.get("requestedModel") != HAIKU for r in assistant):
        raise RuntimeError("Claude transcript did not verify requested model and xhigh effort")
    atomic(cycle_dir / f"identity-{attempt:03d}.json", {"session_id": actual_session, "model": HAIKU,
           "provider": "firstParty", "effort": "xhigh", "native_transcript": str(native)})
    # Keep complete native transcripts alongside CLI events for independent audit.
    (cycle_dir / "native-transcript.jsonl").write_text(native.read_text())
    packet = result.get("result", "")
    if not packet:
        raise RuntimeError("Haiku returned no completion packet")
    atomic(intent, {"status": "complete", "session_id": actual_session, "transport": str(transport)})
    return actual_session, json.dumps({"type": "text", "part": {"text": packet}})


def relay_completion(config, state, fix_id: str, output: str) -> None:
    from tools import opencode_campaign_orchestrator as orch
    packet = protocol.assistant_text(output)
    digest = hashlib.sha256(packet.encode()).hexdigest()
    record = config.state_root / "agents" / f"completion-{digest}.json"
    if read(record).get("delivered"):
        return
    record.parent.mkdir(parents=True, exist_ok=True)
    packet_path = record.with_suffix(".md")
    packet_path.write_text(packet)
    atomic(record, {"delivered": False, "fix_id": fix_id, "packet": str(packet_path),
                   "from": state.fixer_session_id, "to": state.primary_session_id})
    # The packet bytes between markers are unaltered. Additional commit/run facts arrive separately.
    message = (f"Implementer completion packet for {fix_id}, from session {state.fixer_session_id}. "
               "Verbatim relay to your saved session; review follows.\n\n" + packet)
    response = orch._primary_turn(config, state, message, 700000 + int(time.time()))
    if not protocol.assistant_text(response):
        raise RuntimeError("completion delivery has no DeepSeek acknowledgement")
    atomic(record, {**read(record), "delivered": True, "acknowledgement": protocol.assistant_text(response)})
    event(config, "completion-delivered", fix_id=fix_id, from_session=state.fixer_session_id,
          to_session=state.primary_session_id, packet=str(packet_path), sha256=digest)


def notify_run(config, state) -> None:
    from tools import opencode_campaign_orchestrator as orch
    record = Path(config.code_root).parent / "deepseek-run-notification.json"
    if read(record).get("delivered"):
        return
    commit = read(Path(config.code_root).parent / "freeze.json").get("revision")
    message = (f"NEW RUN {state.active_episode_id}, cycle {state.active_cycle}, commit {commit}. "
               "This is a lifecycle notification only. Do not inspect gameplay evidence, "
               "observe, edit or fix while the run is active. "
               f"Original deadline {state.deadline_utc}. Final validation={state.final_validation_started}. "
               "Acknowledge briefly; terminal evidence/plan request follows after natural end.")
    # Lifecycle acknowledgement is allowed after D; gameplay review waits for RUN END.
    saved_deadline = orch._COMMAND_DEADLINE
    orch._COMMAND_DEADLINE = None
    try:
        response = orch._primary_turn(config, state, message, 800000 + int(time.time()))
    finally:
        orch._COMMAND_DEADLINE = saved_deadline
    if not protocol.assistant_text(response):
        raise RuntimeError("DeepSeek did not acknowledge run notification")
    atomic(record, {"delivered": True, "session_id": state.primary_session_id,
                   "commit": commit, "run_id": state.active_episode_id, "response": protocol.assistant_text(response)})
    event(config, "run-notification", session_id=state.primary_session_id,
          run_id=state.active_episode_id, commit=commit)


def observe_terminal(config, episode: str) -> dict:
    from tools import opencode_campaign_orchestrator as orch
    from tools.campaign_runtime import comparator_records
    directory = Path(config.code_root).parent / "observer"
    directory.mkdir(exist_ok=True)
    record = directory / "state.json"
    if read(record).get("status") == "complete":
        return read(record)
    state = orch._load_state(config.state_file)
    # Wait only for evidence finalization; never cut off the natural episode itself.
    index = Path(config.world_root).parent / "laya-runs.jsonl"
    until = time.monotonic() + config.helper_wait_seconds
    while not any(row.get("run_id") == episode and row.get("summary") for row in comparator_records(config.world_root)):
        if time.monotonic() >= until:
            raise RuntimeError(f"terminal run has no durable index row: {index}")
        time.sleep(1)
    current = comparator_records(config.world_root)
    previous = []
    for freeze in sorted((config.state_root / "episodes").glob("*/freeze.json")):
        world = Path(read(freeze)["world_root"])
        if world != config.world_root:
            previous.extend(comparator_records(world))
    evidence = directory / "comparators.json"
    atomic(evidence, {"current": current, "previous": previous, "external": [
        row for root in config.comparator_roots for row in comparator_records(root)]})
    observation_only = time.time() >= datetime.fromisoformat(state.deadline_utc).timestamp() or state.final_validation_started
    report = directory / "findings.md"
    implementer = "owning GPT-6.1-Sol/high T3 thread" if getattr(config, "agent_topology", "legacy") == "deepseek-codex" else "fresh Haiku"
    prompt = f"""TERMINAL RUN {episode}. You are the SAME persistent observer/planner {state.primary_session_id}.
Collect bounded evidence from {evidence}, {config.world_root}/evidence/latest-status.json,
{config.world_root}/evidence/clean-decisions.jsonl, {config.world_root}/logs/laya-runner.log and freeze.json.
Compare preceding run and previous change: {config.state_root}/logs/campaign-loop; handoffs {config.state_root}/agents/handoffs.jsonl.
Trace observation -> decision -> plan -> execution -> outcome. Cite paths/decision indices/ticks.
Separate real accepted checkpoints from proxies; actual research target/stop, requested/observed speed, wall time/ticks.
Summarize unchanged/worsened outcomes, confounders and unresolved issues. Evidence level single live run; no cohort claim.
The harness stops a run after 30 wall minutes without demonstrable useful progress; diagnose what prevented new work.
Busy retries, no-op research calls and stock oscillation are not progress. No manual chest or extra starter chest is authorized.
Observation-only/final run={observation_only}. {'Record final findings only; NO subsequent fix or implementation plan.' if observation_only else 'Choose 1–2 focused reusable changes and prepare concrete ' + implementer + ' handoff: files, implementation plan, regressions failing before/passing after, predicted next-run effects and prior change outcome.'}
Return a complete report, ending with this EXACT plain block, without Markdown heading/bullets/bold/code fence:
SYNTHESIS:
hypothesis: one sentence
confidence: low|medium|high
supporting: cited evidence
contradicting: evidence or none
competing: alternate mechanism
discriminating: test or observation
Read only. Do not edit, write reports yourself or delegate; harness publishes returned text."""
    (directory / "request.md").write_text(prompt)
    atomic(record, {"status": "running", "session_id": state.primary_session_id, "episode": episode})
    saved_deadline = orch._COMMAND_DEADLINE
    orch._COMMAND_DEADLINE = None
    try:
        output = orch._primary_turn(config, state, prompt, 900000 + int(time.time()))
    finally:
        orch._COMMAND_DEADLINE = saved_deadline
    text = protocol.assistant_text(output)
    # Preserve substantive prose before checking its machine envelope. A model
    # may render the requested block as a Markdown heading and bold bullets;
    # request only the envelope in the same session, never repeat the audit.
    (directory / "returned-report.md").write_text(text)
    required = {"hypothesis", "confidence", "supporting", "contradicting", "competing", "discriminating"}
    fields = protocol.marker_fields(output, "SYNTHESIS")
    if len(text) >= 300 and not all(fields.get(key) for key in required):
        saved_deadline = orch._COMMAND_DEADLINE
        orch._COMMAND_DEADLINE = None
        try:
            envelope = orch._primary_turn(config, state,
                "Your substantive terminal report is preserved. Do not re-investigate or repeat it. "
                "Return ONLY this exact plain machine block, with one nonempty value per line, "
                "no Markdown heading/bullets/bold/code fence: SYNTHESIS:\nhypothesis: ...\n"
                "confidence: low|medium|high\nsupporting: cited evidence\ncontradicting: ...\n"
                "competing: ...\ndiscriminating: ...", 950000 + int(time.time()))
        finally:
            orch._COMMAND_DEADLINE = saved_deadline
        (directory / "completion-envelope.jsonl").write_text(envelope)
        fields = protocol.marker_fields(envelope, "SYNTHESIS")
        text += "\n\n" + protocol.assistant_text(envelope)
    if len(protocol.assistant_text(output)) < 300 or not all(fields.get(key) for key in required):
        raise RuntimeError("DeepSeek returned incomplete terminal report after bounded envelope correction")
    report.write_text(text)
    result = {"episode": episode, "status": "complete", "path": str(report),
              "session_id": state.primary_session_id, "observation_only": observation_only,
              "reason": "persistent DeepSeek terminal observations complete"}
    atomic(record, result)
    event(config, "terminal-observation", run_id=episode, session_id=state.primary_session_id,
          report=str(report), observation_only=observation_only)
    return result


def process_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.split(") ", 1)[1].split()[0] != "Z"
    except (ProcessLookupError, FileNotFoundError):
        return False


def detect_runner_failure(config) -> None:
    lifecycle = read(Path(config.code_root).parent / "lifecycle.json")
    pid = lifecycle.get("pid")
    if pid and lifecycle.get("phase") in {"running", "launching"} and not process_alive(pid):
        raise RuntimeError(f"owned runner {pid} died without natural RUN END; preserve episode {config.episode_id}")


def export_planner(config, session_id: str) -> bool:
    """Give OpenCode a regular output file; its pipe export can truncate at 64 KiB."""
    target = config.state_root / "agents/deepseek-session-export.json"
    temporary = target.with_suffix(".exporting")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("w") as output:
            result = subprocess.run([config.opencode_bin, "export", session_id, "--sanitize"],
                                    cwd=config.campaign_worktree, stdout=output,
                                    stderr=subprocess.PIPE, text=True, timeout=60)
        if result.returncode:
            raise ValueError(f"export returncode={result.returncode}")
        payload = json.loads(temporary.read_text())
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise ValueError("invalid session export shape")
        temporary.replace(target)
        return True
    except (subprocess.TimeoutExpired, ValueError) as error:
        if temporary.exists():
            temporary.rename(temporary.with_name(f"failed-export-{time.time_ns()}.json"))
        event(config, "session-export-failed", session_id=session_id, error=str(error))
        return False


def final_report(config, state, *, failure: str | None = None) -> Path:
    """Include every allocated episode and transport/harness failure, not only successful cycles."""
    from tools.campaign_runtime import comparator_records
    root = config.state_root
    runs = []
    details = []
    for episode in sorted((root / "episodes").glob("*")):
        freeze = read(episode / "freeze.json")
        lifecycle = read(episode / "lifecycle.json")
        world = Path(freeze["world_root"]) if freeze else None
        records = comparator_records(world) if world else []
        timing = []
        if world and (world / 'logs/laya-runner.log').exists():
            timing = [line for line in (world / 'logs/laya-runner.log').read_text().splitlines()
                      if 'ELAPSED:' in line or line.startswith('RUN START:') or line.endswith(' RUN END')]
        runs.append({"experiment": episode.name, "freeze": freeze, "lifecycle": lifecycle,
                     "runs": records, "timing_evidence": timing,
                     "progress_watchdog": read(episode / 'progress-watchdog.json'),
                     "forced_stop": read(episode / 'forced-stop.json'),
                     "observer": read(episode / "observer/state.json")})
        details.append(f"### {episode.name}\n\nFreeze: [{episode.name}/freeze.json](episodes/{episode.name}/freeze.json). "
                       f"Lifecycle: [record](episodes/{episode.name}/lifecycle.json).\n\n```json\n"
                       + json.dumps({"revision": freeze.get("revision"), "seed": config.seed,
                                     "records": records, "lifecycle": lifecycle,
                                     "timing_evidence": timing,
                                     "progress_watchdog": read(episode / 'progress-watchdog.json'),
                                     "forced_stop": read(episode / 'forced-stop.json')}, indent=2) + "\n```\n")
        findings = episode / "observer/findings.md"
        if findings.exists():
            details.append("DeepSeek observations, bottlenecks, predictions and comparison:\n\n" + findings.read_text() + "\n")
    ledger = root / "logs/campaign-loop"
    logs = []
    for path in sorted(ledger.glob("*")):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
            logs.append(f"[{path.name}]({path.relative_to(root)})")
    handoffs = root / "agents/handoffs.jsonl"
    packets = sorted((root / "agents").glob("completion-*.md"))
    failures = root / "logs/harness-failures.jsonl"
    config_record = read(root / "campaign.json")
    report = root / "final-report.md"
    candidate = state.pending_fix
    if candidate:
        import shutil
        archive = root / "unverified-candidates" / str(candidate.get("fix_id", "unfinished"))
        archive.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=config.campaign_worktree,
                                capture_output=True, text=True, timeout=60)
        (archive / "candidate.diff").write_text(result.stdout)
        atomic(archive / "state.json", candidate)
        for name in candidate.get("declared", []):
            source = config.campaign_worktree / name
            if source.is_file() and source.resolve().is_relative_to(config.campaign_worktree.resolve()):
                target = archive / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    export_planner(config, state.primary_session_id)
    codex = getattr(config, "agent_topology", "legacy") == "deepseek-codex"
    implementer = "owning GPT-6.1-Sol/high T3 thread" if codex else "fresh Haiku"
    body = ("<!-- Path: campaign/final-report.md | Purpose: Audit every attempted run and improvement handoff. -->\n\n"
            f"# DeepSeek / {implementer} six-hour Laya campaign\n\n"
            f"Status: {state.phase}. Final report generated {datetime.now(timezone.utc).isoformat()}. "
            f"Original deadline {state.deadline_utc}. Branch `{config.campaign_branch}`. "
            "No merge, push, adoption or production promotion.\n\n"
            "## Configuration and identities\n\n```json\n" + json.dumps(config_record, indent=2) + "\n```\n\n"
            f"Persistent DeepSeek session `{state.primary_session_id}`; implementer native session `{state.fixer_session_id}`. "
            "[Transport/bootstrap/model evidence](agents/), [all handoffs](agents/handoffs.jsonl).\n\n"
            "Evidence: focused proofs/fast gate are **unit tests**; fake lifecycle tests are **replay/integration**. "
            f"This campaign attempted {len(runs)} allocated episodes, exploratory **single live runs**, "
            "not a matched cohort adoption result. Real checkpoints and proxies are separate in records. "
            "Wall seconds and game ticks are distinct; seeds/start-save hashes are recorded per freeze. "
            "Same save/seed/speed/engine are intended matches; changing code, load, stochastic policy draws and "
            "mod stamps are confounders. Missing values remain unknown.\n\n"
            "## Every attempted episode\n\n" + "\n".join(details) + "\n"
            "## Implementer changes, tests, predictions and commits\n\n" + ", ".join(logs) + "\n\n")
    for packet in packets:
        body += f"### [{packet.name}]({packet.relative_to(root)})\n\n" + packet.read_text() + "\n\n"
    body += ("Verification logs and captured review diffs: [agents](agents/). "
             "[Baseline fast gate](logs/baseline-fast-gate.log). Every accepted candidate requires "
             "focused fail-before/pass-after proof, DeepSeek review, fixed gate and scoped commit. "
             "Predictions without a subsequent run are untested; the final observed run receives no later fix.\n\n"
             "## Harness failures and unfinished candidates\n\n"
             + ("```json\n" + failures.read_text() + "\n```\n" if failures.exists() else "No recorded harness failure.\n")
             + f"\nLast harness error: {failure or 'none'}. Pending candidate (unverified):\n\n```json\n"
             + json.dumps(candidate, indent=2) + "\n```\n\n"
             "## Audit checklist\n\n"
             "- [ ] Original launch/deadline unchanged and all run starts before D.\n"
             "- [ ] One saved real DeepSeek session across every message.\n"
             + ("- [ ] Implementation handoffs address the same owning GPT thread/native session (user replacement of Haiku).\n" if codex else "- [ ] New real Haiku session per cycle; corrections reuse only that cycle.\n")
             + "- [ ] All verbatim completions acknowledged by saved DeepSeek session.\n"
             "- [ ] Every behavior has fail-before/pass-after proof; fixed gate failures confirmed.\n"
             "- [ ] Scoped commits stay on campaign branch and have subsequent frozen validation evidence.\n"
             "- [ ] Every allocated episode/crash/transport failure is counted; real checkpoints separated from proxies.\n"
             "- [ ] Target success means verified completed research; all other outcomes retain exact stop reason.\n"
             "- [ ] Production untouched; no merge/push/adoption; final run has no subsequent fix.\n"
             + ("- [ ] No scheduled check-ins; 30-minute progress watchdog and absolute seven-hour wrap evidence retained.\n\n" if codex else "- [ ] Two-hour supervision notes, transcripts, diffs and unverified candidates retained.\n\n")
             + "Checklist is for independent audit; unchecked entries are not assertions of completed validation.\n")
    report.write_text(body)
    atomic(root / "run-records.json", runs)
    return report


def main(argv=None) -> int:
    from tools import opencode_campaign_orchestrator as orch
    args = orch._parser().parse_args(argv)
    config = orch._config(args)
    if config.agent_topology == "deepseek-codex":
        from tools.campaign_codex import main as codex_main
        return codex_main(config)
    try:
        code = orch.run_campaign(config)
    except (Exception, KeyboardInterrupt) as error:
        state = orch._load_state(config.state_file)
        path = config.state_root / "logs/harness-failures.jsonl"
        with path.open("a") as stream:
            stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "error": str(error),
                                     "phase": state.phase, "episode": state.episode_runtime,
                                     "pending_fix": state.pending_fix}) + "\n")
        deadline = datetime.fromisoformat(state.deadline_utc).timestamp() if state.deadline_utc else 0
        if time.time() >= deadline and not state.active_episode_id:
            state.phase = "DEADLINE_REACHED"
            orch._save_state(config.state_file, state)
            final_report(config, state, failure=str(error))
            return 0
        # systemd retries twice; never reset D, episode identity or model sessions.
        recovery = read(config.state_root / "recovery.json")
        failures = len(path.read_text().splitlines()) - int(recovery.get("failures_before", 0))
        if failures >= 3 or isinstance(error, KeyboardInterrupt):
            state.phase = "HARNESS_FAILURE"
            orch._save_state(config.state_file, state)
            final_report(config, state, failure=str(error))
            return 0
        raise
    state = orch._load_state(config.state_file)
    final_report(config, state)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
