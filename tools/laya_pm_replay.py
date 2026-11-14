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


def _empty_stream():
    import io
    return io.StringIO("")


def consumption(world: Path) -> dict:
    manifest = json.loads((world/'manifest.json').read_text())
    if manifest.get('production_authority') is not False:
        raise ValueError('PM replay evidence must come from an explicitly disposable world')
    matches = []
    journal = world/'evidence/clean-decisions.jsonl'
    with journal.open() if journal.is_file() else _empty_stream() as stream:
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
    source = "clean_decisions"
    if not matches:
        sidecar = world / "evidence/strict.json"
        if sidecar.is_file():
            side = sidecar_consumption(sidecar)
            matches = side["samples"]
            source = "strict_sidecar"
    return {'kind': 'laya-pm-consumption-v1', 'world': str(world.resolve()), 'source': source,
            'pm_consumption_verified': bool(matches), 'samples': matches,
            'evidence_scope': 'diagnostic_replay_only',
            'placement_receipt_required': True, 'automatic_adoption': False}


def replay_report(world: Path) -> dict:
    """One summary for the controller: provenance, placement and PM consumption."""
    provenance = json.loads((world / "provenance.json").read_text()) if (world / "provenance.json").is_file() else None
    placement_path = world / "evidence/pm-placement.json"
    placement = json.loads(placement_path.read_text()) if placement_path.is_file() else None
    reload_path = world / "provenance-reload.json"
    reload_info = json.loads(reload_path.read_text()) if reload_path.is_file() else None
    try:
        use = consumption(world)
    except (OSError, ValueError) as error:
        use = {"pm_consumption_verified": False, "error": str(error)}
    complete = bool(placement and use.get("pm_consumption_verified"))
    return {"kind": "laya-pm-replay-report-v1", "world": str(world.resolve()),
            "provenance": provenance, "reload": reload_info,
            "reload_verified": bool(reload_info and reload_info.get("reload_verified")),
            "placement": placement, "consumption": use,
            "pm_evidence_complete": complete,
            "evidence_scope": "diagnostic_replay_only", "automatic_adoption": False}


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
    replay.add_argument('--engine-binary', type=Path,
                        default=Path('/home/miniship/.local/share/factorio-rl/runtime/factorio-2.1.24/bin/x64/factorio'))
    replay.add_argument('--require-idle-host', action='store_true',
                        help='also require that no Laya cohort unit is active (strict mode)')
    replay.add_argument('--min-available-gb', type=float, default=REPLAY_MIN_AVAILABLE_GB,
                        help='refuse to boot below this much free memory')
    replay.add_argument('--execute', action='store_true',
                        help='boot the disposable world; without it the plan is validated only')
    report = commands.add_parser('report')
    report.add_argument('--world', type=Path, required=True)
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
    if args.action == 'report':
        result = replay_report(args.world)
        print(json.dumps(result, indent=2))
        return 0 if result["pm_evidence_complete"] else 4
    if args.action == 'reload':
        result = reload_receipt(args.root, args.expected_tick)
        print(json.dumps(result, indent=2))
        return 0 if result['reload_verified'] else 1
    try:
        plan = plan_replay(args.bank, args.generation, args.root, game_port=args.game_port,
                           rcon_port=args.rcon_port, deadline=args.deadline,
                           max_steps=args.max_steps, planned=1,
                           mods_source=args.mods_source)
    except ReplayError as error:
        print(json.dumps({'kind': 'laya-pm-replay-blocked-v1', 'blocker': str(error)}, indent=2))
        return 2
    if not args.execute:
        print(json.dumps({**plan, 'mode': 'dry_run', 'world_touched': False}, indent=2))
        return 0
    if getattr(args, "require_idle_host", False):
        try:
            check_host_idle()
        except ReplayError as error:
            print(json.dumps({"kind": "laya-pm-replay-blocked-v1", "blocker": str(error)}, indent=2))
            return 2
    try:
        capacity = check_replay_capacity(game_port=args.game_port, rcon_port=args.rcon_port,
                                         min_available_gb=args.min_available_gb)
    except ReplayError as error:
        print(json.dumps({"kind": "laya-pm-replay-blocked-v1", "blocker": str(error)}, indent=2))
        return 2
    import subprocess as _sp
    engine = engine_receipt(Path(args.engine_binary) if getattr(args, "engine_binary", None)
                            else ROOT/"factorio_bin_placeholder")
    revision = _sp.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    provenance = provenance_receipt(plan, args.bank/'generations'/args.generation,
                                    args.mods_source, engine, revision, planned=1)
    prepared = materialize(args.bank/'generations'/args.generation, args.root,
                           mods_source=args.mods_source, game_port=args.game_port,
                           rcon_port=args.rcon_port, provenance=provenance,
                           runtime_root=args.engine_binary.resolve().parents[2])
    reload_info = reload_receipt(args.root, int(plan['capture_tick']))
    if not reload_info['reload_verified']:
        print(json.dumps({**reload_info, 'prepared': prepared}, indent=2))
        return 3
    # Persist the reload leg now: it reads live state and cannot be recomputed
    # after the disposable server is closed.
    (args.root/'provenance-reload.json').write_text(
        json.dumps({**reload_info, 'prepared': prepared}, indent=2) + "\n")
    from orchestrator.operation_session import DeterministicSession
    trail: list[str] = []

    def _emit(line):
        print(line, flush=True)
        trail.append(line)

    def _read_trail():
        lines = list(trail)
        trail.clear()
        return lines

    session = DeterministicSession(args.root, emit=_emit)
    evidence = args.root/'evidence/pm-replay.jsonl'
    try:
        summary = drive(session, evidence, max_steps=max(1, args.max_steps),
                        deadline=args.deadline,
                        stock_reader=lambda s: ((s.economics() or {}).get('stock') or {}).get('network') or {},
                        message_reader=_read_trail,
                        consumption_probe=lambda: sample_consumption(
                            args.root, args.root/'evidence/strict.json'))
    finally:
        session.close()
    print(json.dumps({**summary, 'prepared': prepared, 'reload': reload_info,
                      'capacity': capacity}, indent=2))
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


