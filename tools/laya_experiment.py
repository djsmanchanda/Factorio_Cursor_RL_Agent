# Path: tools/laya_experiment.py
# Purpose: Run Laya variants side by side in disposable worlds over a bounded window, then compare how reliably each reaches it.

"""Laya variant experiments.

    python tools/laya_experiment.py run --experiment c1-goal-weight \\
        --variants docs/rl/laya_variants/goal-weight-5.json --seeds 3 --until-phase C1 --max-game-ticks 108000
    python tools/laya_experiment.py report --experiment c1-goal-weight

The baseline (current workflow) always runs alongside the variants with the
same seeds.  Each run is a fresh copy of ``saves/mod_playground.zip`` under
``data/laya-saved-world/exp-<experiment>/``, on its own ports (34240-34259 /
27140-27159, clear of the console runner and the mall fleet), without the
helper.  Runs are indexed and recorded in the run ledger with their variant
and experiment, so ``report`` needs no live server.

A short window (``--until-phase C0`` or ``C1``) tests a localized tweak; a long
window (later phase, larger tick cap) tests whether it holds up.  Adopt a
variant only when it beats the baseline over a long window on several seeds.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.run_laya_clean import RUN_INDEX  # noqa: E402
from training.laya_mission import PHASES  # noqa: E402
from training.laya_variants import load_variant  # noqa: E402

GAME_PORT, RCON_PORT, SLOTS = 34240, 27140, 20
RUNNER = [sys.executable, str(ROOT / "tools/run_laya_clean.py")]
ORDER = [p["phase_id"] for p in PHASES]


def _job(experiment: str, variant: Path | None, seed: int, slot: int, args: argparse.Namespace) -> dict:
    vid = load_variant(variant)["variant_id"]
    root = ROOT / f"data/laya-saved-world/exp-{experiment}/{vid}-s{seed}"
    out = root.parent / f"{vid}-s{seed}.out"
    root.parent.mkdir(parents=True, exist_ok=True)
    ports = ["--game-port", str(GAME_PORT + slot), "--rcon-port", str(RCON_PORT + slot)]
    with out.open("w") as log:
        prepared = subprocess.run(RUNNER + ["prepare", "--root", str(root), *ports], stdout=log, stderr=subprocess.STDOUT)
        if prepared.returncode:
            return {"variant": vid, "seed": seed, "error": f"prepare failed, see {out}"}
        try:
            command = RUNNER + ["run", "--root", str(root), "--seed", str(seed), "--no-helper", "--experiment", experiment,
                               "--speed", str(args.speed)]
            command += ["--variant", str(variant)] if variant else []
            command += ["--until-phase", args.until_phase] if args.until_phase else []
            command += ["--max-game-ticks", str(args.max_game_ticks)] if args.max_game_ticks else []
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            log.write("EXPERIMENT: wall-clock timeout\n")
        finally:
            subprocess.run(RUNNER + ["stop", "--root", str(root)], stdout=log, stderr=subprocess.STDOUT)
    return {"variant": vid, "seed": seed, "root": str(root)}


def run(args: argparse.Namespace) -> int:
    variants = [None] + [Path(v) for v in args.variants]  # None = baseline
    ids = [load_variant(v)["variant_id"] for v in variants]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"variant ids must be distinct: {ids}")
    jobs = [(v, seed) for seed in range(args.seed_base, args.seed_base + args.seeds) for v in variants]
    if args.parallel > SLOTS:
        raise SystemExit(f"at most {SLOTS} parallel worlds")
    with ThreadPoolExecutor(args.parallel) as pool:
        # Slot = job index modulo parallel; the pool never runs two jobs of one slot at once.
        slots = list(range(args.parallel))
        futures = []
        for i, (variant, seed) in enumerate(jobs):
            futures.append(pool.submit(_slot_job, slots, args.experiment, variant, seed, args))
        for future in futures:
            print(json.dumps(future.result()))
    return report(args)


def _slot_job(slots: list[int], experiment: str, variant: Path | None, seed: int, args: argparse.Namespace) -> dict:
    slot = slots.pop()
    try:
        return _job(experiment, variant, seed, slot, args)
    finally:
        slots.append(slot)


def score(rows: list[dict], target: str | None) -> dict:
    """Reliability first (share of runs reaching the target), then speed and cleanliness."""
    reached = [r for r in rows if target and target in r["summary"].get("phases_reached", [])]
    ticks = [r["summary"].get("phase_ticks", {}).get(target) for r in reached]
    ticks = [t for t in ticks if t is not None]
    highest = [max((ORDER.index(p) for p in r["summary"].get("phases_reached", [])), default=-1) for r in rows]
    return {"runs": len(rows), "reached": len(reached), "reach_rate": round(len(reached) / len(rows), 2) if rows else 0,
            "median_ticks": statistics.median(ticks) if ticks else None,
            "mean_highest_phase": round(statistics.mean(highest), 2) if highest else None,
            "failures": sum(r["summary"].get("failures", 0) for r in rows),
            "stops": dict(sorted({s: sum(r["summary"].get("stop") == s for r in rows)
                                  for s in {r["summary"].get("stop") for r in rows}}.items()))}


def report(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in RUN_INDEX.read_text().splitlines() if line.strip()] if RUN_INDEX.is_file() else []
    rows = [r for r in rows if r.get("experiment") == args.experiment]
    if not rows:
        print(f"no runs recorded for experiment {args.experiment}")
        return 1
    target = args.until_phase or next((r["window"]["until_phase"] for r in rows if r.get("window", {}).get("until_phase")), None)
    by_variant: dict[str, list[dict]] = {}
    for row in rows:
        by_variant.setdefault(row.get("variant_id", "baseline"), []).append(row)
    table = {vid: score(vrows, target) for vid, vrows in sorted(by_variant.items())}
    result = {"experiment": args.experiment, "target": target, "variants": table}
    path = ROOT / f"data/laya-saved-world/exp-{args.experiment}/report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"| variant | runs | reached {target} | median ticks | mean highest phase | failures | stops |")
    print("|---|---|---|---|---|---|---|")
    for vid, s in table.items():
        print(f"| {vid} | {s['runs']} | {s['reached']} ({s['reach_rate']:.0%}) | {s['median_ticks']} | "
              f"{s['mean_highest_phase']} | {s['failures']} | {s['stops']} |")
    print(f"report: {path}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["run", "report"])
    p.add_argument("--experiment", required=True)
    p.add_argument("--variants", nargs="*", default=[], help="Variant JSON files; the baseline always runs too")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--seed-base", type=int, default=1)
    p.add_argument("--until-phase", choices=ORDER)
    p.add_argument("--max-game-ticks", type=int)
    p.add_argument("--speed", type=float, default=4.0, help="Live game speed for every run")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--timeout", type=int, default=4 * 3600, help="Wall-clock seconds per run")
    args = p.parse_args(argv)
    if args.action == "run" and not (args.until_phase or args.max_game_ticks):
        p.error("an experiment needs a window: --until-phase and/or --max-game-ticks")
    return run(args) if args.action == "run" else report(args)


if __name__ == "__main__":
    raise SystemExit(main())
