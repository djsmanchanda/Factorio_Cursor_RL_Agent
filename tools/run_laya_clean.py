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
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root
from training.laya_checkpoint import load_bundle, export_bundle, restore_context

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
    p.add_argument("--actions", choices=["geometry", "deterministic"], default="geometry",
                   help="deterministic: Laya chooses among the deterministic runtime's operations "
                        "(training/laya_ops_runner.py); geometry: Laya's own build generators")
    p.add_argument("--policy", choices=["guided", "exploration"], default="guided",
                   help="guided: doc-rule preferences + exploration floor; exploration: uniform")
    p.add_argument("--variant", type=Path, help="Variant JSON (docs/rl/laya_variants/); default: the current workflow")
    p.add_argument("--until-phase", help="Experiment window: stop once this phase is reached")
    p.add_argument("--max-game-ticks", type=int, help="Experiment window: stop after this many game ticks")
    p.add_argument("--research-target", help="Opt-in level-aware research objective")
    p.add_argument("--fixed-science-target", action="store_true", help="Keep the initial research science target")
    p.add_argument("--research-sustain-ticks", type=int, default=18000)
    p.add_argument("--experiment", help="Experiment id recorded with the run")
    p.add_argument("--source-save", type=Path, default=SOURCE_SAVE)
    p.add_argument("--mods-source", type=Path)
    p.add_argument("--resume-bundle", type=Path, help="Hash-verified earned C1 checkpoint")
    p.add_argument("--export-checkpoint", type=Path, help="Save an earned C1 checkpoint after a successful run")
    p.add_argument("--restart-checkpoint", type=Path, help="Earned restart-conditioned ops bundle; fresh policy RNG")
    p.add_argument("--checkpoint-catalog", type=Path, help="Publish earned ops restart bundles between operations")
    p.add_argument("--capture-checkpoints", nargs='+', choices=[f'C{i}' for i in range(1, 7)],
                   help="Limit setup capture to these strict checkpoints")
    p.add_argument("--strict-target", choices=[f"C{i}" for i in range(1, 7)])
    p.add_argument("--validate-reload-only", action="store_true")
    p.add_argument("--reload-receipt", type=Path)
    p.add_argument("--checkpoint-bank", type=Path)
    p.add_argument("--mod-migration-contract", type=Path)
    p.add_argument("--jumpstart", action="store_true",
                   help="Restart a fresh controller on this already-running world after a stall or failure "
                        "(experiment): carry only earlier segments' lifecycle receipts")
    args = p.parse_args(argv)
    root = owned_root(args.root)
    migration = json.loads(args.mod_migration_contract.read_text()) if args.mod_migration_contract else None
    if args.validate_reload_only and not args.restart_checkpoint:
        p.error("reload validation requires --restart-checkpoint")
    resume=load_bundle(args.resume_bundle) if args.resume_bundle else None
    if args.restart_checkpoint and (args.resume_bundle or args.jumpstart or args.actions != "deterministic" or args.stepped):
        p.error("Restart checkpoints require live deterministic operations and no other resume mode")
    if args.checkpoint_catalog and (args.actions != "deterministic" or args.stepped):
        p.error("Ops checkpoint publication requires live deterministic operations")
    if args.capture_checkpoints and not args.checkpoint_catalog:
        p.error("Checkpoint selection requires a publication catalog")
    restart_meta = None
    if args.restart_checkpoint:
        from training.laya_restart_checkpoint import load as load_restart
        restart_meta, restart_context = load_restart(args.restart_checkpoint)
    if args.action == "prepare":
        extra = ["--mods-source", str(args.mods_source)] if args.mods_source else []
        source=(args.restart_checkpoint/'world.zip' if restart_meta else
                args.resume_bundle/'source.zip' if resume else args.source_save)
        print(saved_world("prepare", root, "--source-save", str(source),
                          "--game-port", str(args.game_port), "--rcon-port", str(args.rcon_port), *extra))
        from tools.checkpoint_catalog import atomic_json
        manifest = json.loads((root / 'manifest.json').read_text())
        if restart_meta:
            lineage = {'origin': 'segment', 'source_sha256': restart_meta['world']['sha256'],
                       'parent_generation': restart_meta['generation_id'],
                       'ancestry': restart_context.get('lineage')}
        elif manifest['source_sha256'] == file_hash(SOURCE_SAVE):
            lineage = {'origin': 'C0', 'source_sha256': manifest['source_sha256']}
        else:
            lineage = {'origin': 'unverified', 'source_sha256': manifest['source_sha256']}
        atomic_json(root / 'manifest.json', {**manifest, 'checkpoint_lineage': lineage})
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
    if args.research_target:
        fields = {field.name for field in dataclasses.fields(settings)}
        required = {'research_target', 'research_progressive', 'research_sustain_ticks'}
        if args.actions != 'deterministic' or args.research_sustain_ticks < 300 or not required <= fields:
            p.error('research requires deterministic actions, a supported runner and sustain window >=300 ticks')
        settings = dataclasses.replace(settings, research_target=args.research_target,
                                       research_progressive=not args.fixed_science_target,
                                       research_sustain_ticks=args.research_sustain_ticks)
    elif args.fixed_science_target:
        p.error('fixed science target requires --research-target')
    speed = float(runtime.get("speed", args.speed))
    tag = {"variant_id": variant["variant_id"], "variant_hash": variant_hash(variant), "experiment": args.experiment}
    paths = runner_paths(root)
    paths["journal"].parent.mkdir(exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    source_hash = file_hash(SOURCE_SAVE)
    run_id = f"{root.name}-s{args.seed}-{int(time.time())}"
    mode = "stepped" if args.stepped else "live"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    started = local_stamp()
    log = RunLog(paths["log"], paths["heartbeat"])
    summary = {"decisions": 0, "failures": 0, "phases_reached": [], "stop": "runner_error"}
    with runner_pid_record(paths["pid"]):
        adapter = SavedWorldAdapter(root) if args.stepped else LiveWorld(root, speed)
        from training.laya_restart_checkpoint import ownership_complete, ownership_defect, persist_rebalanced_material
        adapter.checkpoint_ownership_complete = lambda session: ownership_complete(adapter, session)
        adapter.checkpoint_ownership_defect = lambda session: ownership_defect(adapter, session)
        adapter.checkpoint_sync_ownership = persist_rebalanced_material
        manifest = json.loads((root / "manifest.json").read_text())
        log.start(command="run", runner="laya-clean", run_id=run_id, commit=commit, mode=mode, speed=speed,
                  decisions=args.decisions or "unlimited", policy=args.policy, seed=args.seed,
                  variant=f"{tag['variant_id']} {tag['variant_hash'][:19]}", experiment=args.experiment or "-",
                  window=f"until={settings.until_phase or '-'} max_ticks={settings.max_game_ticks or '-'}",
                  game=f"127.0.0.1:{manifest['game_port']}", root=root, log=paths["log"])
        if not args.no_helper:  # Live observer from RUN START; it writes the final review after RUN END.
            log("LAYA HELPER: observing " + launch_helper(run_id, paths["log"], root.resolve()))
        try:
            if not args.stepped and not restart_meta:
                adapter.go_live()
                log(f"LIVE: game unpaused at speed {speed:g}; decisions are taken while the factory runs")
            policy = (GuidedPolicy(args.seed, preferences=load_preferences(ROOT / settings.catalog), **policy_options)
                      if args.policy == "guided" else ExplorationPolicy(args.seed))
            if args.actions == "deterministic":
                from training.laya_ops_runner import lifecycle_receipts, run as run_ops
                carried = (lifecycle_receipts(paths["journal"].parent / "decision-states.jsonl")
                           if args.jumpstart else [])
                session, context = None, {}
                if restart_meta:
                    from training.laya_restart_checkpoint import restore as restore_restart
                    from training.laya_ops import DeterministicSession
                    restored, context = restore_restart(args.restart_checkpoint, root, migration_contract=migration)
                    session = DeterministicSession(root, origin_episode_id=restored.episode_id, emit=log)
                    session.restore_restart_acceptance(restored)
                    carried = context["lifecycle"]
                    from training.laya_restart_checkpoint import validate_restored
                    from tools.checkpoint_catalog import atomic_json
                    receipt = validate_restored(adapter, session, args.restart_checkpoint, migration_contract=migration)
                    atomic_json(args.reload_receipt or root / 'evidence/reload-receipt.json', receipt)
                    if args.checkpoint_bank:
                        from tools.laya_checkpoint_bank import record_validation
                        record_validation(args.checkpoint_bank, restart_meta['generation_id'], receipt)
                    if args.validate_reload_only:
                        print(json.dumps(receipt))
                        return 0
                if restart_meta:
                    adapter.go_live()
                    log("RESTART BENCHMARK: restored strict baseline and ownership; fresh controller/RNG")
                published = set()
                capture_set = set(args.capture_checkpoints or [f'C{i}' for i in range(1, 7)])
                def publish(adapter, session, statuses, summary, results):
                    from training.laya_restart_checkpoint import capture as capture_restart, paused_candidates
                    from training.laya_segment_evidence import current_predicates
                    import shutil
                    current, _ = current_predicates(session)
                    if not any(k in capture_set and v is True and k not in published for k, v in current.items()):
                        return
                    with paused_candidates(adapter, session, summary, run_id) as current:
                        for checkpoint, passed in current.items():
                            if checkpoint not in capture_set or passed is not True or checkpoint in published:
                                continue
                            published.add(checkpoint)  # Never retry an unexplained capture failure.
                            capture_started = time.monotonic()
                            event = {'checkpoint':checkpoint, 'run_id':run_id, 'status':'failed',
                                     'started_at':local_stamp(), 'decisions':summary['decisions']}
                            try:
                                bundle = capture_restart(adapter, session, checkpoint, summary, results,
                                                         run_id=run_id, already_paused=True)
                                target = args.checkpoint_catalog / bundle.name
                                args.checkpoint_catalog.mkdir(parents=True, exist_ok=True)
                                staging = target.with_name("."+target.name+".staging")
                                shutil.copytree(bundle, staging)
                                staging.rename(target)
                                if args.checkpoint_bank:
                                    from tools.laya_checkpoint_bank import register
                                    register(args.checkpoint_bank, target)
                                log(f"CHECKPOINT: {checkpoint} {target}")
                                event.update(status='captured_candidate', generation=bundle.name, bundle=str(target))
                            except Exception as error:
                                log(f"CHECKPOINT FAILED: {checkpoint} {type(error).__name__}: {error}")
                                event['error'] = f'{type(error).__name__}: {error}'
                                stop.set()  # A new setup needs diagnosis before any further capture.
                                break
                            finally:
                                event['duration_seconds'] = time.monotonic()-capture_started
                                with (root/'evidence/capture-events.jsonl').open('a') as stream:
                                    stream.write(json.dumps(event)+'\n')
                summary = run_ops(adapter, policy, journal=paths["journal"], decisions=args.decisions or None,
                                  measure_ticks=args.measure_ticks, seed=args.seed, run_id=run_id, emit=log,
                                  should_stop=stop.is_set, status_path=paths["status"], settings=settings,
                                  carried=carried, session=session,
                                  initial_reached=tuple(context.get("phases_reached", ())),
                                  strict_target=args.strict_target,
                                  loaded_tick=(restart_meta['capture_tick'] if restart_meta else
                                               json.loads((root / 'initial-state.json').read_text())['tick']),
                                  checkpoint_callback=publish if args.checkpoint_catalog else None)
            else:
                summary = run(adapter, policy, journal=paths["journal"],
                              decisions=args.decisions or None, measure_ticks=args.measure_ticks, seed=args.seed,
                              run_id=run_id, emit=log, should_stop=stop.is_set, status_path=paths["status"],
                              settings=settings, initial_reached=resume['phases_reached'] if resume else ())
            if args.export_checkpoint:
                export_bundle(adapter,args.export_checkpoint,summary)
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
             "ended": local_stamp(), "summary": summary, "seed": args.seed, "speed": speed, **tag,
             "window": {"until_phase": settings.until_phase, "max_game_ticks": settings.max_game_ticks}}
    if resume:index['resume_checkpoint']={'path':str(args.resume_bundle),'save_sha256':resume['files']['source.zip']}
    if restart_meta:
        index['restart_checkpoint'] = {'path': str(args.restart_checkpoint),
            'generation': restart_meta['generation_id'], 'origin': restart_meta['checkpoint_id'],
            'loaded_tick': restart_meta['capture_tick'], 'world_sha256': restart_meta['world']['sha256'],
            'continuation_choices': context['continuation_choices'],
            'migration_contract_sha256': context.get('migration_contract_sha256')}
    with RUN_INDEX.open("a") as out:
        out.write(json.dumps(index) + "\n")
    try:
        entry = record_laya_run(index)
        print(f"RUN LEDGER: {entry['outcome']} highest={entry['highest_milestone']} -> {LEDGER}")
    except Exception as error:  # The ledger is bookkeeping; it never fails a run.
        print(f"RUN LEDGER: not recorded: {error}")
    print(json.dumps({"run_id": run_id, "journal": str(paths["journal"]), **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