def _drain_messages(reader) -> list[str]:
    """Session lines emitted since the last step, when a reader was supplied."""
    if reader is None:
        return []
    try:
        return [str(line) for line in (reader() or [])]
    except Exception:  # noqa: BLE001 -- a broken trail must not gate the replay
        return []


def _journal_messages(stream, index: int, lines: list[str]) -> None:
    for line in lines:
        stream.write(json.dumps({"event": "pm_replay_message", "step": index, "line": line}) + "\n")
    if lines:
        stream.flush()


def drive(session, evidence_path: Path, *, max_steps: int = 40,
          stock_reader=lambda session: {}, consumption_probe=None,
          deadline: float | None = None, clock=None, message_reader=None) -> dict:
    """Run the existing production_service operation until PM places and consumes.

    ``session`` is the real DeterministicSession in live replay, or a fake with
    the same ``run`` / ``economics`` surface in tests. Every step is journaled
    with the selected operation, its typed outcome, the exact bill, stock
    changes and the PM-cell census; when ``message_reader`` supplies the lines
    the session emitted, they are streamed as ``pm_replay_message`` events so a
    live trace can show whether a remedy branch (e.g. cell delivery) ran
    without depending on the unit journal. No priority rule is added here: the
    caller fixes the first demonstrated failure, not an unfunded-bill label.
    """
    import time as _time
    clock = _time.time if clock is None else clock
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    stream = evidence_path.open("a")
    steps = []
    placed = False
    consumed = False
    deadline_reached = False
    for index in range(1, max_steps + 1):
        if deadline is not None and clock() >= deadline:
            deadline_reached = True
            break
        before = dict(stock_reader(session) or {})
        try:
            outcome = session.run("production_service", item=PM_ITEM, stock_target=1,
                                  minimum_machines=1, upgrade_bootstrap=True,
                                  allow_promotion=False, machine_cap=1)
        except BaseException:
            _journal_messages(stream, index, _drain_messages(message_reader))
            stream.close()
            raise
        trail = _drain_messages(message_reader)
        _journal_messages(stream, index, trail)
        after = dict(stock_reader(session) or {})
        cells = pm_cells(session.economics())
        step = {"step": index, "operation": "production_service", "item": PM_ITEM,
                "outcome": outcome.get("code"), "ok": outcome.get("ok"),
                "classification": outcome.get("classification"),
                "state": outcome.get("state"),
                "details": outcome.get("details"),
                "message": outcome.get("message"), "bill": pm_bill(outcome),
                "stock_delta": stock_delta(before, after), "messages": len(trail),
                "pm_cells": len(cells),
                "pm_machines": sum(int(cell.get("machines") or 0) for cell in cells)}
        steps.append(step)
        stream.write(json.dumps({"event": "pm_replay_step", **step}) + "\n")
        stream.flush()
        if cells and not placed:
            placed = True
            receipt_path = evidence_path.parent / "pm-placement.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps({
                "kind": "laya-pm-placement-v1", "step": index,
                "pm_item": PM_ITEM,
                "cells": [{"recipe": cell.get("recipe"), "machines": cell.get("machines"),
                           "network": cell.get("network"), "position": cell.get("position")}
                          for cell in cells],
                "machines": sum(int(cell.get("machines") or 0) for cell in cells),
                "stock_delta": step["stock_delta"], "bill": step["bill"],
                "evidence_scope": "diagnostic_replay_only",
                "placement_receipt": True}, indent=2) + "\n")
            step["placement_receipt"] = str(receipt_path)
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
               "deadline_reached": deadline_reached,
               "pm_placed": placed, "pm_consumption_verified": consumed,
               "placement_receipt": str(evidence_path.parent / "pm-placement.json") if placed else None,
               "pm_evidence_complete": bool(placed and consumed),
               "evidence_scope": "diagnostic_replay_only", "automatic_adoption": False}
    try:
        stream.write(json.dumps({"event": "pm_replay_summary", **{k: v for k, v in summary.items()
                                                                  if k != "steps"}}) + "\n")
    finally:
        stream.close()
    return summary


