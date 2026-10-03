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
import json
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.laya_helper_agent import RUN_INDEX, launch as launch_helper
from tools.run_ledger import LEDGER, record_laya_run
from tools.runner_process import runner_pid_record
from training.laya_clean_runner import ExplorationPolicy, LiveWorld, run
from training.laya_run_log import RunLog
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root

SOURCE_SAVE = ROOT / "saves/mod_playground.zip"


def saved_world(action: str, root: Path, *extra: str) -> str:
    return subprocess.run([sys.executable, str(ROOT / "tools/run_laya_saved_world.py"), action, "--root", str(root), *extra],
                          check=True, capture_output=True, text=True).stdout.strip()


def runner_paths(root: Path) -> dict[str, Path]:
    return {"log": root / "logs/laya-runner.log", "pid": root / "laya-runner.pid.json",
            "heartbeat": root / "laya-runner.heartbeat.json", "journal": root / "evidence/clean-decisions.jsonl",
            "status": root / "evidence/latest-status.json"}


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
    args = p.parse_args(argv)
    root = owned_root(args.root)
    if args.action == "prepare":
        print(saved_world("prepare", root, "--source-save", str(SOURCE_SAVE),
                          "--game-port", str(args.game_port), "--rcon-port", str(args.rcon_port)))
        return 0
    if args.action == "stop":
        print(saved_world("stop", root))
        return 0
    if args.decisions < 0 or not 300 <= args.measure_ticks <= 18000:
        p.error("decisions must be >= 0 (0 = no limit) and measure ticks 300..18000")
    paths = runner_paths(root)
    paths["journal"].parent.mkdir(exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    source_hash = file_hash(SOURCE_SAVE)
    run_id = f"{root.name}-s{args.seed}-{int(time.time())}"
    mode = "stepped" if args.stepped else "live"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    log = RunLog(paths["log"], paths["heartbeat"])
    summary = {"decisions": 0, "failures": 0, "phases_reached": [], "stop": "runner_error"}
    with runner_pid_record(paths["pid"]):
        adapter = SavedWorldAdapter(root) if args.stepped else LiveWorld(root, args.speed)
        manifest = json.loads((root / "manifest.json").read_text())
        log.start(command="run", runner="laya-clean", run_id=run_id, commit=commit, mode=mode, speed=args.speed,
                  decisions=args.decisions or "unlimited", policy="exploration", seed=args.seed,
                  game=f"127.0.0.1:{manifest['game_port']}", root=root, log=paths["log"])
        try:
            if not args.stepped:
                adapter.go_live()
                log(f"LIVE: game unpaused at speed {args.speed:g}; decisions are taken while the factory runs")
            summary = run(adapter, ExplorationPolicy(args.seed), journal=paths["journal"],
                          decisions=args.decisions or None, measure_ticks=args.measure_ticks, seed=args.seed,
                          run_id=run_id, emit=log, should_stop=stop.is_set, status_path=paths["status"])
            if stop.is_set():
                log("STOP REQUESTED: runner stopped after its current decision; Factorio keeps running")
        finally:
            adapter.close()
            if file_hash(SOURCE_SAVE) != source_hash:
                log("SOURCE SAVE CHANGED during the run")
            log.end()
    # Index the run for later comparisons, then start the read-only post-run review.
    RUN_INDEX.parent.mkdir(parents=True, exist_ok=True)
    index = {"run_id": run_id, "root": str(root.resolve()), "commit": commit, "mode": mode, "started": started,
             "ended": datetime.now().astimezone().isoformat(timespec="seconds"), "summary": summary}
    with RUN_INDEX.open("a") as out:
        out.write(json.dumps(index) + "\n")
    try:
        entry = record_laya_run(index)
        print(f"RUN LEDGER: {entry['outcome']} highest={entry['highest_milestone']} -> {LEDGER}")
    except Exception as error:  # The ledger is bookkeeping; it never fails a run.
        print(f"RUN LEDGER: not recorded: {error}")
    if not args.no_helper:
        print("LAYA HELPER: started " + launch_helper(run_id, paths["log"]))
    print(json.dumps({"run_id": run_id, "journal": str(paths["journal"]), **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
