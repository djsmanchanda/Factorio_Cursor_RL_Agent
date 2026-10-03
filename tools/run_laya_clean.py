#!/usr/bin/env python3
# Path: tools/run_laya_clean.py
# Purpose: Prepare, run and stop the clean Laya runner on a disposable copy of saves/mod_playground.zip.

"""Clean Laya runner CLI.

prepare/stop reuse the saved-world lifecycle (frozen installed mods, fleet-mode
server, owned root under data/laya-saved-world/).  run drives the decision loop
in training/laya_clean_runner.py.  The source save is never modified.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_clean_runner import ExplorationPolicy, run
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root

SOURCE_SAVE = ROOT / "saves/mod_playground.zip"


def saved_world(action: str, root: Path, *extra: str) -> str:
    return subprocess.run([sys.executable, str(ROOT / "tools/run_laya_saved_world.py"), action, "--root", str(root), *extra],
                          check=True, capture_output=True, text=True).stdout.strip()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "run", "stop"])
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--game-port", type=int, default=34230)
    p.add_argument("--rcon-port", type=int, default=27130)
    p.add_argument("--decisions", type=int, default=20)
    p.add_argument("--measure-ticks", type=int, default=1800)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    root = owned_root(args.root)
    if args.action == "prepare":
        print(saved_world("prepare", root, "--source-save", str(SOURCE_SAVE),
                          "--game-port", str(args.game_port), "--rcon-port", str(args.rcon_port)))
        return 0
    if args.action == "stop":
        print(saved_world("stop", root))
        return 0
    if not 1 <= args.decisions <= 500 or not 300 <= args.measure_ticks <= 18000:
        p.error("decisions must be 1..500 and measure ticks 300..18000")
    source_hash = file_hash(SOURCE_SAVE)
    adapter = SavedWorldAdapter(root)
    run_id = f"{root.name}-s{args.seed}-{int(time.time())}"
    journal = root / "evidence" / "clean-decisions.jsonl"
    journal.parent.mkdir(exist_ok=True)
    try:
        summary = run(adapter, ExplorationPolicy(args.seed), journal=journal, decisions=args.decisions,
                      measure_ticks=args.measure_ticks, seed=args.seed, run_id=run_id)
    finally:
        adapter.close()
    if file_hash(SOURCE_SAVE) != source_hash:
        raise SystemExit("source save changed during the run")
    print(json.dumps({"run_id": run_id, "journal": str(journal), **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
