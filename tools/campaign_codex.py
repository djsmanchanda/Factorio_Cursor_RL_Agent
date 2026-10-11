# Path: tools/campaign_codex.py | Purpose: Route DeepSeek plans to the owning GPT thread and bound the authorized six-hour campaign.
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import threading
import time

from tools.campaign_pingpong import atomic, event, read, require_edit_window


class WrapDeadline(BaseException):
    """The final observation reserve began; preserve and publish, never fix."""


def root_identity(config) -> dict:
    campaign = read(config.state_root / "campaign.json")
    thread = campaign["root_thread_id"]
    with sqlite3.connect(f"file:{Path.home() / '.t3/userdata/state.sqlite'}?mode=ro", uri=True) as db:
        row = db.execute("SELECT provider_name,resume_cursor_json,runtime_payload_json FROM provider_session_runtime WHERE thread_id=?", (thread,)).fetchone()
        selection = db.execute("SELECT model_selection_json,worktree_path FROM projection_threads WHERE thread_id=?", (thread,)).fetchone()
    if not row or not selection or row[0] != "codex":
        raise RuntimeError("Owning T3 thread is not using the Codex provider")
    chosen = json.loads(selection[0])
    runtime = json.loads(row[2])
    options = {item["id"]: item["value"] for item in chosen.get("options", [])}
    if chosen.get("model") != "gpt-6.1-sol" or options.get("reasoningEffort") != "high" or runtime.get("model") != "gpt-6.1-sol":
        raise RuntimeError("Owning GPT model/effort drift")
    if Path(selection[1]).resolve() != config.campaign_worktree.resolve():
        raise RuntimeError("Owning GPT worktree drift")
    session = json.loads(row[1])["threadId"]
    if session != campaign["codex_session_id"]:
        raise RuntimeError("Owning Codex native session drift; do not substitute another agent")
    native_files = list((Path.home() / '.codex/sessions').glob(f'*/*/*/*{session}*.jsonl'))
    if not native_files:
        raise RuntimeError("No real owning Codex transcript")
    native = max(native_files, key=lambda path: path.stat().st_mtime)
    context = None
    for line in native.open():
        entry = json.loads(line)
        if entry.get("type") == "turn_context":
            context = entry["payload"]
    if not context or context.get("model") != "gpt-6.1-sol" or context.get("effort") != "high":
        raise RuntimeError("Native Codex transcript did not accept the exact model/effort")
    return {"thread_id": thread, "codex_session_id": session, "model": chosen["model"],
            "effort": options["reasoningEffort"], "native_selection": chosen,
            "native_transcript": str(native), "native_turn_context": context,
            "active_turn_id": runtime.get("activeTurnId")}


def send_root(config, message_path: Path, intent_path: Path) -> dict:
    campaign = read(config.state_root / "campaign.json")
    selection = config.state_root / "agents/root-model-selection.json"
    if not selection.exists():
        atomic(selection, root_identity(config)["native_selection"])
    command = [campaign["node_bin"], str(Path(__file__).with_name("campaign_t3_native.cjs")),
               str(intent_path), str(message_path), campaign["root_thread_id"], str(selection)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"T3 handoff failed: {result.stderr[:500]}")
    return read(intent_path)


