#!/usr/bin/env python3
# Path: tools/run_laya_clean.py
# Purpose: Prepare, run and stop the clean Laya runner on a disposable copy of saves/mod_playground.zip.

"""Clean Laya runner CLI.

prepare/stop reuse the saved-world lifecycle (frozen installed mods, fleet-mode
server, owned root under data/laya-saved-world/).  run drives the decision loop
in training/laya_clean_runner.py: live (unpaused) by default and, with
``--decisions 0``, until the mission completes, a safe stop, or SIGTERM.
The source save is never modified.

Run artifacts under <root>: logs/laya-runner.log (deterministic format),
laya-runner.pid.json, laya-runner.heartbeat.json, evidence/clean-decisions.jsonl
and evidence/latest-status.json.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.laya_helper_agent import RUN_INDEX, launch as launch_helper
from core.local_time import local_stamp
from tools.run_ledger import LEDGER, record_laya_run
from tools.runner_process import runner_pid_record
from training.laya_clean_runner import ExplorationPolicy, LiveWorld, run
from training.laya_guided_policy import GuidedPolicy, load_preferences
from training.laya_variants import apply as apply_variant, load_variant, variant_hash
from training.laya_run_log import RunLog
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root, speed_status
from training.laya_checkpoint import load_bundle, export_bundle, restore_context

SOURCE_SAVE = ROOT / "saves/mod_playground.zip"


def saved_world(action: str, root: Path, *extra: str) -> str:
    result = subprocess.run([sys.executable, str(ROOT / "tools/run_laya_saved_world.py"), action, "--root", str(root), *extra],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def runner_paths(root: Path) -> dict[str, Path]:
    return {"log": root / "logs/laya-runner.log", "pid": root / "laya-runner.pid.json",
            "heartbeat": root / "laya-runner.heartbeat.json", "journal": root / "evidence/clean-decisions.jsonl",
            "status": root / "evidence/latest-status.json"}


def _game_tick(adapter) -> int | None:
    """Current game tick, or None if it cannot be read (a report field, never a run failure)."""
    try:
        return adapter.tick()
    except Exception:
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "run", "stop"])
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--game-port", type=int, default=34230)
    p.add_argument("--rcon-port", type=int, default=27130)
    p.add_argument("--decisions", type=int, default=0, help="0 = no limit")
    p.add_argument("--measure-ticks", type=int, default=1800)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--stepped", action="store_true", help="Paused fixed-tick stepping instead of live play")
    p.add_argument("--speed", type=float, default=1.0, help="Live game speed")
    p.add_argument("--no-helper", action="store_true", help="Skip the post-run Laya Helper review")
    p.add_argument("--actions", choices=["geometry", "deterministic"], default="geometry",
                   help="deterministic: Laya chooses among the deterministic runtime's operations "
                        "(training/laya_ops_runner.py); geometry: Laya's own build generators")
    p.add_argument("--policy", choices=["guided", "exploration"], default="guided",
                   help="guided: doc-rule preferences + exploration floor; exploration: uniform")
    p.add_argument("--variant", type=Path, help="Variant JSON (docs/rl/laya_variants/); default: the current workflow")
    p.add_argument("--until-phase", help="Experiment window: stop once this phase is reached")
    p.add_argument("--until-accepted", choices=[f"C{i}" for i in range(7)],
                   help="Ops only: stop after the shared checkpoint predicate passes")
    p.add_argument("--research-goal",
                   help="Natural goal run: end only when this technology's target_completed is verified "
                        "(no rate-proxy or decision-count stall cutoff)")
    p.add_argument("--stall-observation-ticks", type=int, help="Useful-state tick window; 0 disables it for a wall-clock campaign watchdog.")
    p.add_argument("--max-game-ticks", type=int, help="Experiment window: stop after this many game ticks")
    p.add_argument("--max-wall-seconds", type=float, help="Cooperative natural episode wall budget; stop after the current operation.")
    p.add_argument("--experiment", help="Experiment id recorded with the run")
    p.add_argument("--source-save", type=Path, default=SOURCE_SAVE)
    p.add_argument("--mods-source", type=Path)
    p.add_argument("--resume-bundle", type=Path, help="Hash-verified earned C1 checkpoint")
    p.add_argument("--export-checkpoint", type=Path, help="Save an earned C1 checkpoint after a successful run")
    args = p.parse_args(argv)
    if args.until_accepted and (args.actions != "deterministic" or args.until_phase):
        p.error("until-accepted requires deterministic actions and cannot be combined with until-phase")
    root = owned_root(args.root)
    resume=load_bundle(args.resume_bundle) if args.resume_bundle else None
    if args.action == "prepare":
        extra = ["--mods-source", str(args.mods_source)] if args.mods_source else []
        source=args.resume_bundle/'source.zip' if resume else args.source_save
        print(saved_world("prepare", root, "--source-save", str(source),
                          "--game-port", str(args.game_port), "--rcon-port", str(args.rcon_port), *extra))
        if resume:restore_context(args.resume_bundle,root)
        return 0
    if args.action == "stop":
        print(saved_world("stop", root))
        return 0
    if args.decisions < 0 or not 300 <= args.measure_ticks <= 18000:
        p.error("decisions must be >= 0 (0 = no limit) and measure ticks 300..18000")
    variant = load_variant(args.variant)
    settings, policy_options, runtime = apply_variant(variant)
    settings = dataclasses.replace(settings, until_phase=args.until_phase or settings.until_phase,
                                   max_game_ticks=args.max_game_ticks or settings.max_game_ticks)
    if args.stall_observation_ticks is not None:
        if args.stall_observation_ticks < 0:
            p.error("stall-observation-ticks must be >= 0")
        settings = dataclasses.replace(settings, stall_observation_ticks=args.stall_observation_ticks or None)
    speed = float(runtime.get("speed", args.speed))
    tag = {"variant_id": variant["variant_id"], "variant_hash": variant_hash(variant), "experiment": args.experiment}
    paths = runner_paths(root)
    paths["journal"].parent.mkdir(exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    source_save = args.source_save.resolve()
    source_hash = file_hash(source_save)
    run_id = f"{root.name}-s{args.seed}-{int(time.time())}"
    mode = "stepped" if args.stepped else "live"
    frozen_identity = ROOT.parent / "freeze.json"
    commit = (json.loads(frozen_identity.read_text())["revision"] if frozen_identity.exists() else
              subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    wall_started = time.monotonic()
    if args.max_wall_seconds is not None and args.max_wall_seconds <= 0:
        p.error("max-wall-seconds must be positive")
    def should_stop():
        return stop.is_set() or (args.max_wall_seconds is not None and time.monotonic() - wall_started >= args.max_wall_seconds)
    started = local_stamp()
    log = RunLog(paths["log"], paths["heartbeat"])
    summary = {"decisions": 0, "failures": 0, "phases_reached": [], "stop": "runner_error"}
    runner_error = None
    with runner_pid_record(paths["pid"]):
        adapter = SavedWorldAdapter(root) if args.stepped else LiveWorld(root, speed)
        manifest = json.loads((root / "manifest.json").read_text())
        live_error = None
        if not args.stepped:  # Unpause before RUN START so the log shows the speed the game runs at.
            try:
                adapter.go_live()
            except Exception as error:  # Re-raised inside the try below, so RUN END is still written.
                live_error = error
        adapter.observe_speed()
        start_tick = _game_tick(adapter)
        log.start(command="run", runner="laya-clean", run_id=run_id, commit=commit, mode=mode, speed=speed,
                  requested_speed="paused" if args.stepped else f"{speed:g}", observed_speed=adapter.observed_speed,
                  decisions=args.decisions or "unlimited", policy=args.policy, seed=args.seed,
                  variant=f"{tag['variant_id']} {tag['variant_hash'][:19]}", experiment=args.experiment or "-",
                  window=f"goal={args.research_goal or settings.goal_technology or '-'} proxy={settings.until_phase or '-'} accepted={args.until_accepted or '-'} max_ticks={settings.max_game_ticks or '-'}",
                  game=f"127.0.0.1:{manifest['game_port']}", root=root, log=paths["log"])
        if not args.no_helper:  # Live observer from RUN START; it writes the final review after RUN END.
            log("LAYA HELPER: observing " + launch_helper(run_id, paths["log"], root.resolve()))
        try:
            if live_error is not None:
                raise live_error
            if not args.stepped:
                log(f"LIVE: game unpaused at speed {speed:g}; decisions are taken while the factory runs")
            policy = (GuidedPolicy(args.seed, preferences=load_preferences(ROOT / settings.catalog), **policy_options)
                      if args.policy == "guided" else ExplorationPolicy(args.seed))
            if args.actions == "deterministic":
                from training.laya_ops_runner import run as run_ops
                summary = run_ops(adapter, policy, journal=paths["journal"], decisions=args.decisions or None,
                                  measure_ticks=args.measure_ticks, seed=args.seed, run_id=run_id, emit=log,
                                  should_stop=should_stop, status_path=paths["status"], settings=settings,
                                  until_accepted=args.until_accepted,
                                  goal=args.research_goal or settings.goal_technology)
            else:
                summary = run(adapter, policy, journal=paths["journal"],
                              decisions=args.decisions or None, measure_ticks=args.measure_ticks, seed=args.seed,
                              run_id=run_id, emit=log, should_stop=should_stop, status_path=paths["status"],
                              settings=settings, initial_reached=resume['phases_reached'] if resume else ())
            if args.export_checkpoint:
                export_bundle(adapter,args.export_checkpoint,summary)
            if args.max_wall_seconds is not None and time.monotonic() - wall_started >= args.max_wall_seconds:
                summary["stop"] = "wall_budget"
                log(f"WALL LIMIT: declared {args.max_wall_seconds:g}s budget reached; current operation finished")
            if stop.is_set():
                request = paths["status"].parent / "campaign-stop.json"
                if request.exists():
                    stopped = json.loads(request.read_text())
                    if stopped.get("run_id") == run_id:
                        summary["stop"] = stopped["reason"]
                        summary["campaign_stop"] = stopped
                log("STOP REQUESTED: runner stopped after its current decision; Factorio keeps running")
        except Exception as error:
            runner_error = error
            summary["stop"] = "runner_error"
            summary["error"] = f"{type(error).__name__}: {error}"
            log(f"FAILURE: runner_error: {summary['error']}")
        finally:
            end_tick = _game_tick(adapter)
            adapter.close()
            if file_hash(source_save) != source_hash:
                log("SOURCE SAVE CHANGED during the run")
            log(log.elapsed(start_tick, end_tick))
            log.end()
    # Index the run for later comparisons, then start the read-only post-run review.
    RUN_INDEX.parent.mkdir(parents=True, exist_ok=True)
    index = {"run_id": run_id, "root": str(root.resolve()), "commit": commit, "mode": mode, "started": started,
             "ended": local_stamp(), "summary": summary, "seed": args.seed, "speed": speed,
             "speed_status": speed_status(adapter), **tag,
             "window": {"research_goal": args.research_goal or settings.goal_technology,
                        "until_phase": settings.until_phase, "until_accepted": args.until_accepted,
                        "max_game_ticks": settings.max_game_ticks}}
    if resume:index['resume_checkpoint']={'path':str(args.resume_bundle),'save_sha256':resume['files']['source.zip']}
    with RUN_INDEX.open("a") as out:
        out.write(json.dumps(index) + "\n")
    try:
        entry = record_laya_run(index)
        print(f"RUN LEDGER: {entry['outcome']} highest={entry['highest_milestone']} -> {LEDGER}")
    except Exception as error:  # The ledger is bookkeeping; it never fails a run.
        print(f"RUN LEDGER: not recorded: {error}")
    print(json.dumps({"run_id": run_id, "journal": str(paths["journal"]), "speed": speed_status(adapter), **summary}))
    return 1 if runner_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
