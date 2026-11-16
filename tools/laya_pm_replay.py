#!/usr/bin/env python3
# Path: tools/laya_pm_replay.py | Purpose: Validate C4 replay provenance and distinguish actual PM consumption from generic C6 passes.
"""Validate C4-to-PM evidence and run an explicitly requested disposable replay."""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, asdict
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
    rows = []
    journal = world/'evidence/clean-decisions.jsonl'
    with journal.open() if journal.is_file() else _empty_stream() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('event') == 'strict_current':
                rows.append(row)
    parsed, source = pm_samples(rows), "clean_decisions"
    if not parsed["observations"] and not parsed["samples"]:
        sidecar = world / "evidence/strict.json"
        if sidecar.is_file():
            parsed, source = sidecar_consumption(sidecar), "strict_sidecar"
    return {'kind': 'laya-pm-consumption-v2', 'world': str(world.resolve()), 'source': source,
            'pm_consumption_verified': bool(parsed["observations"]),
            'observations': parsed["observations"], 'samples': parsed["samples"],
            'sustained_rises': sustained_rises(parsed["samples"]),
            'evidence_scope': 'diagnostic_replay_only',
            'placement_receipt_required': True, 'automatic_adoption': False}


ACCEPTANCE_VERSION = "pm-sustained-v2"
MIN_RISES = 2
MIN_GAP_TICKS = 3600


def acceptance(placed, samples: list[dict], driver: dict | None = None) -> dict:
    """The one PM-evidence verdict for drive, report and verify.

    Complete needs a placement receipt and ``MIN_RISES`` separated rises of
    cumulative PM crafts (``sustained_rises``). A one-sample window
    observation is separate evidence and never completes. With a driver
    summary: a failed driver is never overridden; a current-version claim of
    success that the measurement does not support is contradictory, so the
    verdict is unknown (None).
    """
    rises = sustained_rises(samples)
    measured = bool(placed) and rises >= MIN_RISES
    claim = None if driver is None else driver.get("pm_evidence_complete")
    if driver is not None and claim is not True:
        complete, reason = False, "driver_not_complete"
    elif measured:
        complete, reason = True, "placed_and_sustained"
    elif driver is not None and driver.get("acceptance_version") == ACCEPTANCE_VERSION:
        complete, reason = None, "driver_claim_not_supported_by_measurement"
    else:
        complete, reason = False, "not_placed" if not placed else "consumption_not_sustained"
    return {"version": ACCEPTANCE_VERSION, "complete": complete, "reason": reason, "placed": bool(placed),
            "rises": rises, "min_rises": MIN_RISES, "min_gap_ticks": MIN_GAP_TICKS}