def ask_root(config, message: str, session: str | None) -> tuple[str, str]:
    from tools import opencode_campaign_orchestrator as orch
    state = orch._load_state(config.state_file)
    require_edit_window(config, state)
    identity = root_identity(config)
    if session and session != identity["codex_session_id"]:
        raise RuntimeError("Implementation session drift")
    directory = config.state_root / "agents" / f"codex-cycle-{state.active_cycle:04d}"
    directory.mkdir(parents=True, exist_ok=True)
    active = directory / "active.json"
    previous = read(active)
    # Resume the request that already owns a candidate, including a completion
    # written immediately before the controller lost its own state write.
    reuse = previous and (previous.get("status") == "waiting" or
                          ((state.pending_fix or {}).get("phase") == "fixing" and message == "continue"))
    if reuse:
        packet_path = Path(previous["packet_path"])
        request_path = Path(previous["request_path"])
        intent_path = Path(previous["intent_path"])
    else:
        attempt = len(list(directory.glob("handoff-*.md"))) + 1
        request_path = directory / f"handoff-{attempt:03d}.md"
        packet_path = directory / f"completion-{attempt:03d}.json"
        intent_path = directory / f"native-delivery-{attempt:03d}.json"
        request = f"""AUTONOMOUS LAYA IMPLEMENTATION REQUEST — user authorized GPT to replace Haiku.
You are the owning GPT-6.1-Sol/high thread, not a new helper. DeepSeek is the planner.
Campaign: {config.state_root}; cycle {state.active_cycle}; DeepSeek {state.primary_session_id}.
Branch {config.campaign_branch}; writable worktree {config.campaign_worktree}.
Absolute edit/run deadline {state.deadline_utc}. Check campaign.json before every edit and stop at D.
Follow AGENTS.md, isolation and campaign docs. Implement only DeepSeek's focused plan below.
The saved DeepSeek plan owns this cycle. Investigate to implement and verify it; return contrary evidence to DeepSeek.
No per-cycle approval or check-in is required. Do not operate live Factorio or edit frozen episodes.
Do not stage, commit, push, merge or switch branches: the owning harness performs verified scoped Git writes.
Preserve unrelated files; declare every changed file. Do not weaken tests or expand baseline allowlists.
The harness appends CURRENT_STATUS.md after verification; do not edit it yourself.
Each behavior must have a focused test failing on pre-change HEAD and passing after. Run the prescribed gate:
{config.python} -m pytest tests -m fast -k "not test_runner_records_controller" -q --tb=short.
Only the independently confirmed unchanged four-import firewall baseline may remain failing.
Read this full request and cited findings. On completion atomically write JSON at {packet_path}:
{{"thread_id":"{identity['thread_id']}","codex_session_id":"{identity['codex_session_id']}",
"cycle":{state.active_cycle},"packet":"YOUR VERBATIM COMPLETION TEXT"}}.
The packet includes changes, tests, predictions, unresolved issues, verification status and EXACT plain block:
CAMPAIGN_DECISION:
status: change|no-change|stop
kind: behavior|instrumentation|no-change
files: comma-separated repository-relative paths, or none
test: actual commands/results
regression: tests/test_file.py::test_name (comma-separated for multiple behaviors)
prediction: falsifiable next-run effect
unresolved: issues or none
verification: actual status
reason: one sentence
Harness relays your packet verbatim to the SAVED DeepSeek session, adds commit/run IDs and observes next run.
For a blocked/unfinished candidate return stop and preserve evidence, without claiming it verified.

{message}
"""
        request_path.write_text(request)
        atomic(active, {"status": "waiting", "packet_path": str(packet_path),
                        "request_path": str(request_path), "intent_path": str(intent_path), **identity})
    delivery = send_root(config, request_path, intent_path)
    event(config, "codex-handoff", cycle=state.active_cycle, deepseek_session_id=state.primary_session_id,
          request=str(request_path), **identity, message_id=delivery.get("messageId"))
    deadline = datetime.fromisoformat(state.deadline_utc).timestamp()
    while not packet_path.exists():
        if time.time() >= deadline:
            raise TimeoutError("D reached waiting for root implementation; retain unfinished candidate")
        time.sleep(min(2, max(.01, deadline - time.time())))
    packet = read(packet_path)
    if packet.get("thread_id") != identity["thread_id"] or packet.get("codex_session_id") != identity["codex_session_id"] or packet.get("cycle") != state.active_cycle or not packet.get("packet"):
        raise RuntimeError("Root completion identity/content mismatch")
    require_edit_window(config, orch._load_state(config.state_file))
    confirmed = root_identity(config)
    atomic(request_path.with_suffix('.identity.json'), confirmed)
    delivered_at = delivery.get("deliveredAt")
    if delivered_at and confirmed.get("native_transcript"):
        start = datetime.fromisoformat(delivered_at.replace('Z', '+00:00')).timestamp()
        with Path(confirmed["native_transcript"]).open() as source, request_path.with_suffix('.native.jsonl').open('w') as target:
            for line in source:
                entry = json.loads(line)
                stamp = entry.get("timestamp")
                if stamp and datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() >= start:
                    target.write(line)
    atomic(active, {**read(active), "status": "complete", "completed_at": time.time()})
    return identity["codex_session_id"], json.dumps({"type": "text", "part": {"text": packet["packet"]}})


