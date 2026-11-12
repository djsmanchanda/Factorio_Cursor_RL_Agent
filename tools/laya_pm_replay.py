#!/usr/bin/env python3
# Path: tools/laya_pm_replay.py | Purpose: Validate C4 replay provenance and distinguish actual PM consumption from generic C6 passes.
"""Read-only gates for the C4-to-PM diagnostic fixture; never launches a world."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.laya_checkpoint_bank import validated_receipts
from training.laya_restart_checkpoint import load


def preflight(bank: Path, generation: str) -> dict:
    if not generation or Path(generation).name != generation or generation.startswith('.'):
        raise ValueError('Invalid checkpoint generation')
    bundle = bank/'generations'/generation
    metadata, _ = load(bundle)
    if metadata['checkpoint_id'] != 'C4' or metadata['predicate']['version'] != 'c4-v1':
        raise ValueError('PM replay needs a verified C4 bundle with a compatible origin predicate')
    receipts = validated_receipts(bank, generation)
    if not receipts:
        raise ValueError('PM replay needs a hash-bound disposable reload validation')
    return {'kind': 'laya-pm-diagnostic-preflight-v1', 'bundle': str(bundle.resolve()),
            'generation': generation, 'capture_tick': metadata['capture_tick'],
            'world_sha256': metadata['world']['sha256'], 'reload_receipts': len(receipts),
            'acceptance_eligible': metadata.get('acceptance_eligible') is True,
            'evidence_scope': 'diagnostic_replay_only',
            'next_gate': 'fresh target-code reload, PM placement receipt, then c6-v3 PM consumption'}


def consumption(world: Path) -> dict:
    manifest = json.loads((world/'manifest.json').read_text())
    if manifest.get('production_authority') is not False:
        raise ValueError('PM replay evidence must come from an explicitly disposable world')
    matches = []
    with (world/'evidence/clean-decisions.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('event') != 'strict_current':
                continue
            predicate = (row.get('predicate_evidence') or {}).get('C6') or {}
            count = ((predicate.get('evidence') or {}).get('machine_consumption') or {}).get('productivity-module')
            if (predicate.get('predicate_version') == 'c6-v3'
                    and (row.get('statuses') or {}).get('C6') is True
                    and type(count) is int and count > 0):
                matches.append({'tick': row.get('tick'), 'run_id': row.get('run_id'), 'crafts': count})
    return {'kind': 'laya-pm-consumption-v1', 'world': str(world.resolve()),
            'pm_consumption_verified': bool(matches), 'samples': matches,
            'evidence_scope': 'diagnostic_replay_only',
            'placement_receipt_required': True, 'automatic_adoption': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('preflight')
    prepare.add_argument('--bank', type=Path, required=True)
    prepare.add_argument('--generation', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--world', type=Path, required=True)
    replay = commands.add_parser('replay')
    replay.add_argument('--bank', type=Path, required=True)
    replay.add_argument('--generation', required=True)
    replay.add_argument('--root', type=Path, required=True)
    replay.add_argument('--mods-source', type=Path, required=True)
    replay.add_argument('--game-port', type=int, required=True)
    replay.add_argument('--rcon-port', type=int, required=True)
    replay.add_argument('--deadline', type=float, required=True, help='epoch seconds; the world stops by then')
    replay.add_argument('--max-steps', type=int, default=40)
    replay.add_argument('--execute', action='store_true',
                        help='boot the disposable world; without it the plan is validated only')
    reload_cmd = commands.add_parser('reload')
    reload_cmd.add_argument('--root', type=Path, required=True)
    reload_cmd.add_argument('--expected-tick', type=int, required=True)
    args = parser.parse_args(argv)
    if args.action == 'preflight':
        result = preflight(args.bank, args.generation)
        print(json.dumps(result, indent=2))
        return 0
    if args.action == 'verify':
        result = consumption(args.world)
        print(json.dumps(result, indent=2))
        return 0 if result['pm_consumption_verified'] else 1
    if args.action == 'reload':
        result = reload_receipt(args.root, args.expected_tick)
        print(json.dumps(result, indent=2))
        return 0 if result['reload_verified'] else 1
    try:
        plan = plan_replay(args.bank, args.generation, args.root, game_port=args.game_port,
                           rcon_port=args.rcon_port, deadline=args.deadline, planned=1)
    except ReplayError as error:
        print(json.dumps({'kind': 'laya-pm-replay-blocked-v1', 'blocker': str(error)}, indent=2))
        return 2
    if not args.execute:
        print(json.dumps({**plan, 'mode': 'dry_run', 'world_touched': False}, indent=2))
        return 0
    prepared = materialize(args.bank/'generations'/args.generation, args.root,
                           mods_source=args.mods_source, game_port=args.game_port,
                           rcon_port=args.rcon_port)
    reload_info = reload_receipt(args.root, int(plan['capture_tick']))
    if not reload_info['reload_verified']:
        print(json.dumps({**reload_info, 'prepared': prepared}, indent=2))
        return 3
    from orchestrator.operation_session import DeterministicSession
    session = DeterministicSession(args.root, emit=lambda line: print(line, flush=True))
    evidence = args.root/'evidence/pm-replay.jsonl'
    try:
        summary = drive(session, evidence, max_steps=max(1, args.max_steps),
                        stock_reader=lambda s: ((s.economics() or {}).get('stock') or {}).get('network') or {},
                        consumption_probe=lambda: consumption(args.root))
    finally:
        session.close()
    print(json.dumps({**summary, 'prepared': prepared, 'reload': reload_info}, indent=2))
    return 0 if summary['pm_evidence_complete'] else 4




PM_ITEM = "productivity-module"
PM_MACHINE = "assembling-machine-2"


class ReplayError(RuntimeError):
    """A replay precondition failed; never launch past this."""


def pm_cells(economics: dict) -> list[dict]:
    return [cell for cell in (economics or {}).get("cells") or []
            if isinstance(cell, dict) and cell.get("recipe") == PM_ITEM]


def stock_delta(before: dict, after: dict) -> dict[str, int]:
    keys = set(before) | set(after)
    return {key: int(after.get(key, 0)) - int(before.get(key, 0))
            for key in keys if int(after.get(key, 0)) != int(before.get(key, 0))}


def pm_bill(outcome: dict) -> dict:
    details = outcome.get("details") or {}
    return {"stage": details.get("stage"),
            "required": details.get("required") or {},
            "available": details.get("available") or {},
            "short": {name: int(count) - int((details.get("available") or {}).get(name, 0))
                      for name, count in (details.get("required") or {}).items()
                      if int(count) > int((details.get("available") or {}).get(name, 0))}}


def drive(session, evidence_path: Path, *, max_steps: int = 40,
          stock_reader=lambda session: {}, consumption_probe=None) -> dict:
    """Run the existing production_service operation until PM places and consumes.

    ``session`` is the real DeterministicSession in live replay, or a fake with
    the same ``run`` / ``economics`` surface in tests. Every step is journaled
    with the selected operation, its typed outcome, the exact bill, stock
    changes and the PM-cell census. No priority rule is added here: the caller
    fixes the first demonstrated failure, not an unfunded-bill label.
    """
    steps = []
    placed = False
    consumed = False
    for index in range(1, max_steps + 1):
        before = dict(stock_reader(session) or {})
        outcome = session.run("production_service", item=PM_ITEM, stock_target=1,
                              minimum_machines=1, upgrade_bootstrap=True,
                              allow_promotion=False, machine_cap=1)
        after = dict(stock_reader(session) or {})
        cells = pm_cells(session.economics())
        step = {"step": index, "operation": "production_service", "item": PM_ITEM,
                "outcome": outcome.get("code"), "ok": outcome.get("ok"),
                "message": outcome.get("message"), "bill": pm_bill(outcome),
                "stock_delta": stock_delta(before, after), "pm_cells": len(cells),
                "pm_machines": sum(int(cell.get("machines") or 0) for cell in cells)}
        steps.append(step)
        placed = placed or bool(cells)
        if placed and consumption_probe is not None:
            probe = consumption_probe() or {}
            consumed = bool(probe.get("pm_consumption_verified"))
            step["pm_consumption"] = probe.get("samples") or []
            if consumed:
                break
        if not placed and outcome.get("code") == "production_prerequisite_deferred":
            # A deferral is not progress by itself; keep the bounded budget and
            # let the next step re-observe. The journal records every attempt.
            continue
    summary = {"kind": "laya-pm-replay-v1", "steps": steps,
               "pm_placed": placed, "pm_consumption_verified": consumed,
               "pm_evidence_complete": bool(placed and consumed),
               "evidence_scope": "diagnostic_replay_only", "automatic_adoption": False}
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("a") as stream:
        for step in steps:
            stream.write(json.dumps({"event": "pm_replay_step", **step}) + "\n")
        stream.write(json.dumps({"event": "pm_replay_summary", **{k: v for k, v in summary.items()
                                                                  if k != "steps"}}) + "\n")
    return summary


def plan_replay(bank: Path, generation: str, root: Path, *, game_port: int, rcon_port: int,
                deadline: float, planned: int = 1, now: float | None = None) -> dict:
    """Validate every launch precondition without touching a world."""
    import time as _time
    now = _time.time() if now is None else now
    info = preflight(bank, generation)
    if root.exists():
        raise ReplayError("replay root must be fresh; existing worlds are preserved")
    for name, base in (("game", game_port), ("rcon", rcon_port)):
        if not 1 <= base <= 65535 or base + planned - 1 > 65535:
            raise ReplayError(f"{name} port range {base}..{base + planned - 1} is outside 1..65535")
    if game_port == rcon_port:
        raise ReplayError("game and rcon ports must be distinct")
    if deadline <= now:
        raise ReplayError("replay deadline is not in the future")
    return {**info, "kind": "laya-pm-replay-plan-v1", "root": str(root),
            "game_port": game_port, "rcon_port": rcon_port,
            "deadline": deadline, "max_steps": 40,
            "next_gate": "fresh C4 reload on candidate code/mods, then PM placement and c6-v3 PM consumption"}


def materialize(bundle: Path, root: Path, *, mods_source: Path, game_port: int, rcon_port: int,
                nice: int = 10) -> dict:
    """Copy the C4 world to a fresh disposable root and boot it via the saved-world interface.

    This is the only live step in the driver and is called by the CLI only with
    ``--execute``; it never touches an existing world or campaign.
    """
    import shutil
    import subprocess
    if root.exists():
        raise ReplayError("replay root must be fresh; existing worlds are preserved")
    source = bundle / "world.zip"
    if not source.is_file():
        raise ReplayError("checkpoint bundle has no world.zip")
    root.mkdir(parents=True)
    shutil.copy2(source, root / "baseline.zip")
    command = [sys.executable, str(ROOT / "tools/run_laya_saved_world.py"), "prepare",
               "--root", str(root), "--source-save", str(root / "baseline.zip"),
               "--mods-source", str(mods_source), "--game-port", str(game_port),
               "--rcon-port", str(rcon_port), "--nice", str(nice)]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def reload_receipt(root: Path, expected_tick: int) -> dict:
    """Read the prepared world's tick and compare it with the captured C4 tick."""
    from training.laya_saved_world import SavedWorldAdapter
    adapter = SavedWorldAdapter(root)
    try:
        facts = adapter.query()
    finally:
        adapter.close()
    tick = int(facts.get("tick") or -1)
    return {"kind": "laya-pm-reload-v1", "world": str(root), "expected_tick": expected_tick,
            "observed_tick": tick, "reload_verified": tick == expected_tick,
            "evidence_scope": "diagnostic_replay_only"}


if __name__ == '__main__':
    raise SystemExit(main())