def replay_summary(world: Path):
    journal = world / "evidence/pm-replay.jsonl"
    if not journal.is_file():
        return None
    result = None
    with journal.open() as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("event") == "pm_replay_summary":
                result = row
    return result


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
        use = {"pm_consumption_verified": False, "samples": [], "error": str(error)}
    summary = replay_summary(world)
    verdict = acceptance(placement is not None, use.get("samples") or [], summary)
    if placement is None and summary is None and "error" in use:
        verdict = {**verdict, "complete": None, "reason": "no_evidence"}
    return {"kind": "laya-pm-replay-report-v2", "world": str(world.resolve()),
            "provenance": provenance, "reload": reload_info,
            "reload_verified": bool(reload_info and reload_info.get("reload_verified")),
            "placement": placement, "consumption": use,
            "replay_summary": summary, "acceptance": verdict,
            "pm_evidence_complete": verdict["complete"],
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
    replay.add_argument('--deadline', type=float, required=True, help='epoch seconds; checked between synchronous operations and during clock waits')
    replay.add_argument('--max-steps', type=int, default=40)
    replay.add_argument('--sample-interval', type=float, default=60.0,
                        help='wall seconds between strict samples of the one persistent sidecar')
    replay.add_argument('--fund-shortages', action='store_true',
                        help='after a material_shortage, run the existing produce operation for each short item')
    replay.add_argument('--funding-depth', type=int, default=FUNDING_DEPTH,
                        help='levels of nested shortage bills to fund in one step (1 = the service bill only)')
    replay.add_argument('--finite-bill-batches', action='store_true',
                        help='select the explicit reuse-only finite operation for supported bill items; journal feasibility')
    replay.add_argument('--restore-sidecars', action='store_true',
                        help="install the bundle's deterministic sidecars and run under its episode id, as a cohort restart does")
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
        report = replay_report(args.world)
        result = {**report["consumption"], "acceptance": report["acceptance"]}
        print(json.dumps(result, indent=2))
        return 0 if report["acceptance"]["complete"] is True else 1
    if args.action == 'report':
        result = replay_report(args.world)
        print(json.dumps(result, indent=2))
        return 0 if result["pm_evidence_complete"] is True else 4
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
    with replay_world(args.bank/'generations'/args.generation, args.root,
                      mods_source=args.mods_source, game_port=args.game_port,
                      rcon_port=args.rcon_port, provenance=provenance,
                      runtime_root=args.engine_binary.resolve().parents[2]) as prepared:
        reload_info = reload_receipt(args.root, int(plan['capture_tick']))
        if not reload_info['reload_verified']:
            print(json.dumps({**reload_info, 'prepared': prepared}, indent=2))
            return 3
        # Persist the reload leg now: it reads live state and cannot be recomputed
        # after the disposable server is closed.
        restored = (restore_sidecars(args.bank/'generations'/args.generation, args.root)
                    if args.restore_sidecars else None)
        (args.root/'provenance-reload.json').write_text(
            json.dumps({**reload_info, 'prepared': prepared, 'restored_sidecars': restored}, indent=2) + "\n")
        from orchestrator.operation_session import DeterministicSession
        trail: list[str] = []

        def _emit(line):
            print(line, flush=True)
            trail.append(line)

        def _read_trail():
            lines = list(trail)
            trail.clear()
            return lines

        from training.laya_clean_runner import LiveWorld
        sampler = StrictSampler(args.root, args.root/'evidence/strict.json',
                                interval=args.sample_interval)
        try:
            summary = run_live_replay(
                reload_info, lambda: LiveWorld(args.root),
                lambda: DeterministicSession(args.root, emit=_emit,
                                             origin_episode_id=restored['episode_id'] if restored else None),
                args.root/'evidence/pm-replay.jsonl', max_steps=max(1, args.max_steps),
                deadline=args.deadline, after_live=sampler.start,
                message_reader=_read_trail, consumption_probe=sampler.probe,
                fund_shortages=args.fund_shortages, funding_depth=max(1, args.funding_depth),
                finite_bill_batches=args.finite_bill_batches,
                observe_after_placement=True)
        finally:
            sampler.close()
        print(json.dumps({**summary, 'prepared': prepared, 'reload': reload_info,
                          'capacity': capacity}, indent=2))
        return 0 if summary['pm_evidence_complete'] else 4




PM_ITEM = "productivity-module"
PM_MACHINE = "assembling-machine-2"
FUNDING_DEPTH = 3  # levels of nested construction bills the replay funds in one step


class ReplayError(RuntimeError):
    """A replay precondition failed; never launch past this."""


def pm_cells(economics: dict) -> list[dict]:
    return [cell for cell in (economics or {}).get("cells") or []
            if isinstance(cell, dict) and cell.get("recipe") == PM_ITEM]


def stock_delta(before: dict, after: dict) -> dict[str, int]:
    keys = set(before) | set(after)
    return {key: int(after.get(key, 0)) - int(before.get(key, 0))
            for key in keys if int(after.get(key, 0)) != int(before.get(key, 0))}


def pm_bill(outcome: dict, stock: dict | None = None) -> dict:
    """The bill of a failed operation, with the network level each short item needs.

    ``stock`` is the network stock read just before the failing operation; as
    in the runner (``result["stock_at"]``), it fixes the level at failure time
    (``training.laya_ops_runner.shortage_levels``: stock then plus the
    shortfall). A ``stock_at`` the outcome already carries wins. Without
    either, ``levels`` stays empty: the level is unknown, never ``required``.
    """
    from training.laya_ops_runner import shortage_levels
    details = outcome.get("details") or {}
    required = details.get("required") or {}
    stock_at = details.get("stock_at", outcome.get("stock_at"))
    if stock_at is None and stock is not None:
        stock_at = {item: int(stock.get(item, 0)) for item in required}
    levels = (shortage_levels([{"operation": "bill", "code": "material_shortage", "details": details,
                                "stock_at": stock_at}]) if stock_at is not None else {})
    return {"stage": details.get("stage"),
            "required": required,
            "available": details.get("available") or {},
            "stock_at": stock_at,
            "levels": levels,
            "short": {name: int(count) - int((details.get("available") or {}).get(name, 0))
                      for name, count in required.items()
                      if int(count) > int((details.get("available") or {}).get(name, 0))}}


@dataclass(frozen=True)
class StockObservation:
    network: dict | None
    tick: int | None = None
    source: str = "stock_reader"
    scope: str = "logistic_networks"


def _valid_stock(stock) -> bool:
    return isinstance(stock, dict) and all(
        isinstance(item, str) and bool(item) and type(count) is int and count >= 0
        for item, count in stock.items())


def _survey_stock(adapter) -> StockObservation:
    """Read the same network survey/projection as the ops runner, never bill affordability."""
    from orchestrator.operation_observation import economics_observation

    tick = None
    try:
        facts = adapter.query()
        if isinstance(facts, dict) and type(facts.get("tick")) is int and facts["tick"] >= 0:
            tick = facts["tick"]
            networks = facts.get("networks")
            if isinstance(networks, dict) and all(
                    isinstance(network, dict) and _valid_stock(network.get("items"))
                    for network in networks.values()):
                stock = economics_observation(None, networks)["stock"]["network"]
                return StockObservation(stock, tick, "saved_world_survey")
    except Exception:  # noqa: BLE001 -- a failed survey is not an empty network
        pass
    return StockObservation(None, tick, "saved_world_survey")


def _read_stock(reader, session) -> StockObservation:
    """Keep failed/malformed reads unknown; injected dict readers have no survey tick."""
    if reader is None:
        return StockObservation(None)
    try:
        stock = reader(session)
    except Exception:  # noqa: BLE001 -- unknown stock stays unknown
        return StockObservation(None)
    if not isinstance(stock, StockObservation):
        stock = StockObservation(stock)
    return StockObservation(dict(stock.network) if _valid_stock(stock.network) else None,
                            stock.tick, stock.source, stock.scope)


class Budget:
    """The one wall-clock and game-tick guard checked before every operation the replay starts.

    The first terminating cause is kept: once spent, nothing new starts (an
    operation already running may finish; synchronous calls cannot be cut).
    """

    def __init__(self, *, deadline, clock, tick_reader, initial_tick: int, max_game_ticks: int):
        self.deadline, self.clock, self.tick_reader = deadline, clock, tick_reader
        self.initial_tick, self.max_game_ticks = initial_tick, max_game_ticks
        self.highest_tick, self.cause = initial_tick, None

    def check(self) -> tuple[str | None, int | None]:
        """(terminating cause or None, the tick read)."""
        if self.cause is not None:
            return self.cause, None
        tick = None
        if self.deadline is not None and self.clock() >= self.deadline:
            self.cause = "deadline_reached"
        else:
            tick = self.tick_reader()
            if tick < self.highest_tick:
                self.cause = "clock_not_advancing"
            elif tick - self.initial_tick >= self.max_game_ticks:
                self.cause = "game_tick_budget_exhausted"
            else:
                self.highest_tick = tick
        return self.cause, tick


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


def run_live_replay(reload_info, adapter_factory, session_factory, evidence_path, *,
                    after_live=None, **kwargs):
    """Start live play only after the paused reload has been verified."""
    if not reload_info.get("reload_verified"):
        raise ReplayError("paused reload was not verified")
    adapter = adapter_factory()
    try:
        session = session_factory()
        try:
            start_tick = adapter.tick()
            if start_tick != reload_info["observed_tick"]:
                raise ReplayError("paused reload tick changed before live startup")
            session.stock_networks = lambda: adapter.query().get("networks")
            kwargs.setdefault("stock_reader", lambda _session: _survey_stock(adapter))
            adapter.go_live()
            if after_live is not None:
                after_live()  # baseline sample of the running world, before any attempt
            return drive(session, evidence_path, tick_reader=adapter.tick,
                         initial_tick=start_tick, **kwargs)
        finally:
            session.close()
    finally:
        adapter.close()


def drive(session, evidence_path: Path, *, max_steps: int = 40,
          stock_reader=None, consumption_probe=None,
          deadline: float | None = None, clock=None, message_reader=None,
          tick_reader=None, initial_tick=None, max_game_ticks: int = 108000,
          stall_seconds: float = 10.0, monotonic=None, sleep=None,
          fund_shortages: bool = False, funding_depth: int = FUNDING_DEPTH,
          finite_bill_batches: bool = False,
          observe_after_placement: bool = False) -> dict:
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
    monotonic = _time.monotonic if monotonic is None else monotonic
    sleep = _time.sleep if sleep is None else sleep
    tick_reader = session.tick if tick_reader is None else tick_reader
    initial_tick = tick_reader() if initial_tick is None else initial_tick
    last_tick = initial_tick
    budget = Budget(deadline=deadline, clock=clock, tick_reader=tick_reader,
                    initial_tick=initial_tick, max_game_ticks=max_game_ticks)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("a") as stream:
        steps = []
        placed = False
        consumed = False
        samples: list[dict] = []
        stop_reason = "attempt_budget_exhausted"
        for index in range(1, max_steps + 1):
            # Each attempt waits for the game clock to advance, under the same budget guard.
            wait_started = monotonic()
            while True:
                cause, tick = budget.check()
                if cause is not None:
                    stop_reason = cause
                    break
                if tick > last_tick:
                    last_tick = tick
                    break
                if monotonic() - wait_started >= stall_seconds:
                    stop_reason = "clock_not_advancing"
                    break
                sleep(min(0.1, stall_seconds))
            if stop_reason != "attempt_budget_exhausted":
                break
            stock_before = _read_stock(stock_reader, session)
            cause, _ = budget.check()
            if cause is not None:
                stop_reason = cause
                break
            try:
                outcome = session.run("production_service", item=PM_ITEM, stock_target=1,
                                      minimum_machines=1, upgrade_bootstrap=True,
                                      allow_promotion=False, machine_cap=1)
            except BaseException:
                _journal_messages(stream, index, _drain_messages(message_reader))
                raise
            trail = _drain_messages(message_reader)
            _journal_messages(stream, index, trail)
            stock_after = _read_stock(stock_reader, session)
            cells = pm_cells(session.economics())
            step = {"step": index, "tick": last_tick, "operation": "production_service", "item": PM_ITEM,
                    "outcome": outcome.get("code"), "ok": outcome.get("ok"),
                    "classification": outcome.get("classification"),
                    "state": outcome.get("state"),
                    "details": outcome.get("details"),
                    "message": outcome.get("message"), "bill": pm_bill(outcome, stock_before.network),
                    "stock_observations": {"before": asdict(stock_before), "after": asdict(stock_after)},
                    "stock_delta": (stock_delta(stock_before.network, stock_after.network)
                                    if stock_before.network is not None and stock_after.network is not None
                                    else None), "messages": len(trail),
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
            if placed and consumption_probe is not None and not observe_after_placement:
                probe = consumption_probe() or {}
                samples = probe.get("samples") or []
                consumed = acceptance(placed, samples)["complete"] is True
                step["pm_consumption"] = samples
                if consumed:
                    stop_reason = "pm_evidence_complete"
                    break
            elif placed and consumption_probe is not None:
                # Placement ends the attempt loop: retrying the service cannot add
                # evidence, and fast retries would spend the budget before a second
                # strict sample. Observe the running machine instead.
                observed = observe_consumption(
                    consumption_probe, tick_reader, last_tick, initial_tick=initial_tick,
                    max_game_ticks=max_game_ticks, deadline=deadline, clock=clock,
                    monotonic=monotonic, sleep=sleep, stall_seconds=max(stall_seconds, 30.0))
                samples = observed["samples"]
                step["pm_consumption"] = samples
                stream.write(json.dumps({"event": "pm_replay_observation", "step": index,
                                         **{k: v for k, v in observed.items()}}) + "\n")
                stream.flush()
                consumed = acceptance(placed, samples)["complete"] is True
                last_tick = observed["last_tick"]
                stop_reason = observed["outcome"]
                break
            if fund_shortages and outcome.get("code") == "material_shortage":
                step["funding"] = _fund_shortage(session, stream, index, step["bill"], message_reader,
                                                 max_depth=funding_depth, budget=budget,
                                                 stock_reader=stock_reader, finite_bill_batches=finite_bill_batches)
                if budget.cause is not None:
                    stop_reason = budget.cause
                    break
            if not placed and outcome.get("code") == "production_prerequisite_deferred":
                # A deferral is not progress by itself; keep the bounded budget and
                # let the next step re-observe. The journal records every attempt.
                continue
        verdict = acceptance(placed, samples)
        summary = {"kind": "laya-pm-replay-v1", "steps": steps,
                   "deadline_reached": stop_reason == "deadline_reached", "outcome": stop_reason,
                   "clock": {"initial_tick": initial_tick, "last_advanced_tick": last_tick,
                             "max_game_ticks": max_game_ticks, "stall_seconds": stall_seconds},
                   "pm_placed": placed, "pm_consumption_verified": consumed,
                   "placement_receipt": str(evidence_path.parent / "pm-placement.json") if placed else None,
                   "pm_evidence_complete": verdict["complete"] is True,
                   "acceptance_version": ACCEPTANCE_VERSION, "acceptance": verdict,
                   "evidence_scope": "diagnostic_replay_only", "automatic_adoption": False}
        stream.write(json.dumps({"event": "pm_replay_summary", **{k: v for k, v in summary.items()
                                                                  if k != "steps"}}) + "\n")
        return summary


def sustained_rises(samples: list[dict], min_gap_ticks: int = MIN_GAP_TICKS) -> int:
    """Separated positive increments of cumulative PM crafts.

    Each sample's ``crafts`` is a cumulative count within its ``generation``
    (one strict measurement; a new generation starts again from zero). A rise
    is a sample whose count exceeds the previous sample of its generation,
    while C6 holds (``c6_passed``, default true), at least ``min_gap_ticks``
    after the last counted rise. Samples before C6 holds still advance the
    base, so crafts made before the predicate was eligible never count.
    """
    rises, last_rise, previous = 0, None, {}
    for sample in sorted(samples, key=lambda row: int(row.get("tick") or 0)):
        tick, crafts = int(sample.get("tick") or 0), int(sample.get("crafts") or 0)
        generation = sample.get("generation")
        if (sample.get("c6_passed", True) and crafts > previous.get(generation, 0)
                and (last_rise is None or tick - last_rise >= min_gap_ticks)):
            rises, last_rise = rises + 1, tick
        previous[generation] = max(previous.get(generation, 0), crafts)
    return rises


def observe_consumption(probe, tick_reader, last_tick: int, *, initial_tick: int,
                        max_game_ticks: int, deadline: float | None, clock, monotonic, sleep,
                        stall_seconds: float = 30.0, poll: float = 5.0,
                        min_rises: int = MIN_RISES, min_gap_ticks: int = MIN_GAP_TICKS) -> dict:
    """After PM placement, read the persistent strict report until consumption rises twice."""
    outcome, samples, moved = "deadline_reached", [], monotonic()
    while deadline is None or clock() < deadline:
        tick = tick_reader()
        if tick < last_tick:
            outcome = "clock_not_advancing"
            break
        if tick > last_tick:
            last_tick, moved = tick, monotonic()
        elif monotonic() - moved >= stall_seconds:
            outcome = "clock_not_advancing"
            break
        samples = (probe() or {}).get("samples") or []
        if sustained_rises(samples, min_gap_ticks) >= min_rises:
            outcome = "pm_evidence_complete"
            break
        if tick - initial_tick >= max_game_ticks:
            outcome = "game_tick_budget_exhausted"
            break
        sleep(poll)
    rises = sustained_rises(samples, min_gap_ticks)
    return {"outcome": outcome, "samples": samples, "rises": rises, "min_rises": min_rises,
            "min_gap_ticks": min_gap_ticks, "sustained": rises >= min_rises, "last_tick": last_tick}


def _fund_shortage(session, stream, index: int, bill: dict, message_reader=None, *,
                   max_depth: int = FUNDING_DEPTH, budget: Budget | None = None,
                   stock_reader=None, finite_bill_batches: bool = False) -> list[dict]:
    """Run the existing ``produce`` operation for each short item of a construction bill.

    This mirrors the Laya runner, which turns a material_shortage bill into
    ``op.produce.<item>`` options at the network level fixed when the bill
    failed (training/laya_ops_runner.py shortages/shortage_levels): the stock
    then plus the shortfall, carried in ``bill["levels"]``. An item whose level
    is unknown (no stock observation) is journaled as ``stock_unknown`` and not
    produced. Without funding the replay retries production_service against a
    bill nothing produces.

    A funding attempt can itself answer material_shortage with its own bill
    (live pm-replay-20261115-o3: long-handed-inserter's mall cell needed an
    assembling-machine-1 nothing made, 49 times). Its short items are funded
    next, depth-first, down to ``max_depth`` levels; the runner reaches the
    same item across decisions because ``shortages`` reads every operation's
    latest bill. Within a step each item keeps the highest target requested so
    far and is funded again only when a later bill raises it (a diamond's
    larger branch is never dropped; a cycle stops because targets must rise).
    ``budget`` is checked before every ``produce``, including nested children
    and siblings; once spent nothing further starts and ``budget.cause`` says
    why. Every row records its depth and the item whose bill asked for it.
    """
    funded: list[dict] = []
    targets: dict[str, int] = {}
    unknown: set[str] = set()

    def journal(row: dict) -> None:
        funded.append(row)
        _journal_messages(stream, index, _drain_messages(message_reader))
        stream.write(json.dumps({"event": "pm_replay_funding", **row}) + "\n")
        stream.flush()

    def fund(current: dict, depth: int, parent: str | None) -> None:
        for item in sorted(current.get("short") or {}):
            level = (current.get("levels") or {}).get(item)
            if level is None:
                if item not in unknown and item not in targets:
                    unknown.add(item)
                    journal({"step": index, "item": item, "stock_target": None, "depth": depth,
                             "parent": parent, "outcome": "stock_unknown", "ok": False, "state": None,
                             "message": "no failure-time stock observation; level unknown"})
                continue
            level = max(1, int(level))
            if level <= targets.get(item, 0):
                continue
            if budget is not None:
                cause, _ = budget.check()
                if cause is not None:
                    stream.write(json.dumps({"event": "pm_replay_budget_stop", "step": index,
                                             "cause": cause, "before": {"item": item, "stock_target": level}})
                                 + "\n")
                    stream.flush()
                    return
            targets[item] = level
            stock = _read_stock(stock_reader, session)
            if budget is not None:
                cause, _ = budget.check()
                if cause is not None:
                    stream.write(json.dumps({"event": "pm_replay_budget_stop", "step": index,
                                             "cause": cause, "before": {"item": item, "stock_target": level}})
                                 + "\n")
                    stream.flush()
                    return
            operation, feasibility = 'produce', None
            if finite_bill_batches:
                try:
                    feasibility = session.finite_batch_facts(item, level)
                    if not (isinstance(feasibility, Mapping)
                            and all(type(feasibility.get(key)) is bool
                                    for key in ('known', 'supported', 'eligible'))
                            and feasibility.get('item') == item
                            and type(feasibility.get('stock_target')) is int
                            and feasibility['stock_target'] == level
                            and isinstance(feasibility.get('reason'), str)
                            and feasibility['reason']):
                        feasibility = {'known': False, 'reason': 'malformed_finite_batch_facts'}
                    else:
                        feasibility = dict(feasibility)
                except Exception as error:
                    feasibility = {'known': False, 'reason': f'{type(error).__name__}: {error}'}
                if feasibility.get('supported') is True and feasibility.get('reason') not in {
                        'independent_mall', 'pending_owned_configuration'}:
                    operation = 'produce_finite'
                if budget is not None:
                    cause, _ = budget.check()
                    if cause is not None:
                        stream.write(json.dumps({'event': 'pm_replay_budget_stop', 'step': index,
                            'cause': cause, 'before': {'item': item, 'stock_target': level}}) + '\n')
                        stream.flush()
                        return
            try:
                if feasibility is not None and feasibility.get('known') is not True:
                    result = {'code': 'finite_batch_unknown', 'ok': False,
                              'message': 'finite production feasibility is unknown; no dispatch'}
                else:
                    result = session.run(operation, item=item, stock_target=level)
            except Exception as error:  # noqa: BLE001 -- a failed funding attempt is evidence, not a crash
                result = {"code": "funding_error", "ok": False, "message": f"{type(error).__name__}: {error}"}
            nested = pm_bill(result, stock.network) if result.get("code") == "material_shortage" else None
            row = {"step": index, "item": item, "stock_target": level, "depth": depth, "parent": parent,
                   "outcome": result.get("code"), "ok": result.get("ok"), "state": result.get("state"),
                   "message": result.get("message"), "stock_observation": asdict(stock)}
            if finite_bill_batches:
                row.update(operation=operation, finite_batch_feasibility=feasibility)
            if nested is not None:
                row["bill"] = nested
            journal(row)
            if nested is not None and depth < max_depth:
                fund(nested, depth + 1, item)
            if budget is not None and budget.cause is not None:
                return

    fund(bill, 1, None)
    return funded


class StrictSampler:
    """One strict sidecar for the whole replay; probes read its latest report.

    c6-v3 counts per-machine crafts between consecutive samples of one
    measurement, so a fresh single-sample sidecar per probe can never show
    consumption. The sidecar's first sample is the baseline of the running world.
    """

    def __init__(self, root: Path, report: Path, *, interval: float = 60.0,
                 python: str | None = None, popen=None):
        self.root, self.report, self.interval = root, report, interval
        self.python, self.popen, self.process = python, popen, None

    def start(self) -> None:
        import subprocess
        import sys as _sys
        if self.process is not None:
            return
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.unlink(missing_ok=True)  # never read a report this measurement did not write
        command = [self.python or _sys.executable, str(ROOT / "tools/laya_strict_sidecar.py"),
                   "--world-root", str(self.root), "--out", str(self.report),
                   "--interval", str(self.interval)]
        log = (self.report.parent / "strict-sidecar.log").open("a")
        self.process = (self.popen or subprocess.Popen)(command, cwd=ROOT, stdout=log,
                                                        stderr=subprocess.STDOUT, text=True)

    def probe(self) -> dict:
        try:
            return sidecar_consumption(self.report)
        except (OSError, ValueError):
            return {"pm_consumption_verified": False, "samples": [], "report": str(self.report)}

    def close(self) -> None:
        import subprocess
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=self.interval + 30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


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


def pm_samples(rows: list) -> dict:
    """PM evidence from strict_current rows.

    ``samples``: cumulative PM crafts per row from the measurement's
    ``machine_crafts`` (generation-scoped), with whether c6-v3 passed then;
    these feed ``sustained_rises``. ``observations``: the older one-sample
    signal, a c6-v3 pass whose rolling-window ``machine_consumption`` names
    PM crafts. A window total is not cumulative, so it never feeds rises.
    """
    samples, observations = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        c6 = (row.get("predicate_evidence") or {}).get("C6") or {}
        current = c6.get("predicate_version") == "c6-v3"
        passed = current and (c6.get("status") == "passed" or (row.get("statuses") or {}).get("C6") is True)
        window = ((c6.get("evidence") or {}).get("machine_consumption") or {}).get(PM_ITEM)
        if passed and type(window) is int and window > 0:
            observations.append({"tick": row.get("tick"), "run_id": row.get("run_id"), "window_crafts": window})
        crafts = row.get("machine_crafts")
        if isinstance(crafts, dict) and isinstance(crafts.get("cumulative"), dict):
            count = crafts["cumulative"].get(PM_ITEM, 0)
            if type(count) is int and count >= 0:
                samples.append({"tick": row.get("tick"), "run_id": row.get("run_id"), "crafts": count,
                                "generation": crafts.get("generation"), "c6_passed": bool(passed)})
    return {"samples": samples, "observations": observations}


def sidecar_consumption(path: Path) -> dict:
    """Read PM consumption from a strict-sidecar JSON report.

    The replay session does not write runner evidence, so the sidecar is the
    measurement source. ``pm_consumption_verified`` is the one-sample
    observation (a c6-v3 pass with positive PM window crafts); completion is
    ``acceptance`` over the cumulative ``samples``.
    """
    data = json.loads(path.read_text())
    parsed = pm_samples(data.get("strict_current") or [])
    return {"kind": "laya-pm-sidecar-consumption-v2", "report": str(path),
            "pm_consumption_verified": bool(parsed["observations"]),
            "observations": parsed["observations"], "samples": parsed["samples"],
            "sustained_rises": sustained_rises(parsed["samples"]),
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


@contextmanager
def replay_world(bundle: Path, root: Path, **kwargs):
    """Stop only the fresh disposable world this invocation prepares."""
    import os
    import subprocess
    if root.exists():
        raise ReplayError("replay root must be fresh; existing worlds are preserved")
    try:
        yield materialize(bundle, root, **kwargs)
    finally:
        if (root / "manifest.json").is_file():
            env = dict(os.environ)
            if kwargs.get("runtime_root") is not None:
                env["LAYA_CHECKPOINT_RUNTIME_ROOT"] = str(kwargs["runtime_root"])
            subprocess.run([sys.executable, str(ROOT / "tools/run_laya_saved_world.py"),
                            "stop", "--root", str(root)], cwd=ROOT, env=env,
                           check=True, capture_output=True, text=True, timeout=60)


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



RESTART_CONTEXT = "state/logs/laya-restart-context.json"


def restore_sidecars(bundle: Path, root: Path, *, loader=load) -> dict:
    """Install the bundle's durable deterministic sidecars and name the episode that owns them.

    A cohort restart (training/laya_restart_checkpoint.restore) installs the
    embedded restart roles and every registered ``state/`` file, then opens
    the session under the bundle's episode id, so bootstrap districts,
    material reservations and oil transactions stay owned (live
    pm-replay-20261115-o3: without them 92 of 160 steps were
    ``blocked_source``). The replay uses the same preflight and all-or-nothing
    install (``laya_restart_checkpoint.install_sidecars``), so uncertain
    dispatches stay fenced, spent loan-gate retries stay spent and older
    bundles get unknown-provenance markers. Only the strict snapshot decode is
    skipped: older bundle shapes cannot decode and the replay does not use it.
    """
    from training.laya_restart_checkpoint import install_sidecars
    metadata, context = loader(bundle)
    try:
        return install_sidecars(bundle, root, metadata, context)
    except ValueError as error:
        raise ReplayError(str(error)) from error

if __name__ == '__main__':
    raise SystemExit(main())