def observe_checkpoint(config, state, checkpoint):
    from tools import opencode_campaign_orchestrator as orch
    path = config.state_root / "agents" / f"live-{state.active_cycle:04d}-{checkpoint:04d}.md"
    if path.exists():
        return
    saved = orch._COMMAND_DEADLINE
    orch._COMMAND_DEADLINE = None
    try:
        output = orch._primary_turn(config, state,
            f"Observe SAME run {state.active_episode_id}, bounded {config.state_root}/logs/latest-context.md "
            f"and {Path(config.code_root).parent}/progress-watchdog.json. No edits or live changes. "
            "30 wall minutes without demonstrable useful progress stops the run automatically, independently of your calls. "
            "Distinguish productive work from busy retries and surplus stock. If stopped, diagnose the blocker for your terminal "
            "handoff to the owning GPT-6.1-Sol/high thread; otherwise summarize new evidence briefly. "
            f"At {state.deadline_utc} observation only; no further fix.", 600000 + int(time.time()))
    finally:
        orch._COMMAND_DEADLINE = saved
    from tools.campaign_protocol import assistant_text
    path.write_text(assistant_text(output))
    event(config, "live-observation", run_id=state.active_episode_id,
          session_id=state.primary_session_id, findings=str(path))


def progress_sample(previous: dict, signature: dict, *, now: float, launched_at: float) -> dict:
    """Count demonstrated productive advances, not stock/research-queue churn.

    High-water evidence survives restarts. Falling then replenishing the same
    stock, a successful no-op and changed observation timestamps buy no time.
    """
    last = previous.get("last_progress_at", launched_at)
    useful, live = signature.get("useful", {}), signature.get("live", {})
    counters = {"machines": live.get("machines", 0)}
    for source in (useful.get("crafted", {}), live.get("crafted", {})):
        counters.update({"crafted:" + item: count for item, count in source.items()})
    counters.update({"delivered:" + item: count for item, count in useful.get("delivered", {}).items()})
    research = f"research:{live.get('research', '')}:{live.get('level', 0)}"
    counters[research] = live.get("research_progress", 0)
    counters["target_completed"] = int(bool(live.get("completed")))
    for project, reserved in useful.get("funded") or ():
        counters["funded:" + str(project)] = reserved
    for loan in useful.get("loans") or ():
        if len(loan) > 4 and isinstance(loan[4], (int, float)):
            counters["loan:" + repr(loan[:4])] = loan[4]
    frontier = previous.get("frontier", counters)
    before = previous.get("signature") or {}
    progressed = any(value > frontier.get(key, 0) for key, value in counters.items())
    if previous and live.get("ghosts", 0) < (before.get("live") or {}).get("ghosts", 0):
        progressed = True
    if previous and progressed:
        last = now
    frontier = {key: max(frontier.get(key, 0), counters.get(key, 0)) for key in frontier.keys() | counters.keys()}
    return {"signature": signature, "frontier": frontier, "last_progress_at": last, "sample_at": now,
            "idle_wall_seconds": max(0, now - last)}


def live_progress(config, world: Path, status: dict) -> dict:
    """Read private-world counters even while a blocking operation holds the runner."""
    from tools.rcon_client import RconClient
    manifest = read(world / "manifest.json")
    if manifest.get("rcon_port") != config.rcon_port or manifest.get("game_port") != config.game_port or config.rcon_port == 27017 or config.game_port == 34199:
        raise RuntimeError("Watchdog runtime identity mismatch")
    useful = (status.get("progress") or {}).get("useful") or {}
    items = sorted(set(useful.get("crafted", {})) | set(useful.get("delivered", {})))
    literal = "{" + ",".join("'" + name + "'" for name in items if name.replace('-', '').isalnum()) + "}"
    lua = ("/sc local s=game.surfaces.nauvis;local f=game.forces.player;local p=f.get_item_production_statistics(s);"
           "local made={};for _,n in ipairs(" + literal + ") do made[n]=p.get_input_count(n) end;"
           "local r=f.current_research;rcon.print(helpers.table_to_json({crafted=made,"
           "machines=s.count_entities_filtered{force=f,type={'assembling-machine','furnace','mining-drill','lab','chemical-plant','oil-refinery'}},"
           "ghosts=s.count_entities_filtered{force=f,type='entity-ghost'},research=r and r.name or '',"
           "level=r and r.level or 0,research_progress=f.research_progress}))")
    client = RconClient("127.0.0.1", config.rcon_port, (world / "server/rcon-password").read_text().strip(), timeout=5)
    try:
        probe = json.loads(client.command(lua))
    finally:
        client.close()
    return {"useful": useful, "live": probe}


def stop_owned_runner(config, episode_root: Path, world: Path, run_id: str, reason: str) -> None:
    lifecycle = read(episode_root / "lifecycle.json")
    pid = lifecycle.get("pid")
    if not pid or int(pid) == 1502810:
        raise RuntimeError("No safe owned runner identity")
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b'\0')
    except FileNotFoundError:
        return
    if str(world).encode() not in command or not any(b"run_laya_clean.py" in token for token in command):
        raise RuntimeError("Refusing to stop a process not matching the frozen owned world")
    atomic(world / "evidence/campaign-stop.json", {"run_id": run_id, "reason": reason, "at": time.time()})
    os.kill(int(pid), signal.SIGTERM)
    event(config, "owned-run-stop", run_id=run_id, reason=reason, runner_pid=pid)


