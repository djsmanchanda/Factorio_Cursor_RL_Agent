#!/usr/bin/env python3
# Path: tools/run_laya_saved_world.py | Purpose: Prepare and test a separate disposable copy of a saved Factorio world.

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_input_profile import require_input_profile
from training.laya_saved_world import (
    PROFILE, SavedWorldAdapter, SavedWorldError, decision_state, file_hash, owned_root,
)
from training.laya_starter_kit import verify_starter_kit


def manager(root, action, extra=()):
    manifest = json.loads((root / "manifest.json").read_text())
    return subprocess.run([
        "bash", str(root / "frozen/scripts/manage_linux_deterministic_server.sh"), action,
        "--root", str(root / "server"), "--fleet-mode", "--bootstrap-profile", "supplied-v1",
        "--game-port", str(manifest["game_port"]), "--rcon-port", str(manifest["rcon_port"]),
        "--source-save", str(root / "baseline.zip"), *extra,
    ], check=True, capture_output=True, text=True).stdout.strip()


def prepare(args):
    runtime = getattr(args, "runtime", "disposable_saved_world")
    if runtime not in {"disposable_saved_world", "disposable_mall"}:
        raise SavedWorldError("unsupported disposable runtime")
    update_threads = getattr(args, "update_threads", None)
    cpu_affinity = getattr(args, "cpu_affinity", None)
    nice_level = getattr(args, "nice", 0)
    if update_threads is not None and not 1 <= update_threads <= 64:
        raise SavedWorldError("update threads must be between 1 and 64")
    if not 0 <= nice_level <= 19:
        raise SavedWorldError("nice must be between 0 and 19")
    runtime_args = []
    if update_threads is not None:
        runtime_args += ["--update-threads", str(update_threads)]
    if cpu_affinity:
        runtime_args += ["--cpu-affinity", str(cpu_affinity)]
    if nice_level:
        runtime_args += ["--nice", str(nice_level)]
    root = owned_root(args.root)
    source = args.source_save.resolve()
    if root.exists():
        raise SavedWorldError("prepare requires a fresh root; existing test worlds are preserved")
    if source.is_relative_to(root):
        raise SavedWorldError("source save must be outside the disposable root")
    root.mkdir(parents=True)
    shutil.copy2(source, root / "baseline.zip")
    frozen = root / "frozen"
    (frozen / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/manage_linux_deterministic_server.sh", frozen / "scripts")
    for name, folder in [("factorio_cursor_rl_agent", "factorio_mod"), ("factorio_training_lab", "factorio_training_lab")]:
        shutil.copytree(args.mods_source / name, frozen / folder)
    manifest = {"version": PROFILE, "runtime": runtime, "production_authority": False,
                "comparison_eligible": False, "source_save": str(source), "source_sha256": file_hash(source),
                "game_port": args.game_port, "rcon_port": args.rcon_port, "mod_hashes": {},
                "runtime_settings": {"factorio_update_threads": update_threads,
                                     "cpu_affinity": cpu_affinity, "nice": nice_level}}
    ancestor = getattr(args, "ancestor_source_sha256", None)
    if ancestor is not None:
        if len(ancestor) != 64 or any(c not in "0123456789abcdef" for c in ancestor):
            raise SavedWorldError("invalid ancestor save hash")
        manifest["ancestor_source_sha256"] = ancestor
    manifest["quad_centers"] = getattr(args, "quad_centers", [])
    if runtime == "disposable_mall":
        manifest["version"] = args.mall["version"]
        manifest["mall"] = args.mall
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(manager(root, "bootstrap", runtime_args))
    manifest["initial_copy_sha256"] = file_hash(root / "server/saves/mod_playground.zip")
    if manifest["initial_copy_sha256"] != manifest["source_sha256"]:
        raise SavedWorldError("initial save copy mismatch")
    mods = root / "server/mods"
    manifest["mod_hashes"] = {str(p.relative_to(mods)): file_hash(p) for p in mods.rglob("*")
                             if p.is_file() and p.name != "mod-list.json"}
    manifest["repository_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(manager(root, "start", runtime_args))
    if runtime == "disposable_mall":
        if file_hash(source) != manifest["source_sha256"]:
            raise SavedWorldError("source save changed during preparation")
        return {"root": str(root), "status": "mall_host_prepared"}
    adapter = SavedWorldAdapter(root)
    try:
        adapter.bridge.command('/sc game.tick_paused=true; game.ticks_to_run=0; rcon.print("saved-world-paused")')
        facts = adapter.query()
        (root / "initial-state.json").write_text(json.dumps(facts, indent=2) + "\n")
        (root / "starter-kit-check.json").write_text(json.dumps(verify_starter_kit(facts), indent=2) + "\n")
    finally:
        adapter.close()
    if file_hash(source) != manifest["source_sha256"]:
        raise SavedWorldError("source save changed during preparation")
    return {"root": str(root), "status": "prepared", "tick": facts["tick"]}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "observe", "replay", "run", "stop"])
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--source-save", type=Path, default=ROOT / "saves/mod_playground.zip")
    p.add_argument("--mods-source", type=Path, default=Path.home() / ".local/share/factorio-rl/deterministic/mods")
    p.add_argument("--game-port", type=int, default=34198)
    p.add_argument("--rcon-port", type=int, default=27018)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--option-id", help="Explicit mechanics replay; never attributed to Laya")
    p.add_argument("--checkpoint", type=Path)
    p.add_argument("--base-model", type=Path)
    p.add_argument("--decisions", type=int, default=10)
    p.add_argument("--measure-ticks", type=int, default=1800)
    p.add_argument("--objective", default="Establish and sustain iron plates and transport-belt production from actual resources.")
    p.add_argument("--update-threads", type=int, help="Factorio update-runner threads for this world (1..64)")
    p.add_argument("--cpu-affinity", help="taskset CPU list for this world (for example 0-3,8)")
    p.add_argument("--nice", type=int, default=0, help="Lower this server's CPU priority by 0..19")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1 <= args.decisions <= 100 or not 60 <= args.measure_ticks <= 18000:
        parser().error("decisions must be 1..100 and measurement 60..18000 ticks")
    if args.action == "prepare":
        print(json.dumps(prepare(args)))
        return 0
    root = owned_root(args.root)
    if args.action == "stop":
        print(manager(root, "stop"))
        return 0
    if args.action == "run" and not args.checkpoint:
        parser().error("run requires --checkpoint")
    if args.action == "replay" and not args.option_id:
        parser().error("replay requires --option-id; this is a mechanics test, not a model run")
    if args.action == "run":
        config = json.loads((args.checkpoint / "rl_agent_config.json").read_text())
        require_input_profile(config, {"observation_digest": {"saved_world": {"version": PROFILE}}})
    adapter = SavedWorldAdapter(root)
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)
    journal = evidence / "decisions.jsonl"
    model = None
    history = []
    measurement = None
    try:
        for index in range(args.decisions if args.action == "run" else 1):
            if args.action in {"run", "replay"}:
                adapter.reconcile()
            facts = adapter.query()
            candidates, pruned = adapter.options(facts, args.seed + index)
            observation = {"facts": facts, "candidates": candidates, "pruning": pruned, "adapter_hashes": adapter.code_hashes}
            (evidence / f"observation-{facts['tick']}.json").write_text(json.dumps(observation, indent=2) + "\n")
            if args.action == "observe":
                print(json.dumps({"tick": facts["tick"], "profile": PROFILE,
                                  "options": [{"id": c["option_id"], "summary": c["summary"]} for c in candidates]}, indent=2))
                return 0
            if args.action == "replay":
                selected = args.option_id
                record = {"authority": "mechanics_replay", "comparison_eligible": False, "selected": selected}
            else:
                from training.laya_runtime import LayaModelPredictor
                from training.laya_runner import checkpoint_model_ref
                from training.laya_workflow import LayaWorkflowDirector
                state = decision_state(facts, candidates, root.name, checkpoint_model_ref(args.checkpoint), args.objective, history, measurement)
                # Validate this new domain before loading the GPU or executing.
                config = json.loads((args.checkpoint / "rl_agent_config.json").read_text())
                require_input_profile(config, state)
                if model is None:
                    model = LayaWorkflowDirector(LayaModelPredictor(args.checkpoint, base_model_dir=args.base_model),
                                                 budget_profile="director-2048")
                decision = model.decide("select_candidate", state, state["options"])
                selected = decision["selected_option"]["option_id"]
                record = {"authority": "disposable_saved_world", "comparison_eligible": False,
                          "decision": decision, "selected": selected}
            # Persist exact selection and available alternatives before mutation.
            record.update(tick=facts["tick"], candidates=candidates, profile=PROFILE, status="pending")
            with journal.open("a") as out:
                out.write(json.dumps(record) + "\n")
            receipt = adapter.execute(selected, candidates, args.measure_ticks)
            measurement = receipt["measurement"]
            record.update(status=receipt["status"], measurement=receipt["measurement"])
            with journal.open("a") as out:
                out.write(json.dumps(record) + "\n")
            history.append({"decision_id": f"decision.{index}", "tick": facts["tick"], "summary": selected, "outcome": "pending"})
            print(json.dumps({"selected": selected, "authority": record["authority"], "measurement": receipt["measurement"]}))
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SavedWorldError, ValueError) as error:
        print(f"saved-world test stopped: {error}", file=sys.stderr)
        raise SystemExit(2)
