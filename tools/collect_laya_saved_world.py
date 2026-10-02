#!/usr/bin/env python3
# Path: tools/collect_laya_saved_world.py | Purpose: Collect measured action alternatives from matched disposable saved-world forks.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_laya_saved_world import manager, prepare
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, measured_outcome, owned_root
from training.laya_saved_counterfactual import assess, label_to_case, replay_fingerprint


def collect(args):
    parent = owned_root(args.parent)
    output = owned_root(args.output)
    if output.exists():
        raise SavedWorldError("collector requires a fresh evidence root; existing labels are preserved")
    if output.is_relative_to(parent) or parent.is_relative_to(output):
        raise SavedWorldError("collector and parent roots must be separate")
    output.mkdir(parents=True)
    adapter = SavedWorldAdapter(parent)
    try:
        prefix = adapter.checkpoint()
        menus = []
        for seed in range(args.seed, args.seed + args.menus):
            candidates, pruning = adapter.options(prefix["facts"], seed)
            menus.append({"seed": seed, "candidates": candidates, "pruning": pruning})
    finally:
        adapter.close()
    (output / "prefix.json").write_text(json.dumps(prefix, indent=2) + "\n")
    (output / "menus.json").write_text(json.dumps(menus, indent=2) + "\n")
    # Free only this owned endpoint before sequential branch startup.
    print(manager(parent, "stop"), flush=True)
    outcomes = {}
    unresolved = []
    status = {"phase": "collecting", "completed_menus": 0, "unresolved": unresolved,
              "comparison_eligible": False, "production_authority": False}
    (output / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    for menu in menus:
        label = {"version": "saved-world-cf-v1", "run_id": f"swcf.{output.name}.{menu['seed']}",
                 "ancestor_source_sha256": prefix["ancestor_source_sha256"], "split": "train",
                 "start_fingerprint": replay_fingerprint(prefix["facts"]), "facts": prefix["facts"],
                 "candidates": menu["candidates"], "goal": args.goal, "rate_per_second": args.rate,
                 "sustain_ticks": args.sustain_ticks, "horizon_ticks": args.horizon_ticks, "outcomes": {}, "costs": {}}
        for index, candidate in enumerate(menu["candidates"]):
            key = candidate["option_id"]
            # Deterministic common starts let identical alternatives share their
            # original measured receipt across menus, without replaying a teacher.
            if key not in outcomes:
                branch = output / f"branch-{menu['seed']}-{index}"
                live = None
                try:
                    prepare(SimpleNamespace(root=branch, source_save=Path(prefix["save"]),
                        mods_source=parent / "server/mods", game_port=args.game_port, rcon_port=args.rcon_port,
                        ancestor_source_sha256=prefix["ancestor_source_sha256"]))
                    live = SavedWorldAdapter(branch)
                    before = live.query()
                    fingerprint = replay_fingerprint(before)
                    if fingerprint != label["start_fingerprint"]:
                        raise SavedWorldError("fork did not restore the exact paused starting state")
                    receipt = live.execute(key, [candidate], 900)
                    end_warmup = before["tick"] + args.horizon_ticks - args.sustain_ticks
                    remaining = end_warmup - live.query()["tick"]
                    if remaining < 0:
                        raise SavedWorldError("action exceeded the common measurement horizon")
                    if remaining:
                        live.advance(remaining)
                    live.reconcile()
                    samples = []
                    previous = live.query()
                    for _ in range(args.sustain_ticks // 300):
                        live.advance(300)
                        after = live.query()
                        samples.append(measured_outcome(previous, after))
                        previous = after
                    result = assess(samples, args.goal, args.rate, args.sustain_ticks // 300)
                    result.update(replay_fingerprint=fingerprint, horizon_ticks=previous["tick"] - before["tick"],
                                  branch=str(branch), samples=samples, submission=receipt.get("submission"),
                                  cost=args.horizon_ticks + sum(candidate.get("bill", {}).values()) * 60)
                except (SavedWorldError, ValueError, OSError) as error:
                    result = {"status": "failed", "failure_cause": "tool", "failure_kind": "execution",
                              "safety_violations": 0, "reason": str(error), "branch": str(branch), "cost": args.horizon_ticks}
                finally:
                    if live is not None:
                        live.close()
                    if (branch / "server/factorio.pid").exists():
                        print(manager(branch, "stop"), flush=True)
                outcomes[key] = result
                (output / "outcomes.json").write_text(json.dumps(outcomes, indent=2) + "\n")
                print(json.dumps({"option": key, "status": result["status"], "reason": result.get("reason"),
                                  "rates": result.get("rates")}), flush=True)
                if result.get("failure_cause") == "tool":
                    # A clear environment/mechanics failure needs diagnosis,
                    # not a batch of repeated misleading policy failures.
                    status.update(phase="stopped", failed_option=key, reason=result["reason"])
                    (output / "status.json").write_text(json.dumps(status, indent=2) + "\n")
                    raise SavedWorldError(f"counterfactual collection stopped at {key}: {result['reason']}")
            label["outcomes"][key] = outcomes[key]
            label["costs"][key] = outcomes[key]["cost"]
        with (output / "labels.jsonl").open("a") as out:
            out.write(json.dumps(label) + "\n")
        case, reason = label_to_case(label)
        if case is not None:
            with (output / "cases.jsonl").open("a") as out:
                out.write(json.dumps(case) + "\n")
        else:
            unresolved.append({"menu_seed": menu["seed"], "reason": reason})
        status["completed_menus"] += 1
        (output / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    status = json.loads((output / "status.json").read_text())
    status["phase"] = "complete"
    (output / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    return status


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--menus", type=int, default=4)
    p.add_argument("--goal", default="iron-ore")
    p.add_argument("--rate", type=float, default=.1)
    p.add_argument("--horizon-ticks", type=int, default=7200)
    p.add_argument("--sustain-ticks", type=int, default=1200)
    p.add_argument("--game-port", type=int, default=34198)
    p.add_argument("--rcon-port", type=int, default=27018)
    args = p.parse_args(argv)
    if not 1 <= args.menus <= 32 or not 1800 <= args.horizon_ticks <= 18000 or not 300 <= args.sustain_ticks <= args.horizon_ticks - 1800 or args.sustain_ticks % 300 or args.rate <= 0:
        p.error("invalid bounded collection menu/measurement configuration")
    print(json.dumps(collect(args)), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SavedWorldError, ValueError) as error:
        print(f"saved-world collection stopped: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