def watchdog_once(config, *, now=None) -> None:
    from tools import opencode_campaign_orchestrator as orch
    from tools.campaign_runtime import backend_for
    now = time.time() if now is None else now
    state = orch._load_state(config.state_file)
    if not state.episode_runtime:
        return
    active = backend_for(config).restore_config(config, state.episode_runtime)
    root = Path(active.code_root).parent
    world = Path(active.world_root)
    lifecycle = read(root / "lifecycle.json")
    if lifecycle.get("phase") != "running" or backend_for(active).latest_run(active)[0]:
        return
    run_id = lifecycle["run_id"]
    stop_file = world / "evidence/campaign-stop.json"
    if stop_file.exists():
        request = read(stop_file)
        # Cooperative stop gets one minute to publish its run record. A hung
        # operation is then a hard failure, with its partial evidence retained.
        if now - request["at"] >= 60:
            force_stop_owned(config, root, world, run_id, request["reason"])
        return
    path = root / "progress-watchdog.json"
    status = read(world / "evidence/latest-status.json")
    if status and status.get("run_id") != run_id:
        raise RuntimeError("Progress evidence belongs to a different run")
    previous = read(path)
    try:
        signature = live_progress(active, world, status)
    except (OSError, ValueError, RuntimeError) as error:
        # Missing observations cannot substantiate complete stall. Surface the
        # monitoring failure and preserve its original onset across restarts.
        failed_since = previous.get("probe_failed_since", now)
        atomic(path, {**previous, "probe_failed_since": failed_since, "probe_error": str(error), "sample_at": now})
        if now - failed_since >= 1800:
            stop_owned_runner(config, root, world, run_id, "hard_failure_progress_telemetry")
        return
    sample = progress_sample(previous, signature, now=now, launched_at=lifecycle["launched_at"])
    atomic(path, {**sample, "run_id": run_id, "limit_wall_seconds": 1800})
    if sample["idle_wall_seconds"] >= 1800:
        stop_owned_runner(config, root, world, run_id, "stalled_wall_30m")


def force_stop_owned(config, root: Path, world: Path, run_id: str, reason: str) -> None:
    from tools.campaign_pingpong import process_alive
    lifecycle = read(root / "lifecycle.json")
    pid = lifecycle.get("pid")
    if pid and process_alive(pid):
        stop_owned_runner(config, root, world, run_id, reason)
        os.kill(int(pid), signal.SIGKILL)
    index = world.parent / "laya-runs.jsonl"
    existing = index.read_text() if index.exists() else ""
    if not any(json.loads(line).get("run_id") == run_id for line in existing.splitlines()):
        freeze = read(root / "freeze.json")
        with index.open("a") as stream:
            stream.write(json.dumps({"run_id": run_id, "root": str(world), "commit": freeze.get("revision"),
                "seed": config.seed, "speed": config.run_speed, "ended": datetime.now().astimezone().isoformat(),
                "summary": {"stop": reason, "forced": True, "partial_status": read(world / "evidence/latest-status.json")},
                "window": {"research_goal": config.technology}}) + '\n')
    log = world / "logs/laya-runner.log"
    text = log.read_text() if log.exists() else ""
    if "RUN END" not in text.split("RUN START:")[-1]:
        with log.open("a") as stream:
            stream.write(f"\n+{int(time.time()-lifecycle.get('launched_at',time.time()))}s RUN END\n")
    atomic(root / "forced-stop.json", {"run_id": run_id, "reason": reason, "at": time.time(), "forced": True})


def start_watchdog(config):
    done = threading.Event()
    def watch():
        while not done.wait(10):
            try:
                watchdog_once(config)
            except Exception as error:
                event(config, "watchdog-error", error=str(error))
    thread = threading.Thread(target=watch, name="campaign-progress-watchdog", daemon=True)
    thread.start()
    return done


def prepare_wrap(config):
    campaign = read(config.state_root / "campaign.json")
    wrap = datetime.fromisoformat(campaign["wrap_deadline_utc"]).timestamp()
    remaining = wrap - 300 - time.time()
    if remaining <= 0:
        raise WrapDeadline("absolute wrap reserve reached")
    def alarm(*_):
        raise WrapDeadline("absolute wrap reserve reached; preserve final run and report")
    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, remaining)