def check_mods(bundle: Path, mods_source: Path) -> dict:
    """Compare the source tree with the bundle's recorded mod file hashes.

    A C4 reload is only provenance-valid when the candidate boots the same mod
    bytes that produced the capture. Missing or different files are a blocker,
    never a warning.
    """
    import hashlib
    metadata = json.loads((bundle / "checkpoint.json").read_text())
    wanted = ((metadata.get("provenance") or {}).get("mod_hashes") or {})
    if not wanted:
        raise ReplayError("checkpoint bundle records no mod hashes")
    missing, changed = [], []
    for name, sha in wanted.items():
        target = mods_source / name
        if not target.is_file():
            missing.append(name)
            continue
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            changed.append(name)
    if missing or changed:
        raise ReplayError(f"mod source does not match the bundle: {len(missing)} missing, {len(changed)} changed")
    return {"kind": "laya-pm-mods-v1", "mods_source": str(mods_source), "files": len(wanted),
            "mods_verified": True}


def port_is_free(port: int, kind: str = "game") -> bool:
    """Whether Factorio could bind this port right now (UDP game, TCP rcon)."""
    import socket
    sock = socket.socket(socket.AF_INET,
                         socket.SOCK_DGRAM if kind == "game" else socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def plan_replay(bank: Path, generation: str, root: Path, *, game_port: int, rcon_port: int,
                deadline: float, max_steps: int = 40, planned: int = 1,
                now: float | None = None, mods_source: Path | None = None) -> dict:
    """Validate every launch precondition without touching a world."""
    import time as _time
    now = _time.time() if now is None else now
    info = preflight(bank, generation)
    mods = None
    if mods_source is not None:
        mods = check_mods(bank/'generations'/generation, mods_source)
        info = {**info, "mods": mods}
    if root.exists():
        raise ReplayError("replay root must be fresh; existing worlds are preserved")
    for name, base in (("game", game_port), ("rcon", rcon_port)):
        if not 1 <= base <= 65535 or base + planned - 1 > 65535:
            raise ReplayError(f"{name} port range {base}..{base + planned - 1} is outside 1..65535")
    if game_port == rcon_port:
        raise ReplayError("game and rcon ports must be distinct")
    if not port_is_free(game_port, "game"):
        raise ReplayError(f"game port {game_port} is already in use")
    if not port_is_free(rcon_port, "rcon"):
        raise ReplayError(f"rcon port {rcon_port} is already in use")
    if deadline <= now:
        raise ReplayError("replay deadline is not in the future")
    if max_steps < 1:
        raise ReplayError("replay needs at least one decision step")
    return {**info, "kind": "laya-pm-replay-plan-v1", "root": str(root),
            "game_port": game_port, "rcon_port": rcon_port,
            "deadline": deadline, "max_steps": max_steps,
            "next_gate": "fresh C4 reload on candidate code/mods, then PM placement and c6-v3 PM consumption"}


def sidecar_consumption(path: Path) -> dict:
    """Read PM consumption from a strict-sidecar JSON report.

    The replay session does not write runner evidence, so the sidecar is the
    measurement source: a c6-v3 pass is accepted only when the same sample
    carries positive machine_consumption["productivity-module"].
    """
    data = json.loads(path.read_text())
    matches = []
    for row in data.get("strict_current") or []:
        if not isinstance(row, dict):
            continue
        c6 = (row.get("predicate_evidence") or {}).get("C6") or {}
        evidence = c6.get("evidence") or {}
        count = (evidence.get("machine_consumption") or {}).get("productivity-module")
        passed = c6.get("status") == "passed" or (row.get("statuses") or {}).get("C6") is True
        if c6.get("predicate_version") == "c6-v3" and passed and type(count) is int and count > 0:
            matches.append({"tick": row.get("tick"), "run_id": row.get("run_id"), "crafts": count})
    return {"kind": "laya-pm-sidecar-consumption-v1", "report": str(path),
            "pm_consumption_verified": bool(matches), "samples": matches,
            "evidence_scope": "diagnostic_replay_only", "placement_receipt_required": True}


def sample_consumption(root: Path, report: Path, *, python: str | None = None,
                       timeout: float = 180.0, poll: float = 1.0) -> dict:
    """Take one strict sample, then stop the sidecar.

    laya_strict_sidecar runs until SIGTERM by design, so the replay starts it,
    waits for the first atomically written report, terminates it and parses that
    report. Never leave a sampling child behind.
    """
    import subprocess
    import sys as _sys
    import time as _time
    command = [python or _sys.executable, str(ROOT / "tools/laya_strict_sidecar.py"),
               "--world-root", str(root), "--out", str(report)]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    try:
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                data = json.loads(report.read_text())
            except (OSError, ValueError):
                data = None
            if data and data.get("strict_current"):
                break
            _time.sleep(poll)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return sidecar_consumption(report)


_HOST_INFRA = ("dashboard", "scheduler", "cool-recovery", "pm-replay")


def busy_cohort_units(listing: str) -> list[str]:
    """Active laya cohort units from ``systemctl list-units`` output."""
    busy = []
    for line in listing.splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith("laya-") or not fields[0].endswith(".service"):
            continue
        if any(token in fields[0] for token in _HOST_INFRA):
            continue
        busy.append(fields[0])
    return busy


def check_host_idle(run=None) -> list[str]:
    """Refuse to boot a replay while any other Laya cohort unit is active."""
    import subprocess
    run = run or (lambda args: subprocess.check_output(args, text=True))
    listing = run(["systemctl", "--user", "list-units", "laya-*", "--state=active",
                   "--no-legend", "--plain"])
    busy = busy_cohort_units(listing)
    if busy:
        raise ReplayError("host is not idle for a disposable replay: " + ", ".join(busy))
    return []


REPLAY_MIN_AVAILABLE_GB = 8.0


def available_memory_gb(path: Path = Path("/proc/meminfo")) -> float | None:
    """MemAvailable in GiB, or None when the host does not expose it."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1]) / 1024 / 1024
            except (IndexError, ValueError):
                return None
    return None


def check_replay_capacity(*, game_port: int | None = None, rcon_port: int | None = None,
                          min_available_gb: float = REPLAY_MIN_AVAILABLE_GB,
                          memory_reader=available_memory_gb,
                          port_probe=port_is_free) -> dict:
    """Refuse a replay only for reasons that actually break it.

    Requiring the whole host to be idle meant the fixture could rarely start:
    the cohort queue keeps a unit resident almost continuously. A disposable
    replay needs its own ports free and enough memory for one more server; a
    resident cohort is not a blocker. Strict mode stays available via
    ``check_host_idle``.
    """
    if game_port is not None and not port_probe(game_port, "game"):
        raise ReplayError(f"game port {game_port} is already in use")
    if rcon_port is not None and not port_probe(rcon_port, "rcon"):
        raise ReplayError(f"rcon port {rcon_port} is already in use")
    available = memory_reader()
    if available is not None and available < min_available_gb:
        raise ReplayError(f"only {available:.1f} GiB memory available; "
                          f"a replay needs {min_available_gb:.1f} GiB")
    return {"available_gb": available, "min_available_gb": min_available_gb}


def engine_receipt(binary: Path) -> dict:
    """Hash the engine binary and record its self-reported version."""
    import hashlib
    import subprocess
    if not binary.is_file():
        raise ReplayError("engine binary is missing")
    try:
        version = subprocess.check_output([str(binary), "--version"], text=True, timeout=20).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = "unknown"
    return {"binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "version": version}


def provenance_receipt(plan: dict, bundle: Path, mods_source: Path, engine: dict,
                       code_revision: str, planned: int = 1) -> dict:
    """Immutable provenance written inside the disposable world before boot."""
    return {"kind": "laya-pm-replay-provenance-v1",
            "bundle": str(bundle.resolve()), "bundle_sha256": plan.get("world_sha256"),
            "generation": plan.get("generation"), "capture_tick": plan.get("capture_tick"),
            "acceptance_eligible": plan.get("acceptance_eligible"),
            "evidence_scope": "diagnostic_replay_only",
            "mods_source": str(mods_source.resolve()), "mods_verified": True,
            "engine": engine, "code_revision": code_revision,
            "game_port": plan.get("game_port"), "rcon_port": plan.get("rcon_port"),
            "deadline": plan.get("deadline"), "planned_attempts": planned}


def materialize(bundle: Path, root: Path, *, mods_source: Path, game_port: int, rcon_port: int,
                nice: int = 10, provenance: dict | None = None,
                runtime_root: Path | None = None) -> dict:
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
    # run_laya_saved_world.prepare creates the root itself. Stage the save and
    # the provenance beside it so the receipt exists before the world boots;
    # move the receipt in only after a successful prepare.
    staged_save = root.with_name(root.name + ".baseline.zip")
    staged_provenance = root.with_name(root.name + ".provenance.json")
    shutil.copy2(source, staged_save)
    if provenance is not None:
        staged_provenance.write_text(json.dumps(provenance, indent=2) + "\n")
    command = [sys.executable, str(ROOT / "tools/run_laya_saved_world.py"), "prepare",
               "--root", str(root), "--source-save", str(staged_save),
               "--mods-source", str(mods_source), "--game-port", str(game_port),
               "--rcon-port", str(rcon_port), "--nice", str(nice)]
    try:
        import os as _os
        env = dict(_os.environ)
        if runtime_root is not None:
            env["LAYA_CHECKPOINT_RUNTIME_ROOT"] = str(runtime_root)
        result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, env=env)
    finally:
        pass
    if provenance is not None and staged_provenance.exists():
        root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_provenance, root / "provenance.json")
        staged_provenance.unlink()
    staged_save.unlink(missing_ok=True)
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