def initialize_clock(config):
    """T0 is entry into the actual controller, never its earlier setup work."""
    from tools import opencode_campaign_orchestrator as orch
    campaign = read(config.state_root / "campaign.json")
    if not campaign.get("launch_utc"):
        launch = time.time()
        campaign.update({"launch_utc": datetime.fromtimestamp(launch, timezone.utc).isoformat(),
                         "deadline_utc": datetime.fromtimestamp(launch + 21600, timezone.utc).isoformat(),
                         "wrap_deadline_utc": datetime.fromtimestamp(launch + 25200, timezone.utc).isoformat()})
        atomic(config.state_root / "campaign.json", campaign)
    state = orch._load_state(config.state_file)
    if state.deadline_utc and state.deadline_utc != campaign["deadline_utc"]:
        raise RuntimeError("Original deadline mismatch after restart")
    state.deadline_utc = campaign["deadline_utc"]
    orch._save_state(config.state_file, state)
    return campaign


def finish_owned(config, reason):
    from tools import opencode_campaign_orchestrator as orch
    from tools.campaign_runtime import backend_for, latest_run_from_log
    state = orch._load_state(config.state_file)
    if state.episode_runtime:
        active = backend_for(config).restore_config(config, state.episode_runtime)
        root, world = Path(active.code_root).parent, Path(active.world_root)
        lifecycle = read(root / "lifecycle.json")
        run_id = lifecycle.get("run_id")
        if run_id and not latest_run_from_log(world / 'logs/laya-runner.log')[0]:
            stop_owned_runner(config, root, world, run_id, reason)
            until = time.monotonic() + 60
            while time.monotonic() < until and not latest_run_from_log(world / 'logs/laya-runner.log')[0]:
                time.sleep(1)
            force_stop_owned(config, root, world, run_id, reason)
        # The server is a disposable artifact of this exact frozen world.
        server_pid = world / "server/factorio.pid"
        if server_pid.exists():
            pid = int(server_pid.read_text())
            if pid == 1502810:
                raise RuntimeError("protected production PID in disposable record")
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
                if str(world / "server").encode() not in cmd:
                    raise RuntimeError("private server ownership mismatch")
                os.kill(pid, signal.SIGINT)
            except FileNotFoundError:
                pass
    return state


def publish_return(config, report):
    message = config.state_root / "audit-return.md"
    message.write_text(f"The authorized six-hour DeepSeek/GPT loop has wrapped. No further fixes are authorized. "
                       f"Audit the final report {report}, all run records, sessions, handoffs, gate/proofs and final-run evidence. "
                       "Use GPT-6-Luna/high for reading large logs per AGENTS.md, interrupt readers when done. "
                       "Report findings to the user and preserve unfinished candidates as unverified.")
    send_root(config, message, config.state_root / "audit-return-delivery.json")


def main(config):
    from tools import opencode_campaign_orchestrator as orch
    from tools.campaign_pingpong import final_report
    campaign = initialize_clock(config)
    done = None
    failure = None
    code = 0
    try:
        prepare_wrap(config)
        done = start_watchdog(config)
        code = orch.run_campaign(config)
    except WrapDeadline as error:
        failure = str(error)
        state = finish_owned(config, "absolute_wrap")
        state.phase = "WRAPPED"
        orch._save_state(config.state_file, state)
        event(config, "absolute-wrap", reason=failure)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
        state = orch._load_state(config.state_file)
        path = config.state_root / "logs/harness-failures.jsonl"
        with path.open("a") as stream:
            stream.write(json.dumps({"at": time.time(), "error": failure, "phase": state.phase,
                                     "episode": state.episode_runtime, "pending_fix": state.pending_fix}) + '\n')
        failures = len(path.read_text().splitlines())
        if time.time() < datetime.fromisoformat(campaign["deadline_utc"]).timestamp() and failures < 3:
            # Nonzero makes systemd resume the same durable episode/request.
            return 1
        state = finish_owned(config, "harness_failure" if failures >= 3 else "deadline_reached")
        state.phase = "HARNESS_FAILURE" if failures >= 3 else "DEADLINE_REACHED"
        orch._save_state(config.state_file, state)
    finally:
        if done:
            done.set()
        signal.setitimer(signal.ITIMER_REAL, 0)
        orch._COMMAND_DEADLINE = None
    state = finish_owned(config, "campaign_finished")
    report = final_report(config, state, failure=failure)
    try:
        publish_return(config, report)
    except Exception as error:
        event(config, "audit-return-failed", error=str(error), report=str(report))
    return code
